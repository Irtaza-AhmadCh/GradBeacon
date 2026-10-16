"""Deterministic templates used when the LLM is unavailable or its output fails guards."""

from __future__ import annotations

from app.models.program import ProgramOpportunity
from app.models.results import DISCLAIMER, Explanation, MatchResult


def explanation_template(p: ProgramOpportunity, m: MatchResult) -> Explanation:
    score = f"{m.overall_fit_score}/100" if m.overall_fit_score is not None else "not computed (insufficient verified data)"
    summary = (f"{p.program_name} at {p.institution_name}: profile-fit {score} across {m.coverage}. {DISCLAIMER}")
    return Explanation(summary=summary, match_reasons=m.known_matches[:3],
                       cautions=(m.known_gaps + m.unknowns)[:3], generated_by="template")


def outreach_template(*, professor_name: str, interests: list[str], profile_url: str, student_name: str,
                      student_email: str, current_degree: str, institution: str, student_interests: list[str],
                      goal: str, program_name: str | None) -> tuple[str, str]:
    prof_interest = ", ".join(interests[:2]) if interests else "your research"
    mine = ", ".join(student_interests[:3]) if student_interests else "related topics"
    subject = f"Prospective graduate student interested in {interests[0] if interests else 'your research'}"[:120]
    ask = ("I would be grateful to know whether you expect to consider new graduate students in your group, "
           "and whether there is anything you recommend I review before applying."
           if goal == "inquire_about_openings" else
           "I would value any brief thoughts on whether my interests could fit your group's work.")
    prog = f" I am considering applying to the {program_name}." if program_name else ""
    body = (
        f"Dear Professor {professor_name.split()[-1]},\n\n"
        f"My name is {student_name}, and I am a {current_degree} student at {institution}.{prog}\n\n"
        f"Your official profile describes research in {prof_interest}, which connects with my interests in {mine}.\n\n"
        f"{ask}\n\n"
        f"Your profile: {profile_url}\n\n"
        f"Thank you for your time.\n\nBest regards,\n{student_name}\n{student_email}"
    )
    return subject, body
