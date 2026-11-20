"""ORM models. IDs are UUID strings for SQLite/PostgreSQL portability."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(500), nullable=True)
    authentication_provider: Mapped[str] = mapped_column(String(40), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_membership"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class AzureSubscription(Base):
    __tablename__ = "azure_subscriptions"
    __table_args__ = (
        UniqueConstraint("organization_id", "subscription_id", name="uq_org_subscription"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64))
    subscription_id: Mapped[str] = mapped_column(String(64))
    subscription_name: Mapped[str] = mapped_column(String(200))
    auth_method: Mapped[str] = mapped_column(String(40), default="service_principal")
    connection_status: Mapped[str] = mapped_column(String(30), default="unverified")
    default_region: Mapped[str] = mapped_column(String(60), default="eastus")
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Projects & conversation
# ---------------------------------------------------------------------------
class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_org_status", "organization_id", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    environment: Mapped[str] = mapped_column(String(30), default="dev")
    region: Mapped[str] = mapped_column(String(60), default="eastus")
    azure_subscription_id: Mapped[str | None] = mapped_column(
        ForeignKey("azure_subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProjectConversation(Base):
    __tablename__ = "project_conversations"
    __table_args__ = (Index("ix_conversations_project", "project_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    requirement_type: Mapped[str] = mapped_column(String(40), default="other")
    description: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), default="conversation")
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="proposed")
    priority: Mapped[str] = mapped_column(String(30), default="mandatory")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(400))
    content_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_location: Mapped[str] = mapped_column(String(600))
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    document_type: Mapped[str] = mapped_column(String(50), default="other")
    extraction_status: Mapped[str] = mapped_column(String(30), default="pending")
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------
class ArchitectureVersion(Base):
    __tablename__ = "architecture_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_arch_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    architecture_json: Mapped[dict] = mapped_column(JSON, default=dict)
    diagram_source: Mapped[str] = mapped_column(Text, default="")
    cost_estimate: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    review: Mapped[dict] = mapped_column(JSON, default=dict)
    approval_status: Mapped[str] = mapped_column(String(30), default="draft")
    created_by_agent: Mapped[str] = mapped_column(String(60), default="azure_solution_architect")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArchitectureDecision(Base):
    __tablename__ = "architecture_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    architecture_version_id: Mapped[str] = mapped_column(
        ForeignKey("architecture_versions.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    source_requirement: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternates_considered: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="accepted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Terraform
# ---------------------------------------------------------------------------
class TerraformProject(Base):
    __tablename__ = "terraform_projects"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_tf_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    architecture_version_id: Mapped[str] = mapped_column(
        ForeignKey("architecture_versions.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_digest: Mapped[str] = mapped_column(String(80), default="")
    repository_location: Mapped[str] = mapped_column(String(600), default="")
    status: Mapped[str] = mapped_column(String(30), default="generated")
    kind: Mapped[str] = mapped_column(String(30), default="main")
    module_map: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    files: Mapped[list[TerraformFile]] = relationship(
        back_populates="terraform_project", cascade="all, delete-orphan"
    )


class TerraformFile(Base):
    __tablename__ = "terraform_files"
    __table_args__ = (
        UniqueConstraint("terraform_project_id", "file_path", name="uq_tf_file_path"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    terraform_project_id: Mapped[str] = mapped_column(
        ForeignKey("terraform_projects.id", ondelete="CASCADE"), index=True
    )
    file_path: Mapped[str] = mapped_column(String(600))
    file_content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(80), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    terraform_project: Mapped[TerraformProject] = relationship(back_populates="files")


class ValidationRun(Base):
    __tablename__ = "validation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    terraform_project_id: Mapped[str] = mapped_column(
        ForeignKey("terraform_projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued")
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    log_location: Mapped[str | None] = mapped_column(String(600), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TerraformPlan(Base):
    __tablename__ = "terraform_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    terraform_project_id: Mapped[str] = mapped_column(
        ForeignKey("terraform_projects.id", ondelete="CASCADE"), index=True
    )
    environment: Mapped[str] = mapped_column(String(30), default="dev")
    subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_artifact_location: Mapped[str] = mapped_column(String(600), default="")
    plan_json_location: Mapped[str] = mapped_column(String(600), default="")
    plan_hash: Mapped[str] = mapped_column(String(80), default="")
    source_digest: Mapped[str] = mapped_column(String(80), default="")
    plan_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    destructive: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="generating")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    approval_type: Mapped[str] = mapped_column(String(30))
    artifact_hash: Mapped[str] = mapped_column(String(80))
    architecture_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("architecture_versions.id", ondelete="SET NULL"), nullable=True
    )
    terraform_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("terraform_plans.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(30), default="active")
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Deployment(Base):
    __tablename__ = "deployments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    terraform_plan_id: Mapped[str] = mapped_column(
        ForeignKey("terraform_plans.id", ondelete="RESTRICT"), index=True
    )
    approval_id: Mapped[str] = mapped_column(
        ForeignKey("approvals.id", ondelete="RESTRICT")
    )
    subscription_id: Mapped[str] = mapped_column(String(64), default="")
    environment: Mapped[str] = mapped_column(String(30), default="dev")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    execution_logs: Mapped[str] = mapped_column(Text, default="")
    log_location: Mapped[str | None] = mapped_column(String(600), nullable=True)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_project_ts", "project_id", "timestamp"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="running")
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProjectLock(Base):
    __tablename__ = "project_locks"
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    reason: Mapped[str] = mapped_column(String(200))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMConfiguration(Base):
    """Single-row runtime LLM provider configuration (secret stored encrypted)."""

    __tablename__ = "llm_configurations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String(40), default="deepseek")
    model: Mapped[str] = mapped_column(String(120), default="deepseek-chat")
    base_url: Mapped[str] = mapped_column(String(500), default="https://api.deepseek.com/v1")
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
