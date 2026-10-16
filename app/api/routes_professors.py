from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.api.deps import get_ctx
from app.api.rate_limit import limit
from app.core.errors import InvalidState, ValidationFailed
from app.models.api import ProfessorRequest
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import JobKind
from app.orchestrator.professor_workflow import run_professor_workflow

router = APIRouter(prefix="/api/research", tags=["professors"])


@router.post("/professors", status_code=202, dependencies=[Depends(limit("professors"))])
async def start_professor_research(body: ProfessorRequest, ctx: AppContext = Depends(get_ctx)):
    """Research faculty at shortlisted programs. Requires an APPROVED shortlist (human gate)."""
    parent = ctx.store.get(body.job_id)
    if parent.kind != JobKind.programs:
        raise ValidationFailed("job_id must be a program research job")
    if not parent.shortlist.approved:
        raise InvalidState("Approve a shortlist first", code="shortlist_not_approved")
    missing = [p for p in body.program_ids if p not in parent.shortlist.program_ids]
    if missing:
        raise InvalidState("program_ids must be in the approved shortlist", details=[{"program_id": m} for m in missing])
    if not ctx.live:
        raise InvalidState("Live professor research is unavailable on this deployment", code="live_unavailable")
    pjob = ctx.store.create(JobKind.professors, parent_job_id=parent.job_id)
    max_results = min(body.max_results, ctx.settings.max_professors)
    pjob.task = asyncio.create_task(run_professor_workflow(ctx, pjob, parent, body.program_ids,
                                                           body.research_interests, max_results))
    return {"professor_job_id": pjob.job_id, "events_url": f"/api/research/{pjob.job_id}/events",
            "snapshot_url": f"/api/research/{pjob.job_id}", "max_results": max_results}
