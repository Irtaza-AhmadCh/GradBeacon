"""End-to-end API: research -> decisions -> shortlist gate -> professors -> draft -> edit -> approve -> send gate."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.models.api import APPROVAL_TEXT
from tests.conftest import FAKE_KEY, SECRET
from tests.fakes.fakes import BASE, EXTRACTIONS, ROUTES, FakeEmail, FakeFetcher, FakeLLM, FakeSearchProvider, turn
from tests.integration.test_agent_loop import PID, PROFILE

PROF_TURNS = [
    turn(("search_web", {"query": "example state computer science faculty nlp", "limit": 5,
                         "purpose": "discover_faculty", "target_program_id": None, "gap": "faculty"})),
    turn(("fetch_page", {"url": f"{BASE}/people/rivera", "target_program_id": PID, "expected_page_kind": "faculty_profile"}),
         ("fetch_page", {"url": f"{BASE}/people/chen", "target_program_id": PID, "expected_page_kind": "faculty_profile"})),
    turn(("finish_research", {"reason": "sufficient", "unresolved_gaps": []})),
]


def program_turns():
    return [
        turn(("search_web", {"query": "computer science masters", "limit": 5, "purpose": "discover_programs",
                             "target_program_id": None, "gap": None})),
        turn(("fetch_page", {"url": f"{BASE}/cs/ms", "target_program_id": None, "expected_page_kind": "program_overview"})),
        turn(("fetch_page", {"url": f"{BASE}/graduate/tuition", "target_program_id": PID, "expected_page_kind": "tuition"})),
        turn(("finish_research", {"reason": "sufficient", "unresolved_gaps": []})),
    ]


SEARCH = {"computer science masters": [("MS CS", f"{BASE}/cs/ms")],
          "faculty": [("Rivera", f"{BASE}/people/rivera"), ("Chen", f"{BASE}/people/chen")]}


async def wait_done(c, jid, timeout=20):
    for _ in range(int(timeout / 0.05)):
        snap = (await c.get(f"/api/research/{jid}", params={"since_seq": 0})).json()
        if snap["status"] not in ("running", "queued"):
            return snap
        await asyncio.sleep(0.05)
    raise AssertionError("timeout")


@pytest.fixture
def client_factory(build_app):
    def make(email=None, writes=None, extra_turns=None, **over):
        llm = FakeLLM(turns=program_turns() + (extra_turns or PROF_TURNS), extractions=EXTRACTIONS, writes=writes or [])
        app = build_app(llm=llm, search=FakeSearchProvider(SEARCH), fetcher=FakeFetcher(ROUTES), email=email, **over)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t"), app, llm
    return make


async def _research(c):
    r = await c.post("/api/research/programs", json={"profile": PROFILE})
    assert r.status_code == 202
    snap = await wait_done(c, r.json()["job_id"])
    return r.json()["job_id"], snap


async def _to_professors(c):
    jid, snap = await _research(c)
    r = await c.post(f"/api/research/{jid}/shortlist", json={"program_ids": [PID], "acknowledge_uncertainty": True})
    assert r.status_code == 200, r.text
    r = await c.post("/api/research/professors", json={"job_id": jid, "program_ids": [PID],
                                                       "research_interests": ["natural language processing"]})
    assert r.status_code == 202, r.text
    pj = r.json()["professor_job_id"]
    psnap = await wait_done(c, pj)
    return jid, pj, psnap


STUDENT = {"name": "Ayesha Khan", "email": "ayesha@example.com", "current_degree": "BS Computer Science",
           "institution": "Example College", "interests": ["natural language processing"],
           "achievements": ["Built a Urdu sentiment classifier as my final-year project"]}


async def test_shortlist_gate_and_checklist(client_factory):
    c, _, _ = client_factory()
    async with c:
        jid, snap = await _research(c)
        res = snap["groups"][0]["results"][0]
        assert res["pending_decisions"], "funding was not verified -> student decision expected"
        # professors blocked before approval
        r = await c.post("/api/research/professors", json={"job_id": jid, "program_ids": [PID],
                                                           "research_interests": ["nlp"]})
        assert r.status_code == 409 and r.json()["error"]["code"] == "shortlist_not_approved"
        # pending decisions require explicit acknowledgement
        r = await c.post(f"/api/research/{jid}/shortlist", json={"program_ids": [PID]})
        assert r.status_code == 409 and r.json()["error"]["code"] == "needs_decision"
        # student chooses to continue with uncertainty for each pending field
        for d in res["pending_decisions"]:
            r = await c.post(f"/api/research/{jid}/programs/{PID}/decisions",
                             json={"field": d["field"], "decision": "continue_with_uncertainty"})
            assert r.status_code == 200
        r = await c.post(f"/api/research/{jid}/shortlist", json={"program_ids": [PID]})
        assert r.status_code == 200, r.text
        body = r.json()
        items = body["checklists"][0]["items"]
        assert any(i["category"] == "deadline" and i["value"] == "2026-12-15" for i in items)
        assert any(i["label"].startswith("Verify funding") for i in items)
        # every non-verify item with a value has evidence or is the official link
        for i in items:
            if i["category"] not in ("verify", "link") and i["value"]:
                assert i["evidence"], i
        assert body["combined_checklist"]["groups"][0]["program_id"] == PID
        # unknown program id rejected
        r = await c.post(f"/api/research/{jid}/shortlist", json={"program_ids": ["prg_nope"]})
        assert r.status_code == 400


async def test_professors_email_rules(client_factory):
    c, _, _ = client_factory()
    async with c:
        _, pj, psnap = await _to_professors(c)
        profs = {p["name"]: p for p in psnap["professors"]}
        rivera, chen = profs["Jane Rivera"], profs["Sam Chen"]
        assert rivera["official_email"]["value"] == "jrivera@example.edu"
        assert rivera["intake_status"] is not None
        assert rivera["research_fit_score"] == 100
        assert rivera["verified_publications"][0]["title"].startswith("Efficient Transfer Learning")
        # obfuscated email is never de-obfuscated/guessed (FR-36)
        assert chen["official_email"] is None
        assert chen["contact_fallback_url"]
        assert chen["intake_status"] is None and chen["intake_label"].startswith("Not confirmed")
        # drafting for a professor without an official email -> 409 with department link
        r = await c.post("/api/outreach/draft", json={"professor_job_id": pj, "professor_id": chen["professor_id"],
                                                      "student": STUDENT})
        assert r.status_code == 409 and r.json()["error"]["code"] == "no_official_email"


async def test_outreach_requires_review_and_approval_send_deferred(client_factory):
    c, _, _ = client_factory()  # default: email sending NOT implemented
    async with c:
        _, pj, psnap = await _to_professors(c)
        prof = next(p for p in psnap["professors"] if p["name"] == "Jane Rivera")
        r = await c.post("/api/outreach/draft", json={"professor_job_id": pj, "professor_id": prof["professor_id"],
                                                      "student": STUDENT})
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["status"] == "awaiting_review" and d["generated_by"] == "template"  # LLM write failed -> template
        assert d["recipient_email"] == "jrivera@example.edu"
        assert prof["official_profile_url"] in d["body"]
        assert any("approve" in x for x in d["reminders"])
        # sending before approval is impossible
        r = await c.post("/api/outreach/send", json={"draft_id": d["draft_id"], "approval_token": "x" * 20})
        assert r.status_code == 409
        # wrong confirmation text rejected
        r = await c.post(f"/api/outreach/drafts/{d['draft_id']}/approve",
                         json={"content_hash": d["content_hash"], "confirm": True, "confirmation_text": "ok"})
        assert r.status_code == 400
        # student edits -> new hash; approving the OLD hash fails
        r = await c.put(f"/api/outreach/drafts/{d['draft_id']}",
                        json={"subject": d["subject"], "body": d["body"] + "\n\nP.S. Thank you!", "expected_version": 1})
        assert r.status_code == 200
        d2 = r.json()
        assert d2["version"] == 2 and d2["content_hash"] != d["content_hash"]
        r = await c.post(f"/api/outreach/drafts/{d['draft_id']}/approve",
                         json={"content_hash": d["content_hash"], "confirm": True, "confirmation_text": APPROVAL_TEXT})
        assert r.status_code == 409 and r.json()["error"]["code"] == "draft_changed"
        # HTML rejected in edits; recipient is not an editable field
        r = await c.put(f"/api/outreach/drafts/{d['draft_id']}",
                        json={"subject": "x", "body": "<script>x</script>", "expected_version": 2})
        assert r.status_code == 400
        r = await c.put(f"/api/outreach/drafts/{d['draft_id']}",
                        json={"subject": "x", "body": "y", "expected_version": 2, "recipient_email": "a@b.edu"})
        assert r.status_code == 400
        # explicit approval of exact text
        r = await c.post(f"/api/outreach/drafts/{d['draft_id']}/approve",
                         json={"content_hash": d2["content_hash"], "confirm": True, "confirmation_text": APPROVAL_TEXT})
        assert r.status_code == 200, r.text
        appr = r.json()
        assert appr["delivery"] == "manual" and appr["automatic_sending_available"] is False
        assert appr["manual_send"]["mailto"].startswith("mailto:jrivera@example.edu?subject=")
        # automatic send is not implemented -> 503, and the approval is NOT consumed
        r = await c.post("/api/outreach/send", json={"draft_id": d["draft_id"], "approval_token": appr["approval_token"]})
        assert r.status_code == 503 and r.json()["error"]["code"] == "email_not_configured"
        r = await c.get(f"/api/outreach/drafts/{d['draft_id']}")
        assert r.json()["approval"]["consumed"] is False and r.json()["status"] == "approved"


async def test_send_exactly_once_with_configured_provider(client_factory):
    email = FakeEmail(ok=True)
    c, _, _ = client_factory(email=email)
    async with c:
        _, pj, psnap = await _to_professors(c)
        prof = next(p for p in psnap["professors"] if p["name"] == "Jane Rivera")
        d = (await c.post("/api/outreach/draft", json={"professor_job_id": pj, "professor_id": prof["professor_id"],
                                                       "student": STUDENT})).json()
        appr = (await c.post(f"/api/outreach/drafts/{d['draft_id']}/approve",
                             json={"content_hash": d["content_hash"], "confirm": True,
                                   "confirmation_text": APPROVAL_TEXT})).json()
        assert appr["delivery"] == "live"
        r = await c.post("/api/outreach/send", json={"draft_id": d["draft_id"], "approval_token": appr["approval_token"]})
        assert r.status_code == 200 and r.json()["status"] == "sent"
        assert len(email.sent) == 1
        sent = email.sent[0]
        assert sent.to == "jrivera@example.edu" and sent.subject == d["subject"] and sent.text == d["body"]
        # replaying the same token never sends twice
        r = await c.post("/api/outreach/send", json={"draft_id": d["draft_id"], "approval_token": appr["approval_token"]})
        assert r.status_code == 409
        assert len(email.sent) == 1


async def test_send_failure_requires_reapproval(client_factory):
    email = FakeEmail(ok=False)
    c, _, _ = client_factory(email=email, rate_send_per_hour=10)
    async with c:
        _, pj, psnap = await _to_professors(c)
        prof = next(p for p in psnap["professors"] if p["name"] == "Jane Rivera")
        d = (await c.post("/api/outreach/draft", json={"professor_job_id": pj, "professor_id": prof["professor_id"],
                                                       "student": STUDENT})).json()
        appr = (await c.post(f"/api/outreach/drafts/{d['draft_id']}/approve",
                             json={"content_hash": d["content_hash"], "confirm": True,
                                   "confirmation_text": APPROVAL_TEXT})).json()
        r = await c.post("/api/outreach/send", json={"draft_id": d["draft_id"], "approval_token": appr["approval_token"]})
        assert r.status_code == 200 and r.json()["status"] == "failed"
        assert "Approve again" in r.json()["send_result"]["message"]
        r = await c.post("/api/outreach/send", json={"draft_id": d["draft_id"], "approval_token": appr["approval_token"]})
        assert r.status_code == 409
        assert len(email.sent) == 1  # no automatic retry
        # tampered token rejected
        bad = appr["approval_token"][:-3] + "abc"
        appr2 = (await c.post(f"/api/outreach/drafts/{d['draft_id']}/approve",
                              json={"content_hash": d["content_hash"], "confirm": True,
                                    "confirmation_text": APPROVAL_TEXT})).json()
        r = await c.post("/api/outreach/send", json={"draft_id": d["draft_id"], "approval_token": bad})
        assert r.status_code == 400
        r = await c.post("/api/outreach/send", json={"draft_id": d["draft_id"], "approval_token": appr2["approval_token"]})
        assert r.status_code == 200 and len(email.sent) == 2


async def test_llm_draft_guard_falls_back_to_template(client_factory):
    invented = {"subject": "Hello", "body": "As we discussed at NeurIPS 2024, I published 3 papers. https://evil.com"}
    c, _, _ = client_factory(writes=[invented])
    async with c:
        _, pj, psnap = await _to_professors(c)
        prof = next(p for p in psnap["professors"] if p["name"] == "Jane Rivera")
        d = (await c.post("/api/outreach/draft", json={"professor_job_id": pj, "professor_id": prof["professor_id"],
                                                       "student": STUDENT})).json()
        assert d["generated_by"] == "template"
        assert "NeurIPS" not in d["body"] and "evil.com" not in d["body"]


async def test_fallback_only_mode_is_labelled(build_app):
    app = build_app(mode="fallback_only")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/research/programs", json={"profile": PROFILE})
        assert r.status_code == 200
        snap = r.json()
        assert snap["source_mode"] == "curated_fallback" and snap["status"] == "fallback"
        prog = snap["groups"][0]["results"][0]["program"]
        assert prog["fallback_record"] is True and prog["verification_status"] == "fallback"
        assert "Curated fallback" in prog["verification_label"]
        assert prog["last_verified_at"]
        assert any("not freshly verified" in w for w in snap["warnings"])
        assert any(e["type"] == "fallback_used" for e in snap["events"])
        h = (await c.get("/health")).json()
        assert h["effective_mode"] == "fallback_only"


async def test_search_auth_failure_falls_back(build_app):
    from app.models.common import ErrorCategory
    llm = FakeLLM(turns=[turn(("search_web", {"query": "computer science masters", "limit": 5,
                                              "purpose": "discover_programs", "target_program_id": None, "gap": None}))],
                  extractions=EXTRACTIONS)
    app = build_app(llm=llm, search=FakeSearchProvider(error=ErrorCategory.provider_auth))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/research/programs", json={"profile": PROFILE})
        snap = await wait_done(c, r.json()["job_id"])
        assert snap["source_mode"] == "curated_fallback"
        assert any(e["type"] == "fallback_used" for e in snap["events"])
        text = str(snap)
        assert "tvly-" not in text and FAKE_KEY not in text


async def test_llm_failure_before_results_falls_back(build_app):
    from app.core.errors import ProviderError
    from app.models.common import ErrorCategory
    llm = FakeLLM(turns=[ProviderError(ErrorCategory.provider_auth, "bad key")], extractions=EXTRACTIONS)
    app = build_app(llm=llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/research/programs", json={"profile": PROFILE})
        snap = await wait_done(c, r.json()["job_id"])
        assert snap["status"] == "fallback"


async def test_sse_stream_ends_with_done(client_factory):
    c, _, _ = client_factory()
    async with c:
        jid, _ = await _research(c)
        async with c.stream("GET", f"/api/research/{jid}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
                if "event: done" in body:
                    break
        assert "event: job_started" in body and "event: program" in body and "event: done" in body
        # Last-Event-ID resumes after a sequence number
        async with c.stream("GET", f"/api/research/{jid}/events", headers={"Last-Event-ID": "2"}) as resp:
            body2 = ""
            async for chunk in resp.aiter_text():
                body2 += chunk
                if "event: done" in body2:
                    break
        assert "id: 1\r\n" not in body2 and "id: 1\n" not in body2


async def test_errors_and_security(build_app):
    app = build_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/research/programs", json={"profile": {"degree_paths": []}})
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"
        assert "X-Request-ID" in r.headers
        r = await c.post("/api/research/programs", content=b"x" * 40000, headers={"content-type": "application/json"})
        assert r.status_code == 413
        r = await c.get("/api/research/job_0123456789abcdef")
        assert r.status_code == 410
        r = await c.get("/api/research/garbage")
        assert r.status_code == 404
        bad = dict(PROFILE, gpa={"value": 4.5, "scale": "4.0"})
        r = await c.post("/api/research/programs", json={"profile": bad})
        assert r.status_code == 400
        r = await c.post("/api/research/programs", json={"profile": dict(PROFILE, preferred_states=["XX"])})
        assert r.status_code == 400
        h = await c.get("/health")
        txt = h.text
        assert FAKE_KEY not in txt and SECRET not in txt and "tvly-fake" not in txt
        assert h.json()["providers"]["llm"] == "configured"


async def test_rate_limit_and_busy(build_app):
    app = build_app(rate_research_per_10min=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/research/programs", json={"profile": PROFILE})
        r = await c.post("/api/research/programs", json={"profile": PROFILE})
        assert r.status_code == 429 and "Retry-After" in r.headers


async def test_verify_merges_into_parent(build_app):
    turns = program_turns()[:2] + [turn(("finish_research", {"reason": "sufficient", "unresolved_gaps": []}))] + [
        # verify loop for funding:
        turn(("fetch_page", {"url": f"{BASE}/cs/funding", "target_program_id": PID, "expected_page_kind": "funding"})),
        turn(("finish_research", {"reason": "sufficient", "unresolved_gaps": []})),
    ]
    llm = FakeLLM(turns=turns, extractions=EXTRACTIONS)
    app = build_app(llm=llm, search=FakeSearchProvider(SEARCH))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        jid, snap = await _research(c)
        res = snap["groups"][0]["results"][0]
        assert res["program"]["funding_status"] != "verified"
        sugg = next(d for d in res["pending_decisions"] if d["field"] == "funding")["suggested_sources"]
        assert f"{BASE}/cs/funding" in sugg
        r = await c.post(f"/api/research/{jid}/programs/{PID}/verify",
                         json={"field": "funding", "source_url": f"{BASE}/cs/funding", "profile": PROFILE})
        assert r.status_code == 202, r.text
        await wait_done(c, r.json()["verify_job_id"])
        snap2 = (await c.get(f"/api/research/{jid}", params={"since_seq": 0})).json()
        prog = snap2["groups"][0]["results"][0]["program"]
        assert prog["funding_status"] == "verified"
        assert any(e["type"] == "program_updated" for e in snap2["events"])
        # source_url must be known/official
        r = await c.post(f"/api/research/{jid}/programs/{PID}/verify",
                         json={"field": "tuition", "source_url": "https://random.edu/x"})
        assert r.status_code == 400
