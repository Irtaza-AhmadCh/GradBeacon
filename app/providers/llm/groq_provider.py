"""Groq Chat Completions adapter with native tools and bounded API-key failover."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.core.errors import ProviderError
from app.models.common import ErrorCategory
from app.providers.llm.base import LLMToolCall, LLMTurn, M

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
log = logging.getLogger("gradbeacon.groq")


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Prepare a Pydantic schema for Groq strict structured output."""
    schema = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema


def _is_nullable(prop: dict) -> bool:
    t = prop.get("type")
    return (isinstance(t, list) and "null" in t) or t == "null" or None in (prop.get("enum") or [])


def _groq_parameters(schema: dict) -> dict:
    """Groq validates generated tool calls server-side and 400s if a 'required' property is missing.
    Nullable properties are therefore made optional (the executor treats a missing value as null)."""
    params = json.loads(json.dumps(schema))
    props = params.get("properties", {})
    params["required"] = [k for k in params.get("required", []) if not _is_nullable(props.get(k, {}))]
    return params


def _groq_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t.get("description", ""),
        "parameters": _groq_parameters(t.get("input_schema", {})),
    }} for t in tools]


def _effort(model: str, effort: str) -> dict:
    """gpt-oss reasoning models accept reasoning_effort; lower effort = fewer tokens (rate limits) and lower latency."""
    if model.startswith("openai/gpt-oss") and effort in ("low", "medium", "high"):
        return {"reasoning_effort": effort}
    return {}


TOOL_USE_FAILED_RETRIES = 2
RATE_LIMIT_ROUNDS = 12  # bounded overall by the research loop deadline


def _retry_after(response: httpx.Response) -> float:
    """Seconds to wait from Groq's headers (retry-after, or x-ratelimit-reset-* like '7.66s'); capped."""
    for h in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = response.headers.get(h)
        if not raw:
            continue
        m = re.match(r"^\s*(?:(\d+(?:\.\d+)?)m(?!s))?\s*(\d+(?:\.\d+)?)?\s*(ms|s)?\s*$", raw.strip())
        if m and (m.group(1) or m.group(2)):
            secs = float(m.group(1) or 0) * 60 + float(m.group(2) or 0) / (1000 if m.group(3) == "ms" else 1)
            return max(1.0, min(secs + 0.5, 30.0))
    return 10.0


def _safe_provider_error(response: httpx.Response) -> str:
    """Extract a short provider diagnostic without logging credentials or full payloads."""
    try:
        body = response.json()
        error = body.get("error", {}) if isinstance(body, dict) else {}
        detail = (error.get("message") or error.get("code") or
                  (body.get("failed_generation") if isinstance(body, dict) else "") or "")
    except ValueError:
        detail = ""
    detail = re.sub(r"(?i)bearer\s+\S+|gsk_[A-Za-z0-9_-]+|sk-ant-[A-Za-z0-9_-]+", "[REDACTED]", str(detail))
    detail = " ".join(detail.split())[:300]
    return detail


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str((body.get("error") or {}).get("code") or "") if isinstance(body, dict) else ""
    except ValueError:
        return ""


def _groq_messages(messages: list[dict]) -> list[dict]:
    """Translate the loop's provider-neutral tool history to Groq's chat format."""
    out: list[dict] = []
    for message in messages:
        role, content = message["role"], message.get("content", "")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
            calls = [{"id": b["id"], "type": "function", "function": {
                "name": b["name"], "arguments": json.dumps(b.get("input", {}), separators=(",", ":"))
            }} for b in content if b.get("type") == "tool_use"]
            out.append({"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})})
            continue
        text_parts: list[str] = []
        for block in content:
            if block.get("type") == "tool_result":
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"],
                            "content": str(block.get("content", ""))})
            elif block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        if text_parts:
            out.append({"role": "user", "content": "\n".join(text_parts)})
    return _compact(out)


KEEP_RECENT_TOOL_RESULTS = 4
OLD_TOOL_RESULT_CHARS = 300


def _compact(msgs: list[dict]) -> list[dict]:
    """Keep requests under Groq tokens-per-minute limits: only the latest research-state snapshot is sent in full
    (older ones are superseded), and older tool results are shortened. Tool-call/result pairing is preserved."""
    state_idx = [i for i, m in enumerate(msgs) if m["role"] == "user" and i > 0
                 and str(m.get("content", "")).startswith("Current research state")]
    tool_idx = [i for i, m in enumerate(msgs) if m["role"] == "tool"]
    old_tools = set(tool_idx[:-KEEP_RECENT_TOOL_RESULTS])
    out = []
    for i, m in enumerate(msgs):
        if i in state_idx[:-1]:
            m = {**m, "content": "[earlier research state omitted; see the latest state]"}
        elif i in old_tools and len(str(m.get("content", ""))) > OLD_TOOL_RESULT_CHARS:
            m = {**m, "content": str(m["content"])[:OLD_TOOL_RESULT_CHARS] + " …[truncated older result]"}
        out.append(m)
    return out


class GroqLLMProvider:
    """Uses configured Groq keys in rotation; bad, rate-limited, or unavailable keys are skipped temporarily."""

    def __init__(self, api_keys: Sequence[str], model: str, writing_model: str, timeout: float):
        self.api_keys = list(dict.fromkeys(k.strip() for k in api_keys if k and k.strip()))
        self.model = model
        self.writing_model = writing_model
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False)
        self._cursor = 0
        self._cooldown_until: dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _ordered_indices(self) -> list[int]:
        wait_seconds = 0.0
        async with self._lock:
            if not self.api_keys:
                raise ProviderError(ErrorCategory.provider_auth, "no Groq API keys configured")
            now = time.monotonic()
            start = self._cursor % len(self.api_keys)
            order = [(start + i) % len(self.api_keys) for i in range(len(self.api_keys))]
            usable = [i for i in order if self._cooldown_until.get(i, 0) <= now]
            if not usable:
                earliest = min(self._cooldown_until.get(i, 0) for i in order)
                if earliest - now > 30:
                    raise ProviderError(ErrorCategory.provider_rate_limited, "all Groq keys are cooling down",
                                        retryable=True)
                wait_seconds = max(0, earliest - now)
            else:
                self._cursor = (usable[0] + 1) % len(self.api_keys)
                return usable
        await asyncio.sleep(wait_seconds)
        return await self._ordered_indices()

    async def _request(self, payload: dict) -> dict:
        """Bounded rounds: when every key is rate limited, wait for Groq's retry-after (<= 30s) and retry."""
        for round_ in range(RATE_LIMIT_ROUNDS):
            try:
                return await self._request_once(payload)
            except ProviderError as e:
                if e.category != ErrorCategory.provider_rate_limited or round_ == RATE_LIMIT_ROUNDS - 1:
                    raise
                log.info("groq_rate_limit_wait", extra={"round": round_ + 1})
        raise ProviderError(ErrorCategory.provider_rate_limited, "Groq rate limited", retryable=True)

    async def _request_once(self, payload: dict) -> dict:
        indices = await self._ordered_indices()
        last_error: ProviderError | None = None
        for index in indices:
            try:
                response = await self.client.post(GROQ_CHAT_URL, headers={
                    "Authorization": f"Bearer {self.api_keys[index]}", "Content-Type": "application/json"
                }, json=payload)
            except httpx.TimeoutException:
                last_error = ProviderError(ErrorCategory.provider_timeout, "Groq request timed out", retryable=True)
                await self._cool(index, 5)
                log.warning("groq_key_failover", extra={"key_slot": index + 1, "reason": "timeout"})
                continue
            except httpx.HTTPError:
                last_error = ProviderError(ErrorCategory.provider_unavailable, "Groq connection failed", retryable=True)
                await self._cool(index, 5)
                log.warning("groq_key_failover", extra={"key_slot": index + 1, "reason": "connection"})
                continue

            if response.is_success:
                try:
                    return response.json()
                except ValueError as exc:
                    raise ProviderError(ErrorCategory.invalid_output, "Groq returned invalid JSON") from exc
            status = response.status_code
            if status in (401, 403):
                last_error = ProviderError(ErrorCategory.provider_auth, "Groq key rejected")
                await self._cool(index, 600)
                log.warning("groq_key_failover", extra={"key_slot": index + 1, "reason": "auth"})
                continue
            if status == 429:
                last_error = ProviderError(ErrorCategory.provider_rate_limited, "Groq key rate limited", retryable=True)
                await self._cool(index, _retry_after(response))
                log.warning("groq_key_failover", extra={"key_slot": index + 1, "reason": "rate_limited"})
                continue
            if status >= 500:
                last_error = ProviderError(ErrorCategory.provider_unavailable, f"Groq service error ({status})",
                                           retryable=True)
                await self._cool(index, 10)
                log.warning("groq_key_failover", extra={"key_slot": index + 1, "reason": "service_error"})
                continue
            if status in (400, 413, 422):
                diagnostic = _safe_provider_error(response)
                log.warning("groq_request_rejected", extra={"status": status, "reason": diagnostic or "unspecified"})
                # A malformed tool call generated by the model is a model mistake, not an outage: retryable.
                tool_failed = _error_code(response) == "tool_use_failed" or "tool call validation" in diagnostic.lower()
                raise ProviderError(ErrorCategory.invalid_output,
                                    f"Groq rejected request ({status}): {diagnostic or 'invalid request/schema'}",
                                    retryable=tool_failed)
            raise ProviderError(ErrorCategory.provider_unavailable, f"Groq request failed ({status})")
        raise last_error or ProviderError(ErrorCategory.provider_unavailable, "all Groq keys failed", retryable=True)

    async def _cool(self, index: int, seconds: int) -> None:
        async with self._lock:
            self._cooldown_until[index] = time.monotonic() + seconds

    async def research_turn(self, *, system: str, tools: list[dict], messages: list[dict], effort: str) -> LLMTurn:
        started = time.monotonic()
        payload = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                      *_groq_messages(messages)], "max_completion_tokens": 3000,
                   "tool_choice": "auto", "tools": _groq_tools(tools), **_effort(self.model, effort)}
        for attempt in range(TOOL_USE_FAILED_RETRIES + 1):
            try:
                data = await self._request(payload)
                break
            except ProviderError as e:
                if not (e.category == ErrorCategory.invalid_output and e.retryable) or attempt == TOOL_USE_FAILED_RETRIES:
                    raise
                log.info("groq_tool_call_retry", extra={"attempt": attempt + 1})
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            raw: list[dict] = []
            if msg.get("content"):
                raw.append({"type": "text", "text": msg["content"]})
            calls: list[LLMToolCall] = []
            for call in msg.get("tool_calls") or []:
                function = call["function"]
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ProviderError(ErrorCategory.invalid_output, "Groq returned malformed tool arguments") from exc
                if not isinstance(arguments, dict):
                    raise ProviderError(ErrorCategory.invalid_output, "Groq tool arguments must be an object")
                calls.append(LLMToolCall(id=call["id"], name=function["name"], input=arguments))
                raw.append({"type": "tool_use", "id": call["id"], "name": function["name"], "input": arguments})
            finish = choice.get("finish_reason") or "stop"
            refusal = msg.get("refusal")
            if refusal:
                finish = "refusal"
            elif finish == "length":
                finish = "max_tokens"
            usage = data.get("usage") or {}
            return LLMTurn(raw_content=raw, tool_calls=calls, text=str(msg.get("content") or "")[:500],
                           stop_reason=finish, usage={"input_tokens": usage.get("prompt_tokens", 0),
                                                      "output_tokens": usage.get("completion_tokens", 0)},
                           duration_ms=int((time.monotonic() - started) * 1000))
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(ErrorCategory.invalid_output, "Groq response did not match chat completion shape") from exc

    async def _json_call(self, *, model: str, system: str, user_content: str, schema_model: type[M],
                         effort: str) -> M:
        payload = {"model": model, "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": user_content}],
                   "max_completion_tokens": 6000, "response_format": {"type": "json_schema", "json_schema": {
                       "name": schema_model.__name__.lower(), "strict": True,
                       "schema": strict_schema(schema_model)}}, **_effort(model, effort)}
        data = await self._request(payload)
        try:
            text = data["choices"][0]["message"].get("content") or ""
            return schema_model.model_validate_json(text)
        except (KeyError, IndexError, TypeError, ValidationError, ValueError) as exc:
            raise ProviderError(ErrorCategory.invalid_output, "Groq structured result failed schema validation",
                                retryable=True) from exc

    async def extract_json(self, *, system: str, user_content: str, schema_model: type[M], effort: str) -> M:
        return await self._json_call(model=self.model, system=system, user_content=user_content,
                                     schema_model=schema_model, effort=effort)

    async def write_json(self, *, system: str, user_content: str, schema_model: type[M], effort: str) -> M:
        return await self._json_call(model=self.writing_model, system=system, user_content=user_content,
                                     schema_model=schema_model, effort=effort)
