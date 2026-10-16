from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DegreeLevel(StrEnum):
    masters = "masters"
    phd = "phd"


class FieldStatus(StrEnum):
    verified = "verified"
    partially_verified = "partially_verified"
    conflicting = "conflicting"
    not_found = "not_found"
    unavailable = "unavailable"
    curated_fallback = "curated_fallback"
    unknown = "unknown"


FIELD_LABELS: dict[str, str] = {
    "verified": "Verified from official source",
    "partially_verified": "Partially verified",
    "conflicting": "Needs verification — sources conflict",
    "not_found": "Needs verification — not found",
    "unavailable": "Source unavailable",
    "curated_fallback": "Curated fallback",
    "unknown": "Not yet researched",
}


class SummaryStatus(StrEnum):
    verified = "verified"
    partially_verified = "partially_verified"
    needs_verification = "needs_verification"
    fallback = "fallback"


SUMMARY_LABELS: dict[str, str] = {
    "verified": "Verified from official source",
    "partially_verified": "Partially verified — some fields need checking",
    "needs_verification": "Needs verification",
    "fallback": "Curated fallback — not freshly verified",
}


class DataFreshness(StrEnum):
    live = "live"
    cached = "cached"
    curated = "curated"
    cached_replay = "cached_replay"


class SourceMode(StrEnum):
    live = "live"
    curated_fallback = "curated_fallback"
    mixed = "mixed"
    cached_replay = "cached_replay"


class EligibilityStatus(StrEnum):
    likely_eligible = "likely_eligible"
    needs_verification = "needs_verification"
    prerequisite_not_met = "prerequisite_not_met"


class FundingType(StrEnum):
    assistantship = "assistantship"
    scholarship = "scholarship"
    fellowship = "fellowship"
    tuition_waiver = "tuition_waiver"
    stipend = "stipend"
    other = "other"


class FundingAvailability(StrEnum):
    not_offered = "not_offered"  # official source explicitly says no funding/aid for this program
    mentioned = "mentioned"
    appears_available = "appears_available"
    guaranteed_for_admitted = "guaranteed_for_admitted"


class StudentEligibility(StrEnum):
    confirmed = "confirmed"
    not_confirmed = "not_confirmed"
    unknown = "unknown"


class TuitionPeriod(StrEnum):
    per_year = "per_year"
    per_semester = "per_semester"
    per_term = "per_term"
    per_credit = "per_credit"
    total_program = "total_program"
    other = "other"


class Residency(StrEnum):
    in_state = "in_state"
    out_of_state = "out_of_state"
    international = "international"
    all = "all"
    unspecified = "unspecified"


class FundingPreference(StrEnum):
    need_full_funding = "need_full_funding"
    prefer_funding = "prefer_funding"
    not_required = "not_required"
    unspecified = "unspecified"


class ErrorCategory(StrEnum):
    provider_auth = "provider_auth"
    provider_rate_limited = "provider_rate_limited"
    provider_timeout = "provider_timeout"
    provider_unavailable = "provider_unavailable"
    invalid_output = "invalid_output"
    refusal = "refusal"
    blocked_url = "blocked_url"
    fetch_timeout = "fetch_timeout"
    fetch_http_error = "fetch_http_error"
    unsupported_content = "unsupported_content"
    too_large = "too_large"
    budget_exhausted = "budget_exhausted"
    deadline_exceeded = "deadline_exceeded"
    config_error = "config_error"
