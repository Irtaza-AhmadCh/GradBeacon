"""Extractor: bounded page text -> validated schema, one retry on invalid output (agent spec §12)."""

from __future__ import annotations

import json
import logging

from app.core.errors import ProviderError
from app.extraction.prompts import PROFESSOR_EXTRACTION_SYSTEM, PROGRAM_EXTRACTION_SYSTEM
from app.extraction.schemas import ProfessorPageExtraction, ProgramPageExtraction
from app.models.common import ErrorCategory
from app.models.evidence import Chunk
from app.providers.llm.base import LLMProvider
from app.tools.fetcher import FetchedPage
from app.tools.retrieval import JobPassageIndex

log = logging.getLogger("gradbeacon.extract")

GAP_KEYWORDS = {
    "tuition": "tuition fees cost per credit semester annual",
    "funding": "funding assistantship fellowship scholarship stipend waiver financial support",
    "deadline": "deadline apply application date fall spring priority",
    "documents": "documents transcripts letters recommendation statement purpose resume cv",
    "requirements": "requirements gpa toefl ielts gre minimum admission degree prerequisite",
    "direct_entry_phd": "bachelor master's degree required direct phd admission",
    "research_areas": "research areas specializations focus labs",
    "faculty": "professor faculty research interests email lab",
}


def render_page(page: FetchedPage, max_chars: int, priority_query: str) -> tuple[str, bool]:
    chunks: list[Chunk] = page.chunks
    ordered = JobPassageIndex().rank_chunks(chunks, priority_query) if priority_query else chunks
    picked: list[Chunk] = []
    total = 0
    for c in ordered:
        size = len(c.text) + 60
        if total + size > max_chars:
            continue
        picked.append(c)
        total += size
    truncated = len(picked) < len(chunks)
    picked.sort(key=lambda c: int(c.chunk_id.rsplit("#c", 1)[1]))
    body = "\n\n".join(f"[chunk_id={c.chunk_id}] {c.heading or ''}\n{c.text}" for c in picked)
    title = (page.source.title or "").replace('"', "'")
    return (f'<untrusted_page url="{page.source.final_url}" title="{title}">\n{body}\n</untrusted_page>',
            truncated)


class Extractor:
    def __init__(self, llm: LLMProvider, effort: str, max_chars: int):
        self.llm = llm
        self.effort = effort
        self.max_chars = max_chars

    async def _run(self, system: str, user: str, model):
        try:
            return await self.llm.extract_json(system=system, user_content=user, schema_model=model,
                                               effort=self.effort), 1
        except ProviderError as e:
            if e.category not in (ErrorCategory.invalid_output,):
                raise
            note = f"\n\nPrevious output failed validation: {e.detail[:300]}. Return valid JSON for the schema."
            return await self.llm.extract_json(system=system, user_content=user + note, schema_model=model,
                                               effort=self.effort), 2

    async def extract_program_page(self, page: FetchedPage, target: dict, gaps: list[str]
                                   ) -> tuple[ProgramPageExtraction | None, int, bool]:
        """Returns (extraction|None, llm_calls_used, truncated)."""
        query = " ".join(GAP_KEYWORDS.get(g, g) for g in gaps)
        text, truncated = render_page(page, self.max_chars, query)
        user = "TARGET:" + json.dumps(target, sort_keys=True) + "\n\n" + text
        try:
            result, calls = await self._run(PROGRAM_EXTRACTION_SYSTEM, user, ProgramPageExtraction)
            return result, calls, truncated
        except ProviderError as e:
            log.info("extraction_failed", extra={"category": e.category})
            if e.category in (ErrorCategory.provider_auth,):
                raise
            return None, 2 if e.category == ErrorCategory.invalid_output else 1, truncated

    async def extract_professor_page(self, page: FetchedPage, interests: list[str]
                                     ) -> tuple[ProfessorPageExtraction | None, int]:
        text, _ = render_page(page, self.max_chars, GAP_KEYWORDS["faculty"] + " " + " ".join(interests))
        user = "STUDENT_INTERESTS:" + json.dumps(interests) + "\n\n" + text
        try:
            result, calls = await self._run(PROFESSOR_EXTRACTION_SYSTEM, user, ProfessorPageExtraction)
            return result, calls
        except ProviderError as e:
            log.info("extraction_failed", extra={"category": e.category})
            if e.category in (ErrorCategory.provider_auth,):
                raise
            return None, 2 if e.category == ErrorCategory.invalid_output else 1
