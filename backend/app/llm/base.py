"""LLM provider abstraction. Providers are interchangeable and schema-validated."""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMNotConfigured(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: int
    raw: dict[str, Any] | None = None


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> LLMResult: ...

    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[SchemaT],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        repair_attempts: int = 2,
    ) -> tuple[SchemaT, LLMResult]:
        schema_json = json.dumps(schema.model_json_schema(), indent=None)
        instruction = (
            f"{system}\n\n"
            "Respond with a single JSON object only. No markdown fences, no prose. "
            f"It MUST validate against this JSON Schema:\n{schema_json}"
        )
        prompt = user
        last_error: str | None = None
        for attempt in range(repair_attempts + 1):
            result = self.complete(
                instruction, prompt, max_tokens=max_tokens, temperature=temperature
            )
            try:
                data = _extract_json(result.text)
                return schema.model_validate(data), result
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:2000]
                prompt = (
                    f"{user}\n\nYour previous response was invalid: {last_error}\n"
                    "Return corrected JSON that strictly matches the schema."
                )
        raise LLMError(f"Model did not return valid structured output: {last_error}")


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model response")
    return json.loads(cleaned[start : end + 1])


def timed(fn, *args, **kwargs):
    started = time.perf_counter()
    value = fn(*args, **kwargs)
    return value, int((time.perf_counter() - started) * 1000)
