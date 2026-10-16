"""Interim 'UI': submit a profile, print the live agent trace and ranked results.

Usage: python scripts/run_research_cli.py --base-url http://localhost:8000 [--profile data/demo_profile.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import wait_for_job  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--profile", default="data/demo_profile.json")
    a = ap.parse_args()
    profile = json.loads(Path(a.profile).read_text(encoding="utf-8"))
    with httpx.Client(base_url=a.base_url, timeout=60) as c:
        r = c.post("/api/research/programs", json={"profile": profile})
        r.raise_for_status()
        body = r.json()
        if r.status_code == 200:
            snap = body
        else:
            print(f"job {body['job_id']} ({body['source_mode']})")
            snap = wait_for_job(c, body["job_id"], on_event=lambda e: print(f"  [{e['type']:<24}] {e['summary']}"))
        print(f"\nSTATUS={snap['status']} SOURCE={snap['source_mode']} STOP={snap.get('stop_reason')}")
        print("COUNTS=", json.dumps(snap.get("counts", {})))
        for w in snap.get("warnings", []):
            print("WARNING:", w)
        for g in snap.get("groups", []):
            print(f"\n== {g['degree_level'].upper()} ==")
            for i, res in enumerate(g["results"], 1):
                p, m = res["program"], res["match"]
                print(f"{i}. {p['program_name']} - {p['institution_name']}  fit={m['overall_fit_score']} "
                      f"({m['coverage']})  [{p['verification_label']}]")
                print(f"   {p['official_program_url']}")
                print("   fields:", p["field_labels"])
                for d in res.get("pending_decisions", []):
                    print(f"   ! NEEDS YOUR DECISION: {d['question']}")


if __name__ == "__main__":
    main()
