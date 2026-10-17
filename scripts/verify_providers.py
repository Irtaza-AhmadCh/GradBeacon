"""Phase-0 provider verification with REAL requests. Prints status + latency only (never key values).

Checks: one Tavily search; for the configured LLM provider (Gemini or Groq) one NATIVE tool-use call (a real
tool call must come back) and one structured-output extraction call; each LLM key individually against each
model in the fallback chain (shows which keys still have quota); one outbound HTTPS fetch of an official .edu
page; and that the approval-token secret is set.

Usage: python scripts/verify_providers.py      (reads .env / environment)
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, ConfigDict  # noqa: E402

from app.agent.tool_specs import RESEARCH_TOOL_NAMES, specs_for  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.errors import ProviderError  # noqa: E402
from app.providers.llm.gemini_provider import GeminiLLMProvider  # noqa: E402
from app.providers.llm.groq_provider import GroqLLMProvider  # noqa: E402
from app.providers.search.tavily_provider import TavilySearchProvider  # noqa: E402
from app.tools.fetcher import SafePageFetcher  # noqa: E402


class _Ping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    word: str


def line(name: str, ok: bool, t0: float, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<44} {int((time.monotonic() - t0) * 1000):>6} ms  {detail}")
    return ok


async def check_each_key(s, provider_cls) -> bool:
    """One tiny call per key, walking the model chain: a key passes if any model still accepts it."""
    chain = [s.llm_model, *(s.fallback_models if s.llm_provider == "gemini" else [])]
    any_ok = False
    for slot, key in enumerate(s.llm_api_keys, start=1):
        states = []
        for model in dict.fromkeys(chain):
            llm = provider_cls([key], model, model, s.llm_timeout_seconds)
            try:
                await llm.extract_json(system="Return JSON.", user_content='Return ok=true and word="beacon".',
                                       schema_model=_Ping, effort="low")
                states.append(f"{model}=ok")
                await llm.aclose()
                break
            except ProviderError as e:
                states.append(f"{model}={'quota' if e.category.value == 'provider_rate_limited' else e.category.value}")
            await llm.aclose()
        ok = any(st.endswith("=ok") for st in states)
        any_ok |= ok
        line(f"{s.llm_provider} key slot {slot}", ok, time.monotonic(), " ".join(states))
    return any_ok


async def main() -> int:
    s = get_settings()
    results = []
    missing = s.missing_for_live()
    if missing:
        print("Missing configuration (names only):", ", ".join(missing))
    t0 = time.monotonic()
    secret = s.approval_token_secret.get_secret_value() if s.approval_token_secret else ""
    results.append(line("Approval token secret (>= 32 chars)", len(secret) >= 32, t0, f"length={len(secret)}"))
    if s.tavily_api_key:
        t0 = time.monotonic()
        try:
            res = await TavilySearchProvider(s.tavily_api_key.get_secret_value()).search(
                "computer science graduate admissions site:.edu", 5, None)
            results.append(line("Tavily search (site:.edu)", bool(res), t0,
                                f"{len(res)} results; edu-only={all(r.domain.endswith('.edu') for r in res)}"))
        except ProviderError as e:
            results.append(line("Tavily search", False, t0, e.category.value))
    if s.llm_api_keys:
        provider_cls = GeminiLLMProvider if s.llm_provider == "gemini" else GroqLLMProvider
        print(f"{provider_cls.name} keys configured: {len(s.llm_api_keys)} (values hidden); model chain: "
              f"{', '.join([s.llm_model, *(s.fallback_models if s.llm_provider == 'gemini' else [])])}")
        results.append(await check_each_key(s, provider_cls))
        llm = provider_cls(s.llm_api_keys, s.llm_model, s.writing_model, s.llm_timeout_seconds,
                           fallback_models=s.fallback_models if s.llm_provider == "gemini" else ())
        t0 = time.monotonic()
        try:
            turn = await llm.research_turn(
                system="You choose research actions using tools.", tools=specs_for(RESEARCH_TOOL_NAMES),
                messages=[{"role": "user", "content": "Find official pages for MS Computer Science programs in the US. "
                                                      "Call search_web now."}], effort=s.llm_effort_research)
            names = [c.name for c in turn.tool_calls]
            results.append(line(f"{llm.name} tool use (with fallback chain)", bool(names), t0,
                                f"stop={turn.stop_reason} tools={names}"))
        except ProviderError as e:
            results.append(line(f"{llm.name} tool use", False, t0, e.category.value))
        t0 = time.monotonic()
        try:
            out = await llm.extract_json(system="Return JSON.", user_content='Return ok=true and word="beacon".',
                                         schema_model=_Ping, effort=s.llm_effort_extraction)
            results.append(line(f"{llm.name} structured output (with fallback)", out.ok, t0, f"word={out.word}"))
        except ProviderError as e:
            results.append(line(f"{llm.name} structured output", False, t0, e.category.value))
        await llm.aclose()
    t0 = time.monotonic()
    f = SafePageFetcher(s)
    page = await f.fetch("https://www.cmu.edu/")
    results.append(line("Outbound HTTPS fetch (.edu)", page.ok, t0, f"chunks={len(page.chunks)}"))
    await f.aclose()
    print("\nAll checks passed." if all(results) else "\nSome checks FAILED (see above).")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
