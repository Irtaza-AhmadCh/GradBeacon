"""OutreachSender — deterministic, approval-gated, single attempt. The model never has access to this.

Sending itself is deferred by the product owner: with the default UnconfiguredEmailProvider, `send` refuses
(503) WITHOUT consuming the approval, and the student uses the copy/mailto handoff instead.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

from app.core import clock
from app.core.errors import ConfigError, InvalidState
from app.models.common import ErrorCategory
from app.models.outreach import Draft, DraftStatus, SendResult
from app.orchestrator.job_store import Job
from app.outreach.approval import ApprovalService
from app.providers.email.base import EmailProvider, OutboundEmail
from app.validation.evidence_validator import EMAIL_RE

log = logging.getLogger("gradbeacon.outreach")


def mailto_link(draft: Draft) -> str:
    return (f"mailto:{draft.recipient_email}?subject={quote(draft.subject)}&body={quote(draft.body)}")


def gmail_compose_link(draft: Draft) -> str:
    """Gmail web compose window pre-filled with the approved draft (the student reviews and sends it themselves)."""
    return ("https://mail.google.com/mail/?view=cm&fs=1"
            f"&to={quote(draft.recipient_email, safe='@')}&su={quote(draft.subject, safe='')}"
            f"&body={quote(draft.body, safe='')}")


class OutreachSender:
    def __init__(self, provider: EmailProvider, approvals: ApprovalService, send_mode: str,
                 sandbox_to: str | None):
        self.provider = provider
        self.approvals = approvals
        self.mode = send_mode
        self.sandbox_to = sandbox_to

    @property
    def ready(self) -> bool:
        return self.mode != "disabled" and getattr(self.provider, "configured", False)

    async def send(self, job: Job, draft: Draft, token: str) -> SendResult:
        async with job.lock:
            if draft.status != DraftStatus.approved:
                raise InvalidState("Draft is not approved; review and approve it first", code="not_approved")
            if not self.ready:
                raise ConfigError("Automatic email sending is not set up yet. Use the copy or mailto option to send "
                                  "this approved draft from your own email account.", code="email_not_configured")
            appr = self.approvals.verify_and_consume(draft, token)
            prof = job.professors.get(draft.professor_id)
            official = prof.official_email.value if (prof and prof.official_email) else None
            if official is None or official.lower() != draft.recipient_email.lower() or not EMAIL_RE.match(official):
                draft.status = DraftStatus.failed
                raise InvalidState("Recipient no longer matches the verified official email", code="recipient_mismatch")
            draft.status = DraftStatus.sending
            to = self.sandbox_to if self.mode == "sandbox" else draft.recipient_email
            subject = f"[SANDBOX → {draft.recipient_email}] {draft.subject}" if self.mode == "sandbox" else draft.subject
            body = (f"Sandbox delivery. Intended recipient: {draft.recipient_email}\n\n{draft.body}"
                    if self.mode == "sandbox" else draft.body)
            job.events.append("email_send_started", "outreach", "Sending approved email (single attempt)",
                              payload={"draft_id": draft.draft_id, "recipient_domain": to.split("@")[-1]})
            email = OutboundEmail(to=to, subject=subject, text=body, reply_to=draft.student_email,
                                  cc=[draft.student_email] if (draft.student_email and self.mode == "live") else [],
                                  idempotency_key=appr.approval_id, draft_id=draft.draft_id)
            try:
                async with asyncio.timeout(20):
                    outcome = await self.provider.send(email)
            except TimeoutError:
                outcome = None
            now = clock.utc_now()
            if outcome is not None and outcome.ok:
                draft.status = DraftStatus.sent
                draft.send_result = SendResult(outcome="sent", provider_message_id=outcome.provider_message_id,
                                               mode="sandbox" if self.mode == "sandbox" else "live", delivered_to=to,
                                               at=now, message="Sent once. No automatic retries.")
                job.events.append("email_sent", "outreach", "Approved email sent", payload={"draft_id": draft.draft_id})
            else:
                draft.status = DraftStatus.failed
                cat = (outcome.error_category if outcome else ErrorCategory.provider_timeout) or \
                    ErrorCategory.provider_unavailable
                draft.send_result = SendResult(outcome="failed", error_category=cat,
                                               mode="sandbox" if self.mode == "sandbox" else "live", delivered_to=to,
                                               at=now, message="Send failed. Approve again to retry; nothing is retried "
                                                               "automatically.")
                job.events.append("email_failed", "outreach", f"Email send failed ({cat.value})",
                                  payload={"draft_id": draft.draft_id}, error_category=cat)
            draft.updated_at = now
            return draft.send_result
