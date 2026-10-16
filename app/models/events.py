from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.common import ErrorCategory, Strict

TRACE_TYPES = {
    "job_started", "agent_turn", "agent_note", "search_started", "search_completed", "page_fetched",
    "page_failed", "facts_extracted", "extraction_failed", "tool_rejected", "gap_selected",
    "prompt_injection_flagged", "evidence_retrieved",
}
RESULT_TYPES = {"program", "program_updated", "program_excluded", "professor", "professor_updated"}
STATUS_TYPES = {"warning", "fallback_used", "done", "draft_created", "draft_approved", "email_send_started",
                "email_sent", "email_failed"}


class WorkflowEvent(Strict):
    seq: int
    ts: datetime
    job_id: str
    mode: str
    type: str
    stage: str
    summary: str
    tool: str | None = None
    counts: dict[str, int] | None = None
    duration_ms: int | None = None
    error_category: ErrorCategory | None = None
    payload: dict[str, Any] | None = None
