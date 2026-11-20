"""Auth, organizations, subscriptions, dashboard and system endpoints."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentAccess, CurrentUser, DbSession
from app.config import get_settings
from app.core import audit
from app.core.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized
from app.core.security import create_access_token, hash_password, slugify, verify_password
from app.llm.factory import llm_status
from app.models import Membership, Organization, User
from app.services import dashboard as dashboard_service
from app.services import subscriptions as subscription_service
from app.services import llm_config as llm_config_service
from app.services.artifacts import store
from app.services.core import iso, memberships_for
from app.terraform import policy
from app.terraform.executor import executor_info

router = APIRouter()
settings = get_settings()


def _user_payload(db: Session, user: User) -> dict[str, Any]:
    memberships = memberships_for(db, user)
    organizations = []
    for membership in memberships:
        organization = db.get(Organization, membership.organization_id)
        if organization:
            organizations.append(
                {
                    "id": organization.id,
                    "name": organization.name,
                    "slug": organization.slug,
                    "role": membership.role,
                }
            )
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "authentication_provider": user.authentication_provider,
        "is_platform_admin": llm_config_service.is_platform_admin(user),
        "created_at": iso(user.created_at),
        "organizations": organizations,
    }


@router.post("/auth/register")
def register(payload: dict[str, Any], db: DbSession) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    organization_name = str(payload.get("organization_name", "")).strip() or f"{name}'s Organization"
    if not name or not email or not password:
        raise AppError("name, email and password are required")
    if "@" not in email:
        raise AppError("A valid email address is required")
    if db.scalar(select(User).where(User.email == email)):
        raise Conflict("An account with this email already exists")
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        raise AppError(str(exc)) from exc
    user = User(name=name, email=email, password_hash=password_hash)
    db.add(user)
    db.flush()
    base_slug = slugify(organization_name)[:80]
    slug = base_slug
    counter = 1
    while db.scalar(select(Organization).where(Organization.slug == slug)):
        counter += 1
        slug = f"{base_slug}-{counter}"
    organization = Organization(name=organization_name[:200], slug=slug)
    db.add(organization)
    db.flush()
    db.add(Membership(organization_id=organization.id, user_id=user.id, role="owner"))
    audit.record(
        db,
        organization_id=organization.id,
        user_id=user.id,
        action="organization.created",
        metadata={"name": organization.name},
    )
    audit.record(
        db,
        organization_id=organization.id,
        user_id=user.id,
        action="user.registered",
        metadata={"email": email},
    )
    db.commit()
    return {
        "access_token": create_access_token(user.id, user.email),
        "token_type": "bearer",
        "user": _user_payload(db, user),
    }


@router.post("/auth/login")
def login(payload: dict[str, Any], db: DbSession) -> dict[str, Any]:
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(password, user.password_hash):
        raise Unauthorized("Invalid email or password")
    return {
        "access_token": create_access_token(user.id, user.email),
        "token_type": "bearer",
        "user": _user_payload(db, user),
    }


@router.get("/auth/me")
def me(db: DbSession, user: CurrentUser) -> dict[str, Any]:
    return _user_payload(db, user)


@router.get("/organizations")
def list_organizations(db: DbSession, user: CurrentUser) -> dict[str, Any]:
    payload = _user_payload(db, user)["organizations"]
    return {"items": payload, "total": len(payload)}


@router.get("/organizations/{organization_id}")
def get_organization(
    organization_id: str, db: DbSession, access: CurrentAccess
) -> dict[str, Any]:
    if access.organization.id != organization_id:
        raise NotFound("Organization not found")
    from app.models import Project

    project_count = len(
        list(db.scalars(select(Project.id).where(Project.organization_id == organization_id)))
    )
    member_count = len(
        list(
            db.scalars(
                select(Membership.id).where(Membership.organization_id == organization_id)
            )
        )
    )
    return {
        "id": access.organization.id,
        "name": access.organization.name,
        "slug": access.organization.slug,
        "settings": access.organization.settings,
        "role": access.role,
        "counts": {"projects": project_count, "members": member_count},
    }


@router.patch("/organizations/{organization_id}")
def update_organization(
    organization_id: str, payload: dict[str, Any], db: DbSession, access: CurrentAccess
) -> dict[str, Any]:
    if access.organization.id != organization_id:
        raise NotFound("Organization not found")
    if not access.can_admin:
        raise Forbidden("Admin role required")
    if "name" in payload and str(payload["name"]).strip():
        access.organization.name = str(payload["name"]).strip()[:200]
    if "settings" in payload and isinstance(payload["settings"], dict):
        access.organization.settings = payload["settings"]
    audit.record(
        db,
        organization_id=organization_id,
        user_id=access.user.id,
        action="organization.updated",
        metadata={"fields": list(payload)},
    )
    db.commit()
    return get_organization(organization_id, db, access)


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------
@router.get("/subscriptions")
def list_subscriptions(db: DbSession, access: CurrentAccess) -> dict[str, Any]:
    items = [
        subscription_service.serialize(s)
        for s in subscription_service.list_subscriptions(db, access.organization.id)
    ]
    return {"items": items, "total": len(items)}


@router.post("/subscriptions")
def create_subscription(payload: dict[str, Any], db: DbSession, access: CurrentAccess) -> dict[str, Any]:
    if not access.can_write:
        raise Forbidden("Member role required")
    subscription = subscription_service.create_subscription(
        db,
        access.organization,
        access.user,
        tenant_id=str(payload.get("tenant_id", "")),
        subscription_id=str(payload.get("subscription_id", "")),
        subscription_name=str(payload.get("subscription_name", "")),
        auth_method=str(payload.get("auth_method", "service_principal")),
        default_region=str(payload.get("default_region", "eastus")),
    )
    db.commit()
    return subscription_service.serialize(subscription)


@router.post("/subscriptions/{subscription_id}/verify")
def verify_subscription(
    subscription_id: str, db: DbSession, access: CurrentAccess
) -> dict[str, Any]:
    subscription = subscription_service.get_subscription(db, subscription_id)
    if subscription.organization_id != access.organization.id:
        raise NotFound("Subscription not found")
    if not access.can_write:
        raise Forbidden("Member role required")
    payload = subscription_service.verify_subscription(db, subscription, access.user)
    db.commit()
    return payload


@router.delete("/subscriptions/{subscription_id}")
def delete_subscription(
    subscription_id: str, db: DbSession, access: CurrentAccess
) -> dict[str, bool]:
    subscription = subscription_service.get_subscription(db, subscription_id)
    if subscription.organization_id != access.organization.id:
        raise NotFound("Subscription not found")
    if not access.can_admin:
        raise Forbidden("Admin role required")
    subscription_service.delete_subscription(db, subscription, access.user)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Dashboard & system
# ---------------------------------------------------------------------------
@router.get("/dashboard/summary")
def dashboard(db: DbSession, access: CurrentAccess) -> dict[str, Any]:
    return dashboard_service.dashboard_summary(db, access.organization)


@router.get("/system/health")
def health(db: DbSession) -> dict[str, Any]:
    from sqlalchemy import text

    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/system/info")
def system_info(db: DbSession) -> dict[str, Any]:
    terraform = executor_info()
    return {
        "app": "TerraformAgent",
        "version": "1.0.0",
        "environment": settings.environment,
        "llm": llm_status(),
        "terraform": terraform,
        "database": "postgresql" if not settings.is_sqlite else "sqlite",
        "artifact_store": "filesystem",
        "storage_root_configured": bool(store.root),
        "features": {
            "architecture_versions": True,
            "private_pricing_api": settings.live_pricing,
            "deterministic_policy_engine": True,
            "sse_events": True,
            "azure_verification": True,
        },
        "policy_rules": [
            {"id": rule.id, "title": rule.title, "severity": rule.severity, "can_disable": rule.can_disable}
            for rule in policy.RULES.values()
        ],
    }


@router.get("/system/policies")
def system_policies() -> dict[str, Any]:
    return {
        "items": [
            {
                "id": rule.id,
                "title": rule.title,
                "severity": rule.severity,
                "can_disable": rule.can_disable,
                "message": rule.message,
                "remediation": rule.remediation,
            }
            for rule in policy.RULES.values()
        ],
        "non_disableable": sorted(policy.NON_DISABLEABLE),
    }


@router.get("/system/llm")
def get_llm_configuration(db: DbSession, user: CurrentUser) -> dict[str, Any]:
    llm_config_service.require_platform_admin(user)
    return llm_config_service.serialize_config(db)


@router.put("/system/llm")
def update_llm_configuration(
    payload: dict[str, Any], db: DbSession, access: CurrentAccess
) -> dict[str, Any]:
    llm_config_service.require_platform_admin(access.user)
    result = llm_config_service.save_configuration(
        db,
        access.user,
        provider=str(payload.get("provider", "deepseek")),
        model=str(payload.get("model", "")),
        base_url=str(payload.get("base_url", "")),
        api_key=str(payload.get("api_key", "")) or None,
        organization_id=access.organization.id,
    )
    db.commit()
    return result


@router.post("/system/llm/test")
def test_llm_configuration(
    payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    llm_config_service.require_platform_admin(user)
    return llm_config_service.test_configuration(
        db,
        user,
        provider=payload.get("provider"),
        model=payload.get("model"),
        base_url=payload.get("base_url"),
        api_key=payload.get("api_key"),
    )


@router.delete("/system/llm")
def reset_llm_configuration(db: DbSession, access: CurrentAccess) -> dict[str, Any]:
    llm_config_service.require_platform_admin(access.user)
    result = llm_config_service.reset_configuration(
        db, access.user, access.organization.id
    )
    db.commit()
    return result


@router.get("/audit")
def organization_audit(
    db: DbSession,
    access: CurrentAccess,
    action: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    from app.models import AuditEvent

    query = select(AuditEvent).where(AuditEvent.organization_id == access.organization.id)
    if action:
        query = query.where(AuditEvent.action == action)
    events = list(db.scalars(query.order_by(AuditEvent.timestamp.desc()).limit(limit)))
    items = [dashboard_service.serialize_audit(event) for event in events]
    return {"items": items, "total": len(items)}
