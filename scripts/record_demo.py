"""Record a REAL live run to data/demo_replay.json (the clearly labelled cached backup for the demo).

Usage: python scripts/record_demo.py --base-url <live instance> [--profile data/demo_profile.json]
The replay stores workflow events and validated program records only — never the profile.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import wait_for_job  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--profile", default="data/demo_profile.json")
    ap.add_argument("--out", default="data/demo_replay.json")
    a = ap.parse_args()
    profile = json.loads(Path(a.profile).read_text(encoding="utf-8"))
    with httpx.Client(base_url=a.base_url, timeout=60) as c:
        r = c.post("/api/research/programs", json={"profile": profile})
        r.raise_for_status()
        if r.status_code != 202 or r.json().get("source_mode") != "live":
            sys.exit("Refusing to record: the instance did not start a LIVE run (check /health effective_mode).")
        snap = wait_for_job(c, r.json()["job_id"], on_event=lambda e: print(f"  [{e['type']}] {e['summary']}"))
    n = sum(len(g["results"]) for g in snap["groups"])
    if snap["source_mode"] != "live" or n == 0:
        sys.exit(f"Refusing to record: run ended with source_mode={snap['source_mode']} and {n} results.")
    data = {"captured_at": datetime.now(UTC).isoformat(timespec="seconds"), "status": snap["status"],
            "counts": snap["counts"], "warnings": snap["warnings"], "events": snap["events"]}
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Recorded {len(data['events'])} events, {n} results -> {a.out}")


if __name__ == "__main__":
    main()
