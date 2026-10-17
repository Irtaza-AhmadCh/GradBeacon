"""Gemini adapter: thought-signature round trip, RetryInfo waits, and salvage of partially invalid extractions."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.extraction.schemas import ProgramPageExtraction
from app.providers.llm.gemini_provider import GEMINI_CHAT_URL, GeminiLLMProvider, _gemini_retry_after
from app.providers.llm.groq_provider import _groq_messages, _salvage

SIG = {"google": {"thought_signature": "c2ln"}}


@pytest.mark.asyncio
@respx.mock
async def test_thought_signature_is_echoed_back():
    first = {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function", "extra_content": SIG,
         "function": {"name": "search_web", "arguments": '{"query":"mit ee"}'}}]}}], "usage": {}}
    route = respx.post(GEMINI_CHAT_URL).mock(return_value=httpx.Response(200, json=first))
    p = GeminiLLMProvider(["AQ.key"], "gemini-3-flash-preview", "gemini-3-flash-preview", 10)
    turn = await p.research_turn(system="s", tools=[], messages=[{"role": "user", "content": "go"}], effort="low")
    assert turn.raw_content[0]["extra_content"] == SIG
    history = [{"role": "user", "content": "go"}, {"role": "assistant", "content": turn.raw_content},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}]}]
    await p.research_turn(system="s", tools=[], messages=history, effort="low")
    sent = json.loads(route.calls[1].request.content)
    assert sent["messages"][2]["tool_calls"][0]["extra_content"] == SIG
    assert sent["reasoning_effort"] == "low"
    await p.aclose()


def test_groq_history_does_not_echo_extra_content():
    msgs = [{"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "x", "input": {},
                                               "extra_content": SIG}]}]
    assert "extra_content" not in _groq_messages(msgs)[0]["tool_calls"][0]


def test_retry_info_delay_is_used():
    body = [{"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                                  "retryDelay": "17s"}]}}]
    assert _gemini_retry_after(httpx.Response(429, json=body)) == 17.5
    assert _gemini_retry_after(httpx.Response(429, text="x")) == 10.0


def test_salvage_drops_only_the_invalid_fact():
    ev = {"chunk_id": "s#c0", "quote": "Tuition is $21,000 per quarter"}
    fact = {"field": "tuition", "value": "$21,000", "qualifiers": None, "program_name": None, "funding_type": None,
            "applies_to_all_admitted": None, "requires_separate_application": None, "evidence": [ev]}
    data = {"page_kind": "tuition", "scope": "program", "programs": [], "not_found_fields": [],
            "injection_detected": False, "facts": [fact, {**fact, "value": None}, {**fact, "field": "deadline"}]}
    out = _salvage(json.dumps(data), ProgramPageExtraction)
    assert [f.field for f in out.facts] == ["tuition", "deadline"]


def test_salvage_still_rejects_top_level_errors():
    with pytest.raises(Exception):
        _salvage(json.dumps({"page_kind": None, "facts": []}), ProgramPageExtraction)


DAILY_QUOTA = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "72054s"}]}}]
OK = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}], "usage": {}}


@pytest.mark.asyncio
@respx.mock
async def test_daily_quota_falls_over_to_next_model_without_waiting():
    seen: list[tuple[str, str]] = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.headers["authorization"][-1], body["model"]))
        if body["model"] == "primary":
            return httpx.Response(429, json=DAILY_QUOTA)
        return httpx.Response(200, json=OK)

    respx.post(GEMINI_CHAT_URL).mock(side_effect=handler)
    p = GeminiLLMProvider(["AQ.k1", "AQ.k2"], "primary", "primary", 10, fallback_models=["backup"])
    turn = await p.research_turn(system="s", tools=[], messages=[{"role": "user", "content": "go"}], effort="low")
    assert turn.text == "done"
    assert [m for _, m in seen] == ["primary", "primary", "backup"]  # both keys tried once, then no waiting
    assert {k for k, m in seen if m == "primary"} == {"1", "2"}
    # the exhausted model is skipped on later calls instead of being retried
    seen.clear()
    await p.research_turn(system="s", tools=[], messages=[{"role": "user", "content": "go"}], effort="low")
    assert [m for _, m in seen] == ["backup"]
    await p.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_overloaded_model_is_retried_then_falls_over(monkeypatch):
    seen: list[str] = []

    def handler(request):
        model = json.loads(request.content)["model"]
        seen.append(model)
        return httpx.Response(503, json=[{"error": {"code": 503, "status": "UNAVAILABLE"}}]) if model == "primary" \
            else httpx.Response(200, json=OK)

    respx.post(GEMINI_CHAT_URL).mock(side_effect=handler)
    p = GeminiLLMProvider(["AQ.k1", "AQ.k2"], "primary", "primary", 10, fallback_models=["backup"])
    real_cool = p._cool
    monkeypatch.setattr(p, "_cool", lambda index, model, seconds: real_cool(index, model, 0.01))  # keep test fast
    turn = await p.research_turn(system="s", tools=[], messages=[{"role": "user", "content": "go"}], effort="low")
    assert turn.text == "done"
    assert seen == ["primary"] * 4 + ["backup"]  # 2 keys x (first try + 1 transient retry), then next model
    await p.aclose()
