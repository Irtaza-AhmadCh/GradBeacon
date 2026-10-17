"""Manual-send handoff links must reproduce the approved draft exactly."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from app.outreach.sender import gmail_compose_link


def test_gmail_compose_link_round_trips_the_approved_text():
    draft = SimpleNamespace(recipient_email="jdoe@cs.example.edu", subject="MS applicant: ML & robotics?",
                            body="Dear Prof. Doe,\n\nYour 50% faster #planning paper...\n\nBest,\nAli")
    url = urlparse(gmail_compose_link(draft))
    q = parse_qs(url.query)
    assert (url.scheme, url.netloc, url.path) == ("https", "mail.google.com", "/mail/")
    assert q["view"] == ["cm"]
    assert (q["to"][0], q["su"][0], q["body"][0]) == (draft.recipient_email, draft.subject, draft.body)
