"""Project workspace, conversation, requirements, documents, audit and SSE."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentAccess, CurrentUser, DbSession
from app.core import audit
from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.core.state_machine import STATUS_LABELS
from app.db import SessionLocal
from app.models import (
    Approval,
    ArchitectureVersion,
    AzureSubscription,
    Document,
    Project,
    ProjectConversation,
    Requirement,
    TerraformPlan,
    TerraformProject,
    ValidationRun,
)
from app.services import architecture as architecture_service
from app.services import documents as document_service
from app.services import orchestrator, requirements as requirement_service
from app.services import terraform_service
from app.services.artifacts import store
from app.services.core import (
    Access,
    add_message,
    invalidate_approvals,
    iso,
    open_questions,
    project_access,
    serialize_document,
    serialize_message,
    serialize_project,
    serialize_requirement,
    transition,
)
from app.services.dashboard import project_audit, serialize_audit
from app.services.events import bus

router = APIRouter()


def project_role(min_role: str):
    def dependency(project_id: str, db: DbSession, user: CurrentUser) -> Access:
        return project_access(db, user, project_id, min_role)

    return dependency


Reader = Annotated[Access, Depends(project_role("viewer"))]
Writer = Annotated[Access, Depends(project_role("member"))]
Admin = Annotated[Access, Depends(project_role("admin"))]


@router.get("/projects")
def list_projects(
    db: DbSession,
    access: CurrentAccess,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    from sqlalchemy import func

    query = select(Project).where(Project.organization_id == access.organization.id)
    if status:
        query = query.where(Project.status == status)
    if q:
        query = query.where(Project.name.ilike(f"%{q}%"))
    total = int(
        db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.organization_id == access.organization.id)
        )
        or 0
    )
    projects = list(
        db.scalars(query.order_by(Project.updated_at.desc()).limit(limit).offset(offset))
    )
    return {
        "items": [serialize_project(db, project) for project in projects],
        "total": total,
        "status_labels": STATUS_LABELS,
    }


@router.post("/projects")
def create_project(payload: dict[str, Any], db: DbSession, access: CurrentAccess) -> dict[str, Any]:
    if not access.can_write:
        raise Forbidden("Member role required")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise AppError("Project name is required")
    environment = str(payload.get("environment", "dev"))
    if environment not in ("dev", "staging", "production"):
        raise AppError("environment must be dev, staging or production")
    subscription_id = payload.get("azure_subscription_id")
    if subscription_id:
        subscription = db.get(AzureSubscription, subscription_id)
        if not subscription or subscription.organization_id != access.organization.id:
            raise AppError("Invalid Azure subscription")
    project = Project(
        organization_id=access.organization.id,
        name=name[:200],
        description=str(payload.get("description", ""))[:5000],
        environment=environment,
        region=str(payload.get("region", "eastus"))[:60],
        azure_subscription_id=subscription_id,
        created_by=access.user.id,
        status="draft",
        settings={"backend": {"mode": "local"}, "open_questions": []},
    )
    db.add(project)
    db.flush()
    add_message(
        db,
        project,
        "assistant",
        "Tell me what you need to deploy in Azure. Describe the workload, availability, "
        "network exposure, data services, budget and region — or upload an architecture document. "
        "I will ask only the questions that materially change the design.",
        metadata={"kind": "welcome"},
    )
    audit.record(
        db,
        organization_id=access.organization.id,
        project_id=project.id,
        user_id=access.user.id,
        action="project.created",
        metadata={"name": project.name, "environment": environment},
    )
    db.commit()
    return serialize_project(db, project)


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    project = access.project
    payload = serialize_project(db, project)
    if project.azure_subscription_id:
        subscription = db.get(AzureSubscription, project.azure_subscription_id)
        if subscription:
            payload["subscription"] = {
                "id": subscription.id,
                "subscription_id": subscription.subscription_id,
                "subscription_name": subscription.subscription_name,
                "tenant_id": subscription.tenant_id,
                "connection_status": subscription.connection_status,
            }
    return payload


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str, payload: dict[str, Any], db: DbSession, access: Writer
) -> dict[str, Any]:
    project = access.project
    architecture_change = False
    target_change = False
    if "name" in payload and str(payload["name"]).strip():
        project.name = str(payload["name"]).strip()[:200]
    if "description" in payload:
        project.description = str(payload["description"])[:5000]
    if "environment" in payload:
        environment = str(payload["environment"])
        if environment not in ("dev", "staging", "production"):
            raise AppError("environment must be dev, staging or production")
        if environment != project.environment:
            project.environment = environment
            architecture_change = True
    if "region" in payload:
        region = str(payload["region"])
        if region != project.region:
            project.region = region[:60]
            architecture_change = True
    if "azure_subscription_id" in payload:
        subscription_id = payload["azure_subscription_id"]
        if subscription_id:
            subscription = db.get(AzureSubscription, subscription_id)
            if not subscription or subscription.organization_id != access.organization.id:
                raise AppError("Invalid Azure subscription")
        if subscription_id != project.azure_subscription_id:
            project.azure_subscription_id = subscription_id
            target_change = True
    if "settings" in payload and isinstance(payload["settings"], dict):
        incoming = payload["settings"]
        settings = dict(project.settings or {})
        if "backend" in incoming:
            backend = incoming["backend"] or {"mode": "local"}
            if backend.get("mode") not in ("local", "azurerm"):
                raise AppError("Backend mode must be local or azurerm")
            if backend.get("mode") == "azurerm":
                for field in ("resource_group", "storage_account", "container"):
                    if not str(backend.get(field, "")).strip():
                        raise AppError(f"Backend field '{field}' is required")
            settings["backend"] = backend
            target_change = True
        if "policies" in incoming:
            disabled = incoming["policies"].get("disabled_rules", [])
            if not isinstance(disabled, list):
                raise AppError("disabled_rules must be a list")
            settings["policies"] = {"disabled_rules": [str(x) for x in disabled]}
        project.settings = settings
    material_change = architecture_change or target_change
    if architecture_change and project.status not in ("draft", "gathering_requirements"):
        invalidate_approvals(db, project.id, "Architecture-relevant project configuration changed")
        if project.status not in ("architecture_ready", "gathering_requirements"):
            try:
                transition(
                    db,
                    project,
                    "architecture_ready",
                    user_id=access.user.id,
                    reason="Project environment or region changed",
                )
            except Conflict:
                pass
    elif target_change:
        # Deployment target changes invalidate deployment approvals but do not
        # change the architecture itself.
        invalidate_approvals(
            db,
            project.id,
            "Deployment target changed",
            approval_types=("deployment",),
        )
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=access.user.id,
        action="project.updated",
        metadata={
            "fields": list(payload),
            "architecture_change": architecture_change,
            "target_change": target_change,
        },
    )
    db.commit()
    return get_project(project_id, db, access)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db: DbSession, access: Admin) -> dict[str, bool]:
    project = access.project
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=None,
        user_id=access.user.id,
        action="project.deleted",
        metadata={"project_id": project.id, "name": project.name},
    )
    db.delete(project)
    db.commit()
    return {"deleted": True}


@router.get("/projects/{project_id}/overview")
def project_overview(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    project = access.project
    counts = serialize_project(db, project)["counts"]
    latest_arch = architecture_service.latest_version(db, project.id)
    latest_tf = terraform_service.latest_project(db, project.id)
    validation_passed = None
    if latest_tf:
        validation_passed = db.scalar(
            select(ValidationRun)
            .where(
                ValidationRun.terraform_project_id == latest_tf.id,
                ValidationRun.status == "passed",
            )
            .order_by(ValidationRun.created_at.desc())
        )
    latest_plan = None
    if latest_tf:
        latest_plan = db.scalar(
            select(TerraformPlan)
            .where(TerraformPlan.terraform_project_id == latest_tf.id)
            .order_by(TerraformPlan.created_at.desc())
        )
    approved_architecture = architecture_service.current_approved_version(db, project.id)
    deployment_approval = db.scalar(
        select(Approval).where(
            Approval.project_id == project.id,
            Approval.approval_type == "deployment",
            Approval.status == "active",
        )
    )
    checklist = [
        {"key": "requirements", "label": "Capture requirements", "done": counts["requirements"] > 0},
        {"key": "architecture", "label": "Generate architecture", "done": latest_arch is not None},
        {"key": "review", "label": "Review architecture", "done": bool(latest_arch and latest_arch.review)},
        {"key": "architecture_approval", "label": "Approve architecture", "done": approved_architecture is not None},
        {"key": "terraform", "label": "Generate Terraform", "done": latest_tf is not None},
        {"key": "validation", "label": "Validate Terraform", "done": validation_passed is not None},
        {"key": "plan", "label": "Generate plan", "done": latest_plan is not None},
        {"key": "deployment_approval", "label": "Approve deployment", "done": deployment_approval is not None},
        {"key": "deployment", "label": "Deploy and verify", "done": project.status == "completed"},
    ]
    next_action = next((item for item in checklist if not item["done"]), None)
    return {
        "project": serialize_project(db, project),
        "status_label": STATUS_LABELS.get(project.status, project.status),
        "checklist": checklist,
        "next_action": next_action,
        "open_questions": open_questions(project),
        "latest_architecture": (
            architecture_service.serialize_version(latest_arch, full=False) if latest_arch else None
        ),
        "latest_terraform": (
            terraform_service.serialize_project(db, latest_tf) if latest_tf else None
        ),
        "latest_plan": _plan_brief(latest_plan) if latest_plan else None,
        "validation_passed": bool(validation_passed),
    }


def _plan_brief(plan: TerraformPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "status": plan.status,
        "plan_hash": plan.plan_hash,
        "destructive": plan.destructive,
        "created_at": iso(plan.created_at),
        "summary": plan.plan_summary,
    }


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/messages")
def list_messages(
    project_id: str,
    db: DbSession,
    access: Reader,
    limit: Annotated[int, Query(ge=1, le=300)] = 200,
) -> dict[str, Any]:
    messages = list(
        db.scalars(
            select(ProjectConversation)
            .where(ProjectConversation.project_id == project_id)
            .order_by(ProjectConversation.created_at.desc())
            .limit(limit)
        )
    )
    messages.reverse()
    return {"items": [serialize_message(message) for message in messages]}


@router.post("/projects/{project_id}/messages")
def send_message(
    project_id: str, payload: dict[str, Any], db: DbSession, access: Writer
) -> dict[str, Any]:
    content = str(payload.get("content", "")).strip()
    result = orchestrator.handle_message(db, access.project, access.user, content)
    result["status_label"] = STATUS_LABELS.get(result["status"], result["status"])
    return result


@router.post("/projects/{project_id}/chat/stream")
def chat_stream(
    project_id: str, payload: dict[str, Any], db: DbSession, access: Writer
) -> StreamingResponse:
    content = str(payload.get("content", "")).strip()

    def generate():
        result = orchestrator.handle_message(db, access.project, access.user, content)
        yield f"event: message\ndata: {json.dumps(result, default=str)}\n\n"
        yield f"event: status\ndata: {json.dumps({'status': result['status']})}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Requirements & questions
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/requirements")
def list_requirements(
    project_id: str,
    db: DbSession,
    access: Reader,
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    items = requirement_service.list_requirements(db, project_id)
    if status:
        items = [item for item in items if item.status == status]
    return {"items": [serialize_requirement(item) for item in items], "total": len(items)}


@router.post("/projects/{project_id}/requirements")
def create_requirement(
    project_id: str, payload: dict[str, Any], db: DbSession, access: Writer
) -> dict[str, Any]:
    requirement = requirement_service.create_requirement(
        db,
        access.project,
        access.user,
        description=str(payload.get("description", "")),
        requirement_type=payload.get("requirement_type"),
        priority=str(payload.get("priority", "mandatory")),
        source="user",
    )
    db.commit()
    return serialize_requirement(requirement)


@router.patch("/requirements/{requirement_id}")
def update_requirement(
    requirement_id: str, payload: dict[str, Any], db: DbSession, user: CurrentUser
) -> dict[str, Any]:
    requirement = db.get(Requirement, requirement_id)
    if not requirement:
        raise NotFound("Requirement not found")
    access = project_access(db, user, requirement.project_id, "member")
    updated = requirement_service.update_requirement(
        db,
        requirement,
        user,
        **{key: payload[key] for key in ("description", "priority", "status", "requirement_type") if key in payload},
    )
    db.commit()
    return serialize_requirement(updated)


@router.delete("/requirements/{requirement_id}")
def delete_requirement(requirement_id: str, db: DbSession, user: CurrentUser) -> dict[str, bool]:
    requirement = db.get(Requirement, requirement_id)
    if not requirement:
        raise NotFound("Requirement not found")
    access = project_access(db, user, requirement.project_id, "member")
    requirement_service.delete_requirement(db, requirement, user)
    db.commit()
    return {"deleted": True}


@router.get("/projects/{project_id}/questions")
def list_questions(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    questions = open_questions(access.project)
    return {"items": questions, "total": len(questions)}


@router.post("/projects/{project_id}/questions/{question_id}/answer")
def answer_question(
    project_id: str,
    question_id: str,
    payload: dict[str, Any],
    db: DbSession,
    access: Writer,
) -> dict[str, Any]:
    requirement = requirement_service.answer_clarification(
        db,
        access.project,
        access.user,
        question_id,
        str(payload.get("answer", "")),
    )
    db.commit()
    return {
        "requirement": serialize_requirement(requirement),
        "open_questions": open_questions(access.project),
    }


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@router.post("/projects/{project_id}/documents")
async def upload_document(
    project_id: str,
    db: DbSession,
    access: Writer,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    data = await file.read(25 * 1024 * 1024 + 1)
    document = document_service.upload_document(
        db,
        access.project,
        access.user,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        data=data,
    )
    db.commit()
    payload = serialize_document(document)
    payload["extraction"] = document.extraction
    return payload


@router.get("/projects/{project_id}/documents")
def list_documents(project_id: str, db: DbSession, access: Reader) -> dict[str, Any]:
    items = document_service.list_documents(db, project_id)
    return {"items": [serialize_document(document) for document in items], "total": len(items)}


@router.get("/documents/{document_id}")
def get_document(document_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    document = db.get(Document, document_id)
    if not document:
        raise NotFound("Document not found")
    project_access(db, user, document.project_id)
    payload = serialize_document(document)
    payload["extraction"] = document.extraction
    return payload


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, db: DbSession, user: CurrentUser) -> Response:
    document = db.get(Document, document_id)
    if not document:
        raise NotFound("Document not found")
    project_access(db, user, document.project_id)
    data = store.load(document.storage_location)
    return Response(
        content=data,
        media_type=document.content_type,
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )


@router.post("/documents/{document_id}/extract")
def reextract_document(document_id: str, db: DbSession, user: CurrentUser) -> dict[str, Any]:
    document = db.get(Document, document_id)
    if not document:
        raise NotFound("Document not found")
    project_access(db, user, document.project_id, "member")
    document_service.reextract_document(db, document, user)
    db.commit()
    payload = serialize_document(document)
    payload["extraction"] = document.extraction
    return payload


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, db: DbSession, user: CurrentUser) -> dict[str, bool]:
    document = db.get(Document, document_id)
    if not document:
        raise NotFound("Document not found")
    project_access(db, user, document.project_id, "member")
    document_service.delete_document(db, document, user)
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Audit, export, events
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/audit")
def project_audit_log(
    project_id: str,
    db: DbSession,
    access: Reader,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    events = project_audit(db, project_id, limit)
    return {"items": [serialize_audit(event) for event in events], "total": len(events)}


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, db: DbSession, access: Reader) -> Response:
    project = access.project
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        latest_arch = architecture_service.latest_version(db, project.id)
        if latest_arch:
            archive.writestr(
                "architecture/architecture.json",
                json.dumps(latest_arch.architecture_json, indent=2),
            )
            archive.writestr("architecture/architecture.md", latest_arch.summary or "")
            archive.writestr("architecture/diagram.mmd", latest_arch.diagram_source or "")
        latest_tf = terraform_service.latest_project(db, project.id)
        if latest_tf:
            for file in terraform_service.files_dict(db, latest_tf.id).items():
                archive.writestr(f"terraform/{file[0]}", file[1])
        archive.writestr(
            "project.json",
            json.dumps(serialize_project(db, project), indent=2, default=str),
        )
        archive.writestr(
            "audit.json",
            json.dumps(
                [serialize_audit(event) for event in project_audit(db, project.id, 500)],
                indent=2,
                default=str,
            ),
        )
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        user_id=access.user.id,
        action="project.exported",
        metadata={"format": "zip"},
    )
    db.commit()
    filename = f"{project.name.replace(' ', '-').lower()}-export.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{project_id}/events")
async def project_events(
    project_id: str,
    db: DbSession,
    user: CurrentUser,
    since: Annotated[int, Query()] = 0,
) -> StreamingResponse:
    project_access(db, user, project_id)

    async def stream():
        queue = bus.subscribe(project_id)
        try:
            for event in bus.history(project_id, since):
                yield event.to_sse()
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield event.to_sse()
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(project_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
