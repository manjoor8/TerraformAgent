"""Typed application errors mapped to HTTP responses."""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, detail: str, *, code: str | None = None, extra: dict[str, Any] | None = None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        self.extra = extra or {}


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class InvalidTransition(Conflict):
    code = "invalid_transition"


class ApprovalError(Conflict):
    code = "approval_invalidated"


class PolicyError(AppError):
    status_code = 422
    code = "policy_violation"


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"
