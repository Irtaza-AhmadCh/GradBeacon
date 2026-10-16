"""EvidenceValidator — every claim must quote text that actually exists in a page fetched by this job."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.evidence import Evidence
from app.tools.fetcher import FetchedPage
from app.validation.normalizer import date_tokens_present, digit_groups
from app.validation.text_norm import normalize_for_match

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.edu$", re.I)


@dataclass
class Rejection:
    reason: str


class EvidenceValidator:
    def __init__(self, pages: dict[str, FetchedPage]):
        self.pages = pages
        self._norm_cache: dict[str, str] = {}

    def _chunk_norm(self, chunk_id: str, text: str) -> str:
        if chunk_id not in self._norm_cache:
            self._norm_cache[chunk_id] = normalize_for_match(text)
        return self._norm_cache[chunk_id]

    def check_quote(self, source_id: str, chunk_id: str | None, quote: str) -> Evidence | Rejection:
        page = self.pages.get(source_id)
        if page is None or not page.ok:
            return Rejection("unknown_source")
        q = (quote or "").strip()
        if not (8 <= len(q) <= 300):
            return Rejection("bad_quote_length")
        nq = normalize_for_match(q)
        if len(nq) < 6:
            return Rejection("bad_quote_length")
        ordered = sorted(page.chunks, key=lambda c: 0 if c.chunk_id == chunk_id else 1)
        for c in ordered:
            if nq in self._chunk_norm(c.chunk_id, c.text):
                return Evidence(source_id=source_id, chunk_id=c.chunk_id, url=page.source.final_url, quote=q,
                                retrieved_at=page.source.retrieved_at)
        return Rejection("quote_not_in_source")

    @staticmethod
    def value_supported(field: str, value: str, quote: str) -> bool:
        nq = normalize_for_match(quote)
        nv = normalize_for_match(value)
        if field in ("tuition", "application_fee") or (field == "funding" and "$" in value):
            groups = digit_groups(value)
            return bool(groups) and all(g in nq or g.replace(",", "") in nq.replace(",", "") for g in groups)
        if field == "deadline":
            return date_tokens_present(value, quote)
        if field in ("min_gpa", "english_test"):
            nums = re.findall(r"\d+(?:\.\d+)?", value)
            return bool(nums) and all(n in nq for n in nums)
        if field == "email":
            return value.strip().lower() in quote.lower()
        if field in ("application_url", "funding_url"):
            return nv in nq
        return True

    def validate(self, field: str, value: str, source_id: str, evidence: list[tuple[str | None, str]]
                 ) -> list[Evidence] | Rejection:
        """Validate a claim's evidence list; returns verified Evidence (≥1) or a Rejection."""
        if not evidence:
            return Rejection("no_evidence")
        ok: list[Evidence] = []
        last = Rejection("quote_not_in_source")
        for chunk_id, quote in evidence[:3]:
            res = self.check_quote(source_id, chunk_id, quote)
            if isinstance(res, Rejection):
                last = res
                continue
            if not self.value_supported(field, value, quote):
                last = Rejection("value_not_supported")
                continue
            ok.append(res)
        return ok or last
