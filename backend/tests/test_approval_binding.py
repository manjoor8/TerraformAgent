from __future__ import annotations

import hashlib

import pytest

from app.core.errors import ApprovalError
from app.core.security import hash_bytes
from app.db import SessionLocal
from app.models import (
    Approval,
    ArchitectureVersion,
    AzureSubscription,
    Organization,
    Project,
    TerraformPlan,
    TerraformProject,
    User,
    new_id,
)
from app.services.artifacts import store
from app.services.execution import verify_deployment_authorization


def _fixture_records(db):
    user = User(name="Approver", email=f"approver-{new_id()}@example.test", password_hash="x")
    organization = Organization(name="Approval Org", slug=f"approval-{new_id()[:8]}")
    db.add_all([user, organization])
    db.flush()
    subscription = AzureSubscription(
        organization_id=organization.id,
        tenant_id="tenant",
        subscription_id="sub-123",
        subscription_name="Test",
        connection_status="verified",
    )
    db.add(subscription)
    db.flush()
    project = Project(
        organization_id=organization.id,
        name="Approval Project",
        environment="production",
        region="uaenorth",
        azure_subscription_id=subscription.id,
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    architecture = ArchitectureVersion(
        project_id=project.id,
        version=1,
        architecture_json={"region": "uaenorth", "resources": [], "networks": []},
        approval_status="approved",
        summary="test",
    )
    db.add(architecture)
    db.flush()
    db.add(
        Approval(
            project_id=project.id,
            approval_type="architecture",
            artifact_hash="sha256:arch",
            architecture_version_id=architecture.id,
            approved_by=user.id,
            status="active",
        )
    )
    tf_project = TerraformProject(
        project_id=project.id,
        architecture_version_id=architecture.id,
        version=1,
        source_digest="sha256:source",
        status="generated",
        module_map=[],
    )
    db.add(tf_project)
    db.flush()

    plan_bytes = b"binary-plan-fixture"
    stored = store.save(f"projects/{project.id}/plans/{new_id()}/plan.tfplan", plan_bytes)
    plan_hash = hashlib.sha256(plan_bytes).hexdigest()
    plan = TerraformPlan(
        terraform_project_id=tf_project.id,
        environment="production",
        subscription_id="sub-123",
        plan_artifact_location=stored["key"],
        plan_json_location="unused",
        plan_hash=f"sha256:{plan_hash}",
        source_digest=tf_project.source_digest,
        plan_summary={},
        status="approved",
    )
    db.add(plan)
    db.flush()
    db.add(
        Approval(
            project_id=project.id,
            approval_type="deployment",
            artifact_hash=plan.plan_hash,
            architecture_version_id=architecture.id,
            terraform_plan_id=plan.id,
            approved_by=user.id,
            status="active",
        )
    )
    db.commit()
    return project, architecture, tf_project, plan


def test_valid_approval_passes(client) -> None:
    db = SessionLocal()
    try:
        _project, _architecture, _tf, plan = _fixture_records(db)
        approval = verify_deployment_authorization(db, plan)
        assert approval.artifact_hash == plan.plan_hash
    finally:
        db.close()


def test_plan_hash_mismatch_is_rejected(client) -> None:
    db = SessionLocal()
    try:
        _project, _architecture, _tf, plan = _fixture_records(db)
        plan.plan_hash = "sha256:" + "0" * 64
        db.flush()
        with pytest.raises(ApprovalError):
            verify_deployment_authorization(db, plan)
    finally:
        db.close()


def test_source_change_after_plan_is_rejected(client) -> None:
    db = SessionLocal()
    try:
        _project, _architecture, tf_project, plan = _fixture_records(db)
        tf_project.source_digest = "sha256:changed"
        db.flush()
        with pytest.raises(ApprovalError):
            verify_deployment_authorization(db, plan)
    finally:
        db.close()


def test_invalidated_architecture_approval_blocks_deployment(client) -> None:
    db = SessionLocal()
    try:
        _project, architecture, _tf, plan = _fixture_records(db)
        architecture.approval_status = "superseded"
        db.flush()
        with pytest.raises(ApprovalError):
            verify_deployment_authorization(db, plan)
    finally:
        db.close()


def test_subscription_change_blocks_deployment(client) -> None:
    db = SessionLocal()
    try:
        project, _architecture, _tf, plan = _fixture_records(db)
        other = AzureSubscription(
            organization_id=project.organization_id,
            tenant_id="tenant",
            subscription_id="sub-OTHER",
            subscription_name="Other",
        )
        db.add(other)
        db.flush()
        project.azure_subscription_id = other.id
        db.flush()
        with pytest.raises(ApprovalError):
            verify_deployment_authorization(db, plan)
    finally:
        db.close()
