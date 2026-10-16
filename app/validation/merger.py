"""RecordMerger: validated extraction -> ProgramOpportunity fields, with conflict preservation.

Rules (plan §10.4, PRD FR-13/16/17/28/29):
- Identity/institution come from verified quotes or the page <title>, never model memory.
- Conflicting verified values are kept side-by-side and flagged, never silently resolved.
- Funding existence never implies the student's eligibility.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.core import clock
from app.core.ids import stable_program_id
from app.extraction.schemas import ExtractedFact, ProgramPageExtraction
from app.models.common import (
    DataFreshness,
    DegreeLevel,
    EligibilityStatus,
    FieldStatus,
    FundingAvailability,
    FundingType,
    StudentEligibility,
)
from app.models.evidence import Claim, Evidence
from app.models.profile import US_STATES
from app.models.program import Deadline, FundingItem, ProgramOpportunity, Requirement, Tuition
from app.tools.domains import is_official_domain, registrable_domain
from app.tools.fetcher import FetchedPage
from app.validation.evidence_validator import EvidenceValidator, Rejection
from app.validation.normalizer import (
    annualize,
    parse_date,
    parse_gpa_min,
    parse_money,
    parse_period,
    parse_residency,
    parse_test_mins,
)
from app.validation.status import recompute_status
from app.validation.text_norm import normalize_for_match

_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
FIELD_TO_GAP = {"tuition": "tuition", "funding": "funding", "deadline": "deadline", "document": "documents",
                "documents": "documents", "min_gpa": "requirements", "english_test": "requirements",
                "gre": "requirements", "requirements": "requirements", "prior_degree": "direct_entry_phd",
                "direct_entry_phd": "direct_entry_phd", "application_fee": "fee"}
PAGE_KIND_FIELDS = {"tuition": ["tuition"], "funding": ["funding"], "deadlines": ["deadline"],
                    "admissions_requirements": ["documents", "requirements"]}


def degree_from_text(s: str) -> DegreeLevel | None:
    low = s.lower()
    if re.search(r"\bph\.?\s?d\b|doctor|doctoral", low):
        return DegreeLevel.phd
    if re.search(r"\bm\.?\s?s\.?\b|\bm\.?eng\b|\bmaster|\bm\.?sc\b|\bmcs\b|\bmsc\b", low):
        return DegreeLevel.masters
    return None


def student_has_masters(qualification: str | None) -> bool:
    return bool(qualification) and degree_from_text(qualification) == DegreeLevel.masters


@dataclass
class MergeReport:
    touched: set[str] = field(default_factory=set)
    created: set[str] = field(default_factory=set)
    verified_claims: int = 0
    rejected_claims: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    newly_verified: dict[str, set[str]] = field(default_factory=dict)  # program_id -> gap names
    new_conflicts: dict[str, set[str]] = field(default_factory=dict)
    excluded: set[str] = field(default_factory=set)

    def reject(self, reason: str) -> None:
        self.rejected_claims += 1
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1


class RecordMerger:
    def __init__(self, programs: dict[str, ProgramOpportunity], pages: dict[str, FetchedPage],
                 extra_official: set[str], qualification: str | None = None):
        self.programs = programs
        self.validator = EvidenceValidator(pages)
        self.extra = extra_official
        self.qualification = qualification

    # ---------- identity ----------
    def _find(self, inst: str, name: str, level: DegreeLevel, reg: str) -> ProgramOpportunity | None:
        pid = stable_program_id(inst, name, level.value)
        if pid in self.programs:
            return self.programs[pid]
        nn = normalize_for_match(name)
        for p in self.programs.values():
            if p.degree_level != level or registrable_domain(_host(p.official_program_url)) != reg:
                continue
            if SequenceMatcher(None, normalize_for_match(p.program_name), nn).ratio() >= 0.88:
                return p
        return None

    def merge(self, extraction: ProgramPageExtraction, page: FetchedPage, target_level: DegreeLevel | None,
              page_kind: str, target_program_id: str | None = None) -> MergeReport:
        rep = MergeReport()
        src = page.source.model_copy(update={"page_kind": page_kind})
        reg = src.registrable_domain
        page_programs: dict[str, ProgramOpportunity] = {}

        for ep in extraction.programs:
            ev = self.validator.check_quote(src.source_id, ep.program_evidence.chunk_id, ep.program_evidence.quote)
            if isinstance(ev, Rejection):
                rep.reject("program_" + ev.reason)
                continue
            inst = self._institution(ep, src)
            if not inst:
                rep.reject("institution_unverified")
                continue
            level = DegreeLevel(ep.degree_level) if ep.degree_level != "unclear" else degree_from_text(ep.program_name)
            if level is None:
                rep.reject("degree_unclear")
                continue
            rec = self._find(inst, ep.program_name, level, reg)
            if rec is None:
                pid = stable_program_id(inst, ep.program_name, level.value)
                rec = ProgramOpportunity(
                    program_id=pid, institution_name=inst, program_name=ep.program_name.strip()[:200],
                    degree_level=level, field=(ep.field or ep.program_name)[:120],
                    official_program_url=src.final_url, retrieved_at=src.retrieved_at,
                    data_freshness=DataFreshness.cached if src.freshness == DataFreshness.cached else DataFreshness.live,
                )
                self.programs[pid] = rec
                rep.created.add(pid)
            if page_kind in ("program_overview", "admissions_requirements") or not rec.has_verified_program_page:
                if not rec.has_verified_program_page:
                    rec.official_program_url = src.final_url
                rec.has_verified_program_page = True
            rep.verified_claims += 1
            page_programs[normalize_for_match(ep.program_name)] = rec
            rep.touched.add(rec.program_id)

        # A targeted follow-up page (e.g. a tuition page) may not restate the program name.
        if not page_programs and target_program_id and target_program_id in self.programs:
            tp = self.programs[target_program_id]
            if registrable_domain(_host(tp.official_program_url)) == reg:
                page_programs[normalize_for_match(tp.program_name)] = tp
                rep.touched.add(tp.program_id)

        for rec in page_programs.values():
            self._add_source(rec, src)

        for fact in extraction.facts:
            targets = self._targets(fact, page_programs, reg, target_level, extraction.scope)
            if not targets:
                rep.reject("no_target_program")
                continue
            res = self.validator.validate(fact.field, fact.value, src.source_id,
                                          [(e.chunk_id, e.quote) for e in fact.evidence])
            if isinstance(res, Rejection):
                rep.reject(res.reason)
                continue
            rep.verified_claims += 1
            scope = "program" if fact.program_name else extraction.scope
            for rec in targets:
                self._add_source(rec, src)
                gap = self._apply(rec, fact, res, scope, rep)
                rep.touched.add(rec.program_id)
                if gap:
                    rep.newly_verified.setdefault(rec.program_id, set()).add(gap)

        # Mark checked-but-absent fields so status becomes not_found (not unknown).
        checked = set(PAGE_KIND_FIELDS.get(page_kind, []))
        checked |= {FIELD_TO_GAP[f] for f in extraction.not_found_fields if f in FIELD_TO_GAP}
        for pid in rep.touched:
            rec = self.programs[pid]
            for f in checked:
                if f not in rec.attempted_fields:
                    rec.attempted_fields.append(f)
            self._eligibility(rec)
            recompute_status(rec)
            rec.retrieved_at = max(filter(None, [rec.retrieved_at, src.retrieved_at]))
            if rec.eligibility_status == EligibilityStatus.prerequisite_not_met:
                rep.excluded.add(pid)
        return rep

    def mark_failed(self, program_id: str, gap: str) -> None:
        rec = self.programs.get(program_id)
        if rec and gap not in rec.failed_fields:
            rec.failed_fields.append(gap)
            recompute_status(rec)

    # ---------- helpers ----------
    def _institution(self, ep, src) -> str | None:
        if ep.institution_evidence:
            ev = self.validator.check_quote(src.source_id, ep.institution_evidence.chunk_id,
                                            ep.institution_evidence.quote)
            if not isinstance(ev, Rejection) and normalize_for_match(ep.institution_name) in normalize_for_match(
                    ep.institution_evidence.quote):
                return ep.institution_name.strip()[:200]
        if src.title and normalize_for_match(ep.institution_name) in normalize_for_match(src.title):
            return ep.institution_name.strip()[:200]
        for p in self.programs.values():  # same site, institution already verified earlier in this job
            if registrable_domain(_host(p.official_program_url)) == src.registrable_domain and \
                    normalize_for_match(p.institution_name) == normalize_for_match(ep.institution_name):
                return p.institution_name
        return None

    def _targets(self, fact: ExtractedFact, page_programs: dict, reg: str, target_level, scope: str):
        if fact.program_name:
            key = normalize_for_match(fact.program_name)
            if key in page_programs:
                return [page_programs[key]]
            for k, rec in page_programs.items():
                if SequenceMatcher(None, k, key).ratio() >= 0.85:
                    return [rec]
        if page_programs and scope == "program":
            return list(page_programs.values())
        level_hint = degree_from_text(fact.qualifiers or "")
        same_site = [p for p in self.programs.values()
                     if registrable_domain(_host(p.official_program_url)) == reg
                     and (level_hint is None or p.degree_level == level_hint)]
        return same_site or list(page_programs.values())

    @staticmethod
    def _add_source(rec: ProgramOpportunity, src) -> None:
        if all(s.source_id != src.source_id for s in rec.sources):
            rec.sources.append(src)

    def _claim(self, fact: ExtractedFact, ev: list[Evidence], scope: str) -> Claim:
        return Claim(value=fact.value.strip()[:300], status=FieldStatus.verified, evidence=ev, scope=scope,
                     qualifiers=(fact.qualifiers or None) and fact.qualifiers[:200])

    @staticmethod
    def _conflict(existing: Claim, new: Claim, note: str) -> None:
        if any(e.source_id in {x.source_id for x in existing.evidence} for e in new.evidence):
            return  # same source restating — not a cross-source conflict
        existing.status = FieldStatus.conflicting
        existing.note = note
        existing.alternatives.append(new.model_copy(update={"status": FieldStatus.conflicting}))

    def _apply(self, rec: ProgramOpportunity, fact: ExtractedFact, ev: list[Evidence], scope: str,
               rep: MergeReport) -> str | None:
        claim = self._claim(fact, ev, scope)
        quals = f"{fact.value} {fact.qualifiers or ''}"
        f = fact.field
        if f == "tuition":
            money = parse_money(fact.value)
            amount = money[0] if money else None
            period, residency = parse_period(quals), parse_residency(quals)
            for t in rec.tuition:
                if t.period == period and t.residency == residency and t.amount and amount:
                    if abs(t.amount - amount) / t.amount > 0.01:
                        self._conflict(t.claim, claim, "Official pages list different tuition amounts")
                        rep.new_conflicts.setdefault(rec.program_id, set()).add("tuition")
                    else:
                        t.claim.evidence.extend(e for e in ev if e not in t.claim.evidence)
                    return "tuition"
            rec.tuition.append(Tuition(amount=amount, currency=money[1] if money else "USD", period=period,
                                       residency=residency, annualized_usd=annualize(amount, period)
                                       if (money and money[1] == "USD") else None, claim=claim))
            return "tuition"
        if f == "funding":
            ftype = FundingType(fact.funding_type) if fact.funding_type else FundingType.other
            norm = normalize_for_match(fact.value)[:80]
            if any(fi.type == ftype and normalize_for_match(fi.availability_statement)[:80] == norm for fi in rec.funding):
                return "funding"
            guaranteed = bool(fact.applies_to_all_admitted) and any(
                re.search(r"\b(all|every|guarantee[ds]?|guaranteed)\b", e.quote.lower()) for e in ev)
            level_ok = degree_from_text(quals) in (None, rec.degree_level)
            negative = funding_is_negative(fact.value + " " + " ".join(e.quote for e in ev))
            money = parse_money(fact.value)
            rec.funding.append(FundingItem(
                type=ftype,
                availability=FundingAvailability.not_offered if negative
                else FundingAvailability.guaranteed_for_admitted if (guaranteed and level_ok)
                else FundingAvailability.appears_available,
                availability_statement=fact.value.strip()[:300], amount=money[0] if money else None,
                amount_period=parse_period(quals) if money else None,
                requires_separate_application=fact.requires_separate_application,
                student_eligibility_status=StudentEligibility.not_confirmed, claim=claim))
            return "funding"
        if f == "deadline":
            d = parse_date(fact.value, fact.qualifiers)
            term_m = re.search(r"\b(fall|spring|summer|winter)\s+(20\d{2})\b", quals, re.I)
            term = f"{term_m.group(1).title()} {term_m.group(2)}" if term_m else None
            low = quals.lower()
            atype = ("funding_consideration" if re.search(r"fund|fellowship|assistantship|priority", low) else
                     "international" if "international" in low else "domestic" if "domestic" in low else "all")
            for dl in rec.deadlines:
                if dl.term == term and dl.applicant_type == atype:
                    if dl.date and d and dl.date != d:
                        self._conflict(dl.claim, claim, f"Official pages list different {term or ''} deadlines".replace("  ", " "))
                        rep.new_conflicts.setdefault(rec.program_id, set()).add("deadline")
                    return "deadline"
            rec.deadlines.append(Deadline(date=d, term=term, applicant_type=atype, claim=claim))
            return "deadline"
        if f == "document":
            nv = normalize_for_match(fact.value)
            if not any(normalize_for_match(c.value or "") == nv for c in rec.required_documents):
                rec.required_documents.append(claim)
            return "documents"
        if f == "application_fee":
            if rec.application_fee is None:
                rec.application_fee = claim
            else:
                a, b = parse_money(rec.application_fee.value or ""), parse_money(fact.value)
                if a and b and abs(a[0] - b[0]) > 0.5:
                    self._conflict(rec.application_fee, claim, "Official pages list different application fees")
            return None
        if f == "min_gpa":
            g = parse_gpa_min(fact.value)
            for r in rec.requirements:
                if r.key == "min_gpa":
                    if g and r.numeric_min is not None and r.numeric_scale == g[1] and abs(r.numeric_min - g[0]) > 1e-6:
                        self._conflict(r.claim, claim, "Official pages list different minimum GPAs")
                        rep.new_conflicts.setdefault(rec.program_id, set()).add("min_gpa")
                    return "requirements"
            rec.requirements.append(Requirement(key="min_gpa", label="Minimum GPA", claim=claim,
                                                numeric_min=g[0] if g else None, numeric_scale=g[1] if g else None))
            return "requirements"
        if f == "english_test":
            tests = parse_test_mins(fact.value) or [(None, None)]
            for test, val in tests:
                existing = next((r for r in rec.requirements if r.key == "english_test" and r.test == test), None)
                if existing:
                    if val is not None and existing.numeric_min is not None and existing.numeric_min != val:
                        self._conflict(existing.claim, claim, f"Official pages list different {test} minimums")
                        rep.new_conflicts.setdefault(rec.program_id, set()).add("english_test")
                    continue
                rec.requirements.append(Requirement(key="english_test", label=f"English test minimum ({test or 'see source'})",
                                                    claim=claim, numeric_min=val, test=test))
            return "requirements"
        if f == "gre":
            if not any(r.key == "gre" for r in rec.requirements):
                rec.requirements.append(Requirement(key="gre", label="GRE policy", claim=claim))
            return "requirements"
        if f in ("prior_degree", "direct_entry_phd"):
            if rec.degree_level != DegreeLevel.phd and f == "direct_entry_phd":
                return None
            key = "direct_entry_phd" if rec.degree_level == DegreeLevel.phd else "prior_degree"
            existing = next((r for r in rec.requirements if r.key == key), None)
            verdict = _direct_entry_verdict(fact.value + " " + " ".join(e.quote for e in ev))
            if existing:
                old = _direct_entry_verdict((existing.claim.value or "") + " " +
                                            " ".join(e.quote for e in existing.claim.evidence))
                if old and verdict and old != verdict:
                    self._conflict(existing.claim, claim, "Official pages disagree on whether a master's is required")
                    rep.new_conflicts.setdefault(rec.program_id, set()).add("direct_entry_phd")
                return "direct_entry_phd" if key == "direct_entry_phd" else "requirements"
            rec.requirements.append(Requirement(
                key=key, label="Direct entry after a bachelor's degree" if key == "direct_entry_phd" else "Prior degree",
                claim=claim, test=verdict))
            return "direct_entry_phd" if key == "direct_entry_phd" else "requirements"
        if f == "research_area":
            nv = normalize_for_match(fact.value)
            if not any(normalize_for_match(c.value or "") == nv for c in rec.research_areas) and len(rec.research_areas) < 25:
                rec.research_areas.append(claim)
            return None
        if f in ("application_url", "funding_url"):
            url = fact.value.strip()
            if url.startswith(("http://", "https://")) and is_official_domain(_host(url), self.extra):
                if f == "application_url":
                    rec.application_url = url
                else:
                    rec.official_funding_url = url
            return None
        if f == "state":
            code = fact.value.strip().upper()
            code = code if code in US_STATES else _STATE_NAMES.get(fact.value.strip().lower())
            if code:
                rec.state = code
            return None
        return None

    def _eligibility(self, rec: ProgramOpportunity) -> None:
        if rec.fallback_record:
            return
        if rec.degree_level == DegreeLevel.phd:
            req = next((r for r in rec.requirements if r.key == "direct_entry_phd"), None)
            warn = "Direct entry after a bachelor's degree not confirmed"
            if req is None or req.claim.status == FieldStatus.conflicting:
                rec.eligibility_status = EligibilityStatus.needs_verification
                if warn not in rec.warnings:
                    rec.warnings.append(warn)
                return
            if req.test == "masters_required" and not student_has_masters(self.qualification):
                rec.eligibility_status = EligibilityStatus.prerequisite_not_met
            else:
                rec.eligibility_status = EligibilityStatus.likely_eligible
                if warn in rec.warnings:
                    rec.warnings.remove(warn)
            return
        rec.eligibility_status = EligibilityStatus.needs_verification


_NEG_FUNDING = re.compile(
    r"self[- ]fund|self[- ]support|not (?:eligible for|offer|provide|available)|"
    r"\bno (?:university|departmental|department|program|institutional|financial|funding|aid|scholarships?|"
    r"assistantships?|fellowships?)\b|(?:does|do|will) not (?:offer|provide|have)|cannot (?:offer|provide)|"
    r"unable to (?:offer|provide)|not funded|without funding", re.I)


def funding_is_negative(text: str) -> bool:
    """Explicit statement that funding is NOT offered (a known negative, distinct from 'not found')."""
    return bool(_NEG_FUNDING.search(text or ""))


def _direct_entry_verdict(text: str) -> str | None:
    low = text.lower()
    if re.search(r"master'?s(?: degree)? (?:is )?(?:required|must)|must (?:hold|have) a master|require[sd]? a master", low):
        return "masters_required"
    if re.search(r"bachelor'?s|directly from (?:an )?undergraduate|direct(?:ly)?[- ]entry|without a master", low):
        return "bachelor_ok"
    return None


def _host(url: str) -> str:
    from app.tools.domains import host_of
    return host_of(url)


def now():  # small indirection for tests
    return clock.utc_now()
