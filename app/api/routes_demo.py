"""Demo replay of a recorded real run (clearly labelled) — the cached backup for the live demo."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.api.deps import get_ctx
from app.api.rate_limit import limit
from app.core.errors import InvalidState
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import JobKind
from app.orchestrator.replay import load_replay, run_replay

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/replay", status_code=202, dependencies=[Depends(limit("replay"))])
async def start_replay(ctx: AppContext = Depends(get_ctx)):
    """Start a replay job from data/demo_replay.json. Works in any APP_MODE; every event is labelled as a replay."""
    data = load_replay(ctx.settings.demo_replay_path)
    if data is None:
        raise InvalidState("No recorded demo run is available (run scripts/record_demo.py first)",
                           code="replay_unavailable")
    job = ctx.store.create(JobKind.programs, count_toward_concurrency=False)
    job.task = asyncio.create_task(run_replay(job, data, ctx.settings.demo_replay_speedup))
    return {"job_id": job.job_id, "status": "running", "source_mode": "cached_replay",
            "captured_at": data["captured_at"], "label": f"Cached demo replay captured {data['captured_at']}",
            "events_url": f"/api/research/{job.job_id}/events", "snapshot_url": f"/api/research/{job.job_id}"}
