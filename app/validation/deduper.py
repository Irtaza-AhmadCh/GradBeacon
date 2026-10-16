"""Cross-loop dedupe by program_id or canonical official URL; keeps the better-evidenced record."""

from __future__ import annotations

from app.models.program import ProgramOpportunity
from app.tools.url_safety import canonicalize
from app.validation.status import recompute_status, verified_field_count


def final_dedupe(programs: list[ProgramOpportunity]) -> list[ProgramOpportunity]:
    by_key: dict[str, ProgramOpportunity] = {}
    url_to_key: dict[tuple[str, str], str] = {}
    for p in programs:
        try:
            cu = canonicalize(p.official_program_url)
        except Exception:  # noqa: BLE001
            cu = p.official_program_url
        key = p.program_id if p.program_id in by_key else url_to_key.get((cu, p.degree_level.value), p.program_id)
        if key not in by_key:
            by_key[key] = p
            url_to_key[(cu, p.degree_level.value)] = key
            continue
        a, b = by_key[key], p
        keep, other = (a, b) if verified_field_count(a) >= verified_field_count(b) else (b, a)
        for s in other.sources:
            if all(s.source_id != x.source_id for x in keep.sources):
                keep.sources.append(s)
        for w in other.warnings:
            if w not in keep.warnings:
                keep.warnings.append(w)
        by_key[key] = recompute_status(keep)
    return list(by_key.values())
