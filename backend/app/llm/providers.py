"""OpenAI-compatible and Azure OpenAI providers (httpx, no vendor SDK)."""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.llm.base import LLMError, LLMProvider, LLMResult


class OpenAICompatibleProvider(LLMProvider):
    """Works with OpenAI, DeepSeek, vLLM, Ollama /v1 and similar endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        name: str = "openai-compatible",
        timeout: float = 90.0,
        provider_label: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.name = provider_label or name
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> LLMResult:
        if not self.available:
            raise LLMError(f"{self.name} provider is not configured")
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            raise LLMError(f"{self.name} HTTP {exc.response.status_code}: {body}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name} request failed: {exc}") from exc

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected {self.name} response shape") from exc
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw=data,
        )


class AzureOpenAIProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str,
        timeout: float = 90.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_version = api_version
        self.deployment = deployment
        super().__init__(
            base_url=f"{self.endpoint}/openai/deployments/{deployment}",
            api_key=api_key,
            model=deployment,
            name="azure_openai",
            timeout=timeout,
        )

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> LLMResult:
        if not self.available:
            raise LLMError("Azure OpenAI provider is not configured")
        started = time.perf_counter()
        url = (
            f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions"
            f"?api-version={self.api_version}"
        )
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"api-key": self.api_key, "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"azure_openai HTTP {exc.response.status_code}: {exc.response.text[:500]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"azure_openai request failed: {exc}") from exc
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected azure_openai response shape") from exc
        return LLMResult(
            text=text,
            provider="azure_openai",
            model=self.deployment,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw=data,
        )
