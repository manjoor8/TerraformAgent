"""Terraform project generation, versioned editing and export."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.schemas import ArchitectureSpec
from app.core import audit
from app.core.errors import AppError, Conflict, NotFound
from app.core.security import hash_files
from app.core.state_machine import (
    ARCHITECTURE_APPROVED,
    ARCHITECTURE_READY,
    COMPLETED,
    DEPLOYING,
    GENERATING_TERRAFORM,
    PLANNING,
    VALIDATING,
    TRANSITIONS,
)
from app.models import (
    ArchitectureVersion,
    Project,
    TerraformFile,
    TerraformPlan,
    TerraformProject,
    User,
    new_id,
)
from app.services import architecture as architecture_service
from app.services.artifacts import store
from app.services.core import invalidate_approvals, iso, transition
from app.terraform.formatting import format_files
from app.terraform.generator import ProjectGenInput, TerraformProjectGenerator

ALLOWED_EDIT_SUFFIXES = (".tf", ".tfvars", ".hcl", ".md", ".json", ".yaml", ".yml", ".sh", ".ps1")
MAX_FILE_BYTES = 1_000_000


def get_project(db: Session, terraform_project_id: str) -> TerraformProject:
    project = db.get(TerraformProject, terraform_project_id)
    if not project:
        raise NotFound("Terraform project not found")
    return project


def latest_project(db: Session, project_id: str) -> TerraformProject | None:
    return db.scalar(
        select(TerraformProject)
        .where(TerraformProject.project_id == project_id)
        .order_by(TerraformProject.version.desc())
    )


def list_projects(db: Session, project_id: str) -> list[TerraformProject]:
    return list(
        db.scalars(
            select(TerraformProject)
            .where(TerraformProject.project_id == project_id)
            .order_by(TerraformProject.version.desc())
        )
    )


def files_dict(db: Session, terraform_project_id: str) -> dict[str, str]:
    files = db.scalars(
        select(TerraformFile).where(TerraformFile.terraform_project_id == terraform_project_id)
    )
    return {file.file_path: file.file_content for file in files}


def serialize_project(db: Session, tf_project: TerraformProject, *, include_files: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": tf_project.id,
        "project_id": tf_project.project_id,
        "architecture_version_id": tf_project.architecture_version_id,
        "version": tf_project.version,
        "source_digest": tf_project.source_digest,
        "status": tf_project.status,
        "kind": tf_project.kind,
        "module_map": tf_project.module_map,
        "created_at": iso(tf_project.created_at),
        "file_count": int(
            db.scalar(
                select(func.count())
                .select_from(TerraformFile)
                .where(TerraformFile.terraform_project_id == tf_project.id)
            )
            or 0
        ),
    }
    if include_files:
        payload["files"] = list_files(db, tf_project.id)
    return payload


def list_files(db: Session, terraform_project_id: str) -> list[dict[str, Any]]:
    files = list(
        db.scalars(
            select(TerraformFile)
            .where(TerraformFile.terraform_project_id == terraform_project_id)
            .order_by(TerraformFile.file_path)
        )
    )
    return [
        {
            "path": file.file_path,
            "content": file.file_content,
            "hash": file.content_hash,
            "version": file.version,
            "updated_at": iso(file.updated_at),
        }
        for file in files
    ]


def ensure_can_generate(db: Session, project: Project, user: User) -> ArchitectureVersion:
    version = architecture_service.current_approved_version(db, project.id)
    if not version:
        raise Conflict("An approved architecture is required before generating Terraform")
    if project.status in (DEPLOYING, PLANNING, VALIDATING):
        raise Conflict(f"Cannot generate Terraform while the project is {project.status}")
    return version


def _ensure_generating_state(db: Session, project: Project, user: User) -> None:
    if project.status == GENERATING_TERRAFORM:
        return
    allowed = TRANSITIONS.get(project.status, set())
    if GENERATING_TERRAFORM in allowed:
        transition(db, project, GENERATING_TERRAFORM, user_id=user.id, reason="Generating Terraform")
        return
    if ARCHITECTURE_APPROVED in allowed:
        transition(
            db,
            project,
            ARCHITECTURE_APPROVED,
            user_id=user.id,
            reason="Returning to approved architecture",
        )
        allowed = TRANSITIONS.get(project.status, set())
    if ARCHITECTURE_READY in allowed:
        transition(db, project, ARCHITECTURE_READY, user_id=user.id, reason="Regenerating code")
        allowed = TRANSITIONS.get(project.status, set())
    if ARCHITECTURE_APPROVED in allowed:
        transition(db, project, ARCHITECTURE_APPROVED, user_id=user.id, reason="Regenerating code")
        allowed = TRANSITIONS.get(project.status, set())
    if GENERATING_TERRAFORM in allowed:
        transition(db, project, GENERATING_TERRAFORM, user_id=user.id, reason="Generating Terraform")
        return
    raise Conflict(f"Cannot generate Terraform from project state '{project.status}'")


def generate(db: Session, project: Project, user: User) -> TerraformProject:
    version = ensure_can_generate(db, project, user)
    _ensure_generating_state(db, project, user)

    spec = ArchitectureSpec.model_validate(version.architecture_json)
    generation = TerraformProjectGenerator(
        spec,
        ProjectGenInput(
            project_name=project.name,
            project_id=project.id,
            environment=project.environment,
            region=project.region,
            tags={"owner": "platform-team"},
            backend=(project.settings or {}).get("backend", {"mode": "local"}),
            subscription_id=_subscription_id(db, project),
        ),
    ).build()
    formatted, format_notes = format_files(generation.files)

    tf_project = _persist_version(
        db,
        project=project,
        user=user,
        files=formatted,
        module_map=generation.module_map,
        architecture_version_id=version.id,
        notes=[*generation.notes, *format_notes],
    )
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action="terraform.generated",
        metadata={
            "terraform_project_id": tf_project.id,
            "version": tf_project.version,
            "files": len(formatted),
            "source_digest": tf_project.source_digest,
        },
    )
    transition(
        db,
        project,
        VALIDATING,
        user_id=user.id,
        reason="Terraform generated; validation required",
    )
    project.settings = {
        **(project.settings or {}),
        "terraform_notes": [*generation.notes, *format_notes],
    }
    db.flush()
    return tf_project


def _persist_version(
    db: Session,
    *,
    project: Project,
    user: User,
    files: dict[str, str],
    module_map: list[dict[str, str]],
    architecture_version_id: str,
    notes: list[str],
) -> TerraformProject:
    next_version = int(
        db.scalar(
            select(func.max(TerraformProject.version)).where(
                TerraformProject.project_id == project.id
            )
        )
        or 0
    ) + 1
    digest = hash_files(files)
    tf_project = TerraformProject(
        id=new_id(),
        project_id=project.id,
        architecture_version_id=architecture_version_id,
        version=next_version,
        source_digest=digest,
        status="generated",
        module_map=module_map,
        kind="main",
    )
    db.add(tf_project)
    db.flush()
    for path, content in sorted(files.items()):
        db.add(
            TerraformFile(
                terraform_project_id=tf_project.id,
                file_path=path,
                file_content=content,
                content_hash=hash_files({path: content}),
            )
        )
    archive = store.save_zip(
        f"projects/{project.id}/terraform/{tf_project.id}/terraform-project.zip", files
    )
    tf_project.repository_location = str(archive["key"])
    return tf_project


def _subscription_id(db: Session, project: Project) -> str | None:
    if project.azure_subscription_id:
        from app.models import AzureSubscription

        subscription = db.get(AzureSubscription, project.azure_subscription_id)
        if subscription:
            return subscription.subscription_id
    import os

    return os.environ.get("ARM_SUBSCRIPTION_ID")


def update_file(
    db: Session,
    project: Project,
    tf_project: TerraformProject,
    user: User,
    path: str,
    content: str,
) -> TerraformProject:
    current = files_dict(db, tf_project.id)
    if path not in current:
        raise NotFound(f"File not found: {path}")
    return _write_version(db, project, tf_project, user, current, path, content, action="terraform.file_edited")


def create_file(
    db: Session,
    project: Project,
    tf_project: TerraformProject,
    user: User,
    path: str,
    content: str,
) -> TerraformProject:
    current = files_dict(db, tf_project.id)
    if path in current:
        raise Conflict(f"File already exists: {path}")
    return _write_version(db, project, tf_project, user, current, path, content, action="terraform.file_created")


def delete_file(
    db: Session,
    project: Project,
    tf_project: TerraformProject,
    user: User,
    path: str,
) -> TerraformProject:
    current = files_dict(db, tf_project.id)
    if path not in current:
        raise NotFound(f"File not found: {path}")
    current.pop(path)
    return _write_version(
        db, project, tf_project, user, current, path, None, action="terraform.file_deleted"
    )


def _write_version(
    db: Session,
    project: Project,
    tf_project: TerraformProject,
    user: User,
    files: dict[str, str],
    path: str,
    content: str | None,
    *,
    action: str,
) -> TerraformProject:
    if project.status in (DEPLOYING, PLANNING):
        raise Conflict(f"Cannot edit Terraform while the project is {project.status}")
    normalized = path.replace("\\", "/").strip("/")
    if not normalized or ".." in normalized.split("/"):
        raise AppError("Invalid file path")
    if not normalized.endswith(ALLOWED_EDIT_SUFFIXES):
        raise AppError("Editing this file type is not allowed")
    if content is not None:
        if len(content.encode()) > MAX_FILE_BYTES:
            raise AppError("File content exceeds 1 MB limit")
        files[normalized] = content
    formatted, _ = format_files(files)
    formatted.setdefault("README.md", files.get("README.md", "# Terraform project\n"))

    new_project = _persist_version(
        db,
        project=project,
        user=user,
        files=formatted,
        module_map=tf_project.module_map,
        architecture_version_id=tf_project.architecture_version_id,
        notes=[],
    )
    invalidate_approvals(
        db,
        project.id,
        "Terraform source changed manually",
        approval_types=("deployment",),
    )
    for plan in db.scalars(
        select(TerraformPlan).where(
            TerraformPlan.terraform_project_id == tf_project.id,
            TerraformPlan.status.in_(("generated", "approved")),
        )
    ):
        plan.status = "stale"
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=user.id,
        action=action,
        metadata={
            "path": normalized,
            "terraform_project_id": new_project.id,
            "version": new_project.version,
            "source_digest": new_project.source_digest,
        },
    )
    project.settings = {
        **(project.settings or {}),
        "manual_edits": [
            *((project.settings or {}).get("manual_edits", [])[-20:]),
            {
                "path": normalized,
                "terraform_project_id": new_project.id,
                "at": datetime.now(timezone.utc).isoformat(),
                "user_id": user.id,
            },
        ],
    }
    if project.status != ARCHITECTURE_APPROVED:
        if ARCHITECTURE_APPROVED in TRANSITIONS.get(project.status, set()):
            transition(
                db,
                project,
                ARCHITECTURE_APPROVED,
                user_id=user.id,
                reason="Manual Terraform edit requires re-validation",
            )
        elif ARCHITECTURE_READY in TRANSITIONS.get(project.status, set()):
            transition(
                db,
                project,
                ARCHITECTURE_READY,
                user_id=user.id,
                reason="Manual Terraform edit requires re-validation",
            )
    db.flush()
    return new_project


def export_zip(db: Session, tf_project: TerraformProject) -> bytes:
    if tf_project.repository_location and store.exists(tf_project.repository_location):
        return store.load(tf_project.repository_location)
    files = files_dict(db, tf_project.id)
    archive = store.save_zip(
        f"projects/{tf_project.project_id}/terraform/{tf_project.id}/terraform-project.zip", files
    )
    return store.load(str(archive["key"]))


def source_digest_of(db: Session, tf_project: TerraformProject) -> str:
    return hash_files(files_dict(db, tf_project.id))


def persist_repaired_version(
    db: Session,
    project: Project,
    tf_project: TerraformProject,
    user: User,
    files: dict[str, str],
    notes: list[str],
) -> TerraformProject:
    """Persist AI-repaired files as a new immutable Terraform version."""
    repaired = _persist_version(
        db,
        project=project,
        user=user,
        files=files,
        module_map=tf_project.module_map,
        architecture_version_id=tf_project.architecture_version_id,
        notes=notes,
    )
    invalidate_approvals(
        db,
        project.id,
        "Terraform source changed by the repair workflow",
        approval_types=("deployment",),
    )
    project.settings = {
        **(project.settings or {}),
        "repair_notes": [*((project.settings or {}).get("repair_notes", [])[-10:]), *notes][-30:],
    }
    db.flush()
    return repaired
