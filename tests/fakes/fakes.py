"""Deterministic fakes for LLM, search, fetcher and email (repeatable agent-behaviour tests)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import Settings
from app.core.errors import ProviderError
from app.models.common import ErrorCategory
from app.providers.email.base import EmailSendOutcome, OutboundEmail
from app.providers.llm.base import LLMToolCall, LLMTurn
from app.providers.search.base import SearchResult
from app.tools.domains import host_of
from app.tools.fetcher import FetchedPage, SafePageFetcher, domain_allowed
from app.tools.url_safety import canonicalize

PAGES = Path(__file__).resolve().parents[1] / "fixtures" / "pages"
BASE = "https://www.example.edu"


def turn(*calls: tuple[str, dict], text: str = "", stop: str | None = None) -> LLMTurn:
    tcs = [LLMToolCall(id=f"tu_{i}_{name}_{time.monotonic_ns()}", name=name, input=inp)
           for i, (name, inp) in enumerate(calls)]
    raw = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in tcs]
    return LLMTurn(raw_content=raw, tool_calls=tcs, text=text, stop_reason=stop or ("tool_use" if tcs else "end_turn"),
                   usage={"input_tokens": 100, "output_tokens": 20})


class FakeLLM:
    def __init__(self, turns: list | None = None, extractions: dict[str, Callable] | None = None,
                 writes: list | None = None):
        self.turns = list(turns or [])
        self.extractions = extractions or {}
        self.writes = list(writes or [])
        self.research_calls: list[list[dict]] = []
        self.extract_calls: list[str] = []
        self.write_calls: list[str] = []

    async def research_turn(self, *, system, tools, messages, effort):
        self.research_calls.append([dict(m) for m in messages])  # snapshot (shallow)
        self.last_tools = tools
        if not self.turns:
            return turn(("finish_research", {"reason": "no_more_leads", "unresolved_gaps": []}))
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t(messages) if callable(t) else t

    async def extract_json(self, *, system, user_content, schema_model, effort):
        self.extract_calls.append(user_content)
        for url, fn in self.extractions.items():
            if f'url="{url}' in user_content:
                out = fn(user_content)
                if isinstance(out, Exception):
                    raise out
                return schema_model.model_validate(out)
        raise ProviderError(ErrorCategory.invalid_output, "no scripted extraction")

    async def write_json(self, *, system, user_content, schema_model, effort):
        self.write_calls.append(user_content)
        if not self.writes:
            raise ProviderError(ErrorCategory.provider_unavailable, "no scripted write")
        out = self.writes.pop(0)
        if isinstance(out, Exception):
            raise out
        return schema_model.model_validate(out)


class FakeSearchProvider:
    name = "fake"

    def __init__(self, results: dict[str, list[tuple[str, str]]] | None = None, error: ErrorCategory | None = None):
        self.results = results or {}
        self.error = error
        self.queries: list[str] = []

    async def search(self, query, limit, domain_hints):
        self.queries.append(query)
        if self.error:
            raise ProviderError(self.error, "fake")
        for key, items in self.results.items():
            if key.lower() in query.lower():
                return [SearchResult(title=t, url=u, snippet="snippet", domain=host_of(u), provider="fake")
                        for t, u in items][:limit]
        return []


class FakeFetcher:
    def __init__(self, routes: dict[str, str] | None = None, failures: dict[str, ErrorCategory] | None = None):
        self.settings = Settings(_env_file=None)
        self.real = SafePageFetcher(self.settings)
        self.routes = {canonicalize(k): v for k, v in (routes or {}).items()}
        self.failures = {canonicalize(k): v for k, v in (failures or {}).items()}
        self.fetched: list[str] = []

    async def fetch(self, url: str, *, allow_publication_domains: bool = False) -> FetchedPage:
        url = canonicalize(url)
        self.fetched.append(url)
        started = time.monotonic()
        if not domain_allowed(url, set(), allow_publication_domains):
            return self.real._error_page(url, url, ErrorCategory.blocked_url, started)
        if url in self.failures:
            return self.real._error_page(url, url, self.failures[url], started)
        if url not in self.routes:
            return self.real._error_page(url, url, ErrorCategory.fetch_http_error, started)
        html = (PAGES / self.routes[url]).read_text(encoding="utf-8")
        return self.real._build(url, url, html, started)

    async def aclose(self):
        await self.real.aclose()


class FakeEmail:
    name = "fake"

    def __init__(self, ok: bool = True, configured: bool = True):
        self.ok = ok
        self.configured = configured
        self.sent: list[OutboundEmail] = []

    async def send(self, email: OutboundEmail) -> EmailSendOutcome:
        self.sent.append(email)
        if self.ok:
            return EmailSendOutcome(ok=True, provider_message_id=f"msg_{len(self.sent)}")
        return EmailSendOutcome(ok=False, error_category=ErrorCategory.provider_unavailable)


# ---------------- scripted extraction helpers ----------------
def ev(quote: str) -> dict:
    return {"chunk_id": "", "quote": quote}


def fact(field, value, quote, qualifiers=None, program_name=None, funding_type=None, all_admitted=None,
         separate=None) -> dict:
    return {"field": field, "value": value, "qualifiers": qualifiers, "program_name": program_name,
            "funding_type": funding_type, "applies_to_all_admitted": all_admitted,
            "requires_separate_application": separate, "evidence": [ev(quote)]}


MS_PROGRAM = {
    "institution_name": "Example State University",
    "institution_evidence": ev("The Master of Science in Computer Science at Example State University prepares"),
    "program_name": "Master of Science in Computer Science",
    "program_evidence": ev("The Master of Science in Computer Science at Example State University"),
    "degree_level": "masters", "field": "Computer Science",
}


def ms_page_extraction(_u=None) -> dict:
    return {"page_kind": "program_overview", "scope": "program", "programs": [MS_PROGRAM],
            "facts": [
                fact("deadline", "December 15, 2026",
                     "The application deadline for Fall 2027 admission is December 15, 2026 for all applicants.",
                     "Fall 2027, all applicants"),
                fact("min_gpa", "3.0 on a 4.0 scale", "minimum GPA of 3.0 on a 4.0 scale"),
                fact("english_test", "TOEFL iBT 90", "must submit a TOEFL iBT score of at least 90"),
                fact("document", "Statement of purpose", "Required documents: a statement of purpose"),
                fact("research_area", "machine learning", "Faculty research areas include machine learning"),
                fact("research_area", "natural language processing",
                     "machine learning, natural language processing, computer vision"),
                # A fabricated claim that the validator must reject:
                fact("tuition", "$9,999 per year", "Tuition is $9,999 per year for everyone."),
            ],
            "not_found_fields": ["tuition", "funding"], "injection_detected": False}


def tuition_extraction(_u=None) -> dict:
    return {"page_kind": "tuition", "scope": "institution", "programs": [],
            "facts": [fact("tuition", "$24,000 per academic year",
                           "International graduate students pay $24,000 per academic year in tuition",
                           "international, per academic year"),
                      fact("application_fee", "$75", "The application fee for all graduate programs is $75")],
            "not_found_fields": [], "injection_detected": False}


def funding_extraction(_u=None) -> dict:
    return {"page_kind": "funding", "scope": "department", "programs": [],
            "facts": [fact("funding", "Teaching assistantships are available on a competitive basis",
                           "Teaching assistantships are available on a competitive basis to qualified master's students",
                           "master's", funding_type="assistantship", all_admitted=False, separate=True)],
            "not_found_fields": [], "injection_detected": False}


def conflict_extraction(_u=None) -> dict:
    return {"page_kind": "deadlines", "scope": "institution", "programs": [],
            "facts": [fact("deadline", "January 5, 2027",
                           "the Master of Science in Computer Science application deadline is January 5, 2027",
                           "Fall 2027, all applicants", program_name=None)],
            "not_found_fields": [], "injection_detected": False}


def injection_extraction(_u=None) -> dict:
    return {"page_kind": "other", "scope": "department", "programs": [], "facts": [], "not_found_fields": [],
            "injection_detected": True}


def professor_extraction(_u=None) -> dict:
    return {"page_kind": "faculty_profile", "institution_name": "Example State University", "injection_detected": False,
            "professors": [{
                "name": "Jane Rivera", "name_evidence": ev("Jane Rivera is an Associate Professor of Computer Science"),
                "title": {"value": "Associate Professor", "evidence": ev("is an Associate Professor of Computer Science")},
                "department": {"value": "Computer Science", "evidence": ev("Associate Professor of Computer Science")},
                "research_interests": [
                    {"value": "natural language processing",
                     "evidence": ev("Her research focuses on natural language processing and machine learning")},
                    {"value": "machine learning", "evidence": ev("natural language processing and machine learning")}],
                "publications": [{"title": "Efficient Transfer Learning for Low-Resource Machine Translation", "year": 2025,
                                  "evidence": ev("Efficient Transfer Learning for Low-Resource Machine Translation")}],
                "email": {"value": "jrivera@example.edu", "evidence": ev("Email: jrivera@example.edu")},
                "intake_statement": {"value": "accepting new PhD students",
                                     "evidence": ev("Dr. Rivera is currently accepting new PhD students for Fall 2027.")},
                "profile_url": None}]}


def professor_noemail_extraction(_u=None) -> dict:
    return {"page_kind": "faculty_profile", "institution_name": "Example State University", "injection_detected": False,
            "professors": [{
                "name": "Sam Chen", "name_evidence": ev("Sam Chen is an Assistant Professor of Computer Science"),
                "title": None, "department": None,
                "research_interests": [{"value": "distributed systems",
                                        "evidence": ev("Professor Chen studies distributed systems and cloud computing")}],
                "publications": [],
                "email": {"value": "schen@example.edu", "evidence": ev("Contact: schen [at] example [dot] edu")},
                "intake_statement": None, "profile_url": None}]}


ROUTES = {
    f"{BASE}/cs/ms": "ms_program.html",
    f"{BASE}/graduate/tuition": "tuition.html",
    f"{BASE}/cs/funding": "funding.html",
    f"{BASE}/graduate/deadlines": "conflict_deadline.html",
    f"{BASE}/news": "injection.html",
    f"{BASE}/people/rivera": "faculty_profile.html",
    f"{BASE}/people/chen": "faculty_noemail.html",
}
EXTRACTIONS = {
    f"{BASE}/cs/ms": ms_page_extraction,
    f"{BASE}/graduate/tuition": tuition_extraction,
    f"{BASE}/cs/funding": funding_extraction,
    f"{BASE}/graduate/deadlines": conflict_extraction,
    f"{BASE}/news": injection_extraction,
    f"{BASE}/people/rivera": professor_extraction,
    f"{BASE}/people/chen": professor_noemail_extraction,
}
