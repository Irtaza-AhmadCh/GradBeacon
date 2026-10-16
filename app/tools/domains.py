"""Official-domain policy (.edu + owner-approved extras; publication domains in professor mode)."""

from __future__ import annotations

from urllib.parse import urlsplit

import tldextract

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

PUBLICATION_DOMAINS = {"arxiv.org", "dblp.org", "doi.org", "pubmed.ncbi.nlm.nih.gov", "aclanthology.org"}


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def registrable_domain(host: str) -> str:
    ext = _EXTRACT(host)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}".lower()
    return host.lower()


def is_official_domain(host: str, extra: set[str] | None = None) -> bool:
    host = host.lower().rstrip(".")
    if not host:
        return False
    if host == "edu" or host.endswith(".edu"):
        return True
    if extra and registrable_domain(host) in extra:
        return True
    return False


def is_publication_domain(host: str) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in PUBLICATION_DOMAINS)


def same_institution(url_a: str, url_b: str) -> bool:
    return registrable_domain(host_of(url_a)) == registrable_domain(host_of(url_b))
