"""Email provider slot. SENDING IS DEFERRED by the product owner (2026-10-17).

To implement sending later, add a class implementing `EmailProvider.send` (e.g. student SMTP account,
Gmail OAuth, or a transactional provider) and return it from `providers/factory.py::build_email_provider`.
The approval gate in `app/outreach/approval.py` + `sender.py` already guarantees: one send per explicit
approval, exact approved text, recipient locked to the verified official address, no automatic retries.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from app.models.common import ErrorCategory


class OutboundEmail(BaseModel):
    to: str
    subject: str
    text: str
    reply_to: str | None = None
    cc: list[str] = Field(default_factory=list)
    idempotency_key: str
    draft_id: str


class EmailSendOutcome(BaseModel):
    ok: bool
    provider_message_id: str | None = None
    error_category: ErrorCategory | None = None


class EmailProvider(Protocol):
    name: str
    configured: bool

    async def send(self, email: OutboundEmail) -> EmailSendOutcome: ...


class UnconfiguredEmailProvider:
    """Placeholder until the owner implements a sender. Never sends anything."""

    name = "none"
    configured = False

    async def send(self, email: OutboundEmail) -> EmailSendOutcome:  # pragma: no cover - guarded upstream
        return EmailSendOutcome(ok=False, error_category=ErrorCategory.config_error)
