"""SearchService: cache + official-domain post-filter around a SearchProvider."""

from __future__ import annotations

import logging
import re
import time

from app.core.config import Settings
from app.providers.search.base import SearchProvider, SearchResult
from app.tools.cache import SIX_HOURS, TTLCache
from app.tools.domains import is_official_domain, is_publication_domain
from app.tools.url_safety import canonicalize

log = logging.getLogger("gradbeacon.search")


def normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


class SearchService:
    def __init__(self, provider: SearchProvider, settings: Settings,
                 cache: TTLCache[tuple, list[SearchResult]] | None = None):
        self.provider = provider
        self.s = settings
        self.cache = cache or TTLCache(200, SIX_HOURS)

    async def search(self, query: str, limit: int, *, allow_publication: bool = False,
                     domain_hints: list[str] | None = None) -> tuple[list[SearchResult], bool]:
        """Returns (official results, cached?)."""
        key = (normalize_query(query), limit, tuple(domain_hints or ()), allow_publication)
        hit = self.cache.get(key)
        if hit is not None:
            log.info("search_cache_hit", extra={"query": query, "limit": limit, "results": len(hit),
                                                "domains": [r.domain for r in hit]})
            return hit, True
        # Verified 2026-10-17: Tavily rejects a bare TLD in include_domains (400), so official-domain focus uses a
        # site: operator in the query, and every result is post-filtered by the domain policy below.
        q = query if (allow_publication or domain_hints or "site:" in query) else f"{query} site:.edu"
        started = time.monotonic()
        log.info("search_request", extra={"provider": self.provider.name, "query": q,
                                          "max_results": min(limit * 2, 10),
                                          "domain_hints": domain_hints or []})
        try:
            raw = await self.provider.search(q, min(limit * 2, 10), domain_hints)
            if not raw and q != query:
                log.info("search_retry_without_site_filter", extra={"provider": self.provider.name, "query": query})
                raw = await self.provider.search(query, min(limit * 2, 10), domain_hints)
        except Exception as exc:
            log.exception("search_failed", extra={"provider": self.provider.name, "query": q,
                                                   "error_type": type(exc).__name__,
                                                   "duration_ms": int((time.monotonic() - started) * 1000)})
            raise
        out: list[SearchResult] = []
        seen: set[str] = set()
        for r in raw:
            try:
                cu = canonicalize(r.url)
            except Exception:  # noqa: BLE001
                continue
            ok = is_official_domain(r.domain, self.s.extra_official) or (
                allow_publication and is_publication_domain(r.domain))
            if not ok or cu in seen:
                continue
            seen.add(cu)
            out.append(r.model_copy(update={"url": cu}))
            if len(out) >= limit:
                break
        self.cache.set(key, out)
        log.info("search_response", extra={
            "provider": self.provider.name, "query": q, "provider_results": len(raw),
            "accepted_results": len(out), "duration_ms": int((time.monotonic() - started) * 1000),
            "provider_candidates": [{"title": r.title, "url": r.url, "domain": r.domain,
                                      "score": r.score, "snippet": r.snippet} for r in raw],
            "results": [{"title": r.title, "url": r.url, "domain": r.domain, "score": r.score,
                         "snippet": r.snippet} for r in out],
        })
        return out, False
