"""Application configuration loaded from environment / .env."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", BASE_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "TerraformAgent"
    environment: str = "development"
    debug: bool = False

    secret_key: str = "dev-only-insecure-secret-change-me"
    access_token_expire_minutes: int = 60 * 12

    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'terraformagent.db').as_posix()}"
    artifact_root: str = str(BASE_DIR / "data" / "artifacts")
    workspace_root: str = str(BASE_DIR / "data" / "workspaces")

    max_upload_bytes: int = 25 * 1024 * 1024
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- LLM -------------------------------------------------------------
    llm_provider: str = "auto"  # auto | openai | azure_openai | deepseek | offline
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 3

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-10-21"

    # ---- Terraform -------------------------------------------------------
    terraform_binary: str = Field(default="terraform")
    tflint_binary: str = "tflint"
    checkov_binary: str = "checkov"
    terraform_timeout_seconds: int = 600
    terraform_apply_timeout_seconds: int = 3600
    terraform_plugin_cache_dir: str = str(BASE_DIR / "data" / "plugin-cache")
    allowed_module_sources: str = "registry.terraform.io/hashicorp"
    live_pricing: bool = True

    # ---- Bootstrap admin (local dev convenience) -------------------------
    bootstrap_email: str = "admin@terraformagent.local"
    bootstrap_password: str = "ChangeMe123!"
    bootstrap_org: str = "TerraformAgent"
    # Comma-separated extra platform administrators (bootstrap email is always one).
    platform_admin_emails: str = ""

    # ---- Azure (never persisted to DB) ----------------------------------
    azure_tenant_id: str | None = None
    azure_subscription_id: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def azure_env(self) -> dict[str, str]:
        """Allow-listed Azure credential environment passed to Terraform."""
        keys = (
            "ARM_CLIENT_ID",
            "ARM_CLIENT_SECRET",
            "ARM_TENANT_ID",
            "ARM_SUBSCRIPTION_ID",
            "ARM_USE_OIDC",
            "ARM_USE_MSI",
            "ARM_USE_CLI",
            "ARM_OIDC_TOKEN",
            "ARM_OIDC_TOKEN_FILE_PATH",
            "ARM_ACCESS_KEY",
            "ARM_SAS_TOKEN",
            "ARM_STORAGE_USE_AZUREAD",
        )
        return {k: v for k in keys if (v := os.environ.get(k))}


@lru_cache
def get_settings() -> Settings:
    return Settings()
