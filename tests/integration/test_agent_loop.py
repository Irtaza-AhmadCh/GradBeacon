"""Agent behaviour (agent spec §14.2) with a scripted LLM over real pipeline code (fetch->extract->validate->merge)."""

from __future__ import annotations

import asyncio
import json

import httpx

from app.core.ids import stable_program_id
from tests.fakes.fakes import BASE, EXTRACTIONS, ROUTES, FakeFetcher, FakeLLM, FakeSearchProvider, turn

PID = stable_program_id("Example State University", "Master of Science in Computer Science", "masters")
PROFILE = {"degree_paths": ["masters"], "field": "Computer Science", "qualification": "BS Computer Science",
           "qualification_status": "completed", "gpa": {"value": 3.4, "scale": "4.0"},
           "research_interests": ["natural language processing"], "funding_preference": "prefer_funding",
           "language_test_scores": [{"test": "TOEFL_iBT", "overall": 95}],
           "budget": {"max_tuition_per_year_usd": 30000}}


def _state_from(messages) -> dict:
    last = messages[-1]["content"]
    text = last if isinstance(last, str) else next(b["text"] for b in last if b.get("type") == "text")
    return json.loads(text.split("\n", 1)[1])


async def _run(app, profile=PROFILE, timeout=20.0) -> dict:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/research/programs", json={"profile": profile})
        assert r.status_code in (200, 202), r.text
        if r.status_code == 200:
            return r.json()
        jid = r.json()["job_id"]
        for _ in range(int(timeout / 0.05)):
            snap = (await c.get(f"/api/research/{jid}", params={"since_seq": 0})).json()
            if snap["status"] not in ("running", "queued"):
                return snap
            await asyncio.sleep(0.05)
        raise AssertionError("job did not finish")


def scripted_llm() -> FakeLLM:
    seen = {}

    def t3(messages):
        state = _state_from(messages)
        prog = next(p for p in state["programs"] if p["program_id"] == PID)
        seen["gaps_turn3"] = prog["open_gaps"]
        assert "tuition" in prog["open_gaps"]  # agent observes the missing evidence
        link = next(lk["url"] for lk in state["useful_links"] if "tuition" in lk["url"])
        return turn(("fetch_page", {"url": link, "target_program_id": PID, "expected_page_kind": "tuition"}))

    llm = FakeLLM(turns=[
        turn(("search_web", {"query": "computer science masters program", "limit": 5, "purpose": "discover_programs",
                             "target_program_id": None, "gap": None})),
        turn(("fetch_page", {"url": f"{BASE}/cs/ms", "target_program_id": None,
                             "expected_page_kind": "program_overview"})),
        t3,
        # Forbidden tool + a parallel legitimate call in the same turn:
        turn(("send_email", {"to": "x@example.edu"}),
             ("fetch_page", {"url": f"{BASE}/cs/funding", "target_program_id": PID, "expected_page_kind": "funding"})),
        turn(("finish_research", {"reason": "sufficient", "unresolved_gaps": []})),
    ], extractions=EXTRACTIONS)
    llm.seen = seen
    return llm


async def test_gap_driven_followup_and_safety(build_app):
    llm = scripted_llm()
    fetcher = FakeFetcher(ROUTES)
    search = FakeSearchProvider({"computer science": [("MS CS", f"{BASE}/cs/ms")]})
    app = build_app(llm=llm, fetcher=fetcher, search=search)
    snap = await _run(app)

    assert snap["status"] == "completed", snap["warnings"]
    # 3. the follow-up tool was actually executed
    assert f"{BASE}/graduate/tuition" in fetcher.fetched
    # 4. returned evidence changed the research state
    res = snap["groups"][0]["results"][0]
    prog = res["program"]
    assert prog["program_id"] == PID
    assert prog["tuition_status"] == "verified"
    assert prog["tuition"][0]["amount"] == 24000
    assert prog["funding_status"] == "verified"
    # funding existence never confirms the student's eligibility (FR-17)
    assert all(f["student_eligibility_status"] != "confirmed" for f in prog["funding"])
    # fabricated $9,999 claim was rejected by the evidence validator (FR-29)
    assert all(t["amount"] != 9999 for t in prog["tuition"])
    assert snap["counts"]["rejected_claims"] >= 1
    # 5. stopped when sufficient
    assert "model_finished:sufficient" in snap["stop_reason"]
    # 6. forbidden tool rejected, never executed
    types = [e["type"] for e in snap["events"]]
    assert "tool_rejected" in types
    assert any("send_email" in e["summary"] for e in snap["events"] if e["type"] == "tool_rejected")
    # Real trace events, progressive results, explanation update, done
    for t in ("job_started", "agent_turn", "search_completed", "page_fetched", "facts_extracted", "program", "done"):
        assert t in types, t
    # Score is explainable and labelled as not an admission probability
    m = res["match"]
    assert m["disclaimer"] == "Profile-fit score, not an admission probability."
    assert m["overall_fit_score"] is not None
    assert m["academic_fit"]["score"] == 100  # GPA 3.4>=3.0 and TOEFL 95>=90
    assert res["checklist"]["items"][0]["category"] == "link"


async def test_parallel_results_single_message_and_append_only(build_app):
    llm = scripted_llm()
    app = build_app(llm=llm, search=FakeSearchProvider({"computer science": [("MS CS", f"{BASE}/cs/ms")]}))
    await _run(app)
    # Turn 5 sees history: the parallel turn's two tool_results are in ONE user message, in order.
    final_msgs = llm.research_calls[-1]
    user_msgs = [m for m in final_msgs if m["role"] == "user" and isinstance(m["content"], list)]
    parallel = user_msgs[-1]["content"]
    results = [b for b in parallel if b.get("type") == "tool_result"]
    assert len(results) == 2
    assert results[0]["is_error"] is True and results[1]["is_error"] is False
    # Append-only: each earlier request's messages are a prefix of the next request's messages
    for a, b in zip(llm.research_calls, llm.research_calls[1:], strict=False):
        assert b[: len(a)] == a
    # Assistant content appended unchanged
    assistants = [m for m in final_msgs if m["role"] == "assistant"]
    assert assistants[0]["content"][0]["name"] == "search_web"


async def test_loop_stops_at_turn_budget(build_app):
    calls = [turn(("retrieve_evidence", {"query": f"q{i}", "program_id": None, "top_k": 3})) for i in range(50)]
    llm = FakeLLM(turns=calls, extractions=EXTRACTIONS)
    app = build_app(llm=llm, max_loop_turns=4)
    snap = await _run(app)
    assert "budget" in snap["stop_reason"]
    assert len(llm.research_calls) == 4
    assert snap["status"] == "partial"
    assert any("budget" in w or "limit" in w for w in snap["warnings"])


async def test_followup_failure_reports_uncertainty(build_app):
    from app.models.common import ErrorCategory
    llm = FakeLLM(turns=[
        turn(("search_web", {"query": "computer science masters", "limit": 5, "purpose": "discover_programs",
                             "target_program_id": None, "gap": None})),
        turn(("fetch_page", {"url": f"{BASE}/cs/ms", "target_program_id": None, "expected_page_kind": "program_overview"})),
        turn(("fetch_page", {"url": f"{BASE}/graduate/tuition", "target_program_id": PID, "expected_page_kind": "tuition"})),
        turn(("finish_research", {"reason": "no_more_leads", "unresolved_gaps": ["tuition"]})),
    ], extractions=EXTRACTIONS)
    fetcher = FakeFetcher(ROUTES, failures={f"{BASE}/graduate/tuition": ErrorCategory.fetch_timeout})
    app = build_app(llm=llm, fetcher=fetcher,
                    search=FakeSearchProvider({"computer science": [("MS CS", f"{BASE}/cs/ms")]}))
    snap = await _run(app)
    prog = snap["groups"][0]["results"][0]["program"]
    assert prog["tuition"] == []  # nothing fabricated
    assert prog["tuition_status"] in ("unavailable", "not_found")
    assert any(e["type"] == "page_failed" for e in snap["events"])
    # Needs-your-decision appears for the unresolved critical field after research finished
    fields = {d["field"] for d in snap["groups"][0]["results"][0]["pending_decisions"]}
    assert "tuition" in fields


async def test_conflicting_sources_flagged_not_guessed(build_app):
    llm = FakeLLM(turns=[
        turn(("search_web", {"query": "computer science masters", "limit": 5, "purpose": "discover_programs",
                             "target_program_id": None, "gap": None})),
        turn(("fetch_page", {"url": f"{BASE}/cs/ms", "target_program_id": None, "expected_page_kind": "program_overview"})),
        turn(("search_web", {"query": "example state graduate deadlines", "limit": 3, "purpose": "fill_gap",
                             "target_program_id": PID, "gap": "deadline"})),
        turn(("fetch_page", {"url": f"{BASE}/graduate/deadlines", "target_program_id": PID,
                             "expected_page_kind": "deadlines"})),
        turn(("finish_research", {"reason": "sufficient", "unresolved_gaps": []})),
    ], extractions=EXTRACTIONS)
    search = FakeSearchProvider({"computer science": [("MS CS", f"{BASE}/cs/ms")],
                                 "deadlines": [("Deadlines", f"{BASE}/graduate/deadlines")]})
    app = build_app(llm=llm, search=search)
    snap = await _run(app)
    res = snap["groups"][0]["results"][0]
    assert res["program"]["deadline_status"] == "conflicting"
    conflict = next(d for d in res["pending_decisions"] if d["field"] == "deadline")
    assert conflict["kind"] == "conflict"
    values = {c["value"] for c in conflict["competing_claims"]}
    assert {"December 15, 2026", "January 5, 2027"} <= values
    assert res["program"]["verification_status"] == "needs_verification"


async def test_prompt_injection_cannot_expand_tools(build_app):
    llm = FakeLLM(turns=[
        turn(("search_web", {"query": "computer science masters", "limit": 5, "purpose": "discover_programs",
                             "target_program_id": None, "gap": None})),
        turn(("fetch_page", {"url": f"{BASE}/news", "target_program_id": None, "expected_page_kind": "other"})),
        turn(("finish_research", {"reason": "no_more_leads", "unresolved_gaps": []})),
    ], extractions=EXTRACTIONS)
    app = build_app(llm=llm, search=FakeSearchProvider({"computer science": [("News", f"{BASE}/news")]}))
    snap = await _run(app)
    types = [e["type"] for e in snap["events"]]
    assert "prompt_injection_flagged" in types
    assert llm.last_tools and {t["name"] for t in llm.last_tools} == {"search_web", "fetch_page", "retrieve_evidence",
                                                                    "finish_research"}
    assert snap["groups"] == []  # honest empty state, no padding
    assert any("No credible programs" in w for w in snap["warnings"])


async def test_fetch_permission_and_domain_policy(build_app):
    llm = FakeLLM(turns=[
        turn(("fetch_page", {"url": f"{BASE}/cs/ms", "target_program_id": None, "expected_page_kind": "program_overview"})),
        turn(("fetch_page", {"url": "https://evil.example.com/page", "target_program_id": None,
                             "expected_page_kind": "other"})),
        turn(("search_web", {"query": "cs programs", "limit": 3, "purpose": "discover_programs", "target_program_id": "nope",
                             "gap": None})),
        turn(("finish_research", {"reason": "no_more_leads", "unresolved_gaps": []})),
    ], extractions=EXTRACTIONS)
    fetcher = FakeFetcher(ROUTES)
    app = build_app(llm=llm, fetcher=fetcher)
    snap = await _run(app)
    rejected = [e["summary"] for e in snap["events"] if e["type"] == "tool_rejected"]
    assert any("must come from a search result" in r for r in rejected)
    assert any("allowed official domain" in r for r in rejected)
    assert any("target_program_id" in r for r in rejected)
    assert fetcher.fetched == []  # nothing fetched without permission
