"""Architecture generation, review, versioning and approval."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import heuristics, runtime
from app.agents.heuristics import AgentContext
from app.agents.schemas import ArchitectureReview, ArchitectureSpec, RequirementAnalysis
from app.agents.validation import ArchitectureValidationError, normalize_and_validate
from app.core import audit
from app.core.errors import ApprovalError, Conflict, InvalidTransition, NotFound
from app.core.security import hash_json
from app.core.state_machine import (
    ARCHITECTURE_APPROVED,
    ARCHITECTURE_READY,
    AWAITING_ARCHITECTURE_APPROVAL,
    COMPLETED,
    DEPLOYING,
    DRAFT,
    GATHERING_REQUIREMENTS,
    PLANNING,
    TRANSITIONS,
    ensure_transition,
)
from app.models import (
    AgentRun,
    Approval,
    ArchitectureDecision,
    ArchitectureVersion,
    Project,
    User,
    new_id,
)
from app.services.core import (
    invalidate_approvals,
    iso,
    load_agent_context,
    transition,
)
from app.services.requirements import upsert_from_analysis

TERMINAL_FOR_CHANGE = {"deploying"}


def latest_version(db: Session, project_id: str) -> ArchitectureVersion | None:
    return db.scalar(
        select(ArchitectureVersion)
        .where(ArchitectureVersion.project_id == project_id)
        .order_by(ArchitectureVersion.version.desc())
    )


def get_version(db: Session, version_id: str) -> ArchitectureVersion:
    version = db.get(ArchitectureVersion, version_id)
    if not version:
        raise NotFound("Architecture version not found")
    return version


def list_versions(db: Session, project_id: str) -> list[ArchitectureVersion]:
    return list(
        db.scalars(
            select(ArchitectureVersion)
            .where(ArchitectureVersion.project_id == project_id)
            .order_by(ArchitectureVersion.version.desc())
        )
    )


def serialize_version(version: ArchitectureVersion, *, full: bool = True) -> dict[str, Any]:
    spec = ArchitectureSpec.model_validate(version.architecture_json)
    payload: dict[str, Any] = {
        "id": version.id,
        "project_id": version.project_id,
        "version": version.version,
        "summary": version.summary,
        "region": spec.region,
        "approval_status": version.approval_status,
        "created_at": iso(version.created_at),
        "approved_at": iso(version.approved_at),
        "created_by_agent": version.created_by_agent,
        "resource_count": len(spec.resources),
        "module_count": len({r.module for r in spec.resources}),
        "cost": version.cost_estimate,
        "assumptions": version.assumptions,
        "inventory": heuristics.build_inventory(spec),
        "review": version.review,
    }
    if full:
        payload["architecture"] = version.architecture_json
        payload["diagram"] = version.diagram_source
    return payload


def _log_agent_run(
    db: Session,
    project: Project,
    agent: str,
    result: Any,
    input_summary: str,
    *,
    output_summary: dict[str, Any] | None = None,
) -> AgentRun:
    run = AgentRun(
        project_id=project.id,
        agent=agent,
        status="succeeded",
        input_summary=input_summary[:2000],
        output=output_summary or {},
        provider=getattr(result, "provider", None),
        model=getattr(result, "model", None),
        latency_ms=getattr(result, "latency_ms", None),
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    return run


def _prepare_for_change(db: Session, project: Project, user: User) -> None:
    if project.status in (DEPLOYING, PLANNING):
        raise Conflict("Cannot modify the architecture while an execution is running")
    if project.status == DRAFT:
        transition(db, project, GATHERING_REQUIREMENTS, user_id=user.id, reason="Architecture requested")
    allowed = TRANSITIONS.get(project.status, set())
    if ARCHITECTURE_APPROVED in allowed and project.status != ARCHITECTURE_APPROVED:
        transition(
            db,
            project,
            ARCHITECTURE_APPROVED,
            user_id=user.id,
            reason="Architecture being revised",
        )
        allowed = TRANSITIONS.get(project.status, set())
    if ARCHITECTURE_READY not in allowed and COMPLETED in allowed:
        transition(
            db,
            project,
            COMPLETED,
            user_id=user.id,
            reason="Architecture being revised after deployment",
        )
        allowed = TRANSITIONS.get(project.status, set())
    if ARCHITECTURE_READY in allowed:
        transition(
            db,
            project,
            ARCHITECTURE_READY,
            user_id=user.id,
            reason="Architecture being revised",
        )
        return
    if project.status != ARCHITECTURE_READY:
        raise Conflict(f"Cannot revise the architecture from project state '{project.status}'")


def generate_architecture(
    db: Session,
    project: Project,
    user: User,
    *,
    instructions: str | None = None,
    base_version: ArchitectureVersion | None = None,
    analysis: RequirementAnalysis | None = None,
) -> ArchitectureVersion:
    _prepare_for_change(db, project, user)
    ctx = load_agent_context(db, project)
    if instructions:
        ctx.feedback = instructions
        ctx.messages = [*ctx.messages, instructions]
    if base_version:
        ctx.existing_architecture = base_version.architecture_json

    if analysis is None:
        analysis_result = runtime.requirement_analyst.run(ctx)
        analysis = analysis_result.output
        upsert_from_analysis(db, project, analysis, user)
        _log_agent_run(
            db,
            project,
            "requirement_analyst",
            analysis_result,
            input_summary=(instructions or project.description or "")[:500],
            output_summary={
                "readiness_score": analysis.readiness_score,
                "requirements": len(analysis.requirements),
                "questions": len(analysis.questions),
                "conflicts": len(analysis.conflicts),
            },
        )

    architect_result = runtime.azure_solution_architect.run(ctx, analysis)
    spec = architect_result.output
    _log_agent_run(
        db,
        project,
        "azure_solution_architect",
        architect_result,
        input_summary=analysis.summary[:500],
        output_summary={
            "resources": len(spec.resources),
            "modules": sorted({r.module for r in spec.resources}),
            "monthly_cost": spec.cost.monthly_total,
            "notes": architect_result.notes,
        },
    )

    review_result = runtime.architecture_reviewer.run(spec, project.environment)
    review = review_result.output
    _log_agent_run(
        db,
        project,
        "architecture_reviewer",
        review_result,
        input_summary=spec.summary[:500],
        output_summary={
            "score": review.score,
            "recommendations": len(review.recommendations),
            "risks": len(review.risks),
        },
    )

    diagram = heuristics.generate_mermaid(spec)
    inventory = heuristics.build_inventory(spec)
    review_payload = {
        **review.model_dump(mode="json"),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "inventory": inventory,
    }

    next_version = int(
        db.scalar(
            select(func.max(ArchitectureVersion.version)).where(
                ArchitectureVersion.project_id == project.id
            )
        )
        or 0
    ) + 1
    previous = latest_version(db, project.id)
    if previous:
        if previous.approval_status in ("approved", "awaiting_approval"):
            previous.approval_status = "superseded"

    invalidate_approvals(db, project.id, "New architecture version generated")

    version = ArchitectureVersion(
        id=new_id(),
        project_id=project.id,
        version=next_version,
        architecture_json=spec.model_dump(mode="json"),
        diagram_source=diagram,
        cost_estimate=spec.cost.model_dump(mode="json"),
        summary=spec.summary,
        assumptions=spec.assumptions,
        review=review_payload,
        approval_status="draft",
        created_by_agent="azure_solution_architect",
    )
    db.add(version)
    db.flush()
    for decision in spec.decisions:
        db.add(
            ArchitectureDecision(
                architecture_version_id=version.id,
                decision=decision.decision,
                rationale=decision.rationale,
                source_requirement=decision.source_requirement,
                alternates_considered=decision.alternates_considered,
            )
        )
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="architecture.generated",
        metadata={
            "version": version.version,
            "version_id": version.id,
            "resources": len(spec.resources),
            "monthly_cost": spec.cost.monthly_total,
            "llm": architect_result.used_llm,
        },
    )
    transition(
        db,
        project,
        ARCHITECTURE_READY,
        user_id=user.id,
        reason=f"Architecture v{version.version} generated",
    )
    db.flush()
    return version


def run_review(db: Session, version: ArchitectureVersion, user: User) -> ArchitectureReview:
    project = db.get(Project, version.project_id)
    if not project:
        raise NotFound("Project not found")
    spec = ArchitectureSpec.model_validate(version.architecture_json)
    result = runtime.architecture_reviewer.run(spec, project.environment)
    review = result.output
    version.review = {
        **review.model_dump(mode="json"),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "inventory": heuristics.build_inventory(spec),
    }
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="architecture.reviewed",
        metadata={"version": version.version, "score": review.score, "recommendations": len(review.recommendations)},
    )
    db.flush()
    return review


def submit_for_approval(db: Session, version: ArchitectureVersion, user: User) -> ArchitectureVersion:
    project = db.get(Project, version.project_id)
    if not project:
        raise NotFound("Project not found")
    latest = latest_version(db, project.id)
    if not latest or latest.id != version.id:
        raise Conflict("Only the latest architecture version can be submitted for approval")
    if not version.review:
        raise Conflict("Run the architecture review before submitting for approval")
    version.approval_status = "awaiting_approval"
    if project.status != AWAITING_ARCHITECTURE_APPROVAL:
        if project.status == ARCHITECTURE_READY or project.status == GATHERING_REQUIREMENTS:
            transition(
                db,
                project,
                AWAITING_ARCHITECTURE_APPROVAL,
                user_id=user.id,
                reason=f"Architecture v{version.version} submitted for approval",
            )
        else:
            transition(
                db,
                project,
                AWAITING_ARCHITECTURE_APPROVAL,
                user_id=user.id,
                reason=f"Architecture v{version.version} submitted for approval",
            )
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="architecture.submitted",
        metadata={"version": version.version, "version_id": version.id},
    )
    db.flush()
    return version


def approve_architecture(db: Session, version: ArchitectureVersion, user: User, note: str = "") -> Approval:
    project = db.get(Project, version.project_id)
    if not project:
        raise NotFound("Project not found")
    latest = latest_version(db, project.id)
    if not latest or latest.id != version.id:
        raise ApprovalError("This architecture version is no longer current")
    if version.approval_status not in ("awaiting_approval", "draft"):
        raise ApprovalError(f"Architecture is not awaiting approval (status: {version.approval_status})")
    if version.approval_status == "draft":
        raise ApprovalError("Submit the architecture for review before approving it")
    review = version.review or {}
    if not review.get("recommendations"):
        raise ApprovalError("The architecture review must run before approval")
    unresolved = [
        rec
        for rec in review.get("recommendations", [])
        if rec.get("mandatory") and rec.get("status", "proposed") == "proposed"
    ]
    if unresolved:
        raise ApprovalError(
            "Resolve mandatory recommendations before approval: "
            + ", ".join(rec.get("recommendation", rec.get("id", "")) for rec in unresolved)
        )
    try:
        spec = ArchitectureSpec.model_validate(version.architecture_json)
        normalize_and_validate(spec, environment=project.environment, use_live_pricing=False)
    except (ArchitectureValidationError, ValueError) as exc:
        raise ApprovalError(f"Architecture failed validation: {exc}") from exc

    invalidate_approvals(
        db, project.id, "New architecture approval", approval_types=("architecture",)
    )
    approval = Approval(
        project_id=project.id,
        approval_type="architecture",
        artifact_hash=hash_json(version.architecture_json),
        architecture_version_id=version.id,
        approved_by=user.id,
        status="active",
    )
    db.add(approval)
    version.approval_status = "approved"
    version.approved_at = datetime.now(timezone.utc)
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="architecture.approved",
        metadata={
            "version": version.version,
            "version_id": version.id,
            "approval_id": approval.id,
            "artifact_hash": approval.artifact_hash,
            "note": note,
        },
    )
    if project.status != ARCHITECTURE_APPROVED:
        transition(
            db,
            project,
            ARCHITECTURE_APPROVED,
            user_id=user.id,
            reason=f"Architecture v{version.version} approved",
        )
    db.flush()
    return approval


def reject_architecture(
    db: Session, version: ArchitectureVersion, user: User, reason: str
) -> ArchitectureVersion:
    project = db.get(Project, version.project_id)
    if not project:
        raise NotFound("Project not found")
    version.approval_status = "rejected"
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="architecture.rejected",
        metadata={"version": version.version, "reason": reason},
    )
    if project.status == AWAITING_ARCHITECTURE_APPROVAL:
        transition(
            db,
            project,
            ARCHITECTURE_READY,
            user_id=user.id,
            reason="Architecture rejected; changes requested",
        )
    db.flush()
    return version


def request_changes(
    db: Session, version: ArchitectureVersion, user: User, feedback: str
) -> ArchitectureVersion:
    project = db.get(Project, version.project_id)
    if not project:
        raise NotFound("Project not found")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="architecture.changes_requested",
        metadata={"version": version.version, "feedback": feedback[:1000]},
    )
    return generate_architecture(db, project, user, instructions=feedback, base_version=version)


def update_recommendation(
    db: Session,
    version: ArchitectureVersion,
    user: User,
    recommendation_id: str,
    status: str,
    note: str | None = None,
) -> dict[str, Any]:
    project = db.get(Project, version.project_id)
    if not project:
        raise NotFound("Project not found")
    if status not in ("accepted", "rejected", "proposed"):
        raise Conflict("Recommendation status must be accepted, rejected or proposed")
    review = dict(version.review or {})
    recommendations = list(review.get("recommendations", []))
    found = None
    for rec in recommendations:
        if rec.get("id") == recommendation_id:
            rec["status"] = status
            if note:
                rec["note"] = note
            found = rec
            break
    if not found:
        raise NotFound("Recommendation not found")
    review["recommendations"] = recommendations
    version.review = review
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="recommendation.updated",
        metadata={"version": version.version, "recommendation_id": recommendation_id, "status": status},
    )
    if version.approval_status == "approved":
        invalidate_approvals(
            db, project.id, "Recommendation decision changed after approval", approval_types=("architecture",)
        )
        version.approval_status = "awaiting_approval"
        transition(
            db,
            project,
            AWAITING_ARCHITECTURE_APPROVAL,
            user_id=user.id,
            reason="Recommendation decision changed; re-approval required",
        )
    db.flush()
    return found


def get_diagram(version: ArchitectureVersion) -> str:
    if version.diagram_source:
        return version.diagram_source
    spec = ArchitectureSpec.model_validate(version.architecture_json)
    return heuristics.generate_mermaid(spec)


def current_approved_version(db: Session, project_id: str) -> ArchitectureVersion | None:
    approval = db.scalar(
        select(Approval).where(
            Approval.project_id == project_id,
            Approval.approval_type == "architecture",
            Approval.status == "active",
        )
    )
    if not approval or not approval.architecture_version_id:
        return None
    version = db.get(ArchitectureVersion, approval.architecture_version_id)
    if version and version.approval_status == "approved":
        return version
    return None
