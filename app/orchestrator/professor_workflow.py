"""ProfessorResearchWorkflow — same loop + tools in professor mode, after the student approves a shortlist."""

from __future__ import annotations

import logging
import re
import time

from app.agent import prompts
from app.agent.loop import ResearchLoop
from app.agent.state import LoopSession, ModeConfig, ResearchMode, SharedResearch, useful_links
from app.agent.tool_executor import ToolExecutor
from app.agent.tool_specs import RESEARCH_TOOL_NAMES
from app.core import clock
from app.core.ids import stable_professor_id
from app.extraction.schemas import ExtractedValue, ProfessorPageExtraction
from app.models.common import DataFreshness, ErrorCategory, FieldStatus, SummaryStatus
from app.models.evidence import Claim, Evidence
from app.models.professor import ProfessorMatch, Publication
from app.orchestrator.budgets import Budget, JobLLMBudget
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import Job, JobStatus
from app.scoring.terms import extract_terms
from app.tools.domains import host_of, is_official_domain, registrable_domain
from app.tools.url_safety import canonicalize
from app.validation.evidence_validator import EMAIL_RE, EvidenceValidator, Rejection
from app.validation.text_norm import normalize_for_match

log = logging.getLogger("gradbeacon.professors")
PROF_MODE = ModeConfig(ResearchMode.professors, RESEARCH_TOOL_NAMES, prompts.PROFESSORS, allow_publication_domains=True)
_FACULTY_WORDS = re.compile(r"faculty|people|directory|professor|lab|research|group|team|contact|admission", re.I)
_INTAKE_RE = re.compile(r"accept|recruit|looking for|seeking|taking|not (?:currently )?(?:accepting|taking)|"
                        r"openings?|positions?", re.I)


def professor_fit(interests: list[str], prof: ProfessorMatch) -> tuple[int | None, list[str], str]:
    if not prof.research_interests:
        return None, [], "No verified research interests found on the official profile."
    prof_terms = extract_terms(" ".join(c.value or "" for c in prof.research_interests))
    hit = [i for i in interests[:5] if extract_terms(i) & prof_terms]
    score = round(100 * len(hit) / max(1, min(len(interests), 5))) if interests else None
    listed = ", ".join((c.value or "") for c in prof.research_interests[:3])
    if hit:
        expl = (f"Official profile lists research in {listed}, which overlaps your interests in {', '.join(hit)}.")
    else:
        expl = f"Official profile lists research in {listed}; no direct overlap with your stated interests was found."
    return score, hit, expl


class ProfessorPageHandler:
    def __init__(self, ctx: AppContext, job: Job, shared: SharedResearch, institutions: dict[str, dict],
                 interests: list[str], max_results: int):
        self.ctx, self.job, self.shared = ctx, job, shared
        self.institutions = institutions  # registrable domain -> {name, program_ids, contact_url}
        self.interests = interests
        self.max_results = max_results

    def _val(self, v: ExtractedValue | None, sid: str, validator: EvidenceValidator, field: str = "text"
             ) -> Claim | None:
        if v is None:
            return None
        ev = validator.check_quote(sid, v.evidence.chunk_id, v.evidence.quote)
        if isinstance(ev, Rejection) or not validator.value_supported(field, v.value, v.evidence.quote):
            self.job.counts["rejected_claims"] += 1
            return None
        self.job.counts["verified_claims"] += 1
        return Claim(value=v.value.strip()[:300], status=FieldStatus.verified, evidence=[ev])

    async def __call__(self, page, fctx: dict, session: LoopSession) -> dict:
        job = self.job
        self.shared.extracted_for.add((page.source.source_id, fctx.get("target_program_id")))
        if not page.chunks:
            return {"status": "no_readable_content"}
        if not job.llm_budget.can(1):
            return {"status": "extraction_skipped", "reason": "llm budget exhausted"}
        job.llm_budget.consume(1)
        extraction, calls = await self.ctx.extractor().extract_professor_page(page, self.interests)
        if calls > 1 and job.llm_budget.can(calls - 1):
            job.llm_budget.consume(calls - 1)
        if extraction is None:
            job.events.append("extraction_failed", "extraction", "Could not extract faculty facts from this page",
                              error_category=ErrorCategory.invalid_output)
            return {"status": "extraction_failed"}
        if extraction.injection_detected:
            job.events.append("prompt_injection_flagged", "extraction",
                              "Page contained instructions aimed at AI; ignored (treated as data only)")
        return self._merge(extraction, page)

    def _merge(self, ex: ProfessorPageExtraction, page) -> dict:
        job = self.job
        src = page.source
        validator = EvidenceValidator(self.shared.pages)
        reg = src.registrable_domain
        inst = self.institutions.get(reg)
        official_page = src.is_official
        if inst is None and official_page:
            return {"status": "ok", "note": "Page is not from a shortlisted institution; ignored."}
        page_links = {canonicalize(link.url) for link in page.links}
        touched, added = [], 0
        for ep in ex.professors[:25]:
            nev = validator.check_quote(src.source_id, ep.name_evidence.chunk_id, ep.name_evidence.quote)
            if isinstance(nev, Rejection) or normalize_for_match(ep.name) not in normalize_for_match(ep.name_evidence.quote):
                job.counts["rejected_claims"] += 1
                continue
            if inst is None:  # publication page: may only add publications to an already-known professor
                pid = next((k for k, v in job.professors.items()
                            if normalize_for_match(v.name) == normalize_for_match(ep.name)), None)
                if pid is None:
                    continue
            else:
                pid = stable_professor_id(inst["name"], ep.name)
            prof = job.professors.get(pid)
            if prof is None:
                if inst is None:
                    continue
                profile_url = src.final_url
                if ep.profile_url:
                    try:
                        cu = canonicalize(ep.profile_url)
                        if cu in page_links and is_official_domain(host_of(cu), self.ctx.settings.extra_official):
                            profile_url = cu
                            self.shared.discovered_links.setdefault(cu, next(
                                (lk for lk in page.links if canonicalize(lk.url) == cu), None) or page.links[0])
                    except Exception:  # noqa: BLE001
                        pass
                prof = ProfessorMatch(
                    professor_id=pid, name=ep.name.strip()[:120],
                    name_claim=Claim(value=ep.name.strip(), status=FieldStatus.verified, evidence=[nev]),
                    institution=inst["name"], official_profile_url=profile_url,
                    contact_fallback_url=inst.get("contact_url"), related_program_ids=inst["program_ids"],
                    data_freshness=DataFreshness.cached if src.freshness == DataFreshness.cached else DataFreshness.live)
                job.professors[pid] = prof
            elif ex.page_kind == "faculty_profile" and official_page:
                prof.official_profile_url = src.final_url
            if official_page:
                prof.title = prof.title or self._val(ep.title, src.source_id, validator)
                prof.department = prof.department or self._val(ep.department, src.source_id, validator)
                for it in ep.research_interests[:12]:
                    c = self._val(it, src.source_id, validator)
                    if c and all(normalize_for_match(c.value) != normalize_for_match(x.value or "")
                                 for x in prof.research_interests):
                        prof.research_interests.append(c)
                if ep.email and prof.official_email is None:
                    c = self._val(ep.email, src.source_id, validator, field="email")
                    email = (ep.email.value or "").strip()
                    if c and EMAIL_RE.match(email) and registrable_domain(email.split("@")[1]) == reg:
                        c.value = email
                        prof.official_email = c
                    elif ep.email:
                        job.counts["rejected_emails"] += 1
                if ep.intake_statement and prof.intake_status is None:
                    c = self._val(ep.intake_statement, src.source_id, validator)
                    if c and _INTAKE_RE.search(c.evidence[0].quote):
                        prof.intake_status = c
                        prof.intake_label = "Stated on official page (see quote)"
            for pub in ep.publications[:10]:
                ev = validator.check_quote(src.source_id, pub.evidence.chunk_id, pub.evidence.quote)
                if isinstance(ev, Rejection) or normalize_for_match(pub.title)[:40] not in normalize_for_match(pub.evidence.quote):
                    job.counts["rejected_claims"] += 1
                    continue
                if all(normalize_for_match(p.title) != normalize_for_match(pub.title) for p in prof.verified_publications):
                    prof.verified_publications.append(Publication(
                        title=pub.title.strip()[:300], year=pub.year, url=src.final_url,
                        claim=Claim(value=pub.title.strip()[:300], status=FieldStatus.verified, evidence=[ev])))
            touched.append(pid)
        for pid in dict.fromkeys(touched):
            added += self._maybe_emit(pid)
        return {"status": "ok", "page_kind": ex.page_kind,
                "professors_on_page": len(ex.professors),
                "professors_validated": [{"professor_id": pid, "name": job.professors[pid].name,
                                          "interests": len(job.professors[pid].research_interests),
                                          "official_email_found": job.professors[pid].official_email is not None}
                                         for pid in dict.fromkeys(touched)][:10],
                "shown_to_student": len(job.professor_order)}

    def _maybe_emit(self, pid: str) -> int:
        job = self.job
        prof = job.professors[pid]
        score, terms, expl = professor_fit(self.interests, prof)
        prof.research_fit_score, prof.research_fit_terms, prof.research_fit_explanation = score, terms, expl
        if prof.official_email is None and not prof.contact_fallback_url:
            prof.contact_fallback_url = None
        verified = sum(1 for x in (prof.title, prof.department, prof.official_email) if x) + len(prof.research_interests)
        prof.verification_status = SummaryStatus.verified if (prof.research_interests and verified >= 3) else \
            SummaryStatus.partially_verified if prof.research_interests else SummaryStatus.needs_verification
        if not prof.research_interests:
            return 0
        if pid in job.professor_order:
            job.events.append("professor_updated", "results", f"Updated: {prof.name}", payload=prof.model_dump(mode="json"))
            return 0
        if len(job.professor_order) >= self.max_results:
            return 0
        job.professor_order.append(pid)
        fit = f"research fit {score}" if score is not None else "research fit unknown"
        job.events.append("professor", "results", f"Validated faculty match: {prof.name} — {prof.institution} ({fit})",
                          payload=prof.model_dump(mode="json"))
        return 1


async def run_professor_workflow(ctx: AppContext, pjob: Job, parent: Job, program_ids: list[str],
                                 interests: list[str], max_results: int) -> None:
    s = ctx.settings
    pjob.status = JobStatus.running
    institutions: dict[str, dict] = {}
    shared = SharedResearch()
    pjob.research = shared
    for pid in program_ids:
        p = parent.programs[pid]
        reg = registrable_domain(host_of(p.official_program_url))
        inst = institutions.setdefault(reg, {"name": p.institution_name, "program_ids": [], "contact_url": None})
        inst["program_ids"].append(pid)
        for u in filter(None, [p.department_url, p.official_program_url, p.application_url]):
            try:
                shared.seeded_urls.add(canonicalize(u))
            except Exception:  # noqa: BLE001
                pass
        inst["contact_url"] = inst["contact_url"] or p.department_url or p.official_program_url
    if parent.research is not None:
        for url, link in parent.research.discovered_links.items():
            if registrable_domain(host_of(url)) in institutions and _FACULTY_WORDS.search(f"{link.text} {url}"):
                shared.discovered_links.setdefault(url, link)
    names = ", ".join(v["name"] for v in institutions.values())
    pjob.events.append("job_started", "start", f"Searching official faculty pages at {names}")
    try:
        if not ctx.live:
            pjob.warn("Live professor research is unavailable on this deployment (no AI/search provider configured).")
            pjob.status = JobStatus.fallback
            return
        pjob.llm_budget = JobLLMBudget(left=max(10, s.max_llm_calls - 10))
        deadline_at = time.monotonic() + s.professor_deadline_seconds
        handler = ProfessorPageHandler(ctx, pjob, shared, institutions, interests, max_results)

        def render(session: LoopSession) -> dict:
            return {
                "mode": "professors", "student_interests": interests, "target_professors": max_results,
                "institutions": [{"name": v["name"], "domain": k} for k, v in institutions.items()],
                "professors_found": [{"professor_id": pid, "name": pr.name, "institution": pr.institution,
                                      "verified_interests": len(pr.research_interests),
                                      "official_email_found": pr.official_email is not None,
                                      "shown_to_student": pid in pjob.professor_order}
                                     for pid, pr in list(pjob.professors.items())[:20]],
                "useful_links": useful_links(shared, keywords=_FACULTY_WORDS),
            }

        def failed(url, cat, fctx, session) -> None:
            return None

        sess = LoopSession(name="professors", mode=PROF_MODE, budget=Budget(10, 6, 12, pjob.llm_budget),
                           deadline_at=deadline_at, shared=shared, on_page=handler, on_fetch_failed=failed,
                           render=render, known_program_ids=lambda: set(program_ids))
        ex = ToolExecutor(sess, ctx.providers.search, ctx.providers.fetcher, pjob.events, pjob.gap_attempts,
                          pjob.counts, s.extra_official)
        await ResearchLoop(sess, ctx.providers.llm, ex, pjob.events, s.llm_effort_research).run()
        pjob.stop_reason = sess.stop_reason
        pjob.counts["llm_calls"] = pjob.llm_budget.used
        truncated = (sess.stop_reason or "").startswith(("budget", "deadline", "provider"))
        if not pjob.professor_order:
            pjob.warn("No faculty with verified, matching research interests were found on the official pages checked.")
        if any(pjob.professors[p].official_email is None for p in pjob.professor_order):
            pjob.warn("Some faculty have no officially published email; use the linked department page to make contact.")
        pjob.status = JobStatus.partial if truncated else JobStatus.completed
    except Exception:  # noqa: BLE001
        log.exception("professor_workflow_failed", extra={"job_id": pjob.job_id})
        pjob.status = JobStatus.failed
        pjob.warn("Professor research stopped due to an internal error.")
    finally:
        pjob.finished_at = clock.utc_now()
        pjob.events.append("done", "done", f"Stopped — {len(pjob.professor_order)} faculty match(es) with official evidence",
                           counts=dict(pjob.counts), payload={"status": pjob.status.value,
                                                              "stop_reason": pjob.stop_reason})


def professor_snapshot(job: Job) -> dict:
    profs = [job.professors[p] for p in job.professor_order]
    profs.sort(key=lambda p: (p.research_fit_score is None, -(p.research_fit_score or 0)))
    return {"job_id": job.job_id, "kind": "professors", "status": job.status.value, "stop_reason": job.stop_reason,
            "parent_job_id": job.parent_job_id, "professors": [p.model_dump(mode="json") for p in profs],
            "warnings": job.warnings, "counts": dict(job.counts),
            "notes": ["Emails are shown only when published verbatim on an official university page.",
                      "Accepting-students status is shown only when an official page states it."]}


_ = (Evidence,)  # re-exported type used in payloads
