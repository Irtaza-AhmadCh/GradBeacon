"""Anthropic Claude adapter: native strict tool use for the research loop, structured outputs for extraction.

- Thinking is adaptive (always on for Opus 5.5); depth is controlled only via output_config.effort.
- tool_choice is always "auto" (forced tool choice returns 400 on this model family); tools are strict.
- Server-side refusal fallback (`fallbacks="default"`) is enabled unless LLM_SERVER_FALLBACK=false.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from app.core.errors import ProviderError
from app.models.common import ErrorCategory
from app.providers.llm.base import LLMToolCall, LLMTurn, M

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def strict_schema(model: type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    """API-compatible JSON schema with additionalProperties:false and every property required."""
    schema = anthropic.transform_schema(model)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


def _map_error(e: Exception) -> ProviderError:
    if isinstance(e, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return ProviderError(ErrorCategory.provider_auth, "llm auth")
    if isinstance(e, anthropic.RateLimitError):
        return ProviderError(ErrorCategory.provider_rate_limited, "llm rate limited", retryable=True)
    if isinstance(e, anthropic.APITimeoutError):
        return ProviderError(ErrorCategory.provider_timeout, "llm timeout", retryable=True)
    if isinstance(e, anthropic.BadRequestError):
        return ProviderError(ErrorCategory.invalid_output, "llm bad request")
    if isinstance(e, anthropic.APIStatusError):
        return ProviderError(ErrorCategory.provider_unavailable, f"llm status {e.status_code}", retryable=True)
    if isinstance(e, anthropic.APIConnectionError):
        return ProviderError(ErrorCategory.provider_unavailable, "llm connection", retryable=True)
    return ProviderError(ErrorCategory.provider_unavailable, type(e).__name__)


class AnthropicLLMProvider:
    def __init__(self, api_key: str, model: str, writing_model: str, timeout: float, server_fallback: bool):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self.model = model
        self.writing_model = writing_model
        self.server_fallback = server_fallback

    def _extra(self) -> dict[str, Any]:
        return {"betas": [FALLBACK_BETA], "fallbacks": "default"} if self.server_fallback else {}

    async def research_turn(self, *, system: str, tools: list[dict], messages: list[dict], effort: str) -> LLMTurn:
        started = time.monotonic()
        try:
            resp = await self.client.beta.messages.create(
                model=self.model, max_tokens=8000, system=system, tools=tools, tool_choice={"type": "auto"},
                messages=messages, output_config={"effort": effort}, **self._extra(),
            )
        except Exception as e:  # noqa: BLE001
            raise _map_error(e) from e
        calls = [LLMToolCall(id=b.id, name=b.name, input=dict(b.input or {}))
                 for b in resp.content if getattr(b, "type", None) == "tool_use"]
        text = " ".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        usage = {"input_tokens": getattr(resp.usage, "input_tokens", 0) or 0,
                 "output_tokens": getattr(resp.usage, "output_tokens", 0) or 0}
        return LLMTurn(raw_content=list(resp.content), tool_calls=calls, text=text[:500],
                       stop_reason=resp.stop_reason or "end_turn", usage=usage,
                       duration_ms=int((time.monotonic() - started) * 1000))

    async def _json_call(self, *, model: str, system: str, user_content: str, schema_model: type[M],
                         effort: str) -> M:
        try:
            resp = await self.client.beta.messages.create(
                model=model, max_tokens=16000, system=system,
                messages=[{"role": "user", "content": user_content}],
                output_config={"effort": effort,
                               "format": {"type": "json_schema", "schema": strict_schema(schema_model)}},
                **self._extra(),
            )
        except Exception as e:  # noqa: BLE001
            raise _map_error(e) from e
        if resp.stop_reason == "refusal":
            raise ProviderError(ErrorCategory.refusal, "model refused")
        if resp.stop_reason == "max_tokens":
            raise ProviderError(ErrorCategory.invalid_output, "truncated output", retryable=True)
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        try:
            return schema_model.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            raise ProviderError(ErrorCategory.invalid_output, _short_error(e), retryable=True) from e

    async def extract_json(self, *, system: str, user_content: str, schema_model: type[M], effort: str) -> M:
        return await self._json_call(model=self.model, system=system, user_content=user_content,
                                     schema_model=schema_model, effort=effort)

    async def write_json(self, *, system: str, user_content: str, schema_model: type[M], effort: str) -> M:
        return await self._json_call(model=self.writing_model, system=system, user_content=user_content,
                                     schema_model=schema_model, effort=effort)


def _short_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:5])
    return str(e)[:200]
