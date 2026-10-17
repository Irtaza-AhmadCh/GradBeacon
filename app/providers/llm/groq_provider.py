"""Groq Chat Completions adapter with native tools and bounded API-key failover."""

from __future__ import annotations

import asyncio
import hashlib
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
MAX_WAIT_SECONDS = 30  # longer cooldowns (e.g. a daily quota) skip to the next model instead of waiting
TRANSIENT_RETRIES = 1  # extra round per model for 5xx/timeouts before moving to the next model
TRANSIENT = (ErrorCategory.provider_unavailable, ErrorCategory.provider_timeout)


def _content_audit(content: Any) -> dict[str, Any]:
    """Describe prompt material without copying student profiles, source text, or drafts into logs."""
    raw = content if isinstance(content, str) else json.dumps(content, sort_keys=True, default=str)
    return {"chars": len(raw), "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}


def _request_audit(payload: dict) -> dict[str, Any]:
    messages = payload.get("messages", [])
    tools = payload.get("tools", [])
    response_format = payload.get("response_format", {})
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return {
        "model": payload.get("model"),
        "request_chars": len(serialized),
        "request_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "messages": [{"index": i, "role": m.get("role"), **_content_audit(m.get("content", ""))}
                     for i, m in enumerate(messages)],
        "message_count": len(messages),
        "total_message_chars": sum(len(str(m.get("content", ""))) for m in messages),
        "tools": [{"name": t.get("function", {}).get("name"),
                   "description_chars": len(t.get("function", {}).get("description", "")),
                   "parameters_sha256": hashlib.sha256(json.dumps(
                       t.get("function", {}).get("parameters", {}), sort_keys=True,
                       separators=(",", ":"), default=str).encode("utf-8")).hexdigest()}
                  for t in tools],
        "schema": response_format.get("json_schema", {}).get("name"),
        "schema_sha256": hashlib.sha256(json.dumps(
            response_format.get("json_schema", {}).get("schema", {}), sort_keys=True,
            separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
            if response_format else None,
        "response_format": response_format.get("type"),
        "max_completion_tokens": payload.get("max_completion_tokens"),
        "reasoning_effort": payload.get("reasoning_effort"),
    }


def _model_output_audit(result: BaseModel) -> dict[str, Any]:
    data = result.model_dump(mode="json")
    return {"schema": type(result).__name__, "fields": list(data),
            "list_counts": {key: len(value) for key, value in data.items() if isinstance(value, list)}}


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


def _body(response: httpx.Response) -> Any:
    """Parsed error body; Gemini wraps it in a one-element list. Raises ValueError for non-JSON."""
    body = response.json()
    return body[0] if isinstance(body, list) and body else body


def _safe_provider_error(response: httpx.Response) -> str:
    """Extract a short provider diagnostic without logging credentials or full payloads."""
    try:
        body = _body(response)
        error = body.get("error", {}) if isinstance(body, dict) else {}
        detail = (error.get("message") or error.get("code") or
                  (body.get("failed_generation") if isinstance(body, dict) else "") or "")
    except ValueError:
        detail = ""
    detail = re.sub(r"(?i)bearer\s+\S+|gsk_[A-Za-z0-9_-]+|sk-ant-[A-Za-z0-9_-]+|AQ\.[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}", "[REDACTED]", str(detail))
    detail = " ".join(detail.split())[:300]
    return detail


def _error_code(response: httpx.Response) -> str:
    try:
        body = _body(response)
        return str((body.get("error") or {}).get("code") or "") if isinstance(body, dict) else ""
    except ValueError:
        return ""


def _groq_messages(messages: list[dict], echo_extra: bool = False) -> list[dict]:
    """Translate the loop's provider-neutral tool history to OpenAI-style chat format.
    echo_extra replays provider data attached to tool calls (Gemini 3 requires its thought_signature back)."""
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
            }, **({"extra_content": b["extra_content"]} if echo_extra and b.get("extra_content") else {})}
                for b in content if b.get("type") == "tool_use"]
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


MAX_SALVAGE_DROPS = 25


def _salvage(text: str, schema_model: type[M]) -> M:
    """Validate structured output; if single list items are invalid (e.g. a fact whose required string came back
    null), drop only those items instead of discarding the whole extraction. Top-level errors still fail."""
    try:
        return schema_model.model_validate_json(text)
    except ValidationError as first:
        try:
            data = json.loads(text)
        except ValueError:
            raise first from None
    for _ in range(MAX_SALVAGE_DROPS):
        try:
            return schema_model.model_validate(data)
        except ValidationError as exc:
            if not _drop_invalid_item(data, exc):
                raise
    return schema_model.model_validate(data)


def _drop_invalid_item(data: Any, exc: ValidationError) -> bool:
    """Remove the innermost list element containing the first error; False if the error is not inside a list."""
    for err in exc.errors():
        loc = err["loc"]
        cut = max((i for i, part in enumerate(loc) if isinstance(part, int)), default=None)
        if cut is None:
            continue
        node = data
        try:
            for part in loc[:cut]:
                node = node[part]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(node, list) and loc[cut] < len(node):
            node.pop(loc[cut])
            log.info("structured_item_dropped", extra={"path": ".".join(str(x) for x in loc[:cut + 1]),
                                                       "error_type": err.get("type")})
            return True
    return False


class GroqLLMProvider:
    """Uses configured Groq keys in rotation; bad, rate-limited, or unavailable keys are skipped temporarily."""

    name = "Groq"
    chat_url = GROQ_CHAT_URL
    echo_extra_content = False
    log = log

    def __init__(self, api_keys: Sequence[str], model: str, writing_model: str, timeout: float,
                 fallback_models: Sequence[str] = ()):
        self.api_keys = list(dict.fromkeys(k.strip() for k in api_keys if k and k.strip()))
        self.model = model
        self.writing_model = writing_model
        # Quotas are per model: when every key is out of quota for one model, the next model is tried.
        self.fallback_models = [m.strip() for m in fallback_models if m and m.strip()]
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False)
        self._cursor = 0
        self._cooldown_until: dict[tuple[int, str], float] = {}
        self._lock = asyncio.Lock()

    @property
    def slug(self) -> str:
        return self.name.lower()

    def _effort(self, model: str, effort: str) -> dict:
        return _effort(model, effort)

    def _retry_after(self, response: httpx.Response) -> float:
        return _retry_after(response)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _model_chain(self, model: str) -> list[str]:
        return list(dict.fromkeys([model, *self.fallback_models]))

    def _exhausted(self, model: str) -> bool:
        """Every key is cooling down for longer than we are willing to wait (e.g. a daily quota)."""
        now = time.monotonic()
        return bool(self.api_keys) and all(self._cooldown_until.get((i, model), 0) - now > MAX_WAIT_SECONDS
                                           for i in range(len(self.api_keys)))

    async def _ordered_indices(self, model: str) -> list[int]:
        wait_seconds = 0.0
        async with self._lock:
            if not self.api_keys:
                raise ProviderError(ErrorCategory.provider_auth, f"no {self.name} API keys configured")
            now = time.monotonic()
            start = self._cursor % len(self.api_keys)
            order = [(start + i) % len(self.api_keys) for i in range(len(self.api_keys))]
            usable = [i for i in order if self._cooldown_until.get((i, model), 0) <= now]
            if not usable:
                earliest = min(self._cooldown_until.get((i, model), 0) for i in order)
                if earliest - now > MAX_WAIT_SECONDS:
                    raise ProviderError(ErrorCategory.provider_rate_limited, f"all {self.name} keys are cooling down",
                                        retryable=True)
                wait_seconds = max(0, earliest - now)
            else:
                self._cursor = (usable[0] + 1) % len(self.api_keys)
                return usable
        await asyncio.sleep(wait_seconds)
        return await self._ordered_indices(model)

    async def _request(self, payload: dict) -> dict:
        """Try the requested model, then each fallback model whose quota is not exhausted."""
        chain = self._model_chain(payload["model"])
        last: ProviderError | None = None
        for model in chain:
            if self._exhausted(model):
                continue
            try:
                return await self._request_model({**payload, "model": model})
            except ProviderError as e:
                if e.category == ErrorCategory.provider_rate_limited and self._exhausted(model):
                    self.log.warning(f"{self.slug}_model_quota_exhausted", extra={"model": model})
                elif e.category in TRANSIENT and e.retryable:  # model overloaded/timing out on every key
                    self.log.warning(f"{self.slug}_model_unavailable", extra={"model": model,
                                                                              "error_category": e.category.value})
                else:
                    raise
                last = e
        raise last or ProviderError(ErrorCategory.provider_rate_limited,
                                    f"{self.name} quota exhausted for all models ({', '.join(chain)})", retryable=True)

    async def _request_model(self, payload: dict) -> dict:
        """Bounded rounds: when every key is briefly rate limited, wait for the provider's retry-after and retry;
        transient server errors / timeouts get TRANSIENT_RETRIES extra rounds (keys cool down briefly first)."""
        started = time.monotonic()
        transient_rounds = 0
        audit = _request_audit(payload)
        self.log.info(f"{self.slug}_request_started", extra=audit)
        for round_ in range(RATE_LIMIT_ROUNDS):
            try:
                data = await self._request_once(payload)
                self.log.info(f"{self.slug}_request_completed", extra={
                    "model": audit["model"], "schema": audit["schema"], "rounds": round_ + 1,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "usage": data.get("usage", {}) if isinstance(data, dict) else {},
                })
                return data
            except ProviderError as e:
                self.log.warning(f"{self.slug}_request_attempt_failed", extra={
                    "model": audit["model"], "schema": audit["schema"], "round": round_ + 1,
                    "error_category": e.category.value, "retryable": e.retryable,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                })
                transient = e.category in TRANSIENT and e.retryable and transient_rounds < TRANSIENT_RETRIES
                if transient:
                    transient_rounds += 1
                elif (e.category != ErrorCategory.provider_rate_limited or round_ == RATE_LIMIT_ROUNDS - 1
                        or self._exhausted(payload["model"])):
                    raise
                self.log.info(f"{self.slug}_rate_limit_wait", extra={"round": round_ + 1})
        raise ProviderError(ErrorCategory.provider_rate_limited, f"{self.name} rate limited", retryable=True)

    async def _request_once(self, payload: dict) -> dict:
        model = payload["model"]
        indices = await self._ordered_indices(model)
        last_error: ProviderError | None = None
        for index in indices:
            try:
                response = await self.client.post(self.chat_url, headers={
                    "Authorization": f"Bearer {self.api_keys[index]}", "Content-Type": "application/json"
                }, json=payload)
            except httpx.TimeoutException:
                last_error = ProviderError(ErrorCategory.provider_timeout, f"{self.name} request timed out", retryable=True)
                await self._cool(index, model, 5)
                self.log.warning(f"{self.slug}_key_failover", extra={"key_slot": index + 1, "reason": "timeout"})
                continue
            except httpx.HTTPError:
                last_error = ProviderError(ErrorCategory.provider_unavailable, f"{self.name} connection failed", retryable=True)
                await self._cool(index, model, 5)
                self.log.warning(f"{self.slug}_key_failover", extra={"key_slot": index + 1, "reason": "connection"})
                continue

            if response.is_success:
                try:
                    return response.json()
                except ValueError as exc:
                    raise ProviderError(ErrorCategory.invalid_output, f"{self.name} returned invalid JSON") from exc
            status = response.status_code
            if status in (401, 403):
                last_error = ProviderError(ErrorCategory.provider_auth, f"{self.name} key rejected")
                for m in self._model_chain(model):  # a rejected key is bad for every model
                    await self._cool(index, m, 600)
                self.log.warning(f"{self.slug}_key_failover", extra={"key_slot": index + 1, "reason": "auth"})
                continue
            if status == 429:
                last_error = ProviderError(ErrorCategory.provider_rate_limited, f"{self.name} key rate limited", retryable=True)
                await self._cool(index, model, self._retry_after(response))
                self.log.warning(f"{self.slug}_key_failover", extra={"key_slot": index + 1, "reason": "rate_limited",
                                                                       "model": model})
                continue
            if status >= 500:
                last_error = ProviderError(ErrorCategory.provider_unavailable, f"{self.name} service error ({status})",
                                           retryable=True)
                await self._cool(index, model, 10)
                self.log.warning(f"{self.slug}_key_failover", extra={"key_slot": index + 1, "reason": "service_error"})
                continue
            if status in (400, 413, 422):
                diagnostic = _safe_provider_error(response)
                self.log.warning(f"{self.slug}_request_rejected", extra={"status": status, "reason": diagnostic or "unspecified"})
                # A malformed tool call generated by the model is a model mistake, not an outage: retryable.
                tool_failed = _error_code(response) == "tool_use_failed" or "tool call validation" in diagnostic.lower()
                raise ProviderError(ErrorCategory.invalid_output,
                                    f"{self.name} rejected request ({status}): {diagnostic or 'invalid request/schema'}",
                                    retryable=tool_failed)
            raise ProviderError(ErrorCategory.provider_unavailable, f"{self.name} request failed ({status})")
        raise last_error or ProviderError(ErrorCategory.provider_unavailable, f"all {self.name} keys failed", retryable=True)

    async def _cool(self, index: int, model: str, seconds: float) -> None:
        async with self._lock:
            self._cooldown_until[(index, model)] = time.monotonic() + seconds

    async def research_turn(self, *, system: str, tools: list[dict], messages: list[dict], effort: str) -> LLMTurn:
        started = time.monotonic()
        payload = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                      *_groq_messages(messages, self.echo_extra_content)], "max_completion_tokens": 3000,
                   "tool_choice": "auto", "tools": _groq_tools(tools), **self._effort(self.model, effort)}
        for attempt in range(TOOL_USE_FAILED_RETRIES + 1):
            try:
                data = await self._request(payload)
                break
            except ProviderError as e:
                if not (e.category == ErrorCategory.invalid_output and e.retryable) or attempt == TOOL_USE_FAILED_RETRIES:
                    raise
                self.log.info(f"{self.slug}_tool_call_retry", extra={"attempt": attempt + 1})
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
                    raise ProviderError(ErrorCategory.invalid_output, f"{self.name} returned malformed tool arguments") from exc
                if not isinstance(arguments, dict):
                    raise ProviderError(ErrorCategory.invalid_output, f"{self.name} tool arguments must be an object")
                calls.append(LLMToolCall(id=call["id"], name=function["name"], input=arguments))
                raw.append({"type": "tool_use", "id": call["id"], "name": function["name"], "input": arguments,
                            **({"extra_content": call["extra_content"]} if call.get("extra_content") else {})})
            finish = choice.get("finish_reason") or "stop"
            refusal = msg.get("refusal")
            if refusal:
                finish = "refusal"
            elif finish == "length":
                finish = "max_tokens"
            usage = data.get("usage") or {}
            self.log.info(f"{self.slug}_research_response", extra={
                "model": self.model, "finish_reason": finish,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.input} for c in calls],
                "text_chars": len(str(msg.get("content") or "")),
                "text_sha256": hashlib.sha256(str(msg.get("content") or "").encode("utf-8")).hexdigest(),
                "input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0),
                "duration_ms": int((time.monotonic() - started) * 1000),
            })
            return LLMTurn(raw_content=raw, tool_calls=calls, text=str(msg.get("content") or "")[:500],
                           stop_reason=finish, usage={"input_tokens": usage.get("prompt_tokens", 0),
                                                      "output_tokens": usage.get("completion_tokens", 0)},
                           duration_ms=int((time.monotonic() - started) * 1000))
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(ErrorCategory.invalid_output, f"{self.name} response did not match chat completion shape") from exc

    async def _json_call(self, *, model: str, system: str, user_content: str, schema_model: type[M],
                         effort: str) -> M:
        payload = {"model": model, "messages": [{"role": "system", "content": system},
                                                {"role": "user", "content": user_content}],
                   "max_completion_tokens": 6000, "response_format": {"type": "json_schema", "json_schema": {
                       "name": schema_model.__name__.lower(), "strict": True,
                       "schema": strict_schema(schema_model)}}, **self._effort(model, effort)}
        data = await self._request(payload)
        try:
            text = data["choices"][0]["message"].get("content") or ""
            result = _salvage(text, schema_model)
            self.log.info(f"{self.slug}_structured_response", extra={
                "model": model, "schema": schema_model.__name__, "output_chars": len(text),
                "output_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                **_model_output_audit(result),
            })
            return result
        except (KeyError, IndexError, TypeError, ValidationError, ValueError) as exc:
            raise ProviderError(ErrorCategory.invalid_output, f"{self.name} structured result failed schema validation",
                                retryable=True) from exc

    async def extract_json(self, *, system: str, user_content: str, schema_model: type[M], effort: str) -> M:
        return await self._json_call(model=self.model, system=system, user_content=user_content,
                                     schema_model=schema_model, effort=effort)

    async def write_json(self, *, system: str, user_content: str, schema_model: type[M], effort: str) -> M:
        return await self._json_call(model=self.writing_model, system=system, user_content=user_content,
                                     schema_model=schema_model, effort=effort)
