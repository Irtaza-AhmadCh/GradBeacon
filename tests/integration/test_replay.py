from __future__ import annotations

import json

import httpx

from tests.fakes.fakes import BASE, FakeSearchProvider
from tests.integration.test_agent_loop import PID, PROFILE, _run, scripted_llm
from tests.integration.test_api_flow import wait_done


async def test_demo_replay_is_labelled_and_usable(build_app, tmp_path):
    app = build_app(llm=scripted_llm(), search=FakeSearchProvider({"computer science": [("MS", f"{BASE}/cs/ms")]}))
    snap = await _run(app)
    path = tmp_path / "replay.json"
    path.write_text(json.dumps({"captured_at": "2026-10-17T09:00:00+00:00", "status": snap["status"],
                                "counts": snap["counts"], "events": snap["events"]}), encoding="utf-8")
    app2 = build_app(mode="fallback_only", demo_replay_path=str(path), demo_replay_speedup=1000)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://t") as c:
        assert (await c.get("/health")).json()["demo_replay_available"] is True
        r = await c.post("/api/demo/replay")
        assert r.status_code == 202 and "Cached demo replay captured 2026-10-17" in r.json()["label"]
        rs = await wait_done(c, r.json()["job_id"])
        assert rs["source_mode"] == "cached_replay" and rs["source_mode_label"] == "Cached demo replay"
        assert any("Cached demo replay captured" in w for w in rs["warnings"])
        assert all(e["summary"].startswith("[Replay]") for e in rs["events"] if e["type"] != "done")
        assert rs["groups"][0]["results"][0]["program"]["data_freshness"] == "cached_replay"
        # shortlist approval still works on replayed records (human gate demo)
        r = await c.post(f"/api/research/{rs['job_id']}/shortlist",
                         json={"program_ids": [PID], "acknowledge_uncertainty": True})
        assert r.status_code == 200
    # APP_MODE=demo_replay routes the normal research endpoint to the replay
    app3 = build_app(app_mode="demo_replay", demo_replay_path=str(path), demo_replay_speedup=1000)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app3), base_url="http://t") as c:
        r = await c.post("/api/research/programs", json={"profile": PROFILE})
        assert r.status_code == 202 and r.json()["source_mode"] == "cached_replay"


async def test_replay_unavailable(build_app, tmp_path):
    app = build_app(demo_replay_path=str(tmp_path / "missing.json"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/demo/replay")
        assert r.status_code == 409 and r.json()["error"]["code"] == "replay_unavailable"
