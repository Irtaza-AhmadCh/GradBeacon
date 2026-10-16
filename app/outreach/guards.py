"""Deterministic guards against invented content in outreach drafts (FR-38/39)."""

from __future__ import annotations

import re

from app.deliverables.explanations import FORBIDDEN

DRAFT_FORBIDDEN = FORBIDDEN + ["as we discussed", "as you know me", "we met", "when we spoke", "my paper with you",
                               "you recommended", "admitted", "your offer"]
_URL = re.compile(r"https?://[^\s)>\]]+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_QUOTED = re.compile(r"[\"“]([^\"”]{8,})[\"”]")


def guard_draft(subject: str, body: str, input_text: str, allowed_urls: set[str], allowed_emails: set[str],
                allowed_phrases: list[str]) -> list[str]:
    """Returns a list of violations (empty = OK)."""
    problems: list[str] = []
    text = f"{subject}\n{body}"
    low = text.lower()
    for f in DRAFT_FORBIDDEN:
        if f in low:
            problems.append(f"forbidden phrase: {f}")
    for n in re.findall(r"\d[\d,.]*", text):
        n = n.rstrip(".,")
        if n and n not in input_text:
            problems.append(f"number not in input: {n}")
    for u in _URL.findall(text):
        if u.rstrip(".,") not in allowed_urls:
            problems.append("url not in input")
    for e in _EMAIL.findall(text):
        if e.lower() not in {x.lower() for x in allowed_emails}:
            problems.append("email not allowed")
    lowered = [p.lower() for p in allowed_phrases]
    for q in _QUOTED.findall(text):
        if len(q.split()) >= 4 and not any(q.lower().strip() in p for p in lowered):
            problems.append("quoted text not grounded")
    if re.search(r"<[a-zA-Z/!]", text):
        problems.append("html not allowed")
    return problems
