"""Test configuration: isolated SQLite database, artifact store and Terraform binary."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
TEST_DATA = Path(tempfile.mkdtemp(prefix="terraformagent-tests-"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(TEST_DATA / 'test.db').as_posix()}")
os.environ.setdefault("ARTIFACT_ROOT", str(TEST_DATA / "artifacts"))
os.environ.setdefault("WORKSPACE_ROOT", str(TEST_DATA / "workspaces"))
os.environ.setdefault("TERRAFORM_PLUGIN_CACHE_DIR", str(BACKEND / "data" / "plugin-cache"))
os.environ.setdefault("LIVE_PRICING", "false")
os.environ.setdefault("LLM_PROVIDER", "offline")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("BOOTSTRAP_EMAIL", "test-admin@example.test")
os.environ.setdefault("BOOTSTRAP_PASSWORD", "TestPassword123!")

TERRAFORM = ROOT / "tools" / "terraform-amd64" / "terraform.exe"
if TERRAFORM.exists():
    os.environ.setdefault("TERRAFORM_BINARY", str(TERRAFORM))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def auth(client: TestClient):
    payload = {
        "name": "Test Architect",
        "email": "architect@example.test",
        "password": "TestPassword123!",
        "organization_name": "Test Platform",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers).json()
    headers["X-Organization-Id"] = me["organizations"][0]["id"]
    return headers


@pytest.fixture(scope="session")
def admin_auth(client: TestClient):
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "test-admin@example.test",
            "password": "TestPassword123!",
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["is_platform_admin"] is True
    headers["X-Organization-Id"] = me["organizations"][0]["id"]
    return headers


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires the Terraform CLI")
