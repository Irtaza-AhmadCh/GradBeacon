"""Heading-aware chunking of readable markdown (400–1200 chars, never mid-paragraph unless huge)."""

from __future__ import annotations

import re

from app.models.evidence import Chunk

_HEADING = re.compile(r"^#{1,4}\s+(.*)$")
MIN_CHARS, MAX_CHARS = 400, 1200


def chunk_page(source_id: str, markdown: str) -> list[Chunk]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    para: list[str] = []

    def flush_para():
        if para:
            sections[-1][1].append("\n".join(para).strip())
            para.clear()

    for line in markdown.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            flush_para()
            sections.append((m.group(1).strip(), []))
        elif not line.strip():
            flush_para()
        else:
            para.append(line.rstrip())
    flush_para()

    chunks: list[Chunk] = []
    for heading, paras in sections:
        buf = ""
        for p in paras:
            if not p:
                continue
            while len(p) > MAX_CHARS:  # split giant paragraphs on sentence-ish boundaries
                cut = p.rfind(". ", 0, MAX_CHARS)
                cut = cut + 1 if cut > MIN_CHARS else MAX_CHARS
                piece, p = p[:cut].strip(), p[cut:].strip()
                if buf:
                    chunks.append(_mk(source_id, len(chunks), heading, buf))
                    buf = ""
                chunks.append(_mk(source_id, len(chunks), heading, piece))
            if buf and len(buf) + len(p) + 2 > MAX_CHARS:
                chunks.append(_mk(source_id, len(chunks), heading, buf))
                buf = p
            else:
                buf = f"{buf}\n\n{p}" if buf else p
        if buf:
            if heading is None and chunks and len(buf) < MIN_CHARS // 2 and chunks[-1].heading is None:
                last = chunks[-1]
                chunks[-1] = _mk(source_id, len(chunks) - 1, last.heading, last.text + "\n\n" + buf)
            else:
                chunks.append(_mk(source_id, len(chunks), heading, buf))
    return chunks


def _mk(source_id: str, i: int, heading: str | None, text: str) -> Chunk:
    return Chunk(chunk_id=f"{source_id}#c{i}", source_id=source_id, heading=heading, text=text.strip())
