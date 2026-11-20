"""Shared service helpers: authorization, transitions, audit, serialization, context."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import Forbidden, NotFound
from app.core.state_machine import ensure_transition
from app.models import (
    Approval,
    ArchitectureVersion,
    Document,
    Membership,
    Organization,
    Project,
    ProjectConversation,
    Requirement,
    TerraformPlan,
    TerraformProject,
    User,
)
from app.services.events import bus

ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}


@dataclass
class Access:
    user: User
    organization: Organization
    role: str
    project: Project | None = None

    @property
    def can_write(self) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK["member"]

    @property
    def can_admin(self) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK["admin"]


def memberships_for(db: Session, user: User) -> list[Membership]:
    return list(
        db.scalars(
            select(Membership)
            .where(Membership.user_id == user.id)
            .order_by(Membership.created_at)
        )
    )


def membership_for(
    db: Session, user: User, organization_id: str | None = None
) -> tuple[Membership, Organization]:
    memberships = memberships_for(db, user)
    if not memberships:
        raise Forbidden("User does not belong to any organization")
    if organization_id:
        match = next((m for m in memberships if m.organization_id == organization_id), None)
        if not match:
            raise NotFound("Organization not found")
        organization = db.get(Organization, organization_id)
    else:
        match = memberships[0]
        organization = db.get(Organization, match.organization_id)
    if not organization:
        raise NotFound("Organization not found")
    return match, organization


def project_access(
    db: Session, user: User, project_id: str, min_role: str = "viewer"
) -> Access:
    project = db.get(Project, project_id)
    if not project:
        raise NotFound("Project not found")
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == project.organization_id,
        )
    )
    if not membership:
        # Return 404 to avoid leaking project existence across organizations.
        raise NotFound("Project not found")
    if ROLE_RANK.get(membership.role, 0) < ROLE_RANK[min_role]:
        raise Forbidden("Your role does not permit this action")
    organization = db.get(Organization, project.organization_id)
    assert organization is not None
    return Access(user=user, organization=organization, role=membership.role, project=project)


def transition(
    db: Session,
    project: Project,
    new_status: str,
    *,
    user_id: str | None = None,
    reason: str = "",
) -> None:
    old = project.status
    ensure_transition(old, new_status)
    if old == new_status:
        return
    project.status = new_status
    project.updated_at = datetime.now(timezone.utc)
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user_id,
        action=f"project.status.{new_status}",
        metadata={"from": old, "to": new_status, "reason": reason},
    )
    bus.publish(
        project.id,
        "status",
        {"from": old, "to": new_status, "reason": reason, "label": new_status},
    )


def invalidate_approvals(
    db: Session,
    project_id: str,
    reason: str,
    *,
    approval_types: tuple[str, ...] | None = None,
    keep_approval_id: str | None = None,
) -> int:
    query = select(Approval).where(
        Approval.project_id == project_id, Approval.status == "active"
    )
    if approval_types:
        query = query.where(Approval.approval_type.in_(approval_types))
    if keep_approval_id:
        query = query.where(Approval.id != keep_approval_id)
    approvals = list(db.scalars(query))
    superseded_versions: set[str] = set()
    for approval in approvals:
        approval.status = "invalidated"
        approval.invalidated_reason = reason
        if approval.approval_type == "architecture" and approval.architecture_version_id:
            superseded_versions.add(approval.architecture_version_id)
    for version_id in superseded_versions:
        version = db.get(ArchitectureVersion, version_id)
        if version and version.approval_status == "approved":
            version.approval_status = "superseded"
    if approvals:
        plan_query = select(TerraformPlan).where(
            TerraformPlan.id.in_(
                [a.terraform_plan_id for a in approvals if a.terraform_plan_id]
            ),
            TerraformPlan.status == "approved",
        )
        for plan in db.scalars(plan_query):
            plan.status = "stale"
    return len(approvals)


def project_counts(db: Session, project: Project) -> dict[str, Any]:
    def count(model, *conditions) -> int:
        return int(
            db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
        )

    open_questions = [
        q for q in (project.settings or {}).get("open_questions", []) if q.get("status") == "open"
    ]
    latest_arch = db.scalar(
        select(ArchitectureVersion)
        .where(ArchitectureVersion.project_id == project.id)
        .order_by(ArchitectureVersion.version.desc())
    )
    latest_tf = db.scalar(
        select(TerraformProject)
        .where(TerraformProject.project_id == project.id)
        .order_by(TerraformProject.version.desc())
    )
    pending = int(
        db.scalar(
            select(func.count())
            .select_from(Approval)
            .where(
                Approval.project_id == project.id,
                Approval.status == "active",
                Approval.approval_type == "architecture",
            )
        )
        or 0
    )
    return {
        "documents": count(Document, Document.project_id == project.id),
        "requirements": count(Requirement, Requirement.project_id == project.id),
        "open_questions": len(open_questions),
        "architecture_version": latest_arch.version if latest_arch else None,
        "architecture_status": latest_arch.approval_status if latest_arch else None,
        "terraform_version": latest_tf.version if latest_tf else None,
        "architecture_approvals": pending,
    }


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_project(db: Session, project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "organization_id": project.organization_id,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "environment": project.environment,
        "region": project.region,
        "azure_subscription_id": project.azure_subscription_id,
        "created_by": project.created_by,
        "created_at": iso(project.created_at),
        "updated_at": iso(project.updated_at),
        "counts": project_counts(db, project),
        "settings": {
            "backend": (project.settings or {}).get("backend", {"mode": "local"}),
            "policies": (project.settings or {}).get("policies", {"disabled_rules": []}),
            "terraform_notes": (project.settings or {}).get("terraform_notes", []),
        },
    }


def add_message(
    db: Session,
    project: Project,
    role: str,
    content: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> ProjectConversation:
    message = ProjectConversation(
        project_id=project.id,
        role=role,
        message=content,
        metadata_=metadata or {},
    )
    db.add(message)
    db.flush()
    return message


def serialize_message(message: ProjectConversation) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "message": message.message,
        "metadata": message.metadata_,
        "created_at": iso(message.created_at),
    }


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def merge_questions(project: Project, questions: list[Any]) -> list[dict[str, Any]]:
    settings = dict(project.settings or {})
    existing: list[dict[str, Any]] = list(settings.get("open_questions", []))
    index = {normalize_question(q.get("question", "")): q for q in existing}
    for question in questions:
        data = question.model_dump(mode="json") if hasattr(question, "model_dump") else dict(question)
        key = normalize_question(data.get("question", ""))
        if not key:
            continue
        current = index.get(key)
        if current:
            if current.get("status") == "open":
                current.update(
                    {
                        "category": data.get("category", current.get("category")),
                        "why_it_matters": data.get("why_it_matters", current.get("why_it_matters")),
                        "options": data.get("options", current.get("options", [])),
                        "recommended": data.get("recommended", current.get("recommended")),
                        "blocking": data.get("blocking", current.get("blocking", True)),
                    }
                )
        else:
            data.setdefault("status", "open")
            data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            existing.append(data)
            index[key] = data
    settings["open_questions"] = existing[-80:]
    project.settings = settings
    return [q for q in settings["open_questions"] if q.get("status") == "open"]


def answer_question(project: Project, question_id: str, answer: str) -> dict[str, Any]:
    settings = dict(project.settings or {})
    for question in settings.get("open_questions", []):
        if question.get("id") == question_id:
            question["status"] = "answered"
            question["answer"] = answer
            question["answered_at"] = datetime.now(timezone.utc).isoformat()
            project.settings = settings
            return question
    raise NotFound("Question not found")


def open_questions(project: Project) -> list[dict[str, Any]]:
    return [
        q
        for q in (project.settings or {}).get("open_questions", [])
        if q.get("status") == "open"
    ]


def load_agent_context(db: Session, project: Project) -> Any:
    from app.agents.heuristics import AgentContext

    messages = list(
        db.scalars(
            select(ProjectConversation)
            .where(ProjectConversation.project_id == project.id)
            .order_by(ProjectConversation.created_at.desc())
            .limit(24)
        )
    )
    messages.reverse()
    requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.project_id == project.id)
            .order_by(Requirement.created_at)
        )
    )
    documents = list(
        db.scalars(
            select(Document)
            .where(Document.project_id == project.id, Document.extraction_status == "extracted")
            .order_by(Document.created_at.desc())
            .limit(8)
        )
    )
    answered = [
        q
        for q in (project.settings or {}).get("open_questions", [])
        if q.get("status") == "answered"
    ]
    return AgentContext(
        project_name=project.name,
        description=project.description,
        environment=project.environment,
        region=project.region,
        messages=[m.message for m in messages if m.role == "user"],
        requirements=[
            {
                "requirement_type": r.requirement_type,
                "description": r.description,
                "priority": r.priority,
                "status": r.status,
                "source": r.source,
            }
            for r in requirements
        ],
        documents=[
            {
                "filename": d.filename,
                "extracted_text": (d.extracted_text or "")[:8000],
                "extraction": d.extraction,
            }
            for d in documents
        ],
        answered_questions=[
            {"question": q.get("question"), "answer": q.get("answer")} for q in answered[-20:]
        ],
    )


def serialize_requirement(requirement: Requirement) -> dict[str, Any]:
    return {
        "id": requirement.id,
        "project_id": requirement.project_id,
        "requirement_type": requirement.requirement_type,
        "description": requirement.description,
        "source": requirement.source,
        "source_document_id": requirement.source_document_id,
        "status": requirement.status,
        "priority": requirement.priority,
        "metadata": requirement.metadata_,
        "created_at": iso(requirement.created_at),
        "updated_at": iso(requirement.updated_at),
    }


def serialize_document(document: Document) -> dict[str, Any]:
    return {
        "id": document.id,
        "project_id": document.project_id,
        "filename": document.filename,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "document_type": document.document_type,
        "extraction_status": document.extraction_status,
        "extraction_error": document.extraction_error,
        "content_hash": document.content_hash,
        "extraction": document.extraction,
        "created_at": iso(document.created_at),
        "has_text": bool(document.extracted_text),
    }
