"""Curated fallback dataset (FR-10/15/46): clearly labelled, never presented as freshly verified."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.models.common import DataFreshness, FieldStatus, SummaryStatus
from app.models.profile import StudentProfile
from app.models.program import ProgramOpportunity
from app.scoring.terms import extract_terms
from app.validation.status import recompute_status

log = logging.getLogger("gradbeacon.fallback")
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "curated_programs.json"


def _curate(p: ProgramOpportunity) -> ProgramOpportunity:
    """Force fallback labelling on every claim regardless of what the file says."""
    p.fallback_record = True
    p.data_freshness = DataFreshness.curated

    def mark(c):
        if c is not None and c.status == FieldStatus.verified:
            c.status = FieldStatus.curated_fallback
    for t in p.tuition:
        mark(t.claim)
    for f in p.funding:
        mark(f.claim)
    for d in p.deadlines:
        mark(d.claim)
    for r in p.requirements:
        mark(r.claim)
    for c in p.required_documents + p.research_areas:
        mark(c)
    mark(p.application_fee)
    for s in p.sources:
        s.freshness = DataFreshness.curated
    p.verification_status = SummaryStatus.fallback
    return recompute_status(p)


class CuratedDataset:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.records: list[ProgramOpportunity] = []
        self.dataset_version: str | None = None
        self.curated_at: str | None = None
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("curated_dataset_missing", extra={"path": str(self.path.name)})
            return
        self.dataset_version = raw.get("dataset_version")
        self.curated_at = raw.get("curated_at")
        for rec in raw.get("records", []):
            try:
                self.records.append(_curate(ProgramOpportunity.model_validate(rec)))
            except ValidationError:
                log.warning("curated_record_invalid", extra={"program_id": rec.get("program_id")})
        log.info("curated_dataset_loaded", extra={"count": len(self.records)})

    def select_for(self, profile: StudentProfile) -> tuple[list[ProgramOpportunity], list[str]]:
        warnings: list[str] = []
        levels = set(profile.degree_paths)
        pool = [r.model_copy(deep=True) for r in self.records if r.degree_level in levels]
        if not pool:
            return [], ["No curated fallback records exist for the selected degree pathway(s)."]
        want = extract_terms(" ".join(filter(None, [profile.field, profile.specialization])))
        relevant = [r for r in pool if want & extract_terms(f"{r.field} {r.program_name}")]
        if not relevant:
            warnings.append("No curated programs match your field closely; showing the closest curated records.")
            relevant = pool
        return relevant, warnings
