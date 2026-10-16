"""SearchService: cache + official-domain post-filter around a SearchProvider."""

from __future__ import annotations

import re

from app.core.config import Settings
from app.providers.search.base import SearchProvider, SearchResult
from app.tools.cache import SIX_HOURS, TTLCache
from app.tools.domains import is_official_domain, is_publication_domain
from app.tools.url_safety import canonicalize


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
            return hit, True
        # Verified 2026-10-17: Tavily rejects a bare TLD in include_domains (400), so official-domain focus uses a
        # site: operator in the query, and every result is post-filtered by the domain policy below.
        q = query if (allow_publication or domain_hints or "site:" in query) else f"{query} site:.edu"
        raw = await self.provider.search(q, min(limit * 2, 10), domain_hints)
        if not raw and q != query:
            raw = await self.provider.search(query, min(limit * 2, 10), domain_hints)
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
        return out, False
