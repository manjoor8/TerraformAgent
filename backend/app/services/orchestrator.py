"""Conversational orchestrator: requirement discovery, design and safe action routing."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents import runtime
from app.agents.heuristics import classify_requirement
from app.core import audit
from app.core.errors import AppError, Conflict
from app.core.state_machine import (
    ARCHITECTURE_APPROVED,
    ARCHITECTURE_READY,
    AWAITING_ARCHITECTURE_APPROVAL,
    AWAITING_DEPLOYMENT_APPROVAL,
    COMPLETED,
    DEPLOYMENT_FAILED,
    DRAFT,
    GATHERING_REQUIREMENTS,
    TRANSITIONS,
    VALIDATED,
    VALIDATION_FAILED,
)
from app.models import Project, Requirement, User
from app.services import architecture as architecture_service
from app.services import terraform_service
from app.services.core import (
    add_message,
    iso,
    load_agent_context,
    open_questions,
    serialize_message,
    transition,
)
from app.services.requirements import upsert_from_analysis

DESIGN_WORDS = ("design", "architect", "propose", "draw", "generate architecture", "architecture")
TERRAFORM_WORDS = ("generate terraform", "terraform code", "create terraform", "terraform module")
CHANGE_WORDS = ("change", "add ", "remove", "increase", "decrease", "enable", "disable", "modify", "update", "scale")
APPROVAL_WORDS = ("approve", "approval")


def _contains(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def _format_questions(questions: list[dict[str, Any]]) -> str:
    lines = ["Before I finalize the design, I need a few decisions:"]
    for index, question in enumerate(questions[:4], start=1):
        options = question.get("options") or []
        recommended = question.get("recommended")
        line = f"{index}. **{question.get('question')}**"
        if question.get("why_it_matters"):
            line += f"\n   Why it matters: {question['why_it_matters']}"
        if options:
            line += "\n   Options: " + " · ".join(options)
        if recommended:
            line += f"\n   Recommended: {recommended}"
        if not question.get("blocking", True):
            line += " (optional)"
        lines.append(line)
    return "\n".join(lines)


def handle_message(
    db: Session, project: Project, user: User, content: str
) -> dict[str, Any]:
    if not content.strip():
        raise AppError("Message cannot be empty")
    if len(content) > 20000:
        raise AppError("Message is too long (20,000 character limit)")

    user_message = add_message(db, project, "user", content.strip())
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="chat.message",
        metadata={"length": len(content)},
    )
    if project.status == DRAFT:
        transition(db, project, GATHERING_REQUIREMENTS, user_id=user.id, reason="Requirement gathering started")

    ctx = load_agent_context(db, project)
    analysis_result = runtime.requirement_analyst.run(ctx)
    analysis = analysis_result.output
    added = upsert_from_analysis(db, project, analysis, user)
    questions = open_questions(project)

    latest = architecture_service.latest_version(db, project.id)
    reply_parts: list[str] = []
    generated_version = None
    terraform_project = None
    actions: list[str] = []

    if _contains(content, APPROVAL_WORDS):
        reply_parts.append(
            "I cannot approve architectures or deployments. Approval is a human-only control: "
            "use the Approve button in the Architecture or Terraform Plan tab after reviewing the artifacts."
        )

    wants_terraform = _contains(content, TERRAFORM_WORDS)
    wants_design = _contains(content, DESIGN_WORDS)
    wants_change = _contains(content, CHANGE_WORDS) and latest is not None

    change_states = {
        ARCHITECTURE_APPROVED,
        COMPLETED,
        AWAITING_ARCHITECTURE_APPROVAL,
        AWAITING_DEPLOYMENT_APPROVAL,
        VALIDATION_FAILED,
        VALIDATED,
        DEPLOYMENT_FAILED,
        ARCHITECTURE_READY,
    }

    try:
        if wants_terraform and project.status not in (DRAFT, GATHERING_REQUIREMENTS):
            terraform_project = terraform_service.generate(db, project, user)
            reply_parts.append(
                f"Terraform project v{terraform_project.version} generated "
                f"({terraform_project.source_digest[:19]}…). "
                "Validate it in the Terraform Code tab before planning."
            )
            actions.append("Open Terraform Code → Validate")
        elif wants_design or (wants_change and project.status in change_states) or (
            latest is None
            and not analysis.conflicts
            and (analysis.requirements_complete or analysis.readiness_score >= 80)
        ):
            generated_version = architecture_service.generate_architecture(
                db,
                project,
                user,
                instructions=content if (wants_change or wants_design) and latest else None,
                base_version=latest if latest else None,
                analysis=analysis,
            )
            reply_parts.append(
                f"Architecture v{generated_version.version} is ready: "
                f"{len(generated_version.architecture_json.get('resources', []))} resources, "
                f"~{generated_version.cost_estimate.get('currency', 'USD')} "
                f"{generated_version.cost_estimate.get('monthly_total', 0):,.0f}/month indicative. "
                "Review the diagram, inventory and recommendations, then submit it for approval."
            )
            actions.extend(["Review architecture", "Submit for approval"])
    except (Conflict, AppError) as exc:
        reply_parts.append(f"I could not run that action: {exc}")

    # Contextual summary and questions.
    if generated_version is None:
        reply_parts.append(analysis.summary)
    if analysis.conflicts:
        reply_parts.append("**Conflicts detected:** " + "; ".join(analysis.conflicts))
    if analysis.missing_information:
        reply_parts.append("**Still missing:** " + ", ".join(analysis.missing_information[:6]))
    if questions and generated_version is None:
        reply_parts.append(_format_questions(questions))
        actions.append("Answer clarification questions")
    if not questions and latest is None and generated_version is None:
        reply_parts.append(
            "I have enough information to propose an architecture whenever you are ready — "
            "ask me to design it, or use Generate Architecture."
        )
    if latest is not None and generated_version is None and terraform_project is None:
        reply_parts.append(
            f"Current architecture is v{latest.version} ({latest.approval_status}). "
            "Ask for a change in plain English and I will produce a new version."
        )

    reply_text = "\n\n".join(part for part in reply_parts if part).strip()
    assistant_message = add_message(
        db,
        project,
        "assistant",
        reply_text,
        metadata={
            "questions": [q.get("id") for q in questions[:4]],
            "requirements_added": len(added),
            "architecture_version": generated_version.version if generated_version else None,
            "readiness_score": analysis.readiness_score,
            "actions": actions,
        },
    )
    db.commit()

    return {
        "message": serialize_message(user_message),
        "reply": serialize_message(assistant_message),
        "questions": questions,
        "requirements_added": len(added),
        "status": project.status,
        "architecture_version": generated_version.version if generated_version else None,
        "terraform_version": terraform_project.version if terraform_project else None,
        "actions": actions,
        "readiness_score": analysis.readiness_score,
        "conflicts": analysis.conflicts,
    }
