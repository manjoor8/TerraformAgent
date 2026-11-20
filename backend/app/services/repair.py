"""Bounded AI repair loop for failed Terraform validation.

The model proposes file patches; the deterministic validator and policy engine decide whether
the result is acceptable. The loop is capped at three attempts and produces a new immutable
Terraform version for every accepted repair.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.runtime import terraform_engineer
from app.config import get_settings
from app.core import audit
from app.core.errors import Conflict
from app.core.security import hash_files
from app.core.state_machine import (
    DEPLOYING,
    PLANNING,
    TRANSITIONS,
    VALIDATED,
    VALIDATING,
    VALIDATION_FAILED,
)
from app.llm.factory import get_llm
from app.models import Project, TerraformProject, User, ValidationRun
from app.services import terraform_service
from app.services.artifacts import store
from app.services.core import transition
from app.services.jobs import JobContext, jobs
from app.terraform.formatting import format_files
from app.terraform.validator import ValidationEngine

settings = get_settings()
UTC = timezone.utc
MAX_REPAIR_ATTEMPTS = 3


def start_repair(
    db: Session, project: Project, tf_project: TerraformProject, user: User
) -> str:
    if not get_llm().available:
        raise Conflict(
            "AI repair requires a configured LLM provider. Edit the files manually or configure "
            "an OpenAI-compatible / Azure OpenAI provider."
        )
    if project.status in (DEPLOYING, PLANNING):
        raise Conflict(f"Cannot repair while the project is {project.status}")
    if project.status != VALIDATING:
        if VALIDATING in TRANSITIONS.get(project.status, set()):
            transition(db, project, VALIDATING, user_id=user.id, reason="AI repair started")
        else:
            raise Conflict(f"Cannot repair from project state '{project.status}'")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="validation.repair_started",
        metadata={"terraform_project_id": tf_project.id},
    )
    db.commit()
    return jobs.submit(
        project.id,
        "repair",
        _repair_job,
        metadata={"terraform_project_id": tf_project.id, "user_id": user.id},
    )


def _repair_job(ctx: JobContext, db: Session) -> dict[str, Any]:
    tf_project = db.get(TerraformProject, ctx.metadata["terraform_project_id"])
    project = db.get(Project, tf_project.project_id) if tf_project else None
    if not tf_project or not project:
        raise Conflict("Terraform project not found")
    return run_repair_loop(db, project, tf_project, ctx=ctx)


def run_repair_loop(
    db: Session,
    project: Project,
    tf_project: TerraformProject,
    *,
    ctx: JobContext | None = None,
    max_attempts: int = MAX_REPAIR_ATTEMPTS,
) -> dict[str, Any]:
    current = tf_project
    engine = ValidationEngine()
    notes: list[str] = []

    def publish(payload: dict[str, Any]) -> None:
        if ctx:
            ctx.publish("validation", payload)

    for attempt in range(1, max_attempts + 1):
        if ctx and ctx.cancel_event.is_set():
            notes.append("Repair cancelled by user.")
            break
        files = terraform_service.files_dict(db, current.id)
        publish(
            {
                "status": "running",
                "attempt": attempt,
                "terraform_project_id": current.id,
                "max_attempts": max_attempts,
            }
        )
        outcome = engine.validate(
            files,
            environment=project.environment,
            disabled_rules=list(
                ((project.settings or {}).get("policies") or {}).get("disabled_rules", [])
            ),
            on_event=lambda kind, payload: publish(payload),
            cancel_event=ctx.cancel_event if ctx else None,
        )
        run = ValidationRun(
            terraform_project_id=current.id,
            status="passed" if outcome.report.status == "passed" else "failed",
            results={
                "report": outcome.report.model_dump(mode="json"),
                "source_digest": current.source_digest,
                "terraform_project_id": current.id,
                "files": outcome.files_count,
                "modules": outcome.modules,
                "repair_attempt": attempt,
                "notes": list(notes),
            },
            attempt=int(
                db.scalar(
                    select(func.count())
                    .select_from(ValidationRun)
                    .where(ValidationRun.terraform_project_id == current.id)
                )
                or 0
            )
            + 1,
            completed_at=datetime.now(UTC),
        )
        log_key = f"projects/{project.id}/validation/{run.id}/repair-{attempt}.log"
        store.save_text(log_key, outcome.log)
        run.log_location = log_key
        db.add(run)
        db.flush()

        if outcome.report.status == "passed":
            if project.status == VALIDATING:
                transition(db, project, VALIDATED, reason="AI repair validation passed")
            audit.record(
                db,
                organization_id=project.organization_id,
                project_id=project.id,
                user_id=ctx.metadata.get("user_id") if ctx else None,
                action="validation.repair_succeeded",
                metadata={"attempts": attempt, "terraform_project_id": current.id},
            )
            db.commit()
            publish({"status": "passed", "attempt": attempt, "validation_run_id": run.id})
            return {
                "status": "passed",
                "attempts": attempt,
                "validation_run_id": run.id,
                "terraform_project_id": current.id,
            }

        errors = "\n".join(
            f"{check.name}: {check.details}"
            for check in outcome.report.checks
            if check.status in ("failed", "error")
        )[:12000]
        if attempt == max_attempts:
            notes.append(f"Repair limit ({max_attempts}) reached.")
            break
        repair = terraform_engineer.repair(
            files, errors=errors, environment=project.environment
        )
        if repair is None:
            notes.append("No repair agent available; stopped after deterministic validation.")
            break
        notes.extend(repair.notes)
        repaired_files = repair.output
        if not repaired_files or hash_files(repaired_files) == hash_files(files):
            notes.append("Repair produced no changes.")
            break

        formatted, format_notes = format_files(repaired_files)
        notes.extend(format_notes)
        current = terraform_service.persist_repaired_version(
            db, project, current, _repair_user(db, ctx), formatted, notes
        )
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=ctx.metadata.get("user_id") if ctx else None,
            action="terraform.repaired",
            metadata={
                "attempt": attempt,
                "terraform_project_id": current.id,
                "source_digest": current.source_digest,
                "summary": repair.notes[:5],
            },
        )
        db.commit()
        publish(
            {
                "status": "repaired",
                "attempt": attempt,
                "terraform_project_id": current.id,
                "source_digest": current.source_digest,
                "notes": notes[-5:],
            }
        )

    if project.status == VALIDATING:
        transition(db, project, VALIDATION_FAILED, reason="AI repair did not pass validation")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=ctx.metadata.get("user_id") if ctx else None,
        action="validation.repair_failed",
        metadata={"terraform_project_id": current.id, "notes": notes[-10:]},
    )
    db.commit()
    publish({"status": "failed", "notes": notes[-5:]})
    return {
        "status": "failed",
        "attempts": max_attempts,
        "terraform_project_id": current.id,
        "notes": notes,
    }


def _repair_user(db: Session, ctx: JobContext | None) -> User:
    if ctx and ctx.metadata.get("user_id"):
        user = db.get(User, ctx.metadata["user_id"])
        if user:
            return user
    user = db.scalar(select(User).limit(1))
    if not user:
        raise Conflict("No user available for repair attribution")
    return user
