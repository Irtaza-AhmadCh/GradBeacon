from __future__ import annotations

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from app.orchestrator.job_store import Job


def stream_job_events(request: Request, job: Job) -> EventSourceResponse:
    try:
        after = int(request.headers.get("last-event-id") or 0)
    except ValueError:
        after = 0

    async def gen():
        async for ev in job.events.subscribe(after=after):
            if await request.is_disconnected():
                break
            if ev is None:
                yield {"comment": "keepalive"}
                continue
            yield {"id": str(ev.seq), "event": ev.type, "data": ev.model_dump_json()}

    return EventSourceResponse(gen(), ping=15, headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
