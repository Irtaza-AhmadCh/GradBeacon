from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.common import DataFreshness, ErrorCategory, FieldStatus, Strict


class Source(Strict):
    source_id: str
    url: str
    final_url: str
    title: str | None = None
    domain: str
    registrable_domain: str
    retrieved_at: datetime
    content_hash: str | None = None
    fetch_status: Literal["ok", "error"]
    error_category: ErrorCategory | None = None
    freshness: DataFreshness
    is_official: bool
    page_kind: str | None = None
    retrieval_method: Literal["direct", "provider_extract"] = "direct"


class Chunk(Strict):
    chunk_id: str
    source_id: str
    heading: str | None = None
    text: str


class Evidence(Strict):
    source_id: str
    chunk_id: str | None = None
    url: str
    quote: str = Field(max_length=400)
    retrieved_at: datetime | None = None


class Claim(Strict):
    """A single sourced fact. Value is the human-readable string as published."""

    value: str | None = None
    status: FieldStatus
    evidence: list[Evidence] = Field(default_factory=list)
    scope: Literal["program", "department", "institution"] = "program"
    qualifiers: str | None = None
    note: str | None = None
    alternatives: list[Claim] = Field(default_factory=list)
