"""Scoring + checklist + pending decisions + emission of validated program records; snapshot building."""

from __future__ import annotations

import asyncio
import re
import time

from app.agent.state import SharedResearch
from app.core import clock
from app.deliverables.checklist import build_checklist
from app.deliverables.decisions import compute_pending_decisions
from app.deliverables.templates import explanation_template
from app.models.common import DegreeLevel, EligibilityStatus, FieldStatus
from app.models.program import ExcludedProgram, ProgramOpportunity
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import Job
from app.scoring.scorer import rank
from app.tools.domains import host_of, registrable_domain
from app.validation.status import recompute_status

_FIELD_LINK_WORDS = {
    "tuition": r"tuition|cost|fee|bursar|student account",
    "funding": r"fund|financ|assistantship|fellowship|scholarship|stipend",
    "deadline": r"deadline|apply|application|dates",
    "documents": r"apply|application|requirement|document",
    "min_gpa": r"admission|requirement|apply",
    "english_test": r"english|toefl|ielts|international|requirement",
    "direct_entry_phd": r"admission|requirement|phd|doctoral|apply",
}


def is_emittable(p: ProgramOpportunity, level: DegreeLevel | None) -> bool:
    if level is not None and p.degree_level != level:
        return False
    if not p.has_verified_program_page:
        return False
    if p.eligibility_status == EligibilityStatus.prerequisite_not_met:
        return False
    core = (p.tuition_status, p.funding_status, p.deadline_status, p.documents_status, p.requirements_status)
    return any(s == FieldStatus.verified for s in core)


def suggested_sources(p: ProgramOpportunity, shared: SharedResearch | None, field: str) -> list[str]:
    if shared is None:
        return []
    reg = registrable_domain(host_of(p.official_program_url))
    pat = re.compile(_FIELD_LINK_WORDS.get(field, r"admission|apply"), re.I)
    out = []
    for url, link in shared.discovered_links.items():
        if url in shared.fetched_urls or url in shared.failed_urls:
            continue
        if registrable_domain(host_of(url)) != reg:
            continue
        if pat.search(f"{link.text} {url}"):
            out.append(url)
    return out[:3]


def program_payload(job: Job, pid: str) -> dict:
    p = job.programs[pid]
    return {
        "program": p.model_dump(mode="json"),
        "match": job.matches[pid].model_dump(mode="json") if pid in job.matches else None,
        "checklist": job.checklists[pid].model_dump(mode="json") if pid in job.checklists else None,
        "explanation": job.explanations[pid].model_dump(mode="json") if pid in job.explanations else None,
        "pending_decisions": [d.model_dump(mode="json") for d in job.pending.get(pid, [])],
    }


def refresh_program(ctx: AppContext, job: Job, pid: str, shared: SharedResearch | None) -> None:
    p = recompute_status(job.programs[pid])
    if job.profile is not None:
        job.matches[pid] = ctx.scorer.score(job.profile, p)
    job.checklists[pid] = build_checklist(p)
    resolved = {d.field for k, d in job.decisions.items() if k.startswith(pid + ":")
                and d.decision == "continue_with_uncertainty"}
    fields = ["tuition", "funding", "deadline", "documents", "min_gpa", "english_test", "direct_entry_phd"]
    sugg = {f: suggested_sources(p, shared, f) for f in fields}
    pend = compute_pending_decisions(p, job.gap_attempts, [], job.research_finished, resolved)
    for d in pend:
        d.suggested_sources = sugg.get(d.field, [])
    job.pending[pid] = pend
    job.suggested_sources[pid] = sorted({u for v in sugg.values() for u in v})


def emit_program(ctx: AppContext, job: Job, pid: str, shared: SharedResearch | None, *, event_log=None) -> None:
    """Score, checklist, decisions, then emit `program` (first time) or `program_updated`."""
    refresh_program(ctx, job, pid, shared)
    log = event_log or job.events
    p = job.programs[pid]
    for s in p.sources:
        if s.fetch_status == "ok":
            job.seen_official_urls.add(s.final_url)
    if pid not in job.emitted:
        job.emitted.append(pid)
        if job.first_result_ms is None:
            job.first_result_ms = int((time.monotonic() - job.started_monotonic) * 1000)
            job.counts["first_result_ms"] = job.first_result_ms
        m = job.matches.get(pid)
        score = f"fit {m.overall_fit_score}" if m and m.overall_fit_score is not None else "fit not yet computable"
        log.append("program", "results", f"Validated: {p.program_name} — {p.institution_name} ({score})",
                   payload=program_payload(job, pid))
    else:
        log.append("program_updated", "results", f"Updated: {p.program_name} — {p.institution_name}",
                   payload=program_payload(job, pid))


def exclude_program(job: Job, p: ProgramOpportunity) -> None:
    if any(e.program_id == p.program_id for e in job.excluded):
        return
    req = next((r for r in p.requirements if r.key == "direct_entry_phd"), None)
    ex = ExcludedProgram(program_id=p.program_id, institution_name=p.institution_name, program_name=p.program_name,
                         degree_level=p.degree_level,
                         reason="Official source states a master's degree is required before this PhD",
                         evidence=[e.model_dump(mode="json") for e in (req.claim.evidence if req else [])])
    job.excluded.append(ex)
    if p.program_id in job.emitted:
        job.emitted.remove(p.program_id)
    job.events.append("program_excluded", "results",
                      f"Not shown: {p.program_name} — {p.institution_name} (prerequisite not met per official source)",
                      payload=ex.model_dump(mode="json"))


async def explain_and_update(ctx: AppContext, job: Job, pid: str, shared, sem: asyncio.Semaphore,
                             deadline_at: float) -> None:
    async with sem:
        p, m = job.programs.get(pid), job.matches.get(pid)
        if p is None or m is None:
            return
        use_llm = ctx.live and job.llm_budget is not None and job.llm_budget.can(3) and \
            deadline_at - time.monotonic() > 15
        if use_llm:
            job.llm_budget.consume(1)
            expl, _ = await ctx.explainer().explain(p, m, job.profile_summary)
        else:
            expl = explanation_template(p, m)
        job.explanations[pid] = expl
        if pid in job.emitted:
            job.events.append("program_updated", "results", f"Explanation ready: {p.program_name}",
                              payload=program_payload(job, pid))


def ranked_groups(job: Job) -> list[dict]:
    groups = []
    levels = [lvl for lvl in (DegreeLevel.masters, DegreeLevel.phd)
              if any(job.programs[pid].degree_level == lvl for pid in job.emitted)]
    for lvl in levels:
        pairs = [(job.programs[pid], job.matches[pid]) for pid in job.emitted
                 if job.programs[pid].degree_level == lvl and pid in job.matches]
        groups.append({"degree_level": lvl.value,
                       "results": [program_payload(job, p.program_id) for p, _ in rank(pairs)]})
    return groups


def build_snapshot(job: Job, since_seq: int | None = None) -> dict:
    from app.models.common import SourceMode
    label = None
    if job.source_mode == SourceMode.curated_fallback:
        label = "Curated fallback data — not freshly verified"
    elif job.source_mode == SourceMode.cached_replay:
        label = "Cached demo replay"
    elif job.source_mode == SourceMode.mixed:
        label = "Live results plus clearly labelled curated fallback records"
    snap = {
        "job_id": job.job_id, "kind": job.kind.value, "status": job.status.value, "stop_reason": job.stop_reason,
        "source_mode": job.source_mode.value, "source_mode_label": label, "generated_at": clock.utc_now().isoformat(),
        "groups": ranked_groups(job),
        "excluded": [e.model_dump(mode="json") for e in job.excluded],
        "fallback_results": [program_payload(job, pid) for pid in job.fallback_results if pid in job.programs],
        "warnings": job.warnings, "counts": dict(job.counts),
        "shortlist": {"approved": job.shortlist.approved, "program_ids": job.shortlist.program_ids,
                      "approved_at": job.shortlist.approved_at.isoformat() if job.shortlist.approved_at else None},
        "disclaimer": "Fit scores are profile-fit scores, not admission probabilities. Always confirm details on the "
                      "official pages linked with each result.",
    }
    if since_seq is not None:
        snap["events"] = [e.model_dump(mode="json") for e in job.events.since(since_seq)]
    return snap
