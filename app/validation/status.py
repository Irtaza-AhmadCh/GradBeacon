"""Field-level and summary verification-status rules (agent spec §8.2). Avoids a single green badge."""

from __future__ import annotations

from app.core import clock
from app.models.common import FIELD_LABELS, SUMMARY_LABELS, FieldStatus, SummaryStatus
from app.models.evidence import Claim
from app.models.program import ProgramOpportunity

CORE_FIELDS = ("tuition", "funding", "deadline", "documents", "requirements")


def _list_status(claims: list[Claim], field: str, p: ProgramOpportunity) -> FieldStatus:
    if p.fallback_record:
        return FieldStatus.curated_fallback if claims else FieldStatus.not_found
    if any(c.status == FieldStatus.conflicting for c in claims):
        return FieldStatus.conflicting
    if any(c.status == FieldStatus.verified for c in claims):
        return FieldStatus.verified
    if field in p.failed_fields and field not in p.attempted_fields:
        return FieldStatus.unavailable
    if field in p.attempted_fields:
        return FieldStatus.not_found
    if field in p.failed_fields:
        return FieldStatus.unavailable
    return FieldStatus.unknown


def recompute_status(p: ProgramOpportunity) -> ProgramOpportunity:
    p.tuition_status = _list_status([t.claim for t in p.tuition], "tuition", p)
    p.funding_status = _list_status([f.claim for f in p.funding], "funding", p)
    p.deadline_status = _list_status([d.claim for d in p.deadlines], "deadline", p)
    p.documents_status = _list_status(p.required_documents, "documents", p)
    p.requirements_status = _list_status([r.claim for r in p.requirements], "requirements", p)
    statuses = {
        "tuition": p.tuition_status, "funding": p.funding_status, "deadline": p.deadline_status,
        "documents": p.documents_status, "requirements": p.requirements_status,
    }
    if p.fallback_record:
        p.verification_status = SummaryStatus.fallback
        date = p.last_verified_at.date().isoformat() if p.last_verified_at else "unknown date"
        p.verification_label = f"Curated fallback (last verified {date}) — not freshly verified"
    else:
        verified = sum(1 for s in statuses.values() if s == FieldStatus.verified)
        conflicts = [k for k, s in statuses.items() if s == FieldStatus.conflicting]
        if any(k in ("deadline", "requirements") for k in conflicts):
            p.verification_status = SummaryStatus.needs_verification
        elif p.has_verified_program_page and verified >= 4 and not conflicts:
            p.verification_status = SummaryStatus.verified
        elif verified >= 1:
            p.verification_status = SummaryStatus.partially_verified
        else:
            p.verification_status = SummaryStatus.needs_verification
        p.verification_label = SUMMARY_LABELS[p.verification_status.value]
    p.field_labels = {k: _label(s, p) for k, s in statuses.items()}
    today = clock.utc_now().date()
    for d in p.deadlines:
        if d.date and d.date < today:
            msg = (f"A sourced deadline ({d.date.isoformat()}{', ' + d.term if d.term else ''}) has already passed; "
                   "the official page may not yet show the next cycle — verify current dates.")
            if msg not in p.warnings:
                p.warnings.append(msg)
    return p


def _label(s: FieldStatus, p: ProgramOpportunity) -> str:
    if s == FieldStatus.curated_fallback:
        date = p.last_verified_at.date().isoformat() if p.last_verified_at else "unknown date"
        return f"Curated fallback (last verified {date})"
    return FIELD_LABELS[s.value]


def verified_field_count(p: ProgramOpportunity) -> int:
    good = (FieldStatus.verified, FieldStatus.curated_fallback)
    return sum(1 for s in (p.tuition_status, p.funding_status, p.deadline_status, p.documents_status,
                           p.requirements_status) if s in good)
