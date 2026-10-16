"""Program research, live trace, human decisions, and shortlist approval."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_ctx
from app.api.rate_limit import limit
from app.api.sse import stream_job_events
from app.core import clock
from app.core.errors import InvalidState, NotFound, ValidationFailed
from app.deliverables.checklist import build_checklist, combined_checklist
from app.models.api import DecisionRequest, ResearchRequest, ShortlistRequest, VerifyRequest
from app.models.common import SourceMode
from app.models.results import DecisionRecord
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import FINISHED, JobKind, JobStatus
from app.orchestrator.professor_workflow import professor_snapshot
from app.orchestrator.program_workflow import run_program_workflow
from app.orchestrator.replay import load_replay, run_replay
from app.orchestrator.results import build_snapshot, emit_program
from app.orchestrator.verify_workflow import run_verify_workflow
from app.tools.url_safety import canonicalize

router = APIRouter(prefix="/api/research", tags=["research"])


def _accepted(job, kind_prefix: str = "") -> dict:
    return {"job_id": job.job_id, "status": job.status.value, "source_mode": job.source_mode.value,
            "events_url": f"/api/research/{job.job_id}/events", "snapshot_url": f"/api/research/{job.job_id}"}


@router.post("/programs", status_code=202, dependencies=[Depends(limit("research"))])
async def start_program_research(body: ResearchRequest, ctx: AppContext = Depends(get_ctx)):
    """Validate the profile and start bounded program research. Returns 202 + SSE/snapshot URLs.
    When live research is unavailable, returns 200 with a full, clearly labelled curated-fallback snapshot."""
    if ctx.settings.app_mode == "demo_replay":
        data = load_replay(ctx.settings.demo_replay_path)
        if data is not None:
            job = ctx.store.create(JobKind.programs, count_toward_concurrency=False)
            job.task = asyncio.create_task(run_replay(job, data, ctx.settings.demo_replay_speedup))
            job.status = JobStatus.running
            job.source_mode = SourceMode.cached_replay
            return _accepted(job)
    job = ctx.store.create(JobKind.programs, profile=body.profile)
    if not ctx.live:
        await run_program_workflow(ctx, job, body.target_results)
        return JSONResponse(build_snapshot(job, since_seq=0), status_code=200)
    job.task = asyncio.create_task(run_program_workflow(ctx, job, body.target_results))
    job.status = JobStatus.running
    return _accepted(job)


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request, ctx: AppContext = Depends(get_ctx)):
    """Server-Sent Events stream of real workflow events (supports Last-Event-ID). Ends after `done`."""
    return stream_job_events(request, ctx.store.get(job_id))


@router.get("/{job_id}")
async def job_snapshot(job_id: str, since_seq: int | None = Query(default=None, ge=0),
                       ctx: AppContext = Depends(get_ctx)):
    """Snapshot / polling fallback. Only validated (emittable) records are included."""
    job = ctx.store.get(job_id)
    if job.kind == JobKind.professors:
        snap = professor_snapshot(job)
        snap["drafts"] = [d.model_dump(mode="json") for d in job.drafts.values()]
        if since_seq is not None:
            snap["events"] = [e.model_dump(mode="json") for e in job.events.since(since_seq)]
        return snap
    if job.kind == JobKind.verify:
        return {"job_id": job.job_id, "kind": "verify", "status": job.status.value, "parent_job_id": job.parent_job_id,
                "warnings": job.warnings, "stop_reason": job.stop_reason, "counts": dict(job.counts),
                "events": [e.model_dump(mode="json") for e in job.events.since(since_seq or 0)]}
    return build_snapshot(job, since_seq)


def _program_job(ctx: AppContext, job_id: str, program_id: str):
    job = ctx.store.get(job_id)
    if job.kind != JobKind.programs:
        raise ValidationFailed("Not a program research job")
    if program_id not in job.emitted:
        raise NotFound("Program not in this job's results")
    return job


@router.post("/{job_id}/programs/{program_id}/verify", status_code=202, dependencies=[Depends(limit("verify"))])
async def verify_field(job_id: str, program_id: str, body: VerifyRequest, ctx: AppContext = Depends(get_ctx)):
    """'Verify this': the student asks the agent to check one uncertain field against another official source."""
    job = _program_job(ctx, job_id, program_id)
    if job.status not in FINISHED:
        raise InvalidState("Research is still running; wait for it to finish")
    p = job.programs[program_id]
    if p.fallback_record or job.source_mode.value in ("curated_fallback", "cached_replay"):
        raise InvalidState("Curated/replayed records cannot be re-verified here; run a new live search")
    if not ctx.live:
        raise InvalidState("Live verification is unavailable on this deployment")
    if body.source_url:
        try:
            cu = canonicalize(body.source_url)
        except Exception as e:  # noqa: BLE001
            raise ValidationFailed("Invalid source_url") from e
        allowed = set(job.suggested_sources.get(program_id, [])) | job.seen_official_urls
        if job.research:
            allowed |= set(job.research.discovered_links) | set(job.research.search_result_urls)
        if cu not in allowed:
            raise ValidationFailed("source_url must be one of the suggested sources or an official URL already seen")
    key = f"{program_id}:{body.field}"
    if key in job.verify_running:
        raise InvalidState("A verification for this field is already running")
    vjob = ctx.store.create(JobKind.verify, parent_job_id=job_id, count_toward_concurrency=False)
    job.verify_running.add(key)
    job.decisions[key] = DecisionRecord(program_id=program_id, field=body.field, decision="verify_requested",
                                        at=clock.utc_now())
    vjob.task = asyncio.create_task(run_verify_workflow(ctx, vjob, job, program_id, body.field, body.source_url,
                                                        body.profile))
    return {"verify_job_id": vjob.job_id, "events_url": f"/api/research/{vjob.job_id}/events",
            "snapshot_url": f"/api/research/{vjob.job_id}", "parent_events_url": f"/api/research/{job_id}/events"}


@router.post("/{job_id}/programs/{program_id}/decisions")
async def record_decision(job_id: str, program_id: str, body: DecisionRequest, ctx: AppContext = Depends(get_ctx)):
    """Student chooses to continue with an uncertainty. The field stays flagged; the warning stays visible."""
    job = _program_job(ctx, job_id, program_id)
    key = f"{program_id}:{body.field}"
    job.decisions[key] = DecisionRecord(program_id=program_id, field=body.field, decision=body.decision,
                                        at=clock.utc_now())
    job.pending[program_id] = [d for d in job.pending.get(program_id, []) if d.field != body.field]
    p = job.programs[program_id]
    warn = f"You chose to continue although {body.field.replace('_', ' ')} is unverified or conflicting."
    if warn not in p.warnings:
        p.warnings.append(warn)
    return {"recorded": True, "pending_decisions": [d.model_dump(mode="json") for d in job.pending[program_id]]}


@router.post("/{job_id}/shortlist")
async def approve_shortlist(job_id: str, body: ShortlistRequest, ctx: AppContext = Depends(get_ctx)):
    """Human approval of the shortlist. Unlocks checklist deliverable and professor research."""
    job = ctx.store.get(job_id)
    if job.kind != JobKind.programs:
        raise ValidationFailed("Not a program research job")
    if job.status not in FINISHED:
        raise InvalidState("Research is still running; wait for it to finish")
    ids = list(dict.fromkeys(body.program_ids))
    valid = set(job.emitted) | set(job.fallback_results)
    unknown = [i for i in ids if i not in valid]
    if unknown:
        raise ValidationFailed("Unknown program ids for this job", details=[{"program_id": i} for i in unknown])
    pending = [d.model_dump(mode="json") for i in ids for d in job.pending.get(i, [])]
    if pending and not body.acknowledge_uncertainty:
        raise InvalidState("Some selected programs have unresolved 'Needs your decision' items. Resolve them, or set "
                           "acknowledge_uncertainty=true to continue with the uncertainty.",
                           code="needs_decision", details=pending)
    job.shortlist.approved = True
    job.shortlist.program_ids = ids
    job.shortlist.approved_at = clock.utc_now()
    checklists = []
    for i in ids:
        job.checklists[i] = build_checklist(job.programs[i])
        checklists.append(job.checklists[i])
    job.events.append("shortlist_approved", "human", f"Student approved a shortlist of {len(ids)} program(s)",
                      payload={"program_ids": ids})
    return {"shortlist": {"approved": True, "program_ids": ids, "approved_at": job.shortlist.approved_at.isoformat()},
            "checklists": [c.model_dump(mode="json") for c in checklists],
            "combined_checklist": combined_checklist(checklists, job.programs),
            "next_steps": ["Review each checklist item on the official pages linked.",
                           "Optionally research matching professors: POST /api/research/professors."]}


_ = emit_program
