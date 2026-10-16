"""Explainer: LLM writes wording only from validated facts + scores; deterministic guard + template fallback."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict

from app.core.errors import ProviderError
from app.deliverables.templates import explanation_template
from app.models.program import ProgramOpportunity
from app.models.results import Explanation, MatchResult
from app.providers.llm.base import LLMProvider

FORBIDDEN = ["admission chance", "chance of admission", "probability", "you will be admitted", "guaranteed admission",
             "you qualify for", "you are eligible for funding", "you will receive", "you will get funding",
             "likely to be admitted", "will be accepted"]

SYSTEM = """You write short, factual explanations of why a graduate program fits a student's profile.
Use ONLY the facts and score reasons in the JSON input. Do not add facts, numbers, rankings or reputation claims.
Never describe the score as an admission chance or probability, and never say the student is eligible for or will
receive funding. Mention important unknowns as cautions. summary <= 60 words; up to 3 match_reasons and 3 cautions,
each <= 25 words. Return JSON only."""


class _ExplOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    match_reasons: list[str]
    cautions: list[str]


def explanation_input(p: ProgramOpportunity, m: MatchResult, profile_summary: dict) -> dict:
    return {
        "student": {k: profile_summary.get(k) for k in ("field", "specialization", "research_interests")},
        "program": {"name": p.program_name, "institution": p.institution_name, "degree_level": p.degree_level.value,
                    "verified_research_areas": [c.value for c in p.research_areas][:10],
                    "field_status": p.field_labels, "warnings": p.warnings},
        "score": {"overall_fit_score": m.overall_fit_score, "coverage": m.coverage,
                  "academic": m.academic_fit.model_dump(), "research": m.research_fit.model_dump(),
                  "funding": m.funding_fit.model_dump(), "preference": m.preference_fit.model_dump(),
                  "known_matches": m.known_matches, "known_gaps": m.known_gaps, "unknowns": m.unknowns},
    }


def guard_text(texts: list[str], input_json: str) -> bool:
    joined = " ".join(texts).lower()
    if any(f in joined for f in FORBIDDEN):
        return False
    for num in re.findall(r"\d[\d,.]*", " ".join(texts)):
        n = num.rstrip(".,")
        if n and n not in input_json and n.replace(",", "") not in input_json:
            return False
    return True


class Explainer:
    def __init__(self, llm: LLMProvider | None, effort: str):
        self.llm = llm
        self.effort = effort

    async def explain(self, p: ProgramOpportunity, m: MatchResult, profile_summary: dict) -> tuple[Explanation, int]:
        """Returns (explanation, llm_calls_used)."""
        if self.llm is None:
            return explanation_template(p, m), 0
        inp = json.dumps(explanation_input(p, m, profile_summary), sort_keys=True, default=str)
        try:
            out = await self.llm.write_json(system=SYSTEM, user_content=inp, schema_model=_ExplOut, effort=self.effort)
        except ProviderError:
            return explanation_template(p, m), 1
        texts = [out.summary, *out.match_reasons, *out.cautions]
        if not guard_text(texts, inp) or len(out.summary.split()) > 90:
            return explanation_template(p, m), 1
        summary = out.summary.strip()
        if "not an admission probability" not in summary.lower():
            summary += " (Profile-fit score, not an admission probability.)"
        return Explanation(summary=summary, match_reasons=out.match_reasons[:3], cautions=out.cautions[:3],
                           generated_by="llm"), 1
