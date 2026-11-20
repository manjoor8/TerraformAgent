"""Provider selection. `auto` prefers Azure OpenAI, then OpenAI-compatible, else offline."""
from __future__ import annotations

from app.config import get_settings
from app.llm.base import LLMProvider
from app.llm.providers import AzureOpenAIProvider, OpenAICompatibleProvider

_runtime_provider: LLMProvider | None = None
_runtime_source: str = "environment"


class OfflineProvider(LLMProvider):
    name = "offline"
    model = "deterministic-heuristics"

    @property
    def available(self) -> bool:
        return False

    def complete(self, system: str, user: str, *, max_tokens: int = 4096, temperature: float = 0.2):
        from app.llm.base import LLMNotConfigured

        raise LLMNotConfigured(
            "No LLM provider is configured. Set LLM_PROVIDER and credentials, or use the "
            "deterministic offline reasoner (used automatically)."
        )


def set_runtime_provider(provider: LLMProvider | None, source: str = "runtime") -> None:
    """Override the environment-derived provider until the process restarts."""
    global _runtime_provider, _runtime_source
    _runtime_provider = provider
    _runtime_source = source if provider is not None else "environment"


def clear_runtime_provider() -> None:
    set_runtime_provider(None)


def runtime_provider() -> LLMProvider | None:
    return _runtime_provider


def get_llm() -> LLMProvider:
    if _runtime_provider is not None:
        return _runtime_provider
    settings = get_settings()
    provider = settings.llm_provider.lower().strip()

    azure_ready = bool(settings.azure_openai_endpoint and settings.azure_openai_api_key)
    openai_ready = bool(settings.llm_api_key)

    if provider in ("azure", "azure_openai"):
        return AzureOpenAIProvider(
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            deployment=settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
            timeout=settings.llm_timeout_seconds,
        )
    if provider == "deepseek":
        return OpenAICompatibleProvider(
            base_url=settings.llm_base_url or "https://api.deepseek.com/v1",
            api_key=settings.llm_api_key,
            model=settings.llm_model or "deepseek-chat",
            name="deepseek",
            timeout=settings.llm_timeout_seconds,
        )
    if provider in ("openai", "openai_compatible", "compatible"):
        return OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            name="openai",
            timeout=settings.llm_timeout_seconds,
        )
    if provider == "offline":
        return OfflineProvider()

    # auto
    if azure_ready:
        return AzureOpenAIProvider(
            endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            deployment=settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
            timeout=settings.llm_timeout_seconds,
        )
    if openai_ready:
        return OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            name="openai-compatible",
            timeout=settings.llm_timeout_seconds,
        )
    return OfflineProvider()


def llm_status() -> dict[str, object]:
    settings = get_settings()
    provider = get_llm()
    return {
        "configured_provider": settings.llm_provider,
        "active_provider": provider.name,
        "model": provider.model,
        "available": provider.available,
        "mode": "llm" if provider.available else "deterministic-offline",
        "source": _runtime_source,
        "hint": (
            ""
            if provider.available
            else "Set AZURE_OPENAI_* or LLM_API_KEY, or configure DeepSeek on the AI provider "
            "screen. The deterministic reasoner produces real, schema-valid architecture output."
        ),
    }
