"""Deterministic evidence-gap computation (agent spec §6.3). The LLM chooses which gap to chase."""

from __future__ import annotations

from collections import Counter

from app.models.common import DegreeLevel, FieldStatus
from app.models.program import ProgramOpportunity

GAP_EXHAUST_ATTEMPTS = 2


def requirement_verified(p: ProgramOpportunity, key: str) -> bool:
    return any(r.key == key and r.claim.status == FieldStatus.verified for r in p.requirements)


def compute_gaps(p: ProgramOpportunity, attempts: Counter | None = None) -> list[str]:
    attempts = attempts or Counter()
    open_: list[str] = []
    for field, status in (("tuition", p.tuition_status), ("funding", p.funding_status),
                          ("deadline", p.deadline_status), ("documents", p.documents_status)):
        if status in (FieldStatus.unknown, FieldStatus.not_found, FieldStatus.unavailable):
            open_.append(field)
    if not (requirement_verified(p, "min_gpa") or requirement_verified(p, "english_test")):
        open_.append("requirements")
    if p.degree_level == DegreeLevel.phd and not requirement_verified(p, "direct_entry_phd"):
        open_.append("direct_entry_phd")
    return [g for g in open_ if attempts.get((p.program_id, g), 0) < GAP_EXHAUST_ATTEMPTS]


def exhausted_gaps(p: ProgramOpportunity, attempts: Counter) -> list[str]:
    return [g for (pid, g), n in attempts.items() if pid == p.program_id and n >= GAP_EXHAUST_ATTEMPTS]
