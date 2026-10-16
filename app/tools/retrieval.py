"""Keyword (BM25) retrieval over passages fetched in this job. Vector RAG is intentionally not built."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app.models.evidence import Chunk

_STOP = {
    "the", "and", "for", "are", "with", "that", "this", "from", "your", "you", "our", "will", "can", "all",
    "of", "to", "in", "on", "a", "an", "is", "be", "or", "by", "as", "at", "it", "its", "we", "us", "not",
    "have", "has", "may", "must", "any", "each", "per", "which", "into", "their", "they", "than", "also",
}


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if len(t) >= 2 and t not in _STOP]


@dataclass
class Passage:
    chunk_id: str
    source_id: str
    url: str
    heading: str | None
    text: str
    score: float


class JobPassageIndex:
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._meta: dict[str, dict] = {}
        self._ids: set[str] = set()
        self._bm25: BM25Okapi | None = None

    def add(self, chunks: list[Chunk], meta: dict[str, dict]) -> None:
        for c in chunks:
            if c.chunk_id not in self._ids:
                self._ids.add(c.chunk_id)
                self._chunks.append(c)
        for k, v in meta.items():
            cur = self._meta.setdefault(k, {"program_ids": set(), "url": v.get("url"), "page_kind": v.get("page_kind")})
            cur["program_ids"].update(v.get("program_ids", set()))
            if v.get("page_kind"):
                cur["page_kind"] = v["page_kind"]
        self._bm25 = None

    def page_kind(self, source_id: str) -> str | None:
        return self._meta.get(source_id, {}).get("page_kind")

    def tag_program(self, source_id: str, program_id: str) -> None:
        if source_id in self._meta:
            self._meta[source_id]["program_ids"].add(program_id)

    def search(self, query: str, top_k: int = 5, program_id: str | None = None,
               page_kind: str | None = None) -> list[Passage]:
        if not self._chunks:
            return []
        if self._bm25 is None:
            self._bm25 = BM25Okapi([tokenize((c.heading or "") + " " + c.text) or ["_"] for c in self._chunks])
        q = tokenize(query)
        if not q:
            return []
        scores = self._bm25.get_scores(q)
        ranked = []
        for c, s in zip(self._chunks, scores, strict=True):
            m = self._meta.get(c.source_id, {})
            if program_id and program_id not in m.get("program_ids", set()):
                continue
            if page_kind and m.get("page_kind") != page_kind:
                continue
            if s <= 0:
                continue
            ranked.append((s, c, m))
        ranked.sort(key=lambda x: -x[0])
        return [Passage(chunk_id=c.chunk_id, source_id=c.source_id, url=m.get("url") or "", heading=c.heading,
                        text=c.text[:600], score=round(float(s), 3)) for s, c, m in ranked[:top_k]]

    def rank_chunks(self, chunks: list[Chunk], query: str) -> list[Chunk]:
        """Rank a page's own chunks against gap keywords (used to prioritise extraction context)."""
        if not chunks:
            return []
        bm = BM25Okapi([tokenize((c.heading or "") + " " + c.text) or ["_"] for c in chunks])
        scores = bm.get_scores(tokenize(query) or ["_"])
        return [c for _, c in sorted(zip(scores, chunks, strict=True), key=lambda x: -x[0])]
