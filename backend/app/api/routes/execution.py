"""Terraform project, validation, plan, deployment and job endpoints."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.api.routes.projects import Reader, Writer, project_role
from app.core.errors import AppError, Conflict, NotFound
from app.models import (
    Approval,
    Deployment,
    Project,
    TerraformPlan,
    TerraformProject,
    User,
    ValidationRun,
)
from app.services import execution, terraform_service
from app.services import repair as repair_service
from app.services.artifacts import store
from app.services.core import Access, iso, project_access
from app.services.jobs import job_status

router = APIRouter()


def _tf_access(db: Session, user: User, terraform_project_id: str, min_role: str = "viewer") -> tuple[TerraformProject, Project, Access]:
    tf_project = terraform_service.get_project(db, terraform_project_id)
    access = project_access(db, user, tf_project.project_id, min_role)
    assert access.project is not None
    return tf_project, access.project, access


@router.post("/projects/{project_id}/terraform/generate")
def generate_terraform(project_id: str, db: DbSession, access: Writer) -> dict[str, Any]:
    tf_project = terraform_service.generate(db, access.project, access.user)
    db.commit()
    return terraform_service.serialize_project(db, tf_project, include_files=True)


@router.get("/projects/{project_id}/terraform")
def current_terraform(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    tf_project = terraform_service.latest_project(db, project_id)
    if not tf_project:
        raise NotFound("No Terraform project has been generated yet")
    payload = terraform_service.serialize_project(db, tf_project, include_files=True)
    payload["notes"] = (access.project.settings or {}).get("terraform_notes", [])
    return payload


@router.get("/projects/{project_id}/terraform/versions")
def terraform_versions(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    projects = terraform_service.list_projects(db, project_id)
    return {
        "items": [terraform_service.serialize_project(db, project) for project in projects],
        "total": len(projects),
    }


@router.get("/terraform-projects/{terraform_project_id}")
def get_terraform_project(
    terraform_project_id: str, db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    tf_project, _project, _access = _tf_access(db, user, terraform_project_id)
    return terraform_service.serialize_project(db, tf_project, include_files=True)


@router.get("/terraform-projects/{terraform_project_id}/files")
def list_terraform_files(
    terraform_project_id: str, db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    _tf_project, _project, _access = _tf_access(db, user, terraform_project_id)
    files = terraform_service.list_files(db, terraform_project_id)
    return {"items": files, "total": len(files)}


@router.put("/terraform-projects/{terraform_project_id}/files")
def update_terraform_file(
    terraform_project_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    tf_project, project, access = _tf_access(db, user, terraform_project_id, "member")
    new_project = terraform_service.update_file(
        db,
        project,
        tf_project,
        access.user,
        str(payload.get("path", "")),
        str(payload.get("content", "")),
    )
    db.commit()
    return terraform_service.serialize_project(db, new_project, include_files=True)


@router.post("/terraform-projects/{terraform_project_id}/files")
def create_terraform_file(
    terraform_project_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    tf_project, project, access = _tf_access(db, user, terraform_project_id, "member")
    new_project = terraform_service.create_file(
        db,
        project,
        tf_project,
        access.user,
        str(payload.get("path", "")),
        str(payload.get("content", "")),
    )
    db.commit()
    return terraform_service.serialize_project(db, new_project, include_files=True)


@router.delete("/terraform-projects/{terraform_project_id}/files")
def delete_terraform_file(
    terraform_project_id: str,
    db: DbSession,
    user: CurrentUser,
    path: Annotated[str, Query()],
) -> dict[str, Any]:
    tf_project, project, access = _tf_access(db, user, terraform_project_id, "member")
    new_project = terraform_service.delete_file(db, project, tf_project, access.user, path)
    db.commit()
    return terraform_service.serialize_project(db, new_project, include_files=True)


@router.get("/terraform-projects/{terraform_project_id}/download")
def download_terraform_project(
    terraform_project_id: str, db: DbSession, user: CurrentUser
) -> Response:
    tf_project, project, _access = _tf_access(db, user, terraform_project_id)
    data = terraform_service.export_zip(db, tf_project)
    filename = f"{project.name.replace(' ', '-').lower()}-v{tf_project.version}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/terraform-projects/{terraform_project_id}/module-map")
def module_map(terraform_project_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    tf_project, _project, _access = _tf_access(db, user, terraform_project_id)
    return {"items": tf_project.module_map}


@router.post("/terraform-projects/{terraform_project_id}/validate")
def start_validation(
    terraform_project_id: str, db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    tf_project, project, access = _tf_access(db, user, terraform_project_id, "member")
    run, job_id = execution.start_validation(db, project, tf_project, access.user)
    return {
        "validation_run_id": run.id,
        "job_id": job_id,
        "status": run.status,
    }


@router.post("/terraform-projects/{terraform_project_id}/repair")
def start_repair(
    terraform_project_id: str, db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    tf_project, project, access = _tf_access(db, user, terraform_project_id, "member")
    job_id = repair_service.start_repair(db, project, tf_project, access.user)
    return {"job_id": job_id, "max_attempts": repair_service.MAX_REPAIR_ATTEMPTS}


@router.get("/terraform-projects/{terraform_project_id}/validations")
def list_validations(
    terraform_project_id: str, db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    _tf_project, _project, _access = _tf_access(db, user, terraform_project_id)
    runs = list(
        db.scalars(
            select(ValidationRun)
            .where(ValidationRun.terraform_project_id == terraform_project_id)
            .order_by(ValidationRun.created_at.desc())
        )
    )
    return {"items": [_serialize_validation(run) for run in runs], "total": len(runs)}


@router.get("/validation-runs/{validation_run_id}")
def get_validation(validation_run_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    run = db.get(ValidationRun, validation_run_id)
    if not run:
        raise NotFound("Validation run not found")
    _tf_project, _project, _access = _tf_access(db, user, run.terraform_project_id)
    return _serialize_validation(run)


def _serialize_validation(run: ValidationRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "terraform_project_id": run.terraform_project_id,
        "status": run.status,
        "attempt": run.attempt,
        "results": run.results,
        "log_location": run.log_location,
        "created_at": iso(run.created_at),
        "completed_at": iso(run.completed_at),
    }


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
@router.post("/projects/{project_id}/plan")
def start_plan(
    project_id: str, payload: dict[str, Any], db: DbSession, access: Writer
) -> dict[str, Any]:
    tf_project = terraform_service.latest_project(db, project_id)
    if not tf_project:
        raise Conflict("Generate Terraform before planning")
    plan, job_id = execution.start_plan(
        db,
        access.project,
        tf_project,
        access.user,
        environment=payload.get("environment") or access.project.environment,
    )
    return {"plan_id": plan.id, "job_id": job_id, "status": plan.status}


@router.get("/projects/{project_id}/plans")
def list_plans(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    tf_project_ids = [p.id for p in terraform_service.list_projects(db, project_id)]
    plans = []
    if tf_project_ids:
        plans = list(
            db.scalars(
                select(TerraformPlan)
                .where(TerraformPlan.terraform_project_id.in_(tf_project_ids))
                .order_by(TerraformPlan.created_at.desc())
            )
        )
    return {"items": [_serialize_plan(plan) for plan in plans], "total": len(plans)}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    plan = db.get(TerraformPlan, plan_id)
    if not plan:
        raise NotFound("Plan not found")
    tf_project, project, _access = _tf_access(db, user, plan.terraform_project_id)
    payload = _serialize_plan(plan)
    payload["terraform_project_id"] = tf_project.id
    payload["terraform_version"] = tf_project.version
    payload["source_digest_current"] = tf_project.source_digest
    payload["source_stale"] = plan.source_digest != tf_project.source_digest
    payload["project_name"] = project.name
    approval = db.scalar(
        select(Approval).where(
            Approval.terraform_plan_id == plan.id,
            Approval.approval_type == "deployment",
            Approval.status == "active",
        )
    )
    payload["approval"] = (
        {
            "id": approval.id,
            "artifact_hash": approval.artifact_hash,
            "approved_at": iso(approval.approved_at),
            "approved_by": approval.approved_by,
        }
        if approval
        else None
    )
    return payload


def _serialize_plan(plan: TerraformPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "terraform_project_id": plan.terraform_project_id,
        "environment": plan.environment,
        "subscription_id": plan.subscription_id,
        "plan_hash": plan.plan_hash,
        "source_digest": plan.source_digest,
        "status": plan.status,
        "destructive": plan.destructive,
        "summary": plan.plan_summary,
        "error": plan.error,
        "created_at": iso(plan.created_at),
    }


@router.get("/plans/{plan_id}/download")
def download_plan(plan_id: str, db: DbSession, user: CurrentUser) -> Response:
    plan = db.get(TerraformPlan, plan_id)
    if not plan:
        raise NotFound("Plan not found")
    _tf_access(db, user, plan.terraform_project_id)
    if not plan.plan_artifact_location or not store.exists(plan.plan_artifact_location):
        raise NotFound("Plan artifact is not available")
    data = store.load(plan.plan_artifact_location)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{plan_id}.tfplan"'},
    )


@router.post("/plans/{plan_id}/approve")
def approve_plan(
    plan_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    plan = db.get(TerraformPlan, plan_id)
    if not plan:
        raise NotFound("Plan not found")
    _tf_project, project, access = _tf_access(db, user, plan.terraform_project_id, "member")
    approval = execution.approve_plan(
        db,
        plan,
        access.user,
        note=str(payload.get("note", "")),
        confirm_destructive=bool(payload.get("confirm_destructive", False)),
    )
    db.commit()
    return {
        "approval": {
            "id": approval.id,
            "artifact_hash": approval.artifact_hash,
            "approved_at": iso(approval.approved_at),
            "approved_by": approval.approved_by,
        },
        "plan": _serialize_plan(plan),
        "project_status": project.status,
    }


@router.post("/plans/{plan_id}/reject")
def reject_plan(
    plan_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    plan = db.get(TerraformPlan, plan_id)
    if not plan:
        raise NotFound("Plan not found")
    _tf_project, project, access = _tf_access(db, user, plan.terraform_project_id, "member")
    plan = execution.reject_plan(
        db, plan, access.user, str(payload.get("reason", "Rejected by user"))
    )
    db.commit()
    return {"plan": _serialize_plan(plan), "project_status": project.status}


@router.post("/plans/{plan_id}/deploy")
def deploy_plan(
    plan_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    plan = db.get(TerraformPlan, plan_id)
    if not plan:
        raise NotFound("Plan not found")
    _tf_project, _project, access = _tf_access(db, user, plan.terraform_project_id, "member")
    deployment, job_id = execution.start_deployment(db, plan, access.user)
    return {
        "deployment_id": deployment.id,
        "job_id": job_id,
        "status": deployment.status,
    }


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/deployments")
def list_deployments(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    deployments = list(
        db.scalars(
            select(Deployment)
            .where(Deployment.project_id == project_id)
            .order_by(Deployment.created_at.desc())
        )
    )
    return {
        "items": [_serialize_deployment(deployment) for deployment in deployments],
        "total": len(deployments),
    }


@router.get("/deployments/{deployment_id}")
def get_deployment(deployment_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise NotFound("Deployment not found")
    project_access(db, user, deployment.project_id)
    return _serialize_deployment(deployment)


def _serialize_deployment(deployment: Deployment) -> dict[str, Any]:
    return {
        "id": deployment.id,
        "project_id": deployment.project_id,
        "terraform_plan_id": deployment.terraform_plan_id,
        "approval_id": deployment.approval_id,
        "subscription_id": deployment.subscription_id,
        "environment": deployment.environment,
        "status": deployment.status,
        "summary": deployment.summary,
        "outputs": deployment.outputs,
        "verification": deployment.verification,
        "started_at": iso(deployment.started_at),
        "completed_at": iso(deployment.completed_at),
        "created_at": iso(deployment.created_at),
    }


@router.get("/deployments/{deployment_id}/logs")
def deployment_logs(deployment_id: str, db: DbSession, user: CurrentUser) -> Response:
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise NotFound("Deployment not found")
    project_access(db, user, deployment.project_id)
    if deployment.log_location and store.exists(deployment.log_location):
        return Response(
            content=store.load_text(deployment.log_location),
            media_type="text/plain",
        )
    return Response(content=deployment.execution_logs or "", media_type="text/plain")


@router.post("/deployments/{deployment_id}/cancel")
def cancel_deployment(deployment_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise NotFound("Deployment not found")
    project_access(db, user, deployment.project_id, "member")
    if deployment.status not in ("queued", "running"):
        raise Conflict("Deployment is not running")
    cancelled = execution.jobs.cancel_project(deployment.project_id)
    return {"cancellation_requested": cancelled > 0}


@router.post("/deployments/{deployment_id}/verify")
def verify_deployment(deployment_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise NotFound("Deployment not found")
    project_access(db, user, deployment.project_id, "member")
    report = execution.refresh_verification(db, deployment)
    db.commit()
    return {"verification": report}


@router.get("/deployments/{deployment_id}/verify")
def get_verification(deployment_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    deployment = db.get(Deployment, deployment_id)
    if not deployment:
        raise NotFound("Deployment not found")
    project_access(db, user, deployment.project_id)
    return {"verification": deployment.verification or {}}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    status = job_status(job_id)
    if not status:
        raise NotFound("Job not found")
    project_access(db, user, status["project_id"])
    return status
