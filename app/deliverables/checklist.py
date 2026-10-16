"""Deterministic, source-grounded checklist (FR-30/31). No value without evidence; unknowns labelled."""

from __future__ import annotations

from datetime import date

from app.core import clock
from app.models.common import FieldStatus, StudentEligibility
from app.models.program import ProgramOpportunity
from app.models.results import Checklist, ChecklistItem

_OK = (FieldStatus.verified, FieldStatus.curated_fallback)
_FIELD_NAMES = {"tuition": "tuition and fees", "funding": "funding", "deadline": "application deadline",
                "documents": "required documents", "requirements": "admission requirements"}


def _status(c) -> FieldStatus:
    return c.status


def build_checklist(p: ProgramOpportunity) -> Checklist:
    items: list[ChecklistItem] = []
    pid = p.program_id
    first_src = next((s for s in p.sources if s.fetch_status == "ok"), None)
    items.append(ChecklistItem(
        label="Official program page", category="link", value=p.official_program_url,
        status=FieldStatus.curated_fallback if p.fallback_record else (
            FieldStatus.verified if p.has_verified_program_page else FieldStatus.unknown),
        action_url=p.official_program_url, program_id=pid,
        evidence=[] if first_src is None else []))
    if p.application_url:
        items.append(ChecklistItem(label="Official application portal", category="link", value=p.application_url,
                                   status=FieldStatus.curated_fallback if p.fallback_record else FieldStatus.verified,
                                   action_url=p.application_url, program_id=pid))
    else:
        items.append(ChecklistItem(label="Find the official application portal", category="verify", value=None,
                                   status=FieldStatus.not_found, action_url=p.official_program_url, program_id=pid))
    for d in sorted(p.deadlines, key=lambda d: (d.date is None, d.date or date.max)):
        who = f" ({d.applicant_type.replace('_', ' ')})" if d.applicant_type and d.applicant_type != "all" else ""
        items.append(ChecklistItem(label=f"Application deadline{' ' + d.term if d.term else ''}{who}",
                                   category="deadline", value=d.date.isoformat() if d.date else d.claim.value,
                                   status=d.claim.status, evidence=d.claim.evidence, program_id=pid,
                                   action_url=d.claim.evidence[0].url if d.claim.evidence else None))
    if p.application_fee:
        items.append(ChecklistItem(label="Application fee", category="fee", value=p.application_fee.value,
                                   status=p.application_fee.status, evidence=p.application_fee.evidence, program_id=pid))
    else:
        items.append(ChecklistItem(label="Application fee — needs verification", category="verify", value=None,
                                   status=FieldStatus.not_found if "fee" in p.attempted_fields else FieldStatus.unknown,
                                   action_url=p.official_program_url, program_id=pid))
    for doc in p.required_documents:
        items.append(ChecklistItem(label=f"Prepare: {doc.value}", category="document", value=doc.value,
                                   status=doc.status, evidence=doc.evidence, program_id=pid))
    eng = [r for r in p.requirements if r.key == "english_test"]
    gpa = [r for r in p.requirements if r.key == "min_gpa"]
    gre = [r for r in p.requirements if r.key == "gre"]
    for r in eng or [None]:
        if r is None:
            items.append(ChecklistItem(label="English test minimum — needs verification", category="verify",
                                       status=FieldStatus.unknown, action_url=p.official_program_url, program_id=pid))
        else:
            items.append(ChecklistItem(label=r.label, category="test", value=r.claim.value, status=r.claim.status,
                                       evidence=r.claim.evidence, program_id=pid))
    for r in gpa or [None]:
        if r is None:
            items.append(ChecklistItem(label="Minimum GPA — needs verification", category="verify",
                                       status=FieldStatus.unknown, action_url=p.official_program_url, program_id=pid))
        else:
            items.append(ChecklistItem(label="Minimum GPA", category="gpa", value=r.claim.value, status=r.claim.status,
                                       evidence=r.claim.evidence, program_id=pid))
    for r in gre:
        items.append(ChecklistItem(label="GRE policy", category="test", value=r.claim.value, status=r.claim.status,
                                   evidence=r.claim.evidence, program_id=pid))
    for f in p.funding:
        if f.requires_separate_application:
            items.append(ChecklistItem(label=f"Submit separate funding application ({f.type.value.replace('_', ' ')})",
                                       category="funding", value=f.availability_statement, status=f.claim.status,
                                       evidence=f.claim.evidence, program_id=pid,
                                       action_url=p.official_funding_url or (f.claim.evidence[0].url if f.claim.evidence else None)))
    if not any(f.student_eligibility_status == StudentEligibility.confirmed for f in p.funding):
        items.append(ChecklistItem(label="Funding eligibility for you is not confirmed — check with the program",
                                   category="verify", status=FieldStatus.not_found if p.funding else p.funding_status,
                                   action_url=p.official_funding_url or p.official_program_url, program_id=pid))
    statuses = {"tuition": p.tuition_status, "funding": p.funding_status, "deadline": p.deadline_status,
                "documents": p.documents_status, "requirements": p.requirements_status}
    for field, st in statuses.items():
        if st in (FieldStatus.conflicting, FieldStatus.not_found, FieldStatus.unavailable, FieldStatus.unknown):
            items.append(ChecklistItem(label=f"Verify {_FIELD_NAMES[field]} on the official site", category="verify",
                                       value=None, status=st, action_url=_best_url(p, field), program_id=pid))
    return Checklist(program_id=pid, items=items, generated_at=clock.utc_now())


def _best_url(p: ProgramOpportunity, field: str) -> str:
    kinds = {"tuition": "tuition", "funding": "funding", "deadline": "deadlines"}.get(field)
    if kinds:
        for s in p.sources:
            if s.page_kind == kinds:
                return s.final_url
    if field == "funding" and p.official_funding_url:
        return p.official_funding_url
    return p.official_program_url


def combined_checklist(checklists: list[Checklist], programs: dict[str, ProgramOpportunity]) -> dict:
    def first_deadline(cl: Checklist):
        p = programs.get(cl.program_id)
        ds = [d.date for d in (p.deadlines if p else []) if d.date]
        return (not ds, min(ds) if ds else date.max)

    groups = []
    for cl in sorted(checklists, key=first_deadline):
        p = programs.get(cl.program_id)
        groups.append({"program_id": cl.program_id,
                       "program": f"{p.program_name} — {p.institution_name}" if p else cl.program_id,
                       "items": [i.model_dump(mode="json") for i in cl.items]})
    return {"groups": groups, "note": "Sorted by earliest sourced deadline; programs with unknown deadlines last."}
