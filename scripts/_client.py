"""Shared helpers for CLI scripts (no secrets handled here)."""

from __future__ import annotations

import time

import httpx


def wait_for_job(c: httpx.Client, job_id: str, timeout: float = 300, on_event=None) -> dict:
    seq = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = c.get(f"/api/research/{job_id}", params={"since_seq": seq}).json()
        for e in snap.get("events", []):
            seq = max(seq, e["seq"])
            if on_event:
                on_event(e)
        if snap.get("status") not in ("running", "queued"):
            return c.get(f"/api/research/{job_id}", params={"since_seq": 0}).json()
        time.sleep(1.0)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")
