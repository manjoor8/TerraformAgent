"""Append-only audit trail. Services call record(); nothing updates/deletes."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.core.security import redact_obj


def record(
    db: Session,
    *,
    organization_id: str,
    action: str,
    project_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=organization_id,
        project_id=project_id,
        user_id=user_id,
        action=action,
        metadata_=redact_obj(metadata or {}),
    )
    db.add(event)
    return event
