"""Dashboard aggregates and audit serialization."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.state_machine import AWAITING_ARCHITECTURE_APPROVAL
from app.models import (
    AuditEvent,
    AzureSubscription,
    Deployment,
    Organization,
    Project,
    TerraformPlan,
    TerraformProject,
)
from app.services.core import iso


def dashboard_summary(db: Session, organization: Organization) -> dict[str, Any]:
    project_ids = list(
        db.scalars(select(Project.id).where(Project.organization_id == organization.id))
    )

    def count_deployments(*statuses: str) -> int:
        if not project_ids:
            return 0
        return int(
            db.scalar(
                select(func.count())
                .select_from(Deployment)
                .where(
                    Deployment.project_id.in_(project_ids),
                    Deployment.status.in_(statuses),
                )
            )
            or 0
        )

    status_rows = db.execute(
        select(Project.status, func.count())
        .where(Project.organization_id == organization.id)
        .group_by(Project.status)
    ).all()
    status_counts = {status: int(count) for status, count in status_rows}

    pending_architecture = int(
        db.scalar(
            select(func.count())
            .select_from(Project)
            .where(
                Project.organization_id == organization.id,
                Project.status == AWAITING_ARCHITECTURE_APPROVAL,
            )
        )
        or 0
    )
    pending_plans = int(
        db.scalar(
            select(func.count())
            .select_from(TerraformPlan)
            .join(TerraformProject, TerraformProject.id == TerraformPlan.terraform_project_id)
            .join(Project, Project.id == TerraformProject.project_id)
            .where(
                Project.organization_id == organization.id,
                TerraformPlan.status == "generated",
            )
        )
        or 0
    )
    subscriptions = int(
        db.scalar(
            select(func.count())
            .select_from(AzureSubscription)
            .where(AzureSubscription.organization_id == organization.id)
        )
        or 0
    )
    recent = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.organization_id == organization.id)
            .order_by(AuditEvent.timestamp.desc())
            .limit(15)
        )
    )

    return {
        "projects": len(project_ids),
        "active_deployments": count_deployments("queued", "running"),
        "successful_deployments": count_deployments("succeeded"),
        "failed_deployments": count_deployments("failed", "cancelled"),
        "pending_architecture_approvals": pending_architecture,
        "pending_deployment_approvals": pending_plans,
        "pending_approvals": pending_architecture + pending_plans,
        "subscriptions": subscriptions,
        "status_counts": status_counts,
        "recent_activity": [serialize_audit(event) for event in recent],
    }


def serialize_audit(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "organization_id": event.organization_id,
        "project_id": event.project_id,
        "user_id": event.user_id,
        "action": event.action,
        "metadata": event.metadata_,
        "timestamp": iso(event.timestamp),
    }


def project_audit(db: Session, project_id: str, limit: int = 200) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.project_id == project_id)
            .order_by(AuditEvent.timestamp.desc())
            .limit(min(limit, 500))
        )
    )
