"""Research-term extraction with synonym canonicalization for deterministic research-fit overlap."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_STOP = {
    "the", "and", "for", "with", "of", "in", "on", "a", "an", "to", "is", "are", "be", "or", "by", "as", "at",
    "program", "programs", "graduate", "degree", "master", "masters", "phd", "doctor", "doctoral", "science",
    "sciences", "engineering", "studies", "department", "school", "university", "research", "areas", "area",
    "ms", "m", "s", "including", "focus", "related", "interest", "interests", "field", "fields", "using",
    "applications", "application", "systems", "methods",
}
_SYN_PATH = Path(__file__).resolve().parents[2] / "data" / "synonyms.json"


@lru_cache
def synonyms() -> dict[str, str]:
    try:
        return {k.lower(): v.lower() for k, v in json.loads(_SYN_PATH.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return {}


def extract_terms(text: str) -> set[str]:
    syn = synonyms()
    low = (text or "").lower()
    terms: set[str] = set()
    for phrase, canon in syn.items():  # multi-word phrases first
        if " " in phrase or "-" in phrase or "." in phrase:
            if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", low):
                terms.add(canon)
    for canon in set(syn.values()):
        if re.search(rf"(?<!\w){re.escape(canon)}(?!\w)", low):
            terms.add(canon)
    tokens = [t for t in re.findall(r"[a-z][a-z0-9+#.\-]*", low) if t not in _STOP and len(t) > 1]
    for t in tokens:
        terms.add(syn.get(t, t))
    for a, b in zip(tokens, tokens[1:], strict=False):
        bg = f"{a} {b}"
        terms.add(syn.get(bg, bg))
    return terms


def overlap(student_text: str, program_text: str) -> tuple[set[str], set[str]]:
    s, p = extract_terms(student_text), extract_terms(program_text)
    return s & p, s
