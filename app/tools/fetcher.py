"""SafePageFetcher — bounded, SSRF-checked HTML retrieval with provenance metadata (agent spec §4.2)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin

import httpx

from app.core import clock
from app.core.config import Settings
from app.core.errors import ProviderError
from app.models.common import DataFreshness, ErrorCategory
from app.models.evidence import Chunk, Source
from app.tools.cache import SIX_HOURS, TTLCache
from app.tools.chunker import chunk_page
from app.tools.domains import host_of, is_official_domain, is_publication_domain, registrable_domain
from app.tools.html_text import PageLink, ReadablePage, extract_links, extract_mailto_emails, extract_readable
from app.tools.url_safety import canonicalize, validate_public_url

log = logging.getLogger("gradbeacon.fetch")
USER_AGENT = "GradBeaconBot/0.1 (student research assistant; contact via project README)"
_ALLOWED_CT = ("text/html", "application/xhtml+xml")


@dataclass
class FetchedPage:
    source: Source
    readable: ReadablePage | None = None
    links: list[PageLink] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    mailto_emails: list[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.source.fetch_status == "ok"


class PageFetcher(Protocol):
    async def fetch(self, url: str, *, allow_publication_domains: bool = False) -> FetchedPage: ...


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_\-]+)""", re.I)


def decode_html(body: bytes, header_charset: str | None) -> str:
    """Header charset -> strict UTF-8 -> <meta charset> -> cp1252 (never crashes)."""
    for enc in filter(None, [header_charset]):
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        pass
    m = _META_CHARSET.search(body[:4096])
    if m:
        try:
            return body.decode(m.group(1).decode("ascii", "ignore"))
        except (LookupError, UnicodeDecodeError):
            pass
    return body.decode("cp1252", errors="replace")


def source_id_for(url: str) -> str:
    return "src_" + hashlib.sha256(url.encode()).hexdigest()[:12]


def domain_allowed(url: str, extra: set[str], allow_publication: bool) -> bool:
    host = host_of(url)
    return is_official_domain(host, extra) or (allow_publication and is_publication_domain(host))


class ContentExtractor(Protocol):
    async def extract(self, url: str) -> str | None: ...


_EXTRACT_FALLBACK_CATEGORIES = {ErrorCategory.fetch_http_error, ErrorCategory.fetch_timeout,
                                ErrorCategory.unsupported_content}
_MD_LINK = re.compile(r"\[([^\]]{0,120})\]\((https?://[^)\s]+)\)")


class SafePageFetcher:
    def __init__(self, settings: Settings, cache: TTLCache[str, FetchedPage] | None = None,
                 http: httpx.AsyncClient | None = None, extractor: ContentExtractor | None = None):
        self.s = settings
        self.extractor = extractor if settings.fetch_provider_fallback else None
        self.cache = cache or TTLCache(300, SIX_HOURS)
        self.http = http or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(settings.fetch_total_timeout, connect=settings.fetch_connect_timeout),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )

    def _error_page(self, url: str, final_url: str, cat: ErrorCategory, started: float) -> FetchedPage:
        host = host_of(final_url or url)
        src = Source(
            source_id=source_id_for(url), url=url, final_url=final_url or url, title=None, domain=host,
            registrable_domain=registrable_domain(host), retrieved_at=clock.utc_now(), content_hash=None,
            fetch_status="error", error_category=cat, freshness=DataFreshness.live,
            is_official=is_official_domain(host, self.s.extra_official),
        )
        return FetchedPage(source=src, duration_ms=int((time.monotonic() - started) * 1000))

    async def fetch(self, url: str, *, allow_publication_domains: bool = False) -> FetchedPage:
        started = time.monotonic()
        try:
            url = canonicalize(url)
        except Exception:  # noqa: BLE001
            return self._error_page(url, url, ErrorCategory.blocked_url, started)
        if not domain_allowed(url, self.s.extra_official, allow_publication_domains):
            return self._error_page(url, url, ErrorCategory.blocked_url, started)
        cached = self.cache.get(url)
        if cached is not None:
            src = cached.source.model_copy(update={"freshness": DataFreshness.cached})
            return FetchedPage(source=src, readable=cached.readable, links=cached.links, chunks=cached.chunks,
                               mailto_emails=cached.mailto_emails, duration_ms=0)
        try:
            async with asyncio.timeout(self.s.fetch_total_timeout):
                page = await self._fetch_live(url, allow_publication_domains, started)
        except TimeoutError:
            page = self._error_page(url, url, ErrorCategory.fetch_timeout, started)
        except ProviderError as e:
            page = self._error_page(url, url, e.category, started)
        except httpx.TimeoutException:
            page = self._error_page(url, url, ErrorCategory.fetch_timeout, started)
        except httpx.HTTPError:
            page = self._error_page(url, url, ErrorCategory.fetch_http_error, started)
        if not page.ok and self.extractor and page.source.error_category in _EXTRACT_FALLBACK_CATEGORIES:
            page = await self._via_provider(url, page, started)
        if page.ok:
            self.cache.set(url, page)
        log.info("page_fetch", extra={"domain": page.source.domain, "status": page.source.fetch_status,
                                      "error_category": page.source.error_category,
                                      "duration_ms": page.duration_ms})
        return page

    async def _fetch_live(self, url: str, allow_pub: bool, started: float) -> FetchedPage:
        current = url
        for _hop in range(self.s.max_redirects + 1):
            await validate_public_url(current)
            async with self.http.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        raise ProviderError(ErrorCategory.fetch_http_error, "redirect without location")
                    nxt = canonicalize(urljoin(current, loc))
                    if not domain_allowed(nxt, self.s.extra_official, allow_pub):
                        raise ProviderError(ErrorCategory.blocked_url, "redirect off allowed domains")
                    current = nxt
                    continue
                if resp.status_code >= 400:
                    raise ProviderError(ErrorCategory.fetch_http_error, f"http {resp.status_code}")
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in _ALLOWED_CT:
                    return self._error_page(url, current, ErrorCategory.unsupported_content, started)
                body = bytearray()
                async for part in resp.aiter_bytes():
                    body.extend(part)
                    if len(body) > self.s.max_page_bytes:
                        raise ProviderError(ErrorCategory.too_large, "page too large")
                header_charset = resp.charset_encoding
            html = decode_html(bytes(body), header_charset)
            return self._build(url, current, html, started)
        raise ProviderError(ErrorCategory.fetch_http_error, "too many redirects")

    async def _via_provider(self, url: str, failed: FetchedPage, started: float) -> FetchedPage:
        """Site refused direct access (bot wall/5xx/timeout) or served a PDF: ask the search provider for the
        page content. Clearly labelled retrieval_method=provider_extract; facts still need verbatim quotes."""
        try:
            md = await self.extractor.extract(url)
        except ProviderError:
            return failed
        if not md or len(md.strip()) < 200:
            return failed
        text = md[: self.s.max_page_bytes]
        title = next((ln.lstrip("#").strip() for ln in text.splitlines() if ln.startswith("#")), None)
        sid = source_id_for(url)
        host = host_of(url)
        src = Source(
            source_id=sid, url=url, final_url=url, title=title[:300] if title else None, domain=host,
            registrable_domain=registrable_domain(host), retrieved_at=clock.utc_now(),
            content_hash=hashlib.sha256(text.encode()).hexdigest(), fetch_status="ok", error_category=None,
            freshness=DataFreshness.live, is_official=is_official_domain(host, self.s.extra_official),
            retrieval_method="provider_extract",
        )
        base_reg = registrable_domain(host)
        links, seen = [], set()
        for label, link_url in _MD_LINK.findall(text):
            if registrable_domain(host_of(link_url)) == base_reg and link_url not in seen:
                seen.add(link_url)
                links.append(PageLink(url=link_url, text=label.strip()))
        clean = _MD_LINK.sub(lambda m: m.group(1), text)
        return FetchedPage(source=src, readable=ReadablePage(title=title, text_markdown=clean, headings=[]),
                           links=links[:150], chunks=chunk_page(sid, clean),
                           duration_ms=int((time.monotonic() - started) * 1000))

    def _build(self, url: str, final_url: str, html: str, started: float) -> FetchedPage:
        readable = extract_readable(html)
        mailtos = extract_mailto_emails(html)
        text = readable.text_markdown
        if mailtos:
            # Verbatim addresses from mailto: links (trafilatura drops links). Not inferred.
            text += "\n\n## Email links published on this page\n\n" + "\n".join(f"mailto: {m}" for m in mailtos)
            readable = ReadablePage(title=readable.title, text_markdown=text, headings=readable.headings)
        sid = source_id_for(url)
        host = host_of(final_url)
        src = Source(
            source_id=sid, url=url, final_url=final_url, title=readable.title, domain=host,
            registrable_domain=registrable_domain(host), retrieved_at=clock.utc_now(),
            content_hash=hashlib.sha256(text.encode()).hexdigest(), fetch_status="ok", error_category=None,
            freshness=DataFreshness.live, is_official=is_official_domain(host, self.s.extra_official),
        )
        chunks = chunk_page(sid, text) if len(text) >= 200 else []
        return FetchedPage(source=src, readable=readable, links=extract_links(html, final_url), chunks=chunks,
                           mailto_emails=mailtos, duration_ms=int((time.monotonic() - started) * 1000))

    async def aclose(self) -> None:
        await self.http.aclose()
