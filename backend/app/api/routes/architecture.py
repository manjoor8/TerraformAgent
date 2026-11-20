"""Architecture generation, review, versioning, approval and artifacts."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.api.routes.projects import Reader, Writer, project_role
from app.agents.heuristics import build_inventory, generate_mermaid
from app.agents.schemas import ArchitectureSpec
from app.core import audit
from app.core.errors import NotFound
from app.models import (
    Approval,
    ArchitectureDecision,
    ArchitectureVersion,
    Project,
    User,
)
from app.services import architecture as architecture_service
from app.services.core import Access, iso, project_access

router = APIRouter()


def _version_access(db: Session, user: User, version_id: str, min_role: str = "viewer") -> tuple[ArchitectureVersion, Project, Access]:
    version = db.get(ArchitectureVersion, version_id)
    if not version:
        raise NotFound("Architecture version not found")
    access = project_access(db, user, version.project_id, min_role)
    assert access.project is not None
    return version, access.project, access


def _payload(db: Session, version: ArchitectureVersion, *, full: bool = True) -> dict[str, Any]:
    payload = architecture_service.serialize_version(version, full=full)
    approval = db.scalar(
        select(Approval).where(
            Approval.architecture_version_id == version.id,
            Approval.approval_type == "architecture",
            Approval.status == "active",
        )
    )
    payload["active_approval"] = (
        {
            "id": approval.id,
            "artifact_hash": approval.artifact_hash,
            "approved_by": approval.approved_by,
            "approved_at": iso(approval.approved_at),
        }
        if approval
        else None
    )
    return payload


@router.post("/projects/{project_id}/architecture/generate")
def generate_architecture(
    project_id: str, payload: dict[str, Any], db: DbSession, access: Writer
) -> dict[str, Any]:
    base_version_id = payload.get("base_version_id")
    base_version = None
    if base_version_id:
        base_version = db.get(ArchitectureVersion, base_version_id)
        if not base_version or base_version.project_id != project_id:
            raise NotFound("Base architecture version not found")
    version = architecture_service.generate_architecture(
        db,
        access.project,
        access.user,
        instructions=str(payload.get("instructions", "")).strip() or None,
        base_version=base_version,
    )
    db.commit()
    return _payload(db, version)


@router.get("/projects/{project_id}/architecture")
def current_architecture(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    version = architecture_service.latest_version(db, project_id)
    if not version:
        raise NotFound("No architecture has been generated yet")
    return _payload(db, version)


@router.get("/projects/{project_id}/architecture/versions")
def architecture_versions(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    versions = architecture_service.list_versions(db, project_id)
    return {
        "items": [architecture_service.serialize_version(v, full=False) for v in versions],
        "total": len(versions),
    }


@router.get("/architecture/{version_id}")
def get_architecture(version_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    version, project, access = _version_access(db, user, version_id)
    payload = _payload(db, version)
    decisions = list(
        db.scalars(
            select(ArchitectureDecision)
            .where(ArchitectureDecision.architecture_version_id == version.id)
            .order_by(ArchitectureDecision.created_at)
        )
    )
    payload["decisions"] = [
        {
            "id": decision.id,
            "decision": decision.decision,
            "rationale": decision.rationale,
            "source_requirement": decision.source_requirement,
            "alternates_considered": decision.alternates_considered,
            "status": decision.status,
        }
        for decision in decisions
    ]
    approval = db.scalar(
        select(Approval).where(
            Approval.architecture_version_id == version.id,
            Approval.approval_type == "architecture",
            Approval.status == "active",
        )
    )
    payload["active_approval"] = (
        {
            "id": approval.id,
            "artifact_hash": approval.artifact_hash,
            "approved_by": approval.approved_by,
            "approved_at": iso(approval.approved_at),
        }
        if approval
        else None
    )
    payload["project_status"] = project.status
    return payload


@router.post("/architecture/{version_id}/review")
def review_architecture(version_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    version, project, access = _version_access(db, user, version_id, "member")
    review = architecture_service.run_review(db, version, access.user)
    db.commit()
    return {"review": review.model_dump(mode="json"), "version": architecture_service.serialize_version(version, full=False)}


@router.patch("/architecture/{version_id}/recommendations/{recommendation_id}")
def update_recommendation(
    version_id: str,
    recommendation_id: str,
    payload: dict[str, Any],
    db: DbSession,
    user: CurrentUser,
) -> dict[str, Any]:
    version, _project, access = _version_access(db, user, version_id, "member")
    recommendation = architecture_service.update_recommendation(
        db,
        version,
        access.user,
        recommendation_id,
        str(payload.get("status", "accepted")),
        payload.get("note"),
    )
    db.commit()
    return {"recommendation": recommendation, "version": architecture_service.serialize_version(version, full=False)}


@router.post("/architecture/{version_id}/submit")
def submit_for_approval(version_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    version, _project, access = _version_access(db, user, version_id, "member")
    version = architecture_service.submit_for_approval(db, version, access.user)
    db.commit()
    return architecture_service.serialize_version(version)


@router.post("/architecture/{version_id}/approve")
def approve_architecture(
    version_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    version, project, access = _version_access(db, user, version_id, "member")
    approval = architecture_service.approve_architecture(
        db, version, access.user, note=str(payload.get("note", ""))
    )
    db.commit()
    return {
        "approval": {
            "id": approval.id,
            "artifact_hash": approval.artifact_hash,
            "approved_at": iso(approval.approved_at),
            "approved_by": approval.approved_by,
        },
        "version": architecture_service.serialize_version(version, full=False),
        "project_status": project.status,
    }


@router.post("/architecture/{version_id}/reject")
def reject_architecture(
    version_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    version, project, access = _version_access(db, user, version_id, "member")
    reason = str(payload.get("reason", "")).strip() or "No reason provided"
    version = architecture_service.reject_architecture(db, version, access.user, reason)
    db.commit()
    return {"version": architecture_service.serialize_version(version, full=False), "project_status": project.status}


@router.post("/architecture/{version_id}/request-changes")
def request_changes(
    version_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    version, _project, access = _version_access(db, user, version_id, "member")
    feedback = str(payload.get("feedback", "")).strip()
    if not feedback:
        raise NotFound("Feedback is required")
    new_version = architecture_service.request_changes(db, version, access.user, feedback)
    db.commit()
    return architecture_service.serialize_version(new_version)


@router.get("/architecture/{version_id}/diagram")
def architecture_diagram(version_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    version, _project, _access = _version_access(db, user, version_id)
    return {
        "format": "mermaid",
        "mermaid": architecture_service.get_diagram(version),
    }


@router.get("/architecture/{version_id}/cost")
def architecture_cost(version_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    version, _project, _access = _version_access(db, user, version_id)
    return {
        "currency": version.cost_estimate.get("currency", "USD"),
        "monthly_total": version.cost_estimate.get("monthly_total", 0),
        "lines": version.cost_estimate.get("lines", []),
        "disclaimer": version.cost_estimate.get("disclaimer", ""),
        "estimate_version": version.version,
    }


@router.get("/architecture/{version_id}/inventory")
def architecture_inventory(version_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    version, _project, _access = _version_access(db, user, version_id)
    spec = ArchitectureSpec.model_validate(version.architecture_json)
    return {"items": build_inventory(spec), "total": len(spec.resources)}
