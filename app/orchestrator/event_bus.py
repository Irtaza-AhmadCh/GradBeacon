"""JobEventLog — real backend workflow events (agent spec §13). Summaries are code-generated."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from app.core import clock
from app.models.events import RESULT_TYPES, WorkflowEvent

log = logging.getLogger("gradbeacon.events")
MAX_EVENTS = 2000
_KEEP = RESULT_TYPES | {"done", "fallback_used", "warning"}


class JobEventLog:
    def __init__(self, job_id: str, mode: str):
        self.job_id = job_id
        self.mode = mode
        self.events: list[WorkflowEvent] = []
        self._seq = 0
        self._cond = asyncio.Condition()
        self.closed = False

    def append(self, type: str, stage: str, summary: str, **fields) -> WorkflowEvent:
        self._seq += 1
        ev = WorkflowEvent(seq=self._seq, ts=clock.utc_now(), job_id=self.job_id, mode=self.mode, type=type,
                           stage=stage, summary=summary[:300], **fields)
        self.events.append(ev)
        if len(self.events) > MAX_EVENTS:
            for i, e in enumerate(self.events):
                if e.type not in _KEEP:
                    del self.events[i]
                    break
        if type == "done":
            self.closed = True
        log.info("workflow_event", extra={"job_id": self.job_id, "event": type, "stage": stage, "tool": ev.tool,
                                          "counts": ev.counts, "duration_ms": ev.duration_ms,
                                          "error_category": ev.error_category})
        self._notify()
        return ev

    def _notify(self) -> None:
        async def _n():
            async with self._cond:
                self._cond.notify_all()
        try:
            asyncio.get_running_loop().create_task(_n())
        except RuntimeError:
            pass

    def since(self, seq: int) -> list[WorkflowEvent]:
        return [e for e in self.events if e.seq > seq]

    @property
    def last_seq(self) -> int:
        return self._seq

    async def subscribe(self, after: int = 0, keepalive: float = 15.0) -> AsyncIterator[WorkflowEvent | None]:
        """Yields events after `after`; yields None as a keepalive tick. Ends after 'done'."""
        cursor = after
        while True:
            new = self.since(cursor)
            for e in new:
                cursor = e.seq
                yield e
                if e.type == "done":
                    return
            if self.closed and not self.since(cursor):
                return
            async with self._cond:
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=keepalive)
                except TimeoutError:
                    yield None
