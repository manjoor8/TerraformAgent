"""Azure subscription connections (non-secret metadata only)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import AppError, Conflict, NotFound
from app.models import AzureSubscription, Organization, Project, User
from app.services.azure import AzureClient, AzureAuthError
from app.services.core import iso

AUTH_METHODS = {"managed_identity", "oidc", "service_principal", "azure_cli"}


def list_subscriptions(db: Session, organization_id: str) -> list[AzureSubscription]:
    return list(
        db.scalars(
            select(AzureSubscription)
            .where(AzureSubscription.organization_id == organization_id)
            .order_by(AzureSubscription.created_at)
        )
    )


def serialize(subscription: AzureSubscription) -> dict[str, Any]:
    return {
        "id": subscription.id,
        "organization_id": subscription.organization_id,
        "tenant_id": subscription.tenant_id,
        "subscription_id": subscription.subscription_id,
        "subscription_name": subscription.subscription_name,
        "auth_method": subscription.auth_method,
        "connection_status": subscription.connection_status,
        "default_region": subscription.default_region,
        "permissions": subscription.permissions,
        "created_at": iso(subscription.created_at),
    }


def create_subscription(
    db: Session,
    organization: Organization,
    user: User,
    *,
    tenant_id: str,
    subscription_id: str,
    subscription_name: str,
    auth_method: str = "service_principal",
    default_region: str = "eastus",
) -> AzureSubscription:
    if auth_method not in AUTH_METHODS:
        raise AppError(f"auth_method must be one of {sorted(AUTH_METHODS)}")
    if not tenant_id.strip() or not subscription_id.strip():
        raise AppError("tenant_id and subscription_id are required")
    existing = db.scalar(
        select(AzureSubscription).where(
            AzureSubscription.organization_id == organization.id,
            AzureSubscription.subscription_id == subscription_id,
        )
    )
    if existing:
        raise Conflict("This subscription is already connected to the organization")
    subscription = AzureSubscription(
        organization_id=organization.id,
        tenant_id=tenant_id.strip(),
        subscription_id=subscription_id.strip(),
        subscription_name=subscription_name.strip() or subscription_id.strip(),
        auth_method=auth_method,
        default_region=default_region,
        connection_status="unverified",
    )
    db.add(subscription)
    db.flush()
    audit.record(
        db,
        organization_id=organization.id,
        user_id=user.id,
        action="subscription.connected",
        metadata={
            "subscription_id": subscription.subscription_id,
            "tenant_id": subscription.tenant_id,
            "auth_method": auth_method,
        },
    )
    return subscription


def verify_subscription(
    db: Session, subscription: AzureSubscription, user: User
) -> dict[str, Any]:
    organization_id = subscription.organization_id
    try:
        client = AzureClient(subscription.subscription_id, subscription.tenant_id)
        details = client.verify_connection()
        if details.get("tenant_id") and details["tenant_id"] != subscription.tenant_id:
            raise AppError(
                "The authenticated identity belongs to a different tenant than the connection metadata"
            )
        subscription.connection_status = "verified" if details.get("can_deploy") else "read_only"
        subscription.permissions = details
        subscription.subscription_name = details.get("subscription_name") or subscription.subscription_name
    except (AzureAuthError, AppError) as exc:
        subscription.connection_status = "failed"
        subscription.permissions = {"error": str(exc)[:1000]}
        audit.record(
            db,
            organization_id=organization_id,
            user_id=user.id,
            action="subscription.verification_failed",
            metadata={"subscription_id": subscription.subscription_id, "error": str(exc)[:500]},
        )
        raise
    audit.record(
        db,
        organization_id=organization_id,
        user_id=user.id,
        action="subscription.verified",
        metadata={
            "subscription_id": subscription.subscription_id,
            "status": subscription.connection_status,
            "can_deploy": bool(subscription.permissions.get("can_deploy")),
        },
    )
    db.flush()
    return serialize(subscription)


def delete_subscription(db: Session, subscription: AzureSubscription, user: User) -> None:
    in_use = db.scalar(select(Project).where(Project.azure_subscription_id == subscription.id))
    if in_use:
        raise Conflict("A project references this subscription; reassign it first")
    audit.record(
        db,
        organization_id=subscription.organization_id,
        user_id=user.id,
        action="subscription.disconnected",
        metadata={"subscription_id": subscription.subscription_id},
    )
    db.delete(subscription)


def get_subscription(db: Session, subscription_id: str) -> AzureSubscription:
    subscription = db.get(AzureSubscription, subscription_id)
    if not subscription:
        raise NotFound("Subscription not found")
    return subscription
