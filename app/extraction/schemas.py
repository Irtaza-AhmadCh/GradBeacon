"""Structured-output schemas for extraction. Every field required; nullable where facts may be absent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

PageKind = Literal["program_overview", "admissions_requirements", "tuition", "funding", "deadlines",
                   "faculty_directory", "faculty_profile", "lab", "other"]
FactField = Literal["tuition", "funding", "deadline", "document", "application_fee", "min_gpa", "english_test",
                    "gre", "prior_degree", "direct_entry_phd", "research_area", "application_url", "funding_url",
                    "state"]


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedEvidence(_S):
    chunk_id: str
    quote: str


class ExtractedFact(_S):
    field: FactField
    value: str
    qualifiers: str | None
    program_name: str | None  # which program on the page this fact belongs to; null = whole page scope
    funding_type: Literal["assistantship", "scholarship", "fellowship", "tuition_waiver", "stipend", "other"] | None
    applies_to_all_admitted: bool | None
    requires_separate_application: bool | None
    evidence: list[ExtractedEvidence]


class ExtractedProgram(_S):
    institution_name: str
    institution_evidence: ExtractedEvidence | None
    program_name: str
    program_evidence: ExtractedEvidence
    degree_level: Literal["masters", "phd", "unclear"]
    field: str | None


class ProgramPageExtraction(_S):
    page_kind: PageKind
    scope: Literal["program", "department", "institution"]
    programs: list[ExtractedProgram]
    facts: list[ExtractedFact]
    not_found_fields: list[str]
    injection_detected: bool


class ExtractedValue(_S):
    value: str
    evidence: ExtractedEvidence


class ExtractedPublication(_S):
    title: str
    year: int | None
    evidence: ExtractedEvidence


class ExtractedProfessor(_S):
    name: str
    name_evidence: ExtractedEvidence
    title: ExtractedValue | None
    department: ExtractedValue | None
    research_interests: list[ExtractedValue]
    publications: list[ExtractedPublication]
    email: ExtractedValue | None
    intake_statement: ExtractedValue | None
    profile_url: str | None


class ProfessorPageExtraction(_S):
    page_kind: Literal["faculty_profile", "faculty_directory", "lab", "publication_list", "other"]
    institution_name: str | None
    professors: list[ExtractedProfessor]
    injection_detected: bool
