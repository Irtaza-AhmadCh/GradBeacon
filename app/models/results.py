"""Scoring, explanations, checklist and human-decision models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.common import FieldStatus, Strict
from app.models.evidence import Claim, Evidence

DISCLAIMER = "Profile-fit score, not an admission probability."


class DimensionScore(Strict):
    score: int | None
    status: Literal["known", "unknown", "uncertain"]
    reasons: list[str] = Field(default_factory=list)
    weight: float


class MatchResult(Strict):
    opportunity_id: str
    overall_fit_score: int | None
    coverage: str
    academic_fit: DimensionScore
    research_fit: DimensionScore
    funding_fit: DimensionScore
    preference_fit: DimensionScore
    known_matches: list[str] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    score_version: str
    disclaimer: str = DISCLAIMER
    # Sort keys for local filtering (None = unknown, never 0)
    sort_tuition_annual_usd: float | None = None
    sort_funding_amount_usd: float | None = None
    funding_class: Literal["guaranteed", "available", "not_offered", "none_found", "unknown"] = "unknown"
    verified_field_count: int = 0


class Explanation(Strict):
    summary: str
    match_reasons: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    generated_by: Literal["llm", "template"]


class ChecklistItem(Strict):
    label: str
    category: Literal["link", "deadline", "fee", "document", "test", "gpa", "funding", "verify"]
    value: str | None = None
    status: FieldStatus
    evidence: list[Evidence] = Field(default_factory=list)
    action_url: str | None = None
    program_id: str | None = None


class Checklist(Strict):
    program_id: str
    items: list[ChecklistItem]
    generated_at: datetime


DecisionField = Literal["tuition", "funding", "deadline", "documents", "min_gpa", "english_test", "direct_entry_phd"]


class PendingDecision(Strict):
    program_id: str
    field: DecisionField
    kind: Literal["conflict", "missing", "unavailable"]
    question: str
    competing_claims: list[Claim] = Field(default_factory=list)
    suggested_sources: list[str] = Field(default_factory=list)
    title: str = "Needs your decision / Verify this"


class DecisionRecord(Strict):
    program_id: str
    field: str
    decision: Literal["verify_requested", "continue_with_uncertainty"]
    at: datetime
