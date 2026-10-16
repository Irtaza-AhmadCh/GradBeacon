"""DemoReplay — replays a RECORDED real run, clearly labelled with its original capture time (agent spec §17)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from app.core import clock
from app.models.common import DataFreshness, SourceMode
from app.models.program import ProgramOpportunity
from app.models.results import Checklist, Explanation, MatchResult, PendingDecision
from app.orchestrator.job_store import Job, JobStatus

log = logging.getLogger("gradbeacon.replay")


def load_replay(path: str | Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("events") or not data.get("captured_at"):
        return None
    return data


def _restore_program(job: Job, payload: dict) -> str | None:
    try:
        p = ProgramOpportunity.model_validate(payload["program"])
    except Exception:  # noqa: BLE001
        return None
    p.data_freshness = DataFreshness.cached_replay
    pid = p.program_id
    job.programs[pid] = p
    if payload.get("match"):
        job.matches[pid] = MatchResult.model_validate(payload["match"])
    if payload.get("checklist"):
        job.checklists[pid] = Checklist.model_validate(payload["checklist"])
    if payload.get("explanation"):
        job.explanations[pid] = Explanation.model_validate(payload["explanation"])
    job.pending[pid] = [PendingDecision.model_validate(d) for d in payload.get("pending_decisions", [])]
    return pid


async def run_replay(job: Job, data: dict, speedup: float) -> None:
    captured = data["captured_at"]
    label = f"Cached demo replay captured {captured} — not a live run"
    job.status = JobStatus.running
    job.source_mode = SourceMode.cached_replay
    job.replay_captured_at = captured
    job.warn(label)
    job.research_finished = True
    events = data["events"]
    prev_ts: datetime | None = None
    try:
        for raw in events:
            ts = datetime.fromisoformat(raw["ts"]) if raw.get("ts") else None
            if prev_ts and ts:
                await asyncio.sleep(max(0.0, min(3.0, (ts - prev_ts).total_seconds() / max(speedup, 1.0))))
            prev_ts = ts or prev_ts
            payload = dict(raw.get("payload") or {})
            if raw["type"] in ("program", "program_updated") and "program" in payload:
                pid = _restore_program(job, payload)
                if pid and pid not in job.emitted and raw["type"] == "program":
                    job.emitted.append(pid)
            payload["replay"] = {"captured_at": captured}
            if raw["type"] == "done":
                continue
            job.events.append(raw["type"], raw.get("stage", "replay"), "[Replay] " + raw.get("summary", ""),
                              tool=raw.get("tool"), counts=raw.get("counts"), duration_ms=raw.get("duration_ms"),
                              payload=payload)
        job.counts.update(data.get("counts") or {})
        job.status = JobStatus.completed
        job.stop_reason = "replay_complete"
    except Exception:  # noqa: BLE001
        log.exception("replay_failed")
        job.status = JobStatus.failed
    finally:
        job.finished_at = clock.utc_now()
        job.events.append("done", "done", f"Replay finished — {len(job.emitted)} program record(s). {label}",
                          payload={"status": job.status.value, "source_mode": "cached_replay",
                                   "replay": {"captured_at": captured}})
