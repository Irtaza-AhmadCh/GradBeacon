"""ProfessorMatch — PRD §5.8 / agent spec §7.1. Email only if verbatim on an official page."""

from __future__ import annotations

from pydantic import Field

from app.models.common import DataFreshness, Strict, SummaryStatus
from app.models.evidence import Claim


class Publication(Strict):
    title: str
    year: int | None = None
    url: str | None = None
    claim: Claim


class ProfessorMatch(Strict):
    professor_id: str
    name: str
    name_claim: Claim
    title: Claim | None = None
    department: Claim | None = None
    institution: str
    official_profile_url: str
    lab_url: str | None = None
    research_interests: list[Claim] = Field(default_factory=list)
    verified_publications: list[Publication] = Field(default_factory=list)
    official_email: Claim | None = None
    contact_fallback_url: str | None = None
    intake_status: Claim | None = None
    intake_label: str = "Not confirmed — no official statement found"
    research_fit_score: int | None = None
    research_fit_terms: list[str] = Field(default_factory=list)
    research_fit_explanation: str = ""
    related_program_ids: list[str] = Field(default_factory=list)
    verification_status: SummaryStatus = SummaryStatus.needs_verification
    data_freshness: DataFreshness = DataFreshness.live
