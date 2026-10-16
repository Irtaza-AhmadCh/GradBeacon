"""Smoke-test deployed instances (primary + backup). Prints PASS/FAIL lines; never prints secrets.

Usage: python scripts/smoke_test.py --base-url https://primary... --base-url https://backup... [--live]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import wait_for_job  # noqa: E402

SECRET_PATTERNS = ("sk-ant-", "tvly-", "APPROVAL_TOKEN_SECRET")


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' - ' + detail if detail else ''}")
    return ok


def smoke(base: str, live: bool) -> bool:
    ok = True
    print(f"\n=== {base} ===")
    with httpx.Client(base_url=base, timeout=60) as c:
        h = c.get("/health")
        ok &= check("health 200", h.status_code == 200)
        hj = h.json() if h.status_code == 200 else {}
        prov = hj.get("providers", {})
        ok &= check("no secrets in /health", not any(p in h.text for p in SECRET_PATTERNS))
        ok &= check("effective_mode live", hj.get("effective_mode") == "live", str(hj.get("effective_mode")))
        ok &= check("LLM + search keys configured", prov.get("llm") == "configured" and prov.get("search") == "configured",
                    json.dumps(prov))
        ok &= check("approval signing configured", prov.get("approval_signing") == "configured")
        ok &= check("curated fallback loaded", (hj.get("curated_records") or 0) > 0, f"{hj.get('curated_records')} records")
        check("demo replay available (optional)", bool(hj.get("demo_replay_available")))
        r = c.post("/api/research/programs", json={"profile": {"degree_paths": []}})
        ok &= check("invalid profile -> 400", r.status_code == 400)
        ok &= check("error has no traceback", "Traceback" not in r.text)
        r = c.get("/api/research/job_0123456789abcdef")
        ok &= check("unknown/expired job -> 410", r.status_code == 410)
        if live:
            profile = json.loads(Path("data/demo_profile.json").read_text(encoding="utf-8"))
            t0 = time.monotonic()
            r = c.post("/api/research/programs", json={"profile": profile})
            ok &= check("research accepted", r.status_code in (200, 202), r.text[:200])
            if r.status_code == 202:
                snap = wait_for_job(c, r.json()["job_id"])
                n = sum(len(g["results"]) for g in snap["groups"])
                first = snap["counts"].get("first_result_ms")
                ok &= check("results returned", n > 0, f"{n} results, source={snap['source_mode']}")
                check("first validated result <= 60s", first is not None and first <= 60000, f"first_result_ms={first}")
                gaps = [e for e in snap["events"] if e["type"] == "gap_selected"]
                check("agent made a gap-driven follow-up", bool(gaps), f"{len(gaps)} gap_selected events")
                ok &= check("no secrets in snapshot", not any(p in json.dumps(snap) for p in SECRET_PATTERNS))
                print(f"     total {time.monotonic() - t0:.0f}s; counts={json.dumps(snap['counts'])}")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", action="append", required=True)
    ap.add_argument("--live", action="store_true", help="also run one live research job (uses API credits)")
    a = ap.parse_args()
    results = [smoke(b.rstrip("/"), a.live) for b in a.base_url]
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
