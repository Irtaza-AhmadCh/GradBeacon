"""UI <-> API contract: replays the exact requests frontend/app.js builds and asserts every response field the UI
reads is present. Keep in sync with app.js (buildProfile, renderResults, renderProfessor, outreach handlers)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.models.api import APPROVAL_TEXT
from tests.integration.test_agent_loop import PID
from tests.integration.test_api_flow import client_factory, wait_done  # noqa: F401  (pytest fixture)

APP_JS = (Path(__file__).resolve().parents[2] / "frontend" / "app.js").read_text(encoding="utf-8")

# Shape produced by buildProfile() in app.js (every key it sends, optional ones included).
UI_PROFILE = {
    "degree_paths": ["masters"], "field": "Computer Science", "specialization": None,
    "qualification": "BS Computer Science", "qualification_status": "completed",
    "gpa": {"value": 3.4, "scale": "4.0", "context": None}, "destination_country": "US",
    "research_interests": ["natural language processing"], "projects_or_thesis": None, "experience": None,
    "skills": [], "language_test_scores": [{"test": "TOEFL_iBT", "overall": 95}], "preferred_states": [],
    "funding_preference": "prefer_funding", "budget": {"max_tuition_per_year_usd": 30000},
}
# Student dialog payload built in app.js (#student-info-submit-btn).
UI_STUDENT = {"name": "Ayesha Khan", "email": "ayesha@example.com", "current_degree": "BS Computer Science",
              "institution": "Example College", "interests": ["natural language processing"], "achievements": [],
              "message_goal": "ask_about_research_fit"}


def ui_sse_events() -> set[str]:
    block = re.search(r"const SSE_EVENTS = \[(.*?)\];", APP_JS, re.S).group(1)
    return set(re.findall(r"'([a-z_]+)'", block))


def test_ui_profile_keys_match_build_profile():
    body = APP_JS[APP_JS.index("function buildProfile"):]
    body = body[body.index("return {"):body.index("\n  };")]
    ui_keys = set(re.findall(r"^\s{4}([a-z_]+):", body, re.M))
    assert ui_keys <= set(UI_PROFILE), f"app.js sends keys this test does not cover: {ui_keys - set(UI_PROFILE)}"


def test_ui_message_goal_options_are_accepted():
    from app.models.api import StudentFacts
    goals = StudentFacts.model_fields["message_goal"].annotation.__args__
    assert UI_STUDENT["message_goal"] in goals


async def test_full_ui_flow_contract(client_factory):  # noqa: F811
    c, _, _ = client_factory()
    async with c:
        # Research (POST /api/research/programs) -> UI reads events_url + snapshot_url
        r = await c.post("/api/research/programs", json={"profile": UI_PROFILE, "target_results": 8})
        assert r.status_code == 202, r.text
        job = r.json()
        assert {"job_id", "events_url", "snapshot_url"} <= set(job)
        snap = await wait_done(c, job["job_id"])
        assert snap["status"] in ("completed", "partial", "fallback", "failed")  # FINISHED in app.js

        # Results grid: allRows() + renderResults()
        rows = [*(x for g in snap["groups"] for x in g["results"]), *snap["fallback_results"]]
        assert rows
        row = rows[0]
        p, m = row["program"], row["match"]
        for k in ("program_id", "degree_level", "verification_status", "program_name", "institution_name",
                  "official_program_url", "sources", "deadlines", "state", "official_funding_url",
                  "funding_status", "field", "verification_label"):
            assert k in p, f"program.{k} missing (read by renderResults)"
        for k in ("disclaimer", "verified_field_count", "unknowns", "known_gaps", "overall_fit_score"):
            assert k in m, f"match.{k} missing"
        for k in ("academic_fit", "research_fit", "funding_fit", "preference_fit"):  # renderScoreBreakdown
            assert "score" in m[k], f"match.{k}.score missing"
        for s in p["sources"]:
            assert {"title", "domain", "url", "final_url", "fetch_status"} <= set(s)
        assert "pending_decisions" in row

        # SSE stream: every event type the server emits must be one the UI subscribes to
        events = await c.get(job["events_url"])
        emitted = set(re.findall(r"^event: ([a-z_]+)", events.text, re.M))
        assert emitted, "no SSE events"
        assert emitted <= ui_sse_events(), f"UI ignores server events: {emitted - ui_sse_events()}"

        # Decisions dialog + shortlist approval exactly as app.js sends them
        for d in row["pending_decisions"]:
            assert "field" in d  # renderCard uses d.field for the "Needs decision" buttons
            r = await c.post(f"/api/research/{job['job_id']}/programs/{p['program_id']}/decisions",
                             json={"field": d["field"], "decision": "continue_with_uncertainty"})
            assert r.status_code == 200, r.text
        r = await c.post(f"/api/research/{job['job_id']}/shortlist",
                         json={"program_ids": [p["program_id"]], "acknowledge_uncertainty": True})
        assert r.status_code == 200, r.text

        # Professors: UI uses professor_job_id || job_id, events_url, snapshot_url
        r = await c.post("/api/research/professors", json={
            "job_id": job["job_id"], "program_ids": [PID], "research_interests": ["natural language processing"],
            "max_results": 8})
        assert r.status_code == 202, r.text
        pjob = r.json()
        assert {"professor_job_id", "events_url", "snapshot_url"} <= set(pjob)
        psnap = await wait_done(c, pjob["professor_job_id"])
        profs = psnap["professors"]
        assert profs
        for prof in profs:
            for k in ("professor_id", "name", "research_interests", "research_fit_score", "official_email",
                      "official_profile_url", "title", "institution", "research_fit_explanation"):
                assert k in prof, f"professor.{k} missing (read by renderProfessor)"
        prof = next(x for x in profs if x["official_email"])

        # Draft (student dialog) -> openDraft reads recipient_email/subject/body/reminders/automatic_sending_available
        r = await c.post("/api/outreach/draft", json={
            "professor_job_id": pjob["professor_job_id"], "professor_id": prof["professor_id"],
            "program_id": p["program_id"], "student": UI_STUDENT})
        assert r.status_code == 201, r.text
        d = r.json()
        for k in ("draft_id", "recipient_email", "subject", "body", "reminders", "version", "content_hash",
                  "automatic_sending_available"):
            assert k in d, f"draft.{k} missing"

        # Save edits (PUT) then approve the saved hash with the exact sentence the UI prompts for
        r = await c.put(f"/api/outreach/drafts/{d['draft_id']}",
                        json={"subject": d["subject"], "body": d["body"] + "\n\nThank you.",
                              "expected_version": d["version"]})
        assert r.status_code == 200, r.text
        d = r.json()
        assert "I approve sending this email" in APP_JS and APPROVAL_TEXT == "I approve sending this email"
        r = await c.post(f"/api/outreach/drafts/{d['draft_id']}/approve",
                         json={"content_hash": d["content_hash"], "confirm": True, "confirmation_text": APPROVAL_TEXT})
        assert r.status_code == 200, r.text
        appr = r.json()
        assert appr["automatic_sending_available"] is False
        manual = appr["manual_send"]
        assert manual["gmail"].startswith("https://mail.google.com/mail/?view=cm")
        assert manual["mailto"].startswith(f"mailto:{d['recipient_email']}")
        assert json.dumps(manual)  # serialisable for the UI


def test_frontend_dist_matches_source():
    root = Path(__file__).resolve().parents[2] / "frontend"
    for name in ("app.js", "index.html", "styles.css"):
        assert (root / name).read_bytes() == (root / "dist" / name).read_bytes(), f"frontend/dist/{name} is stale"
