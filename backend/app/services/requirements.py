"""Requirement persistence, clarifications and approval invalidation."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.heuristics import classify_requirement
from app.agents.schemas import RequirementAnalysis
from app.core import audit
from app.core.errors import AppError, Conflict, NotFound
from app.core.state_machine import (
    ARCHITECTURE_APPROVED,
    ARCHITECTURE_READY,
    DEPLOYING,
    PLANNING,
    TRANSITIONS,
    VALIDATING,
)
from app.models import Project, Requirement, User, new_id
from app.services.core import (
    answer_question as _answer_question,
    invalidate_approvals,
    merge_questions,
    open_questions,
    serialize_requirement,
    transition,
)

APPROVED_STATES = {
    "architecture_approved",
    "generating_terraform",
    "validating",
    "validated",
    "planning",
    "awaiting_deployment_approval",
    "deploying",
    "deployment_successful",
    "verification_failed",
    "completed",
}


def list_requirements(db: Session, project_id: str) -> list[Requirement]:
    return list(
        db.scalars(
            select(Requirement)
            .where(Requirement.project_id == project_id)
            .order_by(Requirement.created_at)
        )
    )


def upsert_from_analysis(
    db: Session, project: Project, analysis: RequirementAnalysis, user: User | None
) -> list[Requirement]:
    existing = {r.description.strip().lower() for r in list_requirements(db, project.id)}
    added: list[Requirement] = []
    for item in analysis.requirements:
        key = item.description.strip().lower()
        if not key or key in existing:
            continue
        existing.add(key)
        requirement = Requirement(
            project_id=project.id,
            requirement_type=item.requirement_type,
            description=item.description,
            source=item.source,
            priority=item.priority,
            status="confirmed" if item.source.startswith("document") else "proposed",
        )
        db.add(requirement)
        added.append(requirement)
    merge_questions(project, analysis.questions)
    if added:
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=user.id if user else None,
            action="requirements.extracted",
            metadata={"count": len(added)},
        )
        if project.status in APPROVED_STATES:
            invalidate_approvals(db, project.id, "Requirements changed after approval")
    return added


def create_requirement(
    db: Session,
    project: Project,
    user: User,
    *,
    description: str,
    requirement_type: str | None = None,
    priority: str = "mandatory",
    source: str = "user",
) -> Requirement:
    if not description.strip():
        raise AppError("Requirement description cannot be empty")
    requirement = Requirement(
        project_id=project.id,
        requirement_type=requirement_type or classify_requirement(description),
        description=description.strip()[:2000],
        priority=priority,
        source=source,
        status="confirmed",
    )
    db.add(requirement)
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="requirement.created",
        metadata={"requirement_id": requirement.id, "type": requirement.requirement_type},
    )
    _invalidate_if_needed(db, project, reason="Requirement added")
    return requirement


def update_requirement(
    db: Session, requirement: Requirement, user: User, **changes: Any
) -> Requirement:
    project = db.get(Project, requirement.project_id)
    if not project:
        raise NotFound("Project not found")
    for field in ("description", "priority", "status", "requirement_type"):
        if field in changes and changes[field] is not None:
            if field == "description" and not str(changes[field]).strip():
                raise AppError("Requirement description cannot be empty")
            setattr(requirement, field, changes[field])
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="requirement.updated",
        metadata={"requirement_id": requirement.id, "changes": list(changes)},
    )
    _invalidate_if_needed(db, project, reason="Requirement changed")
    return requirement


def delete_requirement(db: Session, requirement: Requirement, user: User) -> None:
    project = db.get(Project, requirement.project_id)
    if project:
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=user.id,
            action="requirement.deleted",
            metadata={"requirement_id": requirement.id, "description": requirement.description[:200]},
        )
        _invalidate_if_needed(db, project, reason="Requirement removed")
    db.delete(requirement)


def answer_clarification(
    db: Session, project: Project, user: User, question_id: str, answer: str
) -> Requirement:
    if not answer.strip():
        raise AppError("Answer cannot be empty")
    question = _answer_question(project, question_id, answer.strip())
    requirement = Requirement(
        project_id=project.id,
        requirement_type=classify_requirement(
            f"{question.get('category', '')} {question.get('question', '')}"
        ),
        description=f"{question.get('question')} Answer: {answer.strip()}"[:2000],
        source="user",
        priority="mandatory" if question.get("blocking") else "optional",
        status="confirmed",
        metadata_={"question_id": question_id, "category": question.get("category")},
    )
    db.add(requirement)
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="question.answered",
        metadata={"question_id": question_id},
    )
    _invalidate_if_needed(db, project, reason="Clarification answered")
    return requirement


def _invalidate_if_needed(db: Session, project: Project, *, reason: str) -> None:
    if project.status in (DEPLOYING, PLANNING, VALIDATING):
        raise Conflict(f"Cannot change requirements while the project is {project.status}")
    if project.status not in APPROVED_STATES:
        return
    invalidated = invalidate_approvals(db, project.id, reason)
    if not invalidated:
        return
    current = project.status
    if ARCHITECTURE_READY not in TRANSITIONS.get(current, set()):
        if ARCHITECTURE_APPROVED in TRANSITIONS.get(current, set()):
            transition(db, project, ARCHITECTURE_APPROVED, reason="Requirements changed")
            current = project.status
    if ARCHITECTURE_READY in TRANSITIONS.get(current, set()):
        transition(
            db,
            project,
            ARCHITECTURE_READY,
            reason="Requirements changed; architecture must be reviewed again",
        )


def invalidate_for_requirement_change(db: Session, project: Project, reason: str) -> int:
    if project.status in APPROVED_STATES:
        return invalidate_approvals(db, project.id, reason)
    return 0
