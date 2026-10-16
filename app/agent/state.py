"""Research state shared across loops of one job, plus per-loop session state."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.models.common import ErrorCategory
from app.orchestrator.budgets import Budget
from app.providers.search.base import SearchResult
from app.tools.fetcher import FetchedPage
from app.tools.html_text import PageLink
from app.tools.retrieval import JobPassageIndex


class ResearchMode(StrEnum):
    programs = "programs"
    verify = "verify"
    professors = "professors"


@dataclass(frozen=True)
class ModeConfig:
    mode: ResearchMode
    tools: tuple[str, ...]
    system_prompt: str
    allow_publication_domains: bool = False


@dataclass
class SharedResearch:
    """Job-level: pages fetched, passages, and URLs the agent is permitted to fetch."""

    pages: dict[str, FetchedPage] = field(default_factory=dict)  # source_id -> page
    index: JobPassageIndex = field(default_factory=JobPassageIndex)
    search_result_urls: dict[str, SearchResult] = field(default_factory=dict)
    discovered_links: dict[str, PageLink] = field(default_factory=dict)
    link_program: dict[str, str] = field(default_factory=dict)  # link url -> program id it was found for
    seeded_urls: set[str] = field(default_factory=set)
    fetched_urls: set[str] = field(default_factory=set)
    failed_urls: dict[str, ErrorCategory] = field(default_factory=dict)
    in_flight: set[str] = field(default_factory=set)
    extracted_for: set[tuple[str, str | None]] = field(default_factory=set)  # (source_id, target program id)

    def allowed(self, url: str) -> bool:
        return url in self.search_result_urls or url in self.discovered_links or url in self.seeded_urls


PageHandler = Callable[[FetchedPage, dict, "LoopSession"], Awaitable[dict]]
FailHandler = Callable[[str, ErrorCategory, dict, "LoopSession"], None]
StateRenderer = Callable[["LoopSession"], dict]


@dataclass
class LoopSession:
    name: str
    mode: ModeConfig
    budget: Budget
    deadline_at: float
    shared: SharedResearch
    on_page: PageHandler
    on_fetch_failed: FailHandler
    render: StateRenderer
    known_program_ids: Callable[[], set[str]]
    searched_queries: set[str] = field(default_factory=set)
    recent_failures: list[dict] = field(default_factory=list)
    stop_reason: str | None = None
    turns: int = 0

    @property
    def seconds_left(self) -> int:
        return max(0, int(self.deadline_at - time.monotonic()))


_LINK_WORDS = re.compile(r"fund|financ|tuition|cost|fee|admission|apply|deadline|requirement|faculty|people|"
                         r"lab|research|assistantship|fellowship|graduate|phd|master", re.I)


def useful_links(shared: SharedResearch, limit: int = 15, keywords: re.Pattern = _LINK_WORDS) -> list[dict]:
    scored = []
    for url, link in shared.discovered_links.items():
        if url in shared.fetched_urls or url in shared.failed_urls:
            continue
        hits = len(keywords.findall(f"{link.text} {url}"))
        if hits:
            scored.append((hits, url, link))
    scored.sort(key=lambda x: -x[0])
    return [{"url": u, "text": link.text, "program_id": shared.link_program.get(u)} for _, u, link in scored[:limit]]
