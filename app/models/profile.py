"""StudentProfile — PRD §5.2 / §7. Missing optional data is unknown, never negative (FR-05)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.common import DegreeLevel, FundingPreference, Strict

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
_SCALE_MAX = {"4.0": 4.0, "5.0": 5.0, "10.0": 10.0, "100": 100.0}


class GPA(Strict):
    value: float = Field(ge=0, le=100)
    scale: Literal["4.0", "5.0", "10.0", "100", "other"] | None = None
    context: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _check_scale(self) -> GPA:
        if self.scale in _SCALE_MAX and self.value > _SCALE_MAX[self.scale]:
            raise ValueError(f"GPA {self.value} exceeds the {self.scale} scale")
        return self


class LanguageScore(Strict):
    test: Literal["TOEFL_iBT", "IELTS", "Duolingo", "PTE", "other"]
    overall: float = Field(ge=0, le=200)


class BudgetPreference(Strict):
    max_tuition_per_year_usd: int | None = Field(default=None, ge=0, le=500_000)


class StudentProfile(Strict):
    # Required (PRD §5.2)
    degree_paths: list[DegreeLevel] = Field(min_length=1, max_length=2)
    field: str = Field(min_length=2, max_length=120)
    qualification: str = Field(min_length=2, max_length=200)
    qualification_status: Literal["completed", "in_progress"]
    gpa: GPA | None
    destination_country: Literal["US"] = "US"
    # Optional
    specialization: str | None = Field(default=None, max_length=120)
    research_interests: list[str] = Field(default_factory=list, max_length=10)
    projects_or_thesis: str | None = Field(default=None, max_length=1500)
    experience: str | None = Field(default=None, max_length=1500)
    skills: list[str] = Field(default_factory=list, max_length=30)
    language_test_scores: list[LanguageScore] = Field(default_factory=list, max_length=3)
    budget: BudgetPreference | None = None
    funding_preference: FundingPreference = FundingPreference.unspecified
    preferred_states: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("field", "qualification", "specialization", "projects_or_thesis", "experience", mode="before")
    @classmethod
    def _trim(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("degree_paths", mode="before")
    @classmethod
    def _dedupe_paths(cls, v):
        if not isinstance(v, list):
            return v
        out = []
        for p in v:
            if p not in out:
                out.append(p)
        return out

    @field_validator("research_interests", "skills", mode="before")
    @classmethod
    def _clean_list(cls, v):
        if v is None:
            return []
        out = []
        for item in v:
            if isinstance(item, str):
                item = item.strip()[:100]
                if item and item.lower() not in [o.lower() for o in out]:
                    out.append(item)
        return out

    @field_validator("preferred_states", mode="before")
    @classmethod
    def _states(cls, v):
        if v is None:
            return []
        out = []
        for s in v:
            s = str(s).strip().upper()
            if not s:
                continue
            if s not in US_STATES:
                raise ValueError(f"Unknown US state code: {s}")
            if s not in out:
                out.append(s)
        return out


def profile_for_llm(p: StudentProfile) -> dict:
    """Minimal profile sent to model providers (privacy): no GPA, experience, projects."""
    return {
        "degree_paths": [d.value for d in p.degree_paths],
        "field": p.field,
        "specialization": p.specialization,
        "research_interests": p.research_interests,
        "funding_preference": p.funding_preference.value,
    }
