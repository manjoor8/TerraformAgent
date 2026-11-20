"""Runtime LLM provider configuration (DeepSeek / OpenAI-compatible / Azure / offline)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import audit
from app.core.errors import AppError, Forbidden
from app.core.secret_box import decrypt_secret, encrypt_secret, last_four
from app.llm.base import LLMError
from app.llm.factory import (
    OfflineProvider,
    clear_runtime_provider,
    llm_status,
    set_runtime_provider,
)
from app.llm.providers import AzureOpenAIProvider, OpenAICompatibleProvider
from app.models import LLMConfiguration, User

logger = logging.getLogger("terraformagent.llm")

PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "key_hint": "DeepSeek API key (sk-…)",
    },
    "openai_compatible": {
        "label": "OpenAI-compatible",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "key_hint": "Provider API key",
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "model": "gpt-4o",
        "base_url": "https://<resource>.openai.azure.com",
        "key_hint": "Azure OpenAI API key",
    },
    "offline": {
        "label": "Deterministic offline reasoner",
        "model": "deterministic-heuristics",
        "base_url": "",
        "key_hint": "No key required",
    },
}

_PROVIDER_ALIASES = {
    "deepseek": "deepseek",
    "openai": "openai_compatible",
    "openai_compatible": "openai_compatible",
    "compatible": "openai_compatible",
    "azure_openai": "azure_openai",
    "azure": "azure_openai",
    "offline": "offline",
}


def normalise_provider(value: str) -> str:
    key = value.strip().lower()
    if key not in _PROVIDER_ALIASES:
        raise AppError(f"Unknown LLM provider '{value}'")
    return _PROVIDER_ALIASES[key]


def is_platform_admin(user: User) -> bool:
    settings = get_settings()
    allowed = {settings.bootstrap_email.strip().lower()}
    allowed.update(
        email.strip().lower()
        for email in settings.platform_admin_emails.split(",")
        if email.strip()
    )
    return user.email.strip().lower() in allowed


def require_platform_admin(user: User) -> None:
    if not is_platform_admin(user):
        raise Forbidden("Platform administrator role required to configure the model provider")


def _validate_url(url: str) -> str:
    candidate = url.strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in ("https", "http"):
        raise AppError("Base URL must start with https:// (http:// is allowed only for local hosts)")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise AppError("Plain HTTP is only allowed for localhost model servers")
    if not parsed.hostname:
        raise AppError("Base URL must include a host")
    return candidate


def build_provider(
    provider: str,
    *,
    model: str,
    base_url: str,
    api_key: str,
    timeout: float | None = None,
):
    settings = get_settings()
    provider = normalise_provider(provider)
    timeout = timeout or settings.llm_timeout_seconds
    if provider == "offline":
        return OfflineProvider()
    if not model.strip():
        raise AppError("A model name is required")
    if not api_key.strip():
        raise AppError("An API key is required for this provider")
    if provider == "azure_openai":
        return AzureOpenAIProvider(
            endpoint=_validate_url(base_url),
            api_key=api_key.strip(),
            deployment=model.strip(),
            api_version=settings.azure_openai_api_version,
            timeout=timeout,
        )
    return OpenAICompatibleProvider(
        base_url=_validate_url(base_url),
        api_key=api_key.strip(),
        model=model.strip(),
        name="deepseek" if provider == "deepseek" else "openai-compatible",
        timeout=timeout,
    )


def get_row(db: Session) -> LLMConfiguration | None:
    return db.scalar(select(LLMConfiguration).order_by(LLMConfiguration.id).limit(1))


def _stored_key(row: LLMConfiguration | None) -> str | None:
    if not row or not row.api_key_encrypted:
        return None
    try:
        return decrypt_secret(row.api_key_encrypted)
    except ValueError as exc:
        logger.warning("Stored LLM key cannot be decrypted: %s", exc)
        return None


def _effective_key(row: LLMConfiguration | None, provider: str) -> str:
    stored = _stored_key(row)
    if stored:
        return stored
    settings = get_settings()
    if provider == "azure_openai":
        return settings.azure_openai_api_key
    return settings.llm_api_key


def apply_row(row: LLMConfiguration | None) -> None:
    """Apply a stored row to the running process, or fall back to the environment."""
    if row is None or not row.enabled:
        clear_runtime_provider()
        return
    key = _effective_key(row, row.provider)
    try:
        provider = build_provider(
            row.provider,
            model=row.model,
            base_url=row.base_url,
            api_key=key,
        )
    except AppError as exc:
        logger.warning("Stored LLM configuration is unusable: %s", exc)
        clear_runtime_provider()
        return
    set_runtime_provider(provider, "runtime")


def load_runtime_configuration() -> None:
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        apply_row(get_row(db))
    except Exception as exc:  # noqa: BLE001 - startup must not fail on bad config
        logger.warning("Could not load runtime LLM configuration: %s", exc)
        clear_runtime_provider()
    finally:
        db.close()


def serialize_config(db: Session) -> dict[str, object]:
    settings = get_settings()
    row = get_row(db)
    effective = llm_status()
    provider = row.provider if row else "deepseek"
    has_key = bool(_stored_key(row)) or (
        bool(settings.azure_openai_api_key)
        if provider == "azure_openai"
        else bool(settings.llm_api_key)
    )
    last4 = row.api_key_last4 if row and row.api_key_last4 else None
    if not last4 and has_key and provider != "azure_openai":
        last4 = last_four(settings.llm_api_key)
    return {
        "configured": row is not None,
        "provider": provider,
        "model": row.model if row else PRESETS["deepseek"]["model"],
        "base_url": row.base_url if row else PRESETS["deepseek"]["base_url"],
        "has_api_key": has_key,
        "api_key_last4": last4,
        "enabled": bool(row.enabled) if row else False,
        "source": "runtime" if row and row.enabled else "environment",
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "effective": effective,
        "presets": PRESETS,
        "env_provider": settings.llm_provider,
    }


def save_configuration(
    db: Session,
    user: User,
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str | None,
    organization_id: str,
) -> dict[str, object]:
    require_platform_admin(user)
    provider = normalise_provider(provider)
    preset = PRESETS[provider]
    model = (model or preset["model"]).strip()
    base_url = (base_url or preset["base_url"]).strip()
    if provider != "offline":
        base_url = _validate_url(base_url)
    row = get_row(db)
    if row is None:
        row = LLMConfiguration(id=1)
        db.add(row)
    previous_provider = row.provider if row else None
    row.provider = provider
    row.model = model
    row.base_url = base_url
    row.enabled = True
    row.updated_by = user.id
    if api_key and api_key.strip():
        cleaned = api_key.strip()
        row.api_key_encrypted = encrypt_secret(cleaned)
        row.api_key_last4 = last_four(cleaned)
    elif previous_provider and previous_provider != provider and provider != "offline":
        # A different provider must not silently reuse the previous provider's key.
        row.api_key_encrypted = None
        row.api_key_last4 = None
    if provider == "offline":
        row.api_key_encrypted = None
        row.api_key_last4 = None
    if provider != "offline" and not _effective_key(row, provider):
        raise AppError("An API key is required to activate this provider")
    try:
        built = build_provider(
            provider,
            model=row.model,
            base_url=row.base_url,
            api_key=_effective_key(row, provider),
        )
    except AppError:
        raise
    set_runtime_provider(built, "runtime")
    audit.record(
        db,
        organization_id=organization_id,
        user_id=user.id,
        action="llm.configured",
        metadata={
            "provider": row.provider,
            "model": row.model,
            "base_url": row.base_url,
            "key_last4": row.api_key_last4,
        },
    )
    db.flush()
    return serialize_config(db)


def reset_configuration(db: Session, user: User, organization_id: str) -> dict[str, object]:
    """Remove the runtime row and fall back to the environment configuration."""
    require_platform_admin(user)
    row = get_row(db)
    if row is not None:
        db.delete(row)
    clear_runtime_provider()
    audit.record(
        db,
        organization_id=organization_id,
        user_id=user.id,
        action="llm.reset",
        metadata={},
    )
    db.flush()
    return serialize_config(db)


def test_configuration(
    db: Session,
    user: User,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, object]:
    require_platform_admin(user)
    row = get_row(db)
    chosen_provider = normalise_provider(provider or (row.provider if row else "deepseek"))
    preset = PRESETS[chosen_provider]
    chosen_model = (model or (row.model if row and provider is None else preset["model"]) or "").strip()
    chosen_url = (base_url or (row.base_url if row and provider is None else preset["base_url"]) or "").strip()
    if chosen_provider == "offline":
        return {
            "ok": True,
            "provider": "offline",
            "model": "deterministic-heuristics",
            "latency_ms": 0,
            "message": "Offline reasoner selected. No network call is required.",
        }
    key = api_key.strip() if api_key and api_key.strip() else _effective_key(row, chosen_provider)
    if not key:
        return {"ok": False, "provider": chosen_provider, "error": "No API key available"}
    try:
        client = build_provider(
            chosen_provider,
            model=chosen_model,
            base_url=chosen_url,
            api_key=key,
        )
        started = datetime.now(timezone.utc)
        result = client.complete(
            "You are a connectivity probe. Reply with a single short JSON object.",
            '{"ping":"ok"}',
            max_tokens=32,
            temperature=0,
        )
        latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        preview = re.sub(r"\s+", " ", result.text or "")[:200]
        return {
            "ok": True,
            "provider": result.provider,
            "model": result.model,
            "latency_ms": latency,
            "message": "Connection succeeded.",
            "preview": preview,
        }
    except (AppError, LLMError) as exc:
        return {
            "ok": False,
            "provider": chosen_provider,
            "model": chosen_model,
            "error": str(exc)[:600],
        }
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin only
        return {
            "ok": False,
            "provider": chosen_provider,
            "model": chosen_model,
            "error": f"Unexpected provider error: {exc}"[:600],
        }
