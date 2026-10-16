"""Public API errors and internal provider errors."""

from __future__ import annotations

from typing import Any

from app.models.common import ErrorCategory


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    public_message = "Unexpected error"

    def __init__(self, message: str | None = None, *, code: str | None = None, details: Any = None,
                 headers: dict[str, str] | None = None):
        super().__init__(message or self.public_message)
        self.message = message or self.public_message
        if code:
            self.code = code
        self.details = details
        self.headers = headers or {}


class ValidationFailed(AppError):
    status_code, code, public_message = 400, "validation_error", "Invalid request"


class NotFound(AppError):
    status_code, code, public_message = 404, "not_found", "Not found"


class Expired(AppError):
    status_code, code, public_message = 410, "expired", "This job has expired or the server restarted"


class InvalidState(AppError):
    status_code, code, public_message = 409, "invalid_state", "Action not allowed in the current state"


class TooLarge(AppError):
    status_code, code, public_message = 413, "too_large", "Request body too large"


class RateLimited(AppError):
    status_code, code, public_message = 429, "rate_limited", "Too many requests"


class TooBusy(AppError):
    status_code, code, public_message = 429, "busy", "Server is busy with other research jobs; try again shortly"


class ConfigError(AppError):
    status_code, code, public_message = 503, "config_error", "Service is not configured for this action"


class ProviderUnavailable(AppError):
    status_code, code, public_message = 503, "provider_unavailable", "An upstream provider is unavailable"


class ProviderError(Exception):
    """Internal error from a provider/tool. Never leaves the service raw."""

    def __init__(self, category: ErrorCategory, detail: str = "", retryable: bool = False):
        super().__init__(f"{category}: {detail}")
        self.category = category
        self.detail = detail
        self.retryable = retryable
