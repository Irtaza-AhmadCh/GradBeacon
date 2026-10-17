"""Gemini adapter over Google's OpenAI-compatible Chat Completions endpoint (same tool loop as Groq).

Differences handled here: Gemini 3 tool calls carry a thought_signature that must be echoed back in history,
rate-limit waits come from the RetryInfo detail in the 429 body, and reasoning_effort applies to every model.
"""

from __future__ import annotations

import logging
import re

import httpx

from app.providers.llm.groq_provider import GroqLLMProvider, _body

GEMINI_CHAT_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def _gemini_retry_after(response: httpx.Response) -> float:
    """Seconds from RetryInfo.retryDelay (e.g. '17s', or '72054s' for an exhausted daily quota), else the
    retry-after header. Not capped: a long delay marks the key out for that model so the next model is used."""
    raw = response.headers.get("retry-after")
    try:
        body = _body(response)
        for detail in (body.get("error") or {}).get("details") or []:
            if str(detail.get("@type", "")).endswith("RetryInfo") and detail.get("retryDelay"):
                raw = detail["retryDelay"]
                break
    except (ValueError, AttributeError, TypeError):
        pass
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*s?\s*$", str(raw or ""))
    return max(1.0, float(m.group(1)) + 0.5) if m else 10.0


class GeminiLLMProvider(GroqLLMProvider):
    """Configured Gemini keys (ideally from different Google Cloud projects, since quotas are per project) are
    used in rotation; bad, rate-limited, or unavailable keys are skipped temporarily."""

    name = "Gemini"
    chat_url = GEMINI_CHAT_URL
    echo_extra_content = True
    log = logging.getLogger("gradbeacon.gemini")

    def _effort(self, model: str, effort: str) -> dict:
        return {"reasoning_effort": effort} if effort in ("low", "medium", "high") else {}

    def _retry_after(self, response: httpx.Response) -> float:
        return _gemini_retry_after(response)
