"""ProgramResearchWorkflow — deterministic orchestrator around the bounded research agent (one loop per pathway)."""

from __future__ import annotations

import asyncio
import logging
import time

from app.agent import prompts
from app.agent.loop import ResearchLoop
from app.agent.state import LoopSession, ModeConfig, ResearchMode, SharedResearch, useful_links
from app.agent.tool_executor import ToolExecutor
from app.agent.tool_specs import RESEARCH_TOOL_NAMES
from app.core import clock
from app.deliverables.templates import explanation_template
from app.models.common import DegreeLevel, ErrorCategory, SourceMode
from app.models.profile import profile_for_llm
from app.orchestrator.budgets import Budget, JobLLMBudget
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import Job, JobStatus
from app.orchestrator.results import (
    emit_program,
    exclude_program,
    explain_and_update,
    is_emittable,
    program_payload,
    refresh_program,
)
from app.scoring.scorer import rank
from app.validation.deduper import final_dedupe
from app.validation.gaps import GAP_EXHAUST_ATTEMPTS, compute_gaps
from app.validation.merger import RecordMerger

log = logging.getLogger("gradbeacon.workflow")
PROGRAM_MODE = ModeConfig(ResearchMode.programs, RESEARCH_TOOL_NAMES, prompts.PROGRAMS)
_LEVEL_NAME = {DegreeLevel.masters: "Master's", DegreeLevel.phd: "PhD"}


class ProgramPageHandler:
    """Runs extraction -> validation -> merge after each fetch, then emits newly emittable programs."""

    def __init__(self, ctx: AppContext, job: Job, shared: SharedResearch, level_of: dict[str, DegreeLevel | None],
                 targets: dict[DegreeLevel, int], explain_tasks: list, sem: asyncio.Semaphore, deadline_at: float,
                 restrict_to: str | None = None, event_log=None):
        self.ctx, self.job, self.shared = ctx, job, shared
        self.level_of = level_of  # loop name -> degree level
        self.targets = targets
        self.explain_tasks = explain_tasks
        self.sem = sem
        self.deadline_at = deadline_at
        self.restrict_to = restrict_to  # verify mode: only this program id may change
        self.event_log = event_log or job.events

    def _emitted_count(self, level: DegreeLevel) -> int:
        return sum(1 for pid in self.job.emitted if self.job.programs[pid].degree_level == level)

    async def __call__(self, page, fctx: dict, session: LoopSession) -> dict:
        job = self.job
        level = self.level_of.get(session.name)
        target_pid = fctx.get("target_program_id")
        self.shared.extracted_for.add((page.source.source_id, target_pid))
        if len(page.chunks) == 0:
            self.event_log.append("facts_extracted", "extraction", "Page had no readable content", tool="fetch_page")
            if target_pid and fctx.get("gap"):
                RecordMerger(job.programs, self.shared.pages, self.ctx.settings.extra_official).mark_failed(
                    target_pid, fctx["gap"])
            return {"status": "no_readable_content"}
        if not job.llm_budget.can(1):
            return {"status": "extraction_skipped", "reason": "llm budget exhausted"}
        target = {"degree_level": level.value if level else None, "field": job.profile_summary.get("field"),
                  "specialization": job.profile_summary.get("specialization"),
                  "known_programs_on_site": [p.program_name for p in job.programs.values()
                                             if p.official_program_url and page.source.registrable_domain in
                                             p.official_program_url][:8],
                  "target_program": (job.programs[target_pid].program_name if target_pid in job.programs else None),
                  "expected_page_kind": fctx.get("expected_page_kind")}
        gaps = compute_gaps(job.programs[target_pid], job.gap_attempts) if target_pid in job.programs else \
            ["requirements", "deadline", "tuition", "funding", "documents"]
        job.llm_budget.consume(1)
        t0 = time.monotonic()
        extraction, calls, truncated = await self.ctx.extractor().extract_program_page(page, target, gaps)
        if calls > 1 and job.llm_budget.can(calls - 1):
            job.llm_budget.consume(calls - 1)
        job.counts["extractions"] += 1
        if truncated:
            job.counts["truncated_pages"] += 1
        if extraction is None:
            job.counts["extraction_failed"] += 1
            self.event_log.append("extraction_failed", "extraction",
                                  "Could not extract structured facts from this page; facts stay unverified",
                                  error_category=ErrorCategory.invalid_output)
            return {"status": "extraction_failed", "note": "Facts on this page remain unverified."}
        if extraction.injection_detected:
            job.counts["injection_flagged"] += 1
            self.event_log.append("prompt_injection_flagged", "extraction",
                                  "Page contained instructions aimed at AI; ignored (treated as data only)")
        programs_before = set(job.programs)
        merger = RecordMerger(job.programs, self.shared.pages, self.ctx.settings.extra_official, job.qualification)
        rep = merger.merge(extraction, page, level, extraction.page_kind if fctx.get("refocus") else
                           fctx.get("expected_page_kind") or extraction.page_kind, target_program_id=target_pid)
        if self.restrict_to:  # verify mode: discard records created outside the target
            for pid in set(job.programs) - programs_before:
                job.programs.pop(pid, None)
        job.counts["verified_claims"] += rep.verified_claims
        job.counts["rejected_claims"] += rep.rejected_claims
        for pid in rep.touched:
            self.shared.index.tag_program(page.source.source_id, pid)
        self.event_log.append(
            "facts_extracted", "validation",
            f"Validated {rep.verified_claims} claim{'s' if rep.verified_claims != 1 else ''} with source quotes"
            + (f"; rejected {rep.rejected_claims} unsupported" if rep.rejected_claims else ""),
            counts={"verified": rep.verified_claims, "rejected": rep.rejected_claims, "programs": len(rep.touched)},
            payload={"url": page.source.final_url, "newly_verified": {k: sorted(v) for k, v in rep.newly_verified.items()},
                     "conflicts": {k: sorted(v) for k, v in rep.new_conflicts.items()}})
        for pid, fields in rep.new_conflicts.items():
            if pid in job.programs:
                p = job.programs[pid]
                msg = f"Conflicting official information for {p.program_name}: {', '.join(sorted(fields))}"
                p.warnings.append(msg) if msg not in p.warnings else None
                self.event_log.append("warning", "validation", msg)

        await self._emit_ready(rep.touched)
        return {
            "status": "ok", "page_kind": extraction.page_kind,
            "programs_touched": [{"program_id": pid, "program": job.programs[pid].program_name,
                                  "institution": job.programs[pid].institution_name,
                                  "degree_level": job.programs[pid].degree_level.value}
                                 for pid in sorted(rep.touched) if pid in job.programs][:8],
            "newly_verified": {k: sorted(v) for k, v in rep.newly_verified.items()},
            "conflicts": {k: sorted(v) for k, v in rep.new_conflicts.items()},
            "rejected_claims": rep.rejected_claims,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }

    async def _emit_ready(self, touched: set[str]) -> None:
        job = self.job
        for pid in sorted(touched):
            p = job.programs.get(pid)
            if p is None:
                continue
            if self.restrict_to and pid != self.restrict_to:
                continue
            if p.eligibility_status.value == "prerequisite_not_met":
                exclude_program(job, p)
                continue
            if pid in job.emitted:
                emit_program(self.ctx, job, pid, self.shared, event_log=self.event_log)
                continue
            if not is_emittable(p, None) or p.degree_level not in self.targets:
                continue
            if self._emitted_count(p.degree_level) >= self.targets[p.degree_level]:
                continue
            emit_program(self.ctx, job, pid, self.shared, event_log=self.event_log)
            if not self.restrict_to:
                self.explain_tasks.append(asyncio.create_task(
                    explain_and_update(self.ctx, job, pid, self.shared, self.sem, self.deadline_at)))

    def on_fetch_failed(self, url, cat, fctx, session) -> None:
        pid, gap = fctx.get("target_program_id"), fctx.get("gap")
        if pid and gap and pid in self.job.programs:
            RecordMerger(self.job.programs, self.shared.pages, self.ctx.settings.extra_official).mark_failed(pid, gap)
            if pid in self.job.emitted:
                emit_program(self.ctx, self.job, pid, self.shared, event_log=self.event_log)


def render_program_state(job: Job, level: DegreeLevel, target_results: int, first_batch: int, shared: SharedResearch):
    def render(session: LoopSession) -> dict:
        progs = []
        for p in job.programs.values():
            if p.degree_level != level:
                continue
            gaps = compute_gaps(p, job.gap_attempts)
            exhausted = [g for (pid, g), n in job.gap_attempts.items() if pid == p.program_id and n >= GAP_EXHAUST_ATTEMPTS]
            progs.append({
                "program_id": p.program_id, "institution": p.institution_name, "program": p.program_name,
                "url": p.official_program_url, "shown_to_student": p.program_id in job.emitted,
                "fields": {"tuition": p.tuition_status.value, "funding": p.funding_status.value,
                           "deadline": p.deadline_status.value, "documents": p.documents_status.value,
                           "requirements": p.requirements_status.value},
                "open_gaps": gaps, "exhausted_gaps": exhausted,
            })
        emitted = sum(1 for pid in job.emitted if job.programs[pid].degree_level == level)
        return {
            "mode": "programs",
            "student": {k: job.profile_summary.get(k) for k in ("field", "specialization", "research_interests",
                                                                 "funding_preference")},
            "target": {"degree_level": level.value, "target_results": target_results, "first_batch": first_batch,
                       "validated_results_so_far": emitted},
            "programs": progs[:20],
            "excluded_programs": [e.program_name for e in job.excluded if e.degree_level == level],
            "useful_links": useful_links(shared),
        }
    return render


async def run_program_workflow(ctx: AppContext, job: Job, target_results: int | None = None) -> None:
    s = ctx.settings
    job.status = JobStatus.running
    profile = job.profile
    job.profile_summary = profile_for_llm(profile)
    job.qualification = profile.qualification
    levels = list(profile.degree_paths)
    paths = " and ".join(_LEVEL_NAME[lvl] for lvl in levels)
    job.events.append("job_started", "start", f"Starting research for {paths} in {profile.field}")
    explain_tasks: list[asyncio.Task] = []
    try:
        if not ctx.live:
            await use_curated(ctx, job, reason="Live research is unavailable on this deployment")
            return
        per_path = target_results or (s.default_target_results if len(levels) == 1 else 6)
        per_path = max(1, min(per_path, s.max_results_hard_cap // len(levels) if len(levels) > 1 else s.max_results_hard_cap))
        targets = {lvl: per_path for lvl in levels}
        deadline_at = time.monotonic() + s.request_deadline_seconds
        job.llm_budget = JobLLMBudget(left=s.max_llm_calls)
        shared = SharedResearch()
        job.research = shared
        base = Budget(s.max_loop_turns, s.max_search_calls, s.max_page_fetches, job.llm_budget)
        budgets = base.split(len(levels))
        sem = asyncio.Semaphore(2)
        level_of = {f"{lvl.value}": lvl for lvl in levels}
        handler = ProgramPageHandler(ctx, job, shared, level_of, targets, explain_tasks, sem, deadline_at)
        sessions = []
        for lvl, b in zip(levels, budgets, strict=True):
            sess = LoopSession(
                name=lvl.value, mode=PROGRAM_MODE, budget=b, deadline_at=deadline_at - 20, shared=shared,
                on_page=handler, on_fetch_failed=handler.on_fetch_failed,
                render=render_program_state(job, lvl, per_path, min(s.first_batch_candidates, per_path), shared),
                known_program_ids=lambda: set(job.programs),
            )
            ex = ToolExecutor(sess, ctx.providers.search, ctx.providers.fetcher, job.events, job.gap_attempts,
                              job.counts, s.extra_official)
            sessions.append(ResearchLoop(sess, ctx.providers.llm, ex, job.events, s.llm_effort_research))
        await asyncio.gather(*(loop.run() for loop in sessions))
        stops = [lp.s.stop_reason or "unknown" for lp in sessions]
        job.research_finished = True
        await _finish_explanations(explain_tasks, deadline_at)

        # Final dedupe/rank over emitted records; recompute decisions now that research is finished.
        kept = final_dedupe([job.programs[pid] for pid in job.emitted])
        job.emitted = [p.program_id for p in kept]
        for pid in job.emitted:
            refresh_program(ctx, job, pid, shared)
        job.counts["llm_calls"] = job.llm_budget.used
        job.counts["input_tokens"] = job.llm_budget.input_tokens
        job.counts["output_tokens"] = job.llm_budget.output_tokens
        job.counts["official_sources"] = len({src.final_url for pid in job.emitted
                                              for src in job.programs[pid].sources if src.fetch_status == "ok"})
        provider_failed = any(st.startswith(("provider_error", "provider_refusal")) for st in stops)
        truncated = any(st.startswith(("budget", "deadline")) for st in stops)
        job.stop_reason = "; ".join(f"{lp.s.name}: {lp.s.stop_reason}" for lp in sessions)

        reason = _failure_reason(stops)
        timed_out = any(st == "deadline" for st in stops)
        if not job.emitted and (provider_failed or timed_out):
            # Nothing validated: show labelled curated records (with the specific reason) instead of an empty page.
            await use_curated(ctx, job, reason=reason)
            return
        if job.emitted and len(job.emitted) < 3 and provider_failed:
            await use_curated(ctx, job, reason=reason, mixed=True)
        if provider_failed:
            job.warn("A provider error interrupted research; results may be incomplete.")
        if truncated:
            job.warn("Research stopped at its time/budget limit; results may be incomplete.")
        if not job.emitted:
            job.warn("No credible programs with official evidence were found for this profile. Try broadening your "
                     "field or interests; GradBeacon does not pad results with unverified programs.")
        job.status = JobStatus.partial if (truncated or provider_failed) else JobStatus.completed
        for pid in job.emitted:  # final state for the stream (decisions now that research is finished)
            if job.pending.get(pid):
                job.events.append("program_updated", "results", "Some details need your decision",
                                  payload=program_payload(job, pid))
    except Exception:  # noqa: BLE001 — never leave the client loading forever (FR-49)
        log.exception("workflow_failed", extra={"job_id": job.job_id})
        job.status = JobStatus.failed if not job.emitted else JobStatus.partial
        job.warn("An internal error stopped research early.")
        job.stop_reason = job.stop_reason or "internal_error"
    finally:
        for t in explain_tasks:
            if not t.done():
                t.cancel()
        _finish(job)


def _failure_reason(stops: list[str]) -> str:
    """Specific, honest reason for the student (never a generic 'unavailable' when we know why)."""
    joined = " ".join(stops)
    if "provider_rate_limited" in joined:
        return "Live research stopped because the AI provider's rate limit was reached"
    if "provider_auth" in joined:
        return "Live research stopped because an AI or search API key was rejected"
    if "provider_timeout" in joined:
        return "Live research stopped because the AI provider timed out"
    if "provider_refusal" in joined:
        return "Live research stopped because the AI model declined a step"
    if "invalid_output" in joined:
        return "Live research stopped because the AI provider returned an unusable response"
    if "deadline" in joined:
        return "Live research could not validate any program within the time limit"
    return "Live research failed because an AI or search provider was unavailable"


async def _finish_explanations(tasks: list[asyncio.Task], deadline_at: float) -> None:
    if not tasks:
        return
    wait = max(1.0, min(25.0, deadline_at - time.monotonic()))
    await asyncio.wait(tasks, timeout=wait)


def _finish(job: Job) -> None:
    job.finished_at = clock.utc_now()
    job.profile = None  # never retained after the job (privacy)
    elapsed = int((time.monotonic() - job.started_monotonic) * 1000)
    job.events.append("done", "done", _done_summary(job), counts=dict(job.counts), duration_ms=elapsed,
                      payload={"status": job.status.value, "stop_reason": job.stop_reason,
                               "source_mode": job.source_mode.value, "elapsed_ms": elapsed,
                               "results": len(job.emitted), "first_result_ms": job.first_result_ms})


def _done_summary(job: Job) -> str:
    if job.status == JobStatus.fallback:
        return f"Showing {len(job.emitted)} curated fallback record(s) — live research unavailable"
    if job.status == JobStatus.failed:
        return "Stopped: internal error"
    reason = "evidence sufficient" if (job.stop_reason or "").count("model_finished") else (
        "budget or time limit reached" if job.status == JobStatus.partial else "research complete")
    return f"Stopped: {reason} — {len(job.emitted)} validated program record(s)"


async def use_curated(ctx: AppContext, job: Job, reason: str, mixed: bool = False) -> None:
    profile = job.profile
    recs, warns = ctx.curated.select_for(profile) if profile else ([], [])
    job.events.append("fallback_used", "fallback", f"{reason}. Showing clearly labelled curated records.",
                      payload={"curated_at": ctx.curated.curated_at, "dataset_version": ctx.curated.dataset_version})
    for w in warns:
        job.warn(w)
    date = ctx.curated.curated_at or "an earlier date"
    job.warn(f"{reason}. Showing curated fallback records last verified on {date}; they were not freshly verified.")
    pairs = []
    for r in recs:
        job.programs[r.program_id] = r
        pairs.append((r, ctx.scorer.score(profile, r)))
    cap = ctx.settings.max_results_hard_cap
    per_level: dict = {}
    for p, _m in rank(pairs):
        per_level.setdefault(p.degree_level, [])
        if len(per_level[p.degree_level]) < (8 if len(profile.degree_paths) == 1 else 6):
            per_level[p.degree_level].append(p.program_id)
    chosen = [pid for ids in per_level.values() for pid in ids][:cap]
    if mixed:
        job.fallback_results = chosen
        job.source_mode = SourceMode.mixed
    else:
        job.source_mode = SourceMode.curated_fallback
        job.status = JobStatus.fallback
        if not chosen:
            job.warn("No curated fallback records are available for this profile.")
    for pid in chosen:
        refresh_program(ctx, job, pid, None)
        if not mixed:
            job.emitted.append(pid)
            job.events.append("program", "results",
                              f"Curated fallback: {job.programs[pid].program_name} — {job.programs[pid].institution_name}",
                              payload=program_payload(job, pid))
        job.explanations[pid] = explanation_template(job.programs[pid], job.matches[pid])
    job.research_finished = True
