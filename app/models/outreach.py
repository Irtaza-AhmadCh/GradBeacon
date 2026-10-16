from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from app.models.common import ErrorCategory, Strict
from app.models.evidence import Evidence


class DraftStatus(StrEnum):
    awaiting_review = "awaiting_review"
    approved = "approved"
    sending = "sending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"


class Approval(Strict):
    approval_id: str
    content_hash: str
    recipient_hash: str
    issued_at: datetime
    expires_at: datetime
    consumed: bool = False


class SendResult(Strict):
    outcome: Literal["sent", "failed"]
    provider_message_id: str | None = None
    error_category: ErrorCategory | None = None
    mode: Literal["live", "sandbox"]
    delivered_to: str
    at: datetime
    message: str = ""


class Draft(Strict):
    draft_id: str
    job_id: str
    professor_id: str
    program_id: str | None = None
    recipient_email: str
    recipient_source: Evidence
    professor_profile_url: str
    subject: str
    body: str
    version: int
    content_hash: str
    generated_by: Literal["llm", "template"]
    reminders: list[str] = Field(default_factory=list)
    status: DraftStatus
    student_name: str | None = None
    student_email: str | None = None
    approval: Approval | None = None
    send_result: SendResult | None = None
    created_at: datetime
    updated_at: datetime
