"""'Needs your decision / Verify this' — only for conflicts or gaps the agent could not resolve itself."""

from __future__ import annotations

from collections import Counter

from app.models.common import DegreeLevel, FieldStatus
from app.models.evidence import Claim
from app.models.program import ProgramOpportunity
from app.models.results import PendingDecision
from app.validation.gaps import GAP_EXHAUST_ATTEMPTS


def _conflict_claims(claims: list[Claim]) -> list[Claim]:
    out = []
    for c in claims:
        if c.status == FieldStatus.conflicting:
            out.append(c)
            out.extend(c.alternatives)
    return out


def compute_pending_decisions(p: ProgramOpportunity, attempts: Counter | None, suggested: list[str],
                              research_finished: bool, resolved: set[str] | None = None) -> list[PendingDecision]:
    attempts = attempts or Counter()
    resolved = resolved or set()
    out: list[PendingDecision] = []
    name = f"{p.program_name} ({p.institution_name})"

    conflict_fields = {
        "tuition": [t.claim for t in p.tuition],
        "funding": [f.claim for f in p.funding],
        "deadline": [d.claim for d in p.deadlines],
        "min_gpa": [r.claim for r in p.requirements if r.key == "min_gpa"],
        "english_test": [r.claim for r in p.requirements if r.key == "english_test"],
        "direct_entry_phd": [r.claim for r in p.requirements if r.key == "direct_entry_phd"],
    }
    for field, claims in conflict_fields.items():
        cc = _conflict_claims(claims)
        if cc and field not in resolved:
            note = next((c.note for c in cc if c.note), "Official sources disagree")
            out.append(PendingDecision(
                program_id=p.program_id, field=field, kind="conflict",
                question=f"{note} for {name}. Investigate another official source, or continue with this uncertainty?",
                competing_claims=cc[:4], suggested_sources=suggested[:3]))

    if p.fallback_record:
        return out
    critical = {"deadline": p.deadline_status, "tuition": p.tuition_status, "funding": p.funding_status}
    if p.degree_level == DegreeLevel.phd:
        has_de = any(r.key == "direct_entry_phd" for r in p.requirements)
        critical["direct_entry_phd"] = FieldStatus.verified if has_de else FieldStatus.not_found
    for field, st in critical.items():
        if field in resolved or any(d.field == field for d in out):
            continue
        if st not in (FieldStatus.not_found, FieldStatus.unavailable, FieldStatus.unknown):
            continue
        exhausted = attempts.get((p.program_id, field), 0) >= GAP_EXHAUST_ATTEMPTS
        # Never ask the student to decide something the agent can still check itself.
        if not (exhausted or research_finished):
            continue
        kind = "unavailable" if st == FieldStatus.unavailable else "missing"
        what = {"deadline": "application deadline", "tuition": "tuition", "funding": "funding information",
                "direct_entry_phd": "whether a bachelor's degree is sufficient for direct PhD entry"}[field]
        verb = "could not be accessed" if kind == "unavailable" else "was not found on the official pages checked"
        out.append(PendingDecision(
            program_id=p.program_id, field=field, kind=kind,
            question=f"The {what} for {name} {verb}. Ask the agent to check another official page, or continue "
                     f"with this marked as unknown?",
            suggested_sources=suggested[:3]))
    return out
