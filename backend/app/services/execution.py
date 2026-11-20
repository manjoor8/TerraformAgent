"""Terraform validation, plan, approval-bound apply and post-deployment verification."""
from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.heuristics import build_inventory
from app.agents.runtime import plan_analyst
from app.agents.schemas import ArchitectureSpec, PlanSummary
from app.config import get_settings
from app.core import audit
from app.core.errors import ApprovalError, AppError, Conflict, NotFound
from app.core.security import hash_bytes, redact, redact_obj
from app.core.state_machine import (
    ARCHITECTURE_APPROVED,
    AWAITING_DEPLOYMENT_APPROVAL,
    COMPLETED,
    DEPLOYING,
    DEPLOYMENT_FAILED,
    DEPLOYMENT_SUCCESSFUL,
    PLANNING,
    PLANNING_FAILED,
    TRANSITIONS,
    VALIDATED,
    VALIDATING,
    VALIDATION_FAILED,
)
from app.models import (
    Approval,
    ArchitectureVersion,
    AzureSubscription,
    Deployment,
    Project,
    ProjectLock,
    TerraformFile,
    TerraformPlan,
    TerraformProject,
    User,
    ValidationRun,
    new_id,
)
from app.services import architecture as architecture_service
from app.services import terraform_service
from app.services.artifacts import sha256_file, store
from app.services.azure import AzureAuthError, AzureClient
from app.services.core import invalidate_approvals, iso, transition
from app.services.jobs import JobContext, jobs
from app.services.events import bus
from app.terraform.executor import TerraformExecutor, TerraformNotInstalled
from app.terraform.plan_parser import parse_plan_json
from app.terraform.validator import ValidationEngine
from app.terraform.workspace import cleanup, materialize

settings = get_settings()
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------
def _workspace_files(db: Session, tf_project: TerraformProject) -> dict[str, str]:
    files = {
        f.file_path: f.file_content
        for f in db.scalars(
            select(TerraformFile).where(TerraformFile.terraform_project_id == tf_project.id)
        )
    }
    return files


def _workspace(
    tf_project: TerraformProject, files: dict[str, str], run_id: str, environment: str
) -> tuple[Path, Path]:
    root = materialize(files, project_id=tf_project.project_id, run_id=run_id)
    env_dir = root / "environments" / environment
    if not env_dir.exists():
        available = sorted(p.name for p in (root / "environments").glob("*") if p.is_dir())
        if not available:
            raise AppError("Terraform project has no environment directory")
        env_dir = root / "environments" / available[0]
    return root, env_dir


def _backend_mode(project: Project) -> str:
    return str(((project.settings or {}).get("backend") or {}).get("mode", "local"))


def _init_args(project: Project) -> list[str]:
    args = ["init", "-input=false", "-no-color"]
    if _backend_mode(project) == "azurerm":
        args.extend(["-backend-config=backend.hcl", "-reconfigure"])
    return args


def _project_subscription(db: Session, project: Project) -> str | None:
    if project.azure_subscription_id:
        subscription = db.get(AzureSubscription, project.azure_subscription_id)
        if subscription:
            return subscription.subscription_id
    return os.environ.get("ARM_SUBSCRIPTION_ID")


def acquire_lock(db: Session, project_id: str, reason: str) -> None:
    existing = db.get(ProjectLock, project_id)
    if existing:
        raise Conflict(
            f"Project execution is locked ({existing.reason} since {existing.locked_at}). "
            "Wait for it to finish or investigate a stale lock."
        )
    db.add(ProjectLock(project_id=project_id, reason=reason[:200]))
    db.flush()


def release_lock(db: Session, project_id: str) -> None:
    lock = db.get(ProjectLock, project_id)
    if lock:
        db.delete(lock)
        db.flush()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def start_validation(
    db: Session, project: Project, tf_project: TerraformProject, user: User
) -> tuple[ValidationRun, str]:
    if project.status in (DEPLOYING, PLANNING):
        raise Conflict(f"Cannot validate while the project is {project.status}")
    latest = terraform_service.latest_project(db, project.id)
    if latest and latest.id != tf_project.id:
        raise Conflict("Only the latest Terraform project version can be validated")
    attempt = int(
        db.scalar(
            select(func.count())
            .select_from(ValidationRun)
            .where(ValidationRun.terraform_project_id == tf_project.id)
        )
        or 0
    ) + 1
    run = ValidationRun(
        terraform_project_id=tf_project.id,
        status="queued",
        attempt=attempt + 1,
    )
    db.add(run)
    db.flush()
    if project.status != VALIDATING:
        if VALIDATING in TRANSITIONS.get(project.status, set()):
            transition(db, project, VALIDATING, user_id=user.id, reason="Validation started")
        else:
            raise Conflict(f"Cannot validate from project state '{project.status}'")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="validation.started",
        metadata={"validation_run_id": run.id, "terraform_project_id": tf_project.id},
    )
    db.commit()

    job_id = jobs.submit(
        project.id,
        "validation",
        _validation_job,
        metadata={"validation_run_id": run.id, "terraform_project_id": tf_project.id},
    )
    return run, job_id


def _validation_job(ctx: JobContext, db: Session) -> dict[str, Any]:
    run = db.get(ValidationRun, ctx.metadata["validation_run_id"])
    if not run:
        raise NotFound("Validation run not found")
    tf_project = db.get(TerraformProject, run.terraform_project_id)
    project = db.get(Project, tf_project.project_id) if tf_project else None
    if not tf_project or not project:
        raise NotFound("Terraform project not found")
    run.status = "running"
    db.commit()

    files = _workspace_files(db, tf_project)
    disabled_rules = list(((project.settings or {}).get("policies") or {}).get("disabled_rules", []))
    ctx.publish("validation", {"status": "running", "validation_run_id": run.id})

    try:
        engine = ValidationEngine()
        outcome = engine.validate(
            files,
            environment=project.environment,
            disabled_rules=disabled_rules,
            on_event=lambda kind, payload: ctx.publish(kind, payload),
            cancel_event=ctx.cancel_event,
        )
    except TerraformNotInstalled as exc:
        run.status = "error"
        run.results = {"error": str(exc), "source_digest": tf_project.source_digest}
        run.completed_at = datetime.now(UTC)
        project.status = VALIDATION_FAILED
        db.commit()
        ctx.publish("validation", {"status": "error", "error": str(exc)})
        return {"validation_run_id": run.id, "status": "error", "error": str(exc)}

    log_location = f"projects/{project.id}/validation/{run.id}/validation.log"
    store.save_text(log_location, outcome.log)
    results = {
        "report": outcome.report.model_dump(mode="json"),
        "source_digest": tf_project.source_digest,
        "terraform_project_id": tf_project.id,
        "files": outcome.files_count,
        "modules": outcome.modules,
    }
    run.status = "passed" if outcome.report.status == "passed" else "failed"
    run.results = results
    run.log_location = log_location
    run.completed_at = datetime.now(UTC)

    if outcome.report.status == "passed":
        if project.status == VALIDATING:
            transition(db, project, VALIDATED, reason="Validation passed")
    else:
        if project.status == VALIDATING:
            transition(db, project, VALIDATION_FAILED, reason="Validation failed")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=None,
        action="validation.completed",
        metadata={
            "validation_run_id": run.id,
            "status": run.status,
            "source_digest": tf_project.source_digest,
            "errors": outcome.report.summary.get("errors"),
        },
    )
    db.commit()
    ctx.publish(
        "validation",
        {
            "status": run.status,
            "validation_run_id": run.id,
            "summary": outcome.report.summary,
        },
    )
    return {
        "validation_run_id": run.id,
        "status": run.status,
        "summary": outcome.report.summary,
    }


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def _latest_passing_validation(db: Session, tf_project: TerraformProject) -> ValidationRun | None:
    return db.scalar(
        select(ValidationRun)
        .where(
            ValidationRun.terraform_project_id == tf_project.id,
            ValidationRun.status == "passed",
        )
        .order_by(ValidationRun.created_at.desc())
    )


def start_plan(
    db: Session,
    project: Project,
    tf_project: TerraformProject,
    user: User,
    *,
    environment: str | None = None,
) -> tuple[TerraformPlan, str]:
    if project.status in (DEPLOYING, PLANNING):
        raise Conflict(f"Cannot plan while the project is {project.status}")
    latest = terraform_service.latest_project(db, project.id)
    if latest and latest.id != tf_project.id:
        raise Conflict("Only the latest Terraform project can be planned")
    validation = _latest_passing_validation(db, tf_project)
    if not validation or (validation.results or {}).get("source_digest") != tf_project.source_digest:
        raise Conflict("Terraform must pass validation for this exact source version before planning")
    if not architecture_service.current_approved_version(db, project.id):
        raise Conflict("An active architecture approval is required before planning")
    subscription_id = _project_subscription(db, project)
    if not subscription_id:
        raise Conflict("No Azure subscription is configured for this project")

    env = environment or project.environment
    invalidate_approvals(
        db,
        project.id,
        "A new Terraform plan supersedes previous deployment approvals",
        approval_types=("deployment",),
    )
    plan = TerraformPlan(
        terraform_project_id=tf_project.id,
        environment=env,
        subscription_id=subscription_id,
        source_digest=tf_project.source_digest,
        status="generating",
    )
    db.add(plan)
    db.flush()
    if project.status == VALIDATED:
        transition(db, project, PLANNING, user_id=user.id, reason="Terraform plan started")
    elif project.status == PLANNING:
        pass
    else:
        # The validated Terraform version is authoritative; restore the project
        # to the validated state through the legal transition path first.
        if project.status == ARCHITECTURE_APPROVED:
            if VALIDATING in TRANSITIONS.get(project.status, set()):
                transition(db, project, VALIDATING, user_id=user.id, reason="Latest validation reused")
            if VALIDATED in TRANSITIONS.get(project.status, set()):
                transition(db, project, VALIDATED, user_id=user.id, reason="Latest validation reused")
        elif project.status == AWAITING_DEPLOYMENT_APPROVAL:
            if "architecture_approved" in TRANSITIONS.get(project.status, set()):
                transition(
                    db,
                    project,
                    "architecture_approved",
                    user_id=user.id,
                    reason="Re-planning requested",
                )
            if VALIDATING in TRANSITIONS.get(project.status, set()):
                transition(db, project, VALIDATING, user_id=user.id, reason="Re-planning")
            if VALIDATED in TRANSITIONS.get(project.status, set()):
                transition(db, project, VALIDATED, user_id=user.id, reason="Re-planning")
        if project.status != PLANNING:
            if PLANNING in TRANSITIONS.get(project.status, set()):
                transition(db, project, PLANNING, user_id=user.id, reason="Terraform plan started")
            else:
                raise Conflict(f"Cannot plan from project state '{project.status}'")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="plan.started",
        metadata={
            "plan_id": plan.id,
            "terraform_project_id": tf_project.id,
            "subscription_id": subscription_id,
            "environment": env,
        },
    )
    db.commit()

    job_id = jobs.submit(
        project.id,
        "plan",
        _plan_job,
        metadata={"plan_id": plan.id, "terraform_project_id": tf_project.id},
    )
    return plan, job_id


def _plan_job(ctx: JobContext, db: Session) -> dict[str, Any]:
    plan = db.get(TerraformPlan, ctx.metadata["plan_id"])
    if not plan:
        raise NotFound("Plan not found")
    tf_project = db.get(TerraformProject, plan.terraform_project_id)
    project = db.get(Project, tf_project.project_id) if tf_project else None
    if not tf_project or not project:
        raise NotFound("Terraform project not found")

    files = _workspace_files(db, tf_project)
    root, env_dir = _workspace(tf_project, files, f"plan-{plan.id}", project.environment)
    executor = TerraformExecutor()
    log_lines: list[str] = []

    def log(line: str) -> None:
        log_lines.append(line)
        ctx.log(line)

    try:
        ctx.publish("plan", {"status": "initializing", "plan_id": plan.id})
        init = executor.run(_init_args(project), cwd=env_dir, timeout=300, on_line=log, cancel_event=ctx.cancel_event)
        if not init.ok:
            raise AppError(f"terraform init failed:\n{init.output[-3000:]}")
        ctx.publish("plan", {"status": "planning", "plan_id": plan.id})
        plan_args = ["plan", "-input=false", "-no-color", "-out=plan.tfplan"]
        if (env_dir / "terraform.tfvars").exists():
            plan_args.append("-var-file=terraform.tfvars")
        result = executor.run(
            plan_args,
            cwd=env_dir,
            timeout=settings.terraform_timeout_seconds,
            on_line=log,
            cancel_event=ctx.cancel_event,
        )
        if result.exit_code not in (0, 2):
            raise AppError(f"terraform plan failed:\n{result.output[-4000:]}")
        plan_file = env_dir / "plan.tfplan"
        if not plan_file.exists():
            raise AppError("terraform plan did not produce a plan artifact")
        plan_bytes = plan_file.read_bytes()
        plan_hash = hash_bytes(plan_bytes)

        show = executor.run(
            ["show", "-json", "plan.tfplan"],
            cwd=env_dir,
            timeout=180,
            on_line=log,
            cancel_event=ctx.cancel_event,
        )
        if show.exit_code != 0:
            raise AppError(f"terraform show failed:\n{show.output[-3000:]}")
        show_json = show.output
        try:
            plan_data = _json_from_output(show_json)
        except (json.JSONDecodeError, AppError) as exc:
            raise AppError(f"Could not parse Terraform plan JSON: {exc}") from exc

        summary = parse_plan_json(
            plan_data, plan_hash=plan_hash, source_digest=tf_project.source_digest
        )
        analyst = plan_analyst.run(summary)
        summary = analyst.output

        artifact_key = f"projects/{project.id}/plans/{plan.id}/plan.tfplan"
        json_key = f"projects/{project.id}/plans/{plan.id}/plan.json"
        store.save(artifact_key, plan_bytes)
        store.save_text(json_key, show_json)

        plan.plan_artifact_location = artifact_key
        plan.plan_json_location = json_key
        plan.plan_hash = plan_hash
        plan.plan_summary = summary.model_dump(mode="json")
        plan.destructive = bool(summary.destructive_operations)
        plan.status = "generated"
        plan.created_at = datetime.now(UTC)

        if project.status == PLANNING:
            transition(
                db,
                project,
                AWAITING_DEPLOYMENT_APPROVAL,
                reason="Plan generated; approval required",
            )
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=None,
            action="plan.generated",
            metadata={
                "plan_id": plan.id,
                "plan_hash": plan_hash,
                "add": summary.add,
                "change": summary.change,
                "destroy": summary.destroy,
                "replace": summary.replace,
                "destructive": plan.destructive,
            },
        )
        db.commit()
        ctx.publish(
            "plan",
            {
                "status": "generated",
                "plan_id": plan.id,
                "summary": summary.model_dump(mode="json"),
            },
        )
        return {"plan_id": plan.id, "status": "generated", "plan_hash": plan_hash}
    except Exception as exc:
        plan.status = "failed"
        plan.error = str(exc)[:4000]
        log_key = f"projects/{project.id}/plans/{plan.id}/plan.log"
        store.save_text(log_key, "\n".join(log_lines))
        if project.status == PLANNING:
            transition(db, project, PLANNING_FAILED, reason="Plan failed")
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=None,
            action="plan.failed",
            metadata={"plan_id": plan.id, "error": str(exc)[:1000]},
        )
        db.commit()
        ctx.publish("plan", {"status": "failed", "plan_id": plan.id, "error": str(exc)[:2000]})
        raise
    finally:
        cleanup(root)


def approve_plan(
    db: Session,
    plan: TerraformPlan,
    user: User,
    *,
    note: str = "",
    confirm_destructive: bool = False,
) -> Approval:
    tf_project = terraform_service.get_project(db, plan.terraform_project_id)
    project = db.get(Project, tf_project.project_id)
    if not project:
        raise NotFound("Project not found")
    if plan.status != "generated":
        raise ApprovalError(f"Plan is not awaiting approval (status: {plan.status})")
    latest_plan = db.scalar(
        select(TerraformPlan)
        .where(TerraformPlan.terraform_project_id == tf_project.id)
        .order_by(TerraformPlan.created_at.desc())
    )
    if latest_plan and latest_plan.id != plan.id:
        raise ApprovalError("A newer plan exists for this source version; review the latest plan")
    if plan.source_digest != tf_project.source_digest:
        plan.status = "stale"
        raise ApprovalError("Plan is stale: the Terraform source has changed since planning")
    architecture_version = architecture_service.current_approved_version(db, project.id)
    if not architecture_version or architecture_version.id != tf_project.architecture_version_id:
        raise ApprovalError("The architecture approval is no longer valid for this Terraform version")
    if plan.destructive and not confirm_destructive:
        raise ApprovalError(
            "This plan contains destructive operations. Explicit confirmation is required."
        )
    invalidate_approvals(
        db,
        project.id,
        "New deployment approval",
        approval_types=("deployment",),
    )
    approval = Approval(
        project_id=project.id,
        approval_type="deployment",
        artifact_hash=plan.plan_hash,
        architecture_version_id=tf_project.architecture_version_id,
        terraform_plan_id=plan.id,
        approved_by=user.id,
        status="active",
    )
    db.add(approval)
    plan.status = "approved"
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="deployment.approved",
        metadata={
            "plan_id": plan.id,
            "plan_hash": plan.plan_hash,
            "approval_id": approval.id,
            "source_digest": plan.source_digest,
            "subscription_id": plan.subscription_id,
            "environment": plan.environment,
            "note": note,
        },
    )
    db.flush()
    return approval


def reject_plan(db: Session, plan: TerraformPlan, user: User, reason: str) -> TerraformPlan:
    tf_project = terraform_service.get_project(db, plan.terraform_project_id)
    project = db.get(Project, tf_project.project_id)
    if not project:
        raise NotFound("Project not found")
    plan.status = "rejected"
    invalidate_approvals(db, project.id, "Plan rejected", approval_types=("deployment",))
    if project.status == AWAITING_DEPLOYMENT_APPROVAL:
        transition(
            db,
            project,
            "architecture_approved",
            user_id=user.id,
            reason="Plan rejected",
        )
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="plan.rejected",
        metadata={"plan_id": plan.id, "reason": reason[:1000]},
    )
    db.flush()
    return plan


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------
def _active_deployment_approval(db: Session, plan: TerraformPlan) -> Approval:
    approval = db.scalar(
        select(Approval).where(
            Approval.terraform_plan_id == plan.id,
            Approval.approval_type == "deployment",
            Approval.status == "active",
        )
    )
    if not approval:
        raise ApprovalError("No active deployment approval exists for this plan")
    if approval.artifact_hash != plan.plan_hash:
        raise ApprovalError("Approval does not match the plan hash")
    return approval


def verify_deployment_authorization(db: Session, plan: TerraformPlan) -> Approval:
    if plan.status != "approved":
        raise ApprovalError(f"Plan is not approved (status: {plan.status})")
    tf_project = terraform_service.get_project(db, plan.terraform_project_id)
    project = db.get(Project, tf_project.project_id)
    if not project:
        raise NotFound("Project not found")
    if plan.source_digest != tf_project.source_digest:
        raise ApprovalError("Terraform source changed after the plan was approved")
    architecture_approval = architecture_service.current_approved_version(db, project.id)
    if not architecture_approval or architecture_approval.id != tf_project.architecture_version_id:
        raise ApprovalError("Architecture approval is no longer active")
    subscription_id = _project_subscription(db, project)
    if not subscription_id or subscription_id != plan.subscription_id:
        raise ApprovalError("Target subscription does not match the approved plan")
    if plan.environment != project.environment:
        raise ApprovalError("Target environment does not match the approved plan")
    if not plan.plan_artifact_location or not store.exists(plan.plan_artifact_location):
        raise ApprovalError("The approved plan artifact is missing")
    artifact_path = store.path(plan.plan_artifact_location)
    if sha256_file(artifact_path) != plan.plan_hash:
        raise ApprovalError("The plan artifact hash no longer matches the approved hash")
    return _active_deployment_approval(db, plan)


def start_deployment(
    db: Session, plan: TerraformPlan, user: User
) -> tuple[Deployment, str]:
    if plan.status != "approved":
        raise Conflict("The plan must be approved before deployment")
    approval = verify_deployment_authorization(db, plan)
    tf_project = terraform_service.get_project(db, plan.terraform_project_id)
    project = db.get(Project, tf_project.project_id)
    if not project:
        raise NotFound("Project not found")
    active = db.scalar(
        select(Deployment).where(
            Deployment.project_id == project.id,
            Deployment.status.in_(("queued", "running")),
        )
    )
    if active:
        raise Conflict("A deployment is already running for this project")
    acquire_lock(db, project.id, f"deployment {plan.id}")
    deployment = Deployment(
        project_id=project.id,
        terraform_plan_id=plan.id,
        approval_id=approval.id,
        subscription_id=plan.subscription_id or "",
        environment=plan.environment,
        status="queued",
        summary={"plan_hash": plan.plan_hash},
    )
    db.add(deployment)
    db.flush()
    transition(db, project, DEPLOYING, user_id=user.id, reason="Deployment started")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="deployment.started",
        metadata={
            "deployment_id": deployment.id,
            "plan_id": plan.id,
            "approval_id": approval.id,
            "plan_hash": plan.plan_hash,
            "subscription_id": plan.subscription_id,
        },
    )
    db.commit()

    job_id = jobs.submit(
        project.id,
        "deployment",
        _deployment_job,
        metadata={"deployment_id": deployment.id},
    )
    return deployment, job_id


def _preflight_matches(approved: PlanSummary, fresh: PlanSummary) -> bool:
    def signature(summary: PlanSummary) -> list[tuple[str, str]]:
        return sorted((r.address, r.action) for r in summary.resources)

    return signature(approved) == signature(fresh)


def _deployment_job(ctx: JobContext, db: Session) -> dict[str, Any]:
    deployment = db.get(Deployment, ctx.metadata["deployment_id"])
    if not deployment:
        raise NotFound("Deployment not found")
    plan = db.get(TerraformPlan, deployment.terraform_plan_id)
    tf_project = db.get(TerraformProject, plan.terraform_project_id) if plan else None
    project = db.get(Project, deployment.project_id)
    if not plan or not tf_project or not project:
        raise NotFound("Deployment context not found")

    started = datetime.now(UTC)
    deployment.status = "running"
    deployment.started_at = started
    db.commit()

    files = _workspace_files(db, tf_project)
    root, env_dir = _workspace(tf_project, files, f"deploy-{deployment.id}", project.environment)
    executor = TerraformExecutor()
    log_lines: list[str] = []
    result: dict[str, Any] = {}

    def log(line: str) -> None:
        log_lines.append(line)
        ctx.log(line)

    try:
        ctx.publish("deployment", {"status": "preparing", "deployment_id": deployment.id})
        artifact = store.path(plan.plan_artifact_location)
        if sha256_file(artifact) != plan.plan_hash:
            raise ApprovalError("Plan artifact changed before execution")
        runner_plan = env_dir / "approved.tfplan"
        shutil.copyfile(artifact, runner_plan)
        if sha256_file(runner_plan) != plan.plan_hash:
            raise ApprovalError("Plan artifact copy failed integrity verification")

        init = executor.run(_init_args(project), cwd=env_dir, timeout=300, on_line=log, cancel_event=ctx.cancel_event)
        if not init.ok:
            raise AppError(f"terraform init failed:\n{init.output[-3000:]}")

        # Staleness preflight: never apply the saved plan if the infrastructure
        # or state changed since approval.
        ctx.publish("deployment", {"status": "verifying_plan", "deployment_id": deployment.id})
        preflight_args = ["plan", "-refresh=false", "-input=false", "-no-color", "-out=preflight.tfplan"]
        if (env_dir / "terraform.tfvars").exists():
            preflight_args.append("-var-file=terraform.tfvars")
        preflight = executor.run(
            preflight_args,
            cwd=env_dir,
            timeout=settings.terraform_timeout_seconds,
            on_line=log,
            cancel_event=ctx.cancel_event,
        )
        if preflight.exit_code not in (0, 2):
            raise AppError(f"Plan staleness check failed:\n{preflight.output[-3000:]}")
        show = executor.run(
            ["show", "-json", "preflight.tfplan"],
            cwd=env_dir,
            timeout=180,
            on_line=log,
            cancel_event=ctx.cancel_event,
        )
        if show.exit_code != 0:
            raise AppError("Could not inspect the preflight plan")
        fresh = parse_plan_json(
            _json_from_output(show.output), source_digest=tf_project.source_digest
        )
        approved_summary = PlanSummary.model_validate(plan.plan_summary or {})
        if not _preflight_matches(approved_summary, fresh):
            invalidate_approvals(db, project.id, "Plan became stale before apply", approval_types=("deployment",))
            plan.status = "stale"
            db.commit()
            raise ApprovalError(
                "The saved plan is no longer applicable (infrastructure or state changed). "
                "Generate and approve a new plan."
            )

        ctx.publish("deployment", {"status": "applying", "deployment_id": deployment.id})
        apply_result = executor.run(
            ["apply", "-input=false", "-no-color", "approved.tfplan"],
            cwd=env_dir,
            timeout=settings.terraform_apply_timeout_seconds,
            on_line=log,
            cancel_event=ctx.cancel_event,
        )
        if not apply_result.ok:
            raise AppError(f"terraform apply failed:\n{apply_result.output[-6000:]}")

        outputs = _collect_outputs(executor, env_dir)
        state_resources = _collect_state_resources(executor, env_dir)

        deployment.status = "succeeded"
        deployment.outputs = redact_obj(outputs)
        deployment.summary = {
            **(plan.plan_summary or {}),
            "plan_hash": plan.plan_hash,
            "duration_seconds": int((datetime.now(UTC) - started).total_seconds()),
            "resources": state_resources,
        }
        plan.status = "applied"

        verification = verify_deployment(
            db, project, tf_project, plan, state_resources=state_resources
        )
        deployment.verification = verification
        if verification.get("status") == "failed":
            transition(db, project, "verification_failed", reason="Post-deployment verification failed")
        else:
            transition(db, project, DEPLOYMENT_SUCCESSFUL, reason="Terraform apply completed")
            transition(db, project, COMPLETED, reason="Deployment verified")
        deployment.completed_at = datetime.now(UTC)
    except (ApprovalError, AppError, TerraformNotInstalled) as exc:
        deployment.status = "cancelled" if ctx.cancel_event.is_set() else "failed"
        deployment.summary = {
            **(deployment.summary or {}),
            "error": str(exc)[:4000],
            "duration_seconds": int((datetime.now(UTC) - started).total_seconds()),
        }
        deployment.completed_at = datetime.now(UTC)
        if project.status == DEPLOYING:
            transition(db, project, DEPLOYMENT_FAILED, reason="Deployment failed")
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            user_id=None,
            action="deployment.failed",
            metadata={"deployment_id": deployment.id, "error": str(exc)[:1000]},
        )
        result = {"deployment_id": deployment.id, "status": deployment.status, "error": str(exc)}
    else:
        result = {
            "deployment_id": deployment.id,
            "status": "succeeded",
            "verification": deployment.verification.get("status"),
        }
    finally:
        log_key = f"projects/{project.id}/deployments/{deployment.id}/apply.log"
        store.save_text(log_key, "\n".join(log_lines))
        deployment.log_location = log_key
        deployment.execution_logs = "\n".join(log_lines)[-500_000:]
        release_lock(db, project.id)
        db.commit()
        cleanup(root)

    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=None,
        action=f"deployment.{deployment.status}",
        metadata={
            "deployment_id": deployment.id,
            "plan_hash": plan.plan_hash,
            "verification": deployment.verification.get("status"),
        },
    )
    db.commit()
    ctx.publish(
        "deployment",
        {
            "status": deployment.status,
            "deployment_id": deployment.id,
            "verification": deployment.verification.get("status"),
        },
    )
    return result


def _json_from_output(output: str) -> dict[str, Any]:
    start = output.find("{")
    end = output.rfind("}")
    if start == -1 or end == -1:
        raise AppError("Terraform JSON output was empty")
    return json.loads(output[start : end + 1])


def _collect_outputs(executor: TerraformExecutor, env_dir: Path) -> dict[str, Any]:
    result = executor.run(["output", "-json"], cwd=env_dir, timeout=120)
    if result.exit_code != 0:
        return {}
    try:
        data = _json_from_output(result.output)
    except (json.JSONDecodeError, AppError):
        return {}
    outputs: dict[str, Any] = {}
    for name, item in data.items():
        if isinstance(item, dict) and "value" in item:
            outputs[name] = "[sensitive]" if item.get("sensitive") else item.get("value")
        else:
            outputs[name] = item
    return outputs


def _collect_state_resources(executor: TerraformExecutor, env_dir: Path) -> list[dict[str, Any]]:
    result = executor.run(["show", "-json"], cwd=env_dir, timeout=180)
    if result.exit_code != 0:
        return []
    try:
        state = _json_from_output(result.output)
    except (json.JSONDecodeError, AppError):
        return []
    resources: list[dict[str, Any]] = []

    def walk(module: dict[str, Any]) -> None:
        for resource in module.get("resources") or []:
            if resource.get("mode") != "managed":
                continue
            values = resource.get("values") or {}
            resources.append(
                {
                    "address": resource.get("address", ""),
                    "type": resource.get("type", ""),
                    "name": resource.get("name", ""),
                    "id": values.get("id") or values.get("resource_id") or "",
                }
            )
        for child in module.get("child_modules") or []:
            walk(child)

    walk(((state.get("values") or {}).get("root_module") or {}))
    return resources


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
ARM_TYPE_MAP = {
    "azurerm_resource_group": "microsoft.resources/resourcegroups",
    "azurerm_virtual_network": "microsoft.network/virtualnetworks",
    "azurerm_network_security_group": "microsoft.network/networksecuritygroups",
    "azurerm_application_gateway": "microsoft.network/applicationgateways",
    "azurerm_public_ip": "microsoft.network/publicipaddresses",
    "azurerm_nat_gateway": "microsoft.network/natgateways",
    "azurerm_private_endpoint": "microsoft.network/privateendpoints",
    "azurerm_linux_virtual_machine": "microsoft.compute/virtualmachines",
    "azurerm_windows_virtual_machine": "microsoft.compute/virtualmachines",
    "azurerm_linux_web_app": "microsoft.web/sites",
    "azurerm_service_plan": "microsoft.web/serverfarms",
    "azurerm_kubernetes_cluster": "microsoft.containerservice/managedclusters",
    "azurerm_mssql_server": "microsoft.sql/servers",
    "azurerm_mssql_database": "microsoft.sql/servers/databases",
    "azurerm_postgresql_flexible_server": "microsoft.dbforpostgresql/flexibleservers",
    "azurerm_mysql_flexible_server": "microsoft.dbformysql/flexibleservers",
    "azurerm_storage_account": "microsoft.storage/storageaccounts",
    "azurerm_key_vault": "microsoft.keyvault/vaults",
    "azurerm_log_analytics_workspace": "microsoft.operationalinsights/workspaces",
    "azurerm_application_insights": "microsoft.insights/components",
}


def verify_deployment(
    db: Session,
    project: Project,
    tf_project: TerraformProject,
    plan: TerraformPlan | None,
    *,
    state_resources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    version = db.get(ArchitectureVersion, tf_project.architecture_version_id)
    if not version:
        return {"status": "failed", "notes": ["Architecture version not found"]}
    spec = ArchitectureSpec.model_validate(version.architecture_json)
    expected_types = {
        ARM_TYPE_MAP[r.type] for r in spec.resources if r.type in ARM_TYPE_MAP
    }
    expected_types.discard("microsoft.resources/resourcegroups")
    report: dict[str, Any] = {
        "status": "incomplete",
        "checked": 0,
        "matched": 0,
        "missing": [],
        "unexpected": [],
        "resources": [],
        "notes": [],
        "checked_at": datetime.now(UTC).isoformat(),
    }
    try:
        client = AzureClient(plan.subscription_id if plan else (_project_subscription(db, project) or ""))
        resources = client.list_resources(tag_name="project", tag_value=project.name)
    except (AzureAuthError, AppError) as exc:
        report["status"] = "unsupported"
        report["notes"].append(f"Live Azure verification unavailable: {exc}")
        return report

    actual_types: dict[str, list[dict[str, Any]]] = {}
    for resource in resources:
        resource_type = str(resource.get("type", "")).lower()
        actual_types.setdefault(resource_type, []).append(resource)
    report["checked"] = sum(len(v) for v in actual_types.values())

    for expected in sorted(expected_types):
        found = actual_types.get(expected, [])
        if found:
            report["matched"] += 1
            report["resources"].append(
                {"type": expected, "state": "present", "count": len(found)}
            )
        else:
            report["missing"].append({"type": expected, "state": "missing"})

    if report["missing"]:
        report["status"] = "failed"
        report["notes"].append(
            "Some planned resource types were not found among resources tagged with the project name."
        )
    else:
        report["status"] = "verified"
    if state_resources:
        report["notes"].append(
            f"{len(state_resources)} managed resource(s) recorded in Terraform state."
        )
    return report


def refresh_verification(db: Session, deployment: Deployment) -> dict[str, Any]:
    plan = db.get(TerraformPlan, deployment.terraform_plan_id)
    tf_project = db.get(TerraformProject, plan.terraform_project_id) if plan else None
    project = db.get(Project, deployment.project_id)
    if not plan or not tf_project or not project:
        raise NotFound("Deployment context not found")
    report = verify_deployment(db, project, tf_project, plan)
    deployment.verification = report
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=None,
        action="deployment.verified",
        metadata={"deployment_id": deployment.id, "status": report.get("status")},
    )
    db.flush()
    return report
