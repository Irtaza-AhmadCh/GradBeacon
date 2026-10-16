"""HTML -> readable markdown text + same-site links. Page scripts are never executed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import lxml.html
import trafilatura

from app.tools.domains import registrable_domain


@dataclass
class ReadablePage:
    title: str | None
    text_markdown: str
    headings: list[str] = field(default_factory=list)


@dataclass
class PageLink:
    url: str
    text: str


def _doc(html: str):
    try:
        return lxml.html.fromstring(html)
    except Exception:  # noqa: BLE001
        return None


def extract_readable(html: str) -> ReadablePage:
    doc = _doc(html)
    title = None
    if doc is not None:
        t = doc.findtext(".//title")
        if t and t.strip():
            title = " ".join(t.split())[:300]
        else:
            h1 = doc.xpath("//h1")
            if h1:
                title = " ".join(h1[0].text_content().split())[:300] or None
    text = None
    try:
        text = trafilatura.extract(html, output_format="markdown", include_tables=True, include_links=False,
                                   favor_recall=True)
    except Exception:  # noqa: BLE001
        text = None
    if not text and doc is not None:
        for bad in doc.xpath("//script|//style|//noscript"):
            bad.drop_tree()
        text = re.sub(r"[ \t]+", " ", doc.text_content())
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    text = text or ""
    headings = [ln.lstrip("#").strip() for ln in text.splitlines() if ln.startswith("#")]
    return ReadablePage(title=title, text_markdown=text, headings=headings[:60])


def extract_links(html: str, base_url: str, max_links: int = 150) -> list[PageLink]:
    doc = _doc(html)
    if doc is None:
        return []
    base_reg = registrable_domain(urlsplit(base_url).hostname or "")
    seen: set[str] = set()
    out: list[PageLink] = []
    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absu = urljoin(base_url, href).split("#")[0]
        parts = urlsplit(absu)
        if parts.scheme not in ("http", "https"):
            continue
        if registrable_domain(parts.hostname or "") != base_reg:
            continue
        if absu in seen:
            continue
        seen.add(absu)
        txt = " ".join(a.text_content().split())[:120]
        out.append(PageLink(url=absu, text=txt))
        if len(out) >= max_links:
            break
    return out


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def extract_mailto_emails(html: str) -> list[str]:
    """Emails published as mailto: links (trafilatura drops links). Verbatim only, never inferred."""
    doc = _doc(html)
    if doc is None:
        return []
    out: list[str] = []
    for a in doc.xpath("//a[starts-with(@href,'mailto:')]"):
        addr = a.get("href", "")[7:].split("?")[0].strip()
        if _EMAIL_RE.fullmatch(addr) and addr not in out:
            out.append(addr)
    return out[:20]
