"""Request/response DTOs for the public API."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from app.models.common import Strict
from app.models.profile import StudentProfile
from app.models.results import DecisionField

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ResearchRequest(Strict):
    profile: StudentProfile
    target_results: int | None = Field(default=None, ge=1, le=15)


class JobAccepted(Strict):
    job_id: str
    status: str
    source_mode: str
    events_url: str
    snapshot_url: str


class VerifyRequest(Strict):
    field: DecisionField
    source_url: str | None = Field(default=None, max_length=2048)
    profile: StudentProfile | None = None  # optional: re-score after verification (not stored)


class DecisionRequest(Strict):
    field: DecisionField
    decision: Literal["continue_with_uncertainty"]


class ShortlistRequest(Strict):
    program_ids: list[str] = Field(min_length=1, max_length=8)
    acknowledge_uncertainty: bool = False


class ProfessorRequest(Strict):
    job_id: str
    program_ids: list[str] = Field(min_length=1, max_length=5)
    research_interests: list[str] = Field(min_length=1, max_length=10)
    max_results: int = Field(default=8, ge=1, le=8)

    @field_validator("research_interests")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        out = [x.strip()[:100] for x in v if x and x.strip()]
        if not out:
            raise ValueError("At least one research interest is required")
        return out


class StudentFacts(Strict):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=200)
    current_degree: str = Field(min_length=2, max_length=200)
    institution: str = Field(min_length=2, max_length=200)
    interests: list[str] = Field(default_factory=list, max_length=10)
    achievements: list[str] = Field(default_factory=list, max_length=5)
    message_goal: Literal["inquire_about_openings", "ask_about_research_fit"] = "ask_about_research_fit"

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL.match(v):
            raise ValueError("Invalid email address")
        return v

    @field_validator("achievements")
    @classmethod
    def _ach(cls, v: list[str]) -> list[str]:
        for a in v:
            if len(a) > 200:
                raise ValueError("Each achievement must be at most 200 characters")
        return [a.strip() for a in v if a.strip()]


class DraftRequest(Strict):
    professor_job_id: str
    professor_id: str
    program_id: str | None = None
    student: StudentFacts


class DraftEdit(Strict):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    expected_version: int = Field(ge=1)

    @field_validator("subject", "body")
    @classmethod
    def _plain(cls, v: str) -> str:
        if re.search(r"<[a-zA-Z/!]", v):
            raise ValueError("HTML is not allowed; plain text only")
        return _CTRL.sub("", v)


APPROVAL_TEXT = "I approve sending this email"


class ApproveRequest(Strict):
    content_hash: str = Field(min_length=64, max_length=64)
    confirm: Literal[True]
    confirmation_text: str

    @field_validator("confirmation_text")
    @classmethod
    def _text(cls, v: str) -> str:
        if v != APPROVAL_TEXT:
            raise ValueError(f'confirmation_text must be exactly "{APPROVAL_TEXT}"')
        return v


class SendRequest(Strict):
    draft_id: str
    approval_token: str = Field(min_length=10, max_length=2000)
