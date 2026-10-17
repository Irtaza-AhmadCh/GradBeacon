"""Outreach: draft -> student edits -> explicit approval -> (send | copy/mailto). Nothing sends without approval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_ctx
from app.api.rate_limit import limit
from app.core import clock
from app.core.errors import ConfigError, InvalidState, NotFound
from app.core.ids import new_id
from app.models.api import ApproveRequest, DraftEdit, DraftRequest, SendRequest
from app.models.outreach import Draft, DraftStatus
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import JobKind
from app.outreach.approval import ApprovalService, content_hash
from app.outreach.drafter import REMINDERS, OutreachDrafter
from app.outreach.sender import OutreachSender, gmail_compose_link, mailto_link

router = APIRouter(prefix="/api/outreach", tags=["outreach"])


def _approvals(ctx: AppContext) -> ApprovalService:
    s = ctx.settings
    secret = s.approval_token_secret.get_secret_value() if s.approval_token_secret else None
    return ApprovalService(secret, s.approval_token_ttl_seconds)


def _sender(ctx: AppContext) -> OutreachSender:
    return OutreachSender(ctx.providers.email, _approvals(ctx), ctx.settings.email_send_mode,
                          ctx.settings.email_sandbox_to)


def _view(ctx: AppContext, d: Draft) -> dict:
    sender = _sender(ctx)
    out = d.model_dump(mode="json", exclude={"approval"})
    out["approval"] = ({"expires_at": d.approval.expires_at.isoformat(), "consumed": d.approval.consumed}
                       if d.approval else None)
    out["email_send_mode"] = ctx.settings.email_send_mode
    out["automatic_sending_available"] = sender.ready
    if d.status == DraftStatus.approved or d.status == DraftStatus.failed:
        out["manual_send"] = {"mailto": mailto_link(d), "gmail": gmail_compose_link(d), "copy_text": f"To: {d.recipient_email}\nSubject: {d.subject}\n\n{d.body}",
                              "note": "Send from your own email account using this exact approved text."}
    return out


@router.post("/draft", status_code=201, dependencies=[Depends(limit("draft"))])
async def create_draft(body: DraftRequest, ctx: AppContext = Depends(get_ctx)):
    """Create an editable draft grounded in verified professor evidence + student-asserted facts. Does NOT send."""
    job = ctx.store.get(body.professor_job_id)
    if job.kind != JobKind.professors:
        raise InvalidState("professor_job_id must be a professor research job")
    prof = job.professors.get(body.professor_id)
    if prof is None or body.professor_id not in job.professor_order:
        raise NotFound("Professor not found in this job's results")
    if prof.official_email is None:
        raise InvalidState("No official email is published for this professor; contact the department instead",
                           code="no_official_email",
                           details={"contact_fallback_url": prof.contact_fallback_url,
                                    "official_profile_url": prof.official_profile_url})
    program_name = None
    if body.program_id:
        parent = ctx.store.get(job.parent_job_id) if job.parent_job_id else None
        if parent and body.program_id in parent.programs:
            program_name = f"{parent.programs[body.program_id].program_name} at {parent.programs[body.program_id].institution_name}"
    drafter = OutreachDrafter(ctx.providers.llm if ctx.live else None, ctx.settings.llm_effort_writing)
    subject, text, by = await drafter.draft(prof, program_name, body.student.model_dump())
    now = clock.utc_now()
    d = Draft(draft_id=new_id("drf"), job_id=job.job_id, professor_id=prof.professor_id, program_id=body.program_id,
              recipient_email=prof.official_email.value, recipient_source=prof.official_email.evidence[0],
              professor_profile_url=prof.official_profile_url, subject=subject, body=text, version=1,
              content_hash=content_hash(subject, text), generated_by=by, reminders=REMINDERS,
              status=DraftStatus.awaiting_review, student_name=body.student.name, student_email=body.student.email,
              created_at=now, updated_at=now)
    job.drafts[d.draft_id] = d
    ctx.store.drafts_index[d.draft_id] = job.job_id
    job.events.append("draft_created", "outreach", f"Draft prepared for review ({by}); nothing has been sent",
                      payload={"draft_id": d.draft_id})
    return _view(ctx, d)


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: str, ctx: AppContext = Depends(get_ctx)):
    _, d = ctx.store.find_draft(draft_id)
    return _view(ctx, d)


@router.put("/drafts/{draft_id}")
async def edit_draft(draft_id: str, body: DraftEdit, ctx: AppContext = Depends(get_ctx)):
    """Student edits. Bumps version, recomputes hash, invalidates any previous approval. Recipient is not editable."""
    job, d = ctx.store.find_draft(draft_id)
    async with job.lock:
        if d.status in (DraftStatus.sending, DraftStatus.sent):
            raise InvalidState("This draft was already sent or is sending")
        if d.status == DraftStatus.cancelled:
            raise InvalidState("This draft was cancelled")
        if body.expected_version != d.version:
            raise InvalidState("Draft was changed elsewhere; reload it", code="version_conflict",
                               details={"current_version": d.version})
        d.subject, d.body = body.subject.strip(), body.body.strip()
        d.version += 1
        d.content_hash = content_hash(d.subject, d.body)
        d.approval = None
        d.status = DraftStatus.awaiting_review
        d.updated_at = clock.utc_now()
    return _view(ctx, d)


@router.post("/drafts/{draft_id}/approve", dependencies=[Depends(limit("approve"))])
async def approve_draft(draft_id: str, body: ApproveRequest, ctx: AppContext = Depends(get_ctx)):
    """Explicit final approval of the EXACT current text (hash-bound, single use, expires)."""
    job, d = ctx.store.find_draft(draft_id)
    if ctx.settings.email_send_mode == "disabled":
        raise ConfigError("Email sending is disabled on this deployment", code="email_disabled")
    async with job.lock:
        if d.status not in (DraftStatus.awaiting_review, DraftStatus.failed, DraftStatus.approved):
            raise InvalidState(f"Draft cannot be approved in status '{d.status.value}'")
        if body.content_hash != d.content_hash:
            raise InvalidState("You are approving a different version than the stored draft; reload and review",
                               code="draft_changed")
        token, appr = _approvals(ctx).issue(d)
        d.approval = appr
        d.status = DraftStatus.approved
        d.updated_at = clock.utc_now()
    job.events.append("draft_approved", "human", "Student approved the final draft text",
                      payload={"draft_id": d.draft_id})
    sender = _sender(ctx)
    view = _view(ctx, d)
    return {"approval_token": token, "expires_at": appr.expires_at.isoformat(), "recipient_email": d.recipient_email,
            "delivery": ctx.settings.email_send_mode if sender.ready else "manual",
            "sandbox_recipient": ctx.settings.email_sandbox_to if ctx.settings.email_send_mode == "sandbox" else None,
            "automatic_sending_available": sender.ready, "manual_send": view.get("manual_send"),
            "note": None if sender.ready else "Automatic sending is not set up yet; use manual_send (mailto/copy)."}


@router.post("/send", dependencies=[Depends(limit("send"))])
async def send_email(body: SendRequest, request: Request, ctx: AppContext = Depends(get_ctx)):
    """Send exactly the approved draft once to the official address. Never retries without a new approval."""
    job, d = ctx.store.find_draft(body.draft_id)
    sender = _sender(ctx)
    if sender.ready:
        request.app.state.limits.send_global.check("global")
    result = await sender.send(job, d, body.approval_token)
    return {"draft_id": d.draft_id, "status": d.status.value, "send_result": result.model_dump(mode="json")}


@router.post("/drafts/{draft_id}/cancel")
async def cancel_draft(draft_id: str, ctx: AppContext = Depends(get_ctx)):
    job, d = ctx.store.find_draft(draft_id)
    async with job.lock:
        if d.status in (DraftStatus.sending, DraftStatus.sent):
            raise InvalidState("This draft was already sent or is sending")
        d.status = DraftStatus.cancelled
        d.approval = None
        d.updated_at = clock.utc_now()
    return _view(ctx, d)
