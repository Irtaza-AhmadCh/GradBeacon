"""OutreachDrafter — grounded, editable draft. LLM writes wording only; guards + fixed template fallback."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from app.core.errors import InvalidState, ProviderError
from app.deliverables.templates import outreach_template
from app.models.professor import ProfessorMatch
from app.outreach.guards import guard_draft
from app.providers.llm.base import LLMProvider

SYSTEM = """You draft a short, polite, specific first-contact email from a prospective graduate student to a professor.
Use ONLY facts in the JSON input: the professor's verified research interests/publications and the student's own
statements (labelled student_asserted). Do not invent papers, interests, achievements, numbers, connections or prior
contact. Do not claim or imply admission. Mention at most two professor interests. Include the professor's official
profile URL once. Plain text only, <= 220 words. End with the student's name and email. Return JSON only."""


class _DraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str
    body: str


REMINDERS = [
    "Verify the professor's contact preferences on their official page before sending (some ask students to apply first).",
    "This email will be sent only after you review, optionally edit, and explicitly approve the final text.",
    "Check every statement about yourself is accurate; GradBeacon only used the facts you provided.",
]


class OutreachDrafter:
    def __init__(self, llm: LLMProvider | None, effort: str):
        self.llm = llm
        self.effort = effort

    async def draft(self, prof: ProfessorMatch, program_name: str | None, student: dict) -> tuple[str, str, str]:
        if not prof.research_interests or prof.official_email is None:
            raise InvalidState("Not enough verified information to draft (needs official email and research interests)")
        interests = [c.value for c in prof.research_interests if c.value][:5]
        pubs = [{"title": p.title, "year": p.year, "url": p.url} for p in prof.verified_publications[:3]]
        payload = {
            "professor": {"name": prof.name, "title": prof.title.value if prof.title else None,
                          "department": prof.department.value if prof.department else None,
                          "verified_research_interests": interests, "verified_publications": pubs,
                          "official_profile_url": prof.official_profile_url},
            "program_name": program_name,
            "student_asserted": {k: student.get(k) for k in ("name", "email", "current_degree", "institution",
                                                             "interests", "achievements")},
            "goal": student.get("message_goal"),
        }
        input_text = json.dumps(payload, sort_keys=True)
        template = outreach_template(
            professor_name=prof.name, interests=interests, profile_url=prof.official_profile_url,
            student_name=student["name"], student_email=student["email"], current_degree=student["current_degree"],
            institution=student["institution"], student_interests=student.get("interests") or [],
            goal=student.get("message_goal") or "ask_about_research_fit", program_name=program_name)
        if self.llm is None:
            return *template, "template"
        try:
            out = await self.llm.write_json(system=SYSTEM, user_content=input_text, schema_model=_DraftOut,
                                            effort=self.effort)
        except ProviderError:
            return *template, "template"
        allowed_urls = {prof.official_profile_url} | {p["url"] for p in pubs if p["url"]}
        phrases = interests + [p["title"] for p in pubs] + list(student.get("achievements") or []) + \
            list(student.get("interests") or [])
        problems = guard_draft(out.subject, out.body, input_text, allowed_urls, {student["email"]}, phrases)
        if problems or len(out.body.split()) > 260 or len(out.subject) > 120 or not out.body.strip():
            return *template, "template"
        return out.subject.strip(), out.body.strip(), "llm"
