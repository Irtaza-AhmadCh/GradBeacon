"""Build data/curated_programs.json from a seed of OFFICIAL URLs + verbatim quotes, using the real pipeline.

Every seed claim is re-checked against a fresh fetch: the quote must appear verbatim in the official page and the
value must be supported by the quote (EvidenceValidator), then merged with the same RecordMerger as live research.
Claims that fail are dropped and reported. Nothing is taken from memory.

Usage:
  python scripts/curate_fallback.py --seed data/curation_seed.json --out data/curated_programs.json
  python scripts/curate_fallback.py --seed data/seed_parts/cs.json --check     # validate only, print report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import clock  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.ids import stable_program_id  # noqa: E402
from app.extraction.schemas import (  # noqa: E402
    ExtractedEvidence,
    ExtractedFact,
    ExtractedProgram,
    ProgramPageExtraction,
)
from app.models.common import DegreeLevel  # noqa: E402
from app.tools.fetcher import SafePageFetcher  # noqa: E402
from app.validation.merger import RecordMerger  # noqa: E402
from app.validation.status import recompute_status  # noqa: E402


def _ev(q: str) -> ExtractedEvidence:
    return ExtractedEvidence(chunk_id="", quote=q)


async def curate(seed: list[dict]) -> tuple[list[dict], list[str]]:
    settings = Settings(_env_file=None)
    fetcher = SafePageFetcher(settings)
    report: list[str] = []
    records = []
    for entry in seed:
        programs: dict = {}
        pages: dict = {}
        level = DegreeLevel(entry["degree_level"])
        pid = stable_program_id(entry["institution"], entry["program_name"], level.value)
        label = f"{entry['institution']} — {entry['program_name']} ({level.value})"
        for i, pg in enumerate(entry["pages"]):
            page = await fetcher.fetch(pg["url"])
            if not page.ok:
                report.append(f"[FAIL] {label}: fetch {pg['url']} -> {page.source.error_category}")
                continue
            pages[page.source.source_id] = page
            merger = RecordMerger(programs, pages, settings.extra_official, qualification="Bachelor's degree")
            progs = []
            if i == 0 or pg.get("program_quote"):
                progs.append(ExtractedProgram(
                    institution_name=entry["institution"],
                    institution_evidence=_ev(pg["institution_quote"]) if pg.get("institution_quote") else None,
                    program_name=entry["program_name"], program_evidence=_ev(pg.get("program_quote") or ""),
                    degree_level=level.value, field=entry.get("field")))
            facts = [ExtractedFact(field=f["field"], value=f["value"], qualifiers=f.get("qualifiers"), program_name=None,
                                   funding_type=f.get("funding_type"),
                                   applies_to_all_admitted=f.get("applies_to_all_admitted"),
                                   requires_separate_application=f.get("requires_separate_application"),
                                   evidence=[_ev(f["quote"])]) for f in pg.get("facts", [])]
            ex = ProgramPageExtraction(page_kind=pg.get("page_kind", "program_overview"), scope=pg.get("scope", "program"),
                                       programs=progs, facts=facts, not_found_fields=[], injection_detected=False)
            rep = merger.merge(ex, page, level, ex.page_kind, target_program_id=pid if pid in programs else None)
            report.append(f"[{'OK' if rep.rejected_claims == 0 else 'WARN'}] {label}: {pg['url']} verified="
                          f"{rep.verified_claims} rejected={rep.rejected_claims} {rep.rejection_reasons or ''}")
        rec = programs.get(pid) or next(iter(programs.values()), None)
        if rec is None:
            report.append(f"[DROP] {label}: program identity could not be verified")
            continue
        rec.last_verified_at = rec.retrieved_at or clock.utc_now()
        rec.field = entry.get("field") or rec.field
        recompute_status(rec)
        records.append(rec.model_dump(mode="json"))
    await fetcher.aclose()
    return records, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", nargs="+", required=True)
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    seed: list[dict] = []
    for path in a.seed:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        seed.extend(data["records"] if isinstance(data, dict) else data)
    records, report = asyncio.run(curate(seed))
    print("\n".join(report))
    print(f"\n{len(records)} record(s) built from {len(seed)} seed entr(ies).")
    if a.out and not a.check:
        now = clock.utc_now()
        Path(a.out).write_text(json.dumps({"dataset_version": now.date().isoformat(), "curated_at": now.date().isoformat(),
                                           "note": "Built by scripts/curate_fallback.py from official pages; every "
                                                   "claim's quote was verified verbatim at curation time.",
                                           "records": records}, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
