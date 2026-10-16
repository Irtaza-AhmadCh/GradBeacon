"""ProgramOpportunity — agent spec §6.1 + PRD §7. Missing amounts are None, never 0."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.common import (
    DataFreshness,
    DegreeLevel,
    EligibilityStatus,
    FieldStatus,
    FundingAvailability,
    FundingType,
    Residency,
    Strict,
    StudentEligibility,
    SummaryStatus,
    TuitionPeriod,
)
from app.models.evidence import Claim, Source

RequirementKey = Literal[
    "min_gpa", "english_test", "gre", "prior_degree", "prerequisite_courses", "direct_entry_phd", "other"
]


class Requirement(Strict):
    key: RequirementKey
    label: str
    claim: Claim
    numeric_min: float | None = None
    numeric_scale: str | None = None
    test: str | None = None


class Tuition(Strict):
    amount: float | None = None
    currency: str = "USD"
    period: TuitionPeriod = TuitionPeriod.other
    residency: Residency = Residency.unspecified
    annualized_usd: float | None = None  # only when reliably computable (sorting/scoring)
    claim: Claim


class FundingItem(Strict):
    type: FundingType
    availability: FundingAvailability
    availability_statement: str
    amount: float | None = None
    amount_period: TuitionPeriod | None = None
    requires_separate_application: bool | None = None
    student_eligibility_status: StudentEligibility = StudentEligibility.unknown
    claim: Claim


class Deadline(Strict):
    date: dt.date | None = None
    term: str | None = None
    applicant_type: Literal["all", "international", "domestic", "funding_consideration"] | None = None
    claim: Claim


class ProgramOpportunity(Strict):
    program_id: str
    institution_name: str
    program_name: str
    degree_level: DegreeLevel
    field: str
    official_program_url: str
    department_url: str | None = None
    official_funding_url: str | None = None
    application_url: str | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    tuition: list[Tuition] = Field(default_factory=list)
    tuition_status: FieldStatus = FieldStatus.unknown
    funding: list[FundingItem] = Field(default_factory=list)
    funding_status: FieldStatus = FieldStatus.unknown
    deadlines: list[Deadline] = Field(default_factory=list)
    deadline_status: FieldStatus = FieldStatus.unknown
    required_documents: list[Claim] = Field(default_factory=list)
    documents_status: FieldStatus = FieldStatus.unknown
    requirements_status: FieldStatus = FieldStatus.unknown
    application_fee: Claim | None = None
    research_areas: list[Claim] = Field(default_factory=list)
    state: str | None = None
    eligibility_status: EligibilityStatus = EligibilityStatus.needs_verification
    verification_status: SummaryStatus = SummaryStatus.needs_verification
    verification_label: str = ""
    field_labels: dict[str, str] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    retrieved_at: datetime | None = None
    last_verified_at: datetime | None = None
    data_freshness: DataFreshness = DataFreshness.live
    fallback_record: bool = False
    warnings: list[str] = Field(default_factory=list)
    # internal bookkeeping (serialized; harmless)
    attempted_fields: list[str] = Field(default_factory=list)
    failed_fields: list[str] = Field(default_factory=list)
    has_verified_program_page: bool = False


class ExcludedProgram(Strict):
    program_id: str
    institution_name: str
    program_name: str
    degree_level: DegreeLevel
    reason: str
    evidence: list = Field(default_factory=list)
