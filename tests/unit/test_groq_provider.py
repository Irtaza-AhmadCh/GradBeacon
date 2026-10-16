"""Groq adapter regressions: optional tool fields, tool_use_failed retry, retry-after waits, history compaction."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.agent.tool_specs import RESEARCH_TOOL_NAMES, specs_for
from app.core.errors import ProviderError
from app.models.common import ErrorCategory
from app.providers.llm import groq_provider as gp
from app.providers.llm.groq_provider import GROQ_CHAT_URL, GroqLLMProvider, _compact, _groq_tools

OK = {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
    {"id": "call_1", "type": "function", "function": {"name": "search_web",
                                                      "arguments": json.dumps({"query": "cs ms", "limit": 5,
                                                                               "purpose": "discover_programs"})}}]}}],
      "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
TOOL_FAILED = {"error": {"code": "tool_use_failed", "message": "Tool call validation failed: missing properties"}}


def test_nullable_tool_fields_are_optional_for_groq():
    tools = {t["function"]["name"]: t["function"]["parameters"] for t in _groq_tools(specs_for(RESEARCH_TOOL_NAMES))}
    assert set(tools["search_web"]["required"]) == {"query", "limit", "purpose"}
    assert set(tools["fetch_page"]["required"]) == {"url", "expected_page_kind"}
    assert set(tools["retrieve_evidence"]["required"]) == {"query", "top_k"}


@respx.mock
async def test_tool_use_failed_is_retried_then_succeeds():
    route = respx.post(GROQ_CHAT_URL).mock(side_effect=[httpx.Response(400, json=TOOL_FAILED), httpx.Response(200, json=OK)])
    p = GroqLLMProvider(["gsk_a"], "openai/gpt-oss-120b", "openai/gpt-oss-120b", 10)
    turn = await p.research_turn(system="s", tools=specs_for(RESEARCH_TOOL_NAMES),
                                 messages=[{"role": "user", "content": "go"}], effort="low")
    assert route.call_count == 2 and turn.tool_calls[0].name == "search_web"
    sent = json.loads(route.calls[-1].request.content)
    assert sent["reasoning_effort"] == "low" and sent["tool_choice"] == "auto"


@respx.mock
async def test_non_tool_400_is_not_retried():
    route = respx.post(GROQ_CHAT_URL).mock(return_value=httpx.Response(400, json={"error": {"message": "bad model"}}))
    p = GroqLLMProvider(["gsk_a"], "m", "m", 10)
    with pytest.raises(ProviderError) as e:
        await p.research_turn(system="s", tools=[], messages=[{"role": "user", "content": "go"}], effort="low")
    assert e.value.category == ErrorCategory.invalid_output and route.call_count == 1


@respx.mock
async def test_rate_limit_waits_for_retry_after(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(gp.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(gp.time, "monotonic", lambda: 1000.0 + sum(slept))
    route = respx.post(GROQ_CHAT_URL).mock(side_effect=[httpx.Response(429, headers={"retry-after": "3"}),
                                                        httpx.Response(200, json=OK)])
    p = GroqLLMProvider(["gsk_a"], "m", "m", 10)
    turn = await p.research_turn(system="s", tools=specs_for(RESEARCH_TOOL_NAMES),
                                 messages=[{"role": "user", "content": "go"}], effort="low")
    assert route.call_count == 2 and turn.tool_calls
    assert slept and 3 <= slept[0] <= 4


@respx.mock
async def test_second_key_used_when_first_rate_limited():
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"][-5:])
        return httpx.Response(429, headers={"retry-after": "5"}) if len(seen) == 1 else httpx.Response(200, json=OK)
    respx.post(GROQ_CHAT_URL).mock(side_effect=handler)
    p = GroqLLMProvider(["gsk_key_1", "gsk_key_2"], "m", "m", 10)
    await p.research_turn(system="s", tools=[], messages=[{"role": "user", "content": "go"}], effort="low")
    assert seen == ["key_1", "key_2"]


def test_history_compaction_keeps_latest_state_and_pairs():
    msgs = [{"role": "user", "content": "Research request"}]
    for i in range(6):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 2000})
        msgs.append({"role": "user", "content": f"Current research state:\n{{\"turn\": {i}}}"})
    out = _compact(msgs)
    states = [m["content"] for m in out if m["role"] == "user" and "research state" in m["content"]]
    assert states[-1].endswith('{"turn": 5}') and all("omitted" in s for s in states[:-1])
    tools = [m for m in out if m["role"] == "tool"]
    assert len(tools) == 6 and all(len(t["content"]) < 400 for t in tools[:2]) and len(tools[-1]["content"]) == 2000
