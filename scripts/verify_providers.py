"""Phase-0 provider verification with REAL requests. Prints status + latency only (never key values).

Checks: one Tavily search, one Groq NATIVE tool-use call (a real tool_use block must come back),
one structured-output extraction call, and one outbound HTTPS fetch of an official .edu page.

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
from app.providers.llm.groq_provider import GroqLLMProvider  # noqa: E402
from app.providers.search.tavily_provider import TavilySearchProvider  # noqa: E402
from app.tools.fetcher import SafePageFetcher  # noqa: E402


class _Ping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    word: str


def line(name: str, ok: bool, t0: float, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<34} {int((time.monotonic() - t0) * 1000):>6} ms  {detail}")
    return ok


async def main() -> int:
    s = get_settings()
    results = []
    missing = s.missing_for_live()
    if missing:
        print("Missing configuration (names only):", ", ".join(missing))
    if s.tavily_api_key:
        t0 = time.monotonic()
        try:
            res = await TavilySearchProvider(s.tavily_api_key.get_secret_value()).search(
                "computer science graduate admissions site:.edu", 5, None)
            results.append(line("Tavily search (site:.edu)", bool(res), t0,
                                f"{len(res)} results; edu-only={all(r.domain.endswith('.edu') for r in res)}"))
        except ProviderError as e:
            results.append(line("Tavily search", False, t0, e.category.value))
    if s.groq_api_keys:
        print(f"Groq keys configured: {len(s.groq_api_keys)} (values hidden)")
        llm = GroqLLMProvider(s.groq_api_keys, s.llm_model, s.writing_model, s.llm_timeout_seconds)
        t0 = time.monotonic()
        try:
            turn = await llm.research_turn(
                system="You choose research actions using tools.", tools=specs_for(RESEARCH_TOOL_NAMES),
                messages=[{"role": "user", "content": "Find official pages for MS Computer Science programs in the US. "
                                                      "Call search_web now."}], effort=s.llm_effort_research)
            names = [c.name for c in turn.tool_calls]
            results.append(line(f"Groq tool use ({s.llm_model})", bool(names), t0,
                                f"stop={turn.stop_reason} tools={names}"))
        except ProviderError as e:
            results.append(line("Groq tool use", False, t0, e.category.value))
        t0 = time.monotonic()
        try:
            out = await llm.extract_json(system="Return JSON.", user_content='Return ok=true and word="beacon".',
                                         schema_model=_Ping, effort=s.llm_effort_extraction)
            results.append(line("Groq structured output", out.ok, t0, f"word={out.word}"))
        except ProviderError as e:
            results.append(line("Groq structured output", False, t0, e.category.value))
    t0 = time.monotonic()
    f = SafePageFetcher(s)
    page = await f.fetch("https://www.cmu.edu/")
    results.append(line("Outbound HTTPS fetch (.edu)", page.ok, t0, f"chunks={len(page.chunks)}"))
    await f.aclose()
    print("\nAll checks passed." if all(results) else "\nSome checks FAILED (see above).")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
