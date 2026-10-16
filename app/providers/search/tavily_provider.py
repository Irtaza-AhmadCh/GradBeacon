"""Tavily adapter. Normalizes results; never synthesizes URLs."""

from __future__ import annotations

import asyncio

from tavily import AsyncTavilyClient
from tavily import errors as tv_errors

from app.core.errors import ProviderError
from app.models.common import ErrorCategory
from app.providers.search.base import SearchResult
from app.tools.domains import host_of


class TavilySearchProvider:
    name = "tavily"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self._client = AsyncTavilyClient(api_key=api_key)
        self._timeout = timeout

    async def search(self, query: str, limit: int, domain_hints: list[str] | None) -> list[SearchResult]:
        try:
            async with asyncio.timeout(self._timeout):
                resp = await self._client.search(
                    query=query, max_results=limit, search_depth="basic",
                    include_domains=domain_hints or None, timeout=self._timeout,
                )
        except tv_errors.InvalidAPIKeyError as e:
            raise ProviderError(ErrorCategory.provider_auth, "search auth") from e
        except tv_errors.MissingAPIKeyError as e:
            raise ProviderError(ErrorCategory.provider_auth, "search key missing") from e
        except tv_errors.UsageLimitExceededError as e:
            raise ProviderError(ErrorCategory.provider_rate_limited, "search quota") from e
        except tv_errors.ForbiddenError as e:
            raise ProviderError(ErrorCategory.provider_auth, "search forbidden") from e
        except (TimeoutError, tv_errors.TimeoutError) as e:
            raise ProviderError(ErrorCategory.provider_timeout, "search timeout", retryable=True) from e
        except tv_errors.BadRequestError as e:
            raise ProviderError(ErrorCategory.invalid_output, "search bad request") from e
        except Exception as e:  # noqa: BLE001
            raise ProviderError(ErrorCategory.provider_unavailable, type(e).__name__, retryable=True) from e
        return self._normalize(resp)

    async def extract(self, url: str) -> str | None:
        """Page content retrieved by the provider (used only when direct fetch is refused). Markdown or None."""
        try:
            async with asyncio.timeout(self._timeout + 5):
                resp = await self._client.extract(urls=[url], extract_depth="basic", format="markdown",
                                                  timeout=self._timeout)
        except tv_errors.InvalidAPIKeyError as e:
            raise ProviderError(ErrorCategory.provider_auth, "extract auth") from e
        except Exception:  # noqa: BLE001
            return None
        for r in (resp or {}).get("results", []) or []:
            content = r.get("raw_content")
            if isinstance(content, str) and content.strip():
                return content
        return None

    def _normalize(self, resp) -> list[SearchResult]:
        out: list[SearchResult] = []
        for r in (resp or {}).get("results", []) or []:
            url = r.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            out.append(SearchResult(title=(r.get("title") or "")[:300], url=url,
                                    snippet=(r.get("content") or "")[:500], domain=host_of(url),
                                    score=r.get("score"), provider=self.name))
        return out
