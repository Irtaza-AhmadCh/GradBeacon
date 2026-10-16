from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import date, timedelta

import httpx
import pytest
import respx

from app.core import clock
from app.core.config import Settings
from app.core.errors import InvalidState, ProviderError
from app.core.logging import SecretRedactionFilter
from app.models.common import DegreeLevel, ErrorCategory, FieldStatus, FundingPreference, TuitionPeriod
from app.models.evidence import Claim
from app.models.outreach import Draft, DraftStatus
from app.models.profile import StudentProfile
from app.models.program import ProgramOpportunity, Requirement, Tuition
from app.outreach.approval import ApprovalService, content_hash
from app.providers.llm.groq_provider import strict_schema
from app.scoring.scorer import Scorer
from app.tools.fetcher import SafePageFetcher, decode_html
from app.tools.url_safety import canonicalize, check_static, validate_public_url
from app.validation.normalizer import annualize, parse_date, parse_gpa_min, parse_money, parse_period, parse_test_mins


# ---------------- SSRF / URL safety ----------------
@pytest.mark.parametrize("url", [
    "ftp://example.edu/x", "http://user:pw@example.edu/", "http://127.0.0.1/", "http://10.0.0.5/",
    "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://100.64.1.1/",
    "http://localhost/", "http://example.edu:8080/", "http://intranet.local/",
])
def test_static_url_rejections(url):
    with pytest.raises(ProviderError) as e:
        check_static(url)
    assert e.value.category == ErrorCategory.blocked_url


async def test_dns_resolving_to_private_is_blocked(monkeypatch):
    async def fake_getaddrinfo(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", port))]
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ProviderError) as e:
        await validate_public_url("https://rebind.example.edu/")
    assert e.value.category == ErrorCategory.blocked_url


def test_canonicalize():
    assert canonicalize("HTTPS://WWW.Example.EDU/a/b/?utm_source=x&id=2#frag") == "https://www.example.edu/a/b?id=2"


# ---------------- Fetcher (respx) ----------------
@pytest.fixture
def public_dns(monkeypatch):
    async def fake_getaddrinfo(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo",
                        lambda self, host, port, **kw: fake_getaddrinfo(host, port))


@respx.mock
async def test_fetcher_policies(public_dns):
    s = Settings(_env_file=None)
    f = SafePageFetcher(s)
    html = "<html><head><title>T</title></head><body><p>" + ("Graduate admissions text. " * 30) + "</p></body></html>"
    respx.get("https://a.example.edu/ok").mock(return_value=httpx.Response(200, html=html))
    respx.get("https://a.example.edu/pdf").mock(return_value=httpx.Response(200, content=b"%PDF",
                                                                            headers={"content-type": "application/pdf"}))
    respx.get("https://a.example.edu/redir").mock(return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/"}))
    respx.get("https://a.example.edu/offsite").mock(return_value=httpx.Response(302, headers={"location": "https://evil.com/"}))
    respx.get("https://a.example.edu/big").mock(return_value=httpx.Response(200, content=b"x" * (s.max_page_bytes + 10),
                                                                            headers={"content-type": "text/html"}))
    respx.get("https://a.example.edu/404").mock(return_value=httpx.Response(404, html="no"))
    ok = await f.fetch("https://a.example.edu/ok")
    assert ok.ok and ok.source.is_official and ok.chunks and ok.source.content_hash
    again = await f.fetch("https://a.example.edu/ok")
    assert again.source.freshness.value == "cached"
    assert (await f.fetch("https://a.example.edu/pdf")).source.error_category == ErrorCategory.unsupported_content
    assert (await f.fetch("https://a.example.edu/redir")).source.error_category == ErrorCategory.blocked_url
    assert (await f.fetch("https://a.example.edu/offsite")).source.error_category == ErrorCategory.blocked_url
    assert (await f.fetch("https://a.example.edu/big")).source.error_category == ErrorCategory.too_large
    r404 = await f.fetch("https://a.example.edu/404")
    assert r404.source.error_category == ErrorCategory.fetch_http_error and r404.source.url  # URL kept (FR-48)
    assert (await f.fetch("https://www.google.com/")).source.error_category == ErrorCategory.blocked_url


def test_decode_html_fallbacks():
    assert decode_html("café".encode("cp1252"), None) == "café"
    assert decode_html("naïve".encode(), "utf-8") == "naïve"


# ---------------- Normalizer ----------------
def test_money_and_periods():
    assert parse_money("$54,000 per year") == (54000.0, "USD")
    assert parse_money("USD 1,850 per credit") == (1850.0, "USD")
    assert parse_money("Tuition varies") is None
    assert parse_period("$1,200 per credit hour") == TuitionPeriod.per_credit
    assert parse_period("per academic year") == TuitionPeriod.per_year
    assert annualize(1200, TuitionPeriod.per_credit) is None  # never invents credit counts
    assert annualize(10000, TuitionPeriod.per_semester) == 20000


def test_dates_gpa_tests():
    assert parse_date("December 15, 2026") == date(2026, 12, 15)
    assert parse_date("December 1", "Fall 2027") == date(2026, 12, 1)
    assert parse_date("Rolling admissions") is None
    assert parse_gpa_min("3.0 on a 4.0 scale") == (3.0, "4.0")
    assert ("TOEFL_iBT", 90.0) in parse_test_mins("TOEFL iBT score of at least 90; IELTS 7.0")
    assert ("IELTS", 7.0) in parse_test_mins("TOEFL iBT score of at least 90; IELTS 7.0")


# ---------------- Scoring ----------------
def _profile(**kw) -> StudentProfile:
    base = dict(degree_paths=["masters"], field="Computer Science", qualification="BS CS", qualification_status="completed",
                gpa={"value": 8.5, "scale": "10.0"}, research_interests=["machine learning"])
    base.update(kw)
    return StudentProfile.model_validate(base)


def _program(**kw) -> ProgramOpportunity:
    v = Claim(value="x", status=FieldStatus.verified)
    base = dict(program_id="prg_1", institution_name="U", program_name="MS Computer Science", degree_level=DegreeLevel.masters,
                field="Computer Science", official_program_url="https://u.edu/p",
                requirements=[Requirement(key="min_gpa", label="Minimum GPA", claim=v, numeric_min=3.0, numeric_scale="4.0")],
                research_areas=[Claim(value="machine learning", status=FieldStatus.verified)])
    base.update(kw)
    return ProgramOpportunity(**base)


def test_scorer_unknowns_not_zero_and_deterministic():
    s = Scorer()
    p = _program()
    m1, m2 = s.score(_profile(), p), s.score(_profile(), p)
    assert m1 == m2
    assert m1.academic_fit.score is None  # scale mismatch -> unknown, not 0
    assert any("scale" in r.lower() for r in m1.academic_fit.reasons)
    assert m1.funding_fit.score is None and m1.funding_fit.status == "unknown"
    assert m1.overall_fit_score is None  # only research known -> insufficient
    assert "not an admission probability" in m1.disclaimer


def test_scorer_budget_and_funding_unknown_never_zero():
    s = Scorer()
    p = _program(tuition=[Tuition(amount=20000, period=TuitionPeriod.per_year, residency="international",
                                  annualized_usd=20000, claim=Claim(value="$20,000", status=FieldStatus.verified))])
    m = s.score(_profile(gpa={"value": 3.5, "scale": "4.0"}, budget={"max_tuition_per_year_usd": 25000},
                         funding_preference=FundingPreference.need_full_funding), p)
    assert m.academic_fit.score == 100 and m.preference_fit.score == 100
    assert m.funding_fit.score is None  # funding missing -> unknown, never 0
    assert m.overall_fit_score is not None and m.sort_tuition_annual_usd == 20000


def test_profile_optional_fields_and_validation():
    p = StudentProfile.model_validate(dict(degree_paths=["phd", "phd", "masters"], field=" CS ", qualification="BS",
                                           qualification_status="in_progress", gpa=None))
    assert p.degree_paths == [DegreeLevel.phd, DegreeLevel.masters] and p.field == "CS"
    with pytest.raises(ValueError):
        StudentProfile.model_validate(dict(degree_paths=[], field="CS", qualification="BS",
                                           qualification_status="completed", gpa=None))


# ---------------- Strict schema ----------------
def test_strict_schema_nested():
    from app.extraction.schemas import ProgramPageExtraction
    sch = strict_schema(ProgramPageExtraction)

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object" and "properties" in n:
                assert n["additionalProperties"] is False
                assert set(n["required"]) == set(n["properties"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(sch)
    json.dumps(sch)


# ---------------- Redaction ----------------
def test_secret_redaction():
    f = SecretRedactionFilter(["supersecretvalue123"])
    rec = logging.LogRecord("x", logging.INFO, "", 0, "key=%s and sk-ant-abc123XYZ tvly-zzz999", ("supersecretvalue123",), None)
    rec.extra_field = "supersecretvalue123"
    f.filter(rec)
    assert "supersecretvalue123" not in rec.msg and "sk-ant-abc" not in rec.msg and "tvly-zzz" not in rec.msg
    assert rec.extra_field == "[REDACTED]"


# ---------------- Approval tokens ----------------
def _draft() -> Draft:
    now = clock.utc_now()
    from app.models.evidence import Evidence
    return Draft(draft_id="drf_0123456789abcdef", job_id="pjob_x", professor_id="prof_x", recipient_email="p@u.edu",
                 recipient_source=Evidence(source_id="s", url="https://u.edu", quote="Email: p@u.edu"),
                 professor_profile_url="https://u.edu", subject="S", body="B", version=1,
                 content_hash=content_hash("S", "B"), generated_by="template", status=DraftStatus.awaiting_review,
                 created_at=now, updated_at=now)


def test_approval_expiry_and_recipient(monkeypatch):
    svc = ApprovalService("x" * 40, 600)
    d = _draft()
    tok, appr = svc.issue(d)
    d.approval = appr
    later = clock.utc_now() + timedelta(seconds=700)
    monkeypatch.setattr(clock, "utc_now", lambda: later)
    with pytest.raises(InvalidState) as e:
        svc.verify_and_consume(d, tok)
    assert e.value.code == "approval_expired"
    monkeypatch.undo()
    tok, appr = svc.issue(d)
    d.approval = appr
    d.recipient_email = "other@u.edu"
    with pytest.raises(InvalidState) as e:
        svc.verify_and_consume(d, tok)
    assert e.value.code == "recipient_mismatch"


def test_approval_requires_secret():
    from app.core.errors import ConfigError
    with pytest.raises(ConfigError):
        ApprovalService(None, 600).issue(_draft())


@respx.mock
async def test_provider_extract_fallback_when_site_refuses(public_dns):
    class Ext:
        async def extract(self, url):
            return "# Graduate Admissions\n\nThe application deadline is December 1 for Fall 2027 admission. " * 5 + \
                   "\n\n[Tuition](https://a.example.edu/tuition) [Elsewhere](https://other.org/x)"
    f = SafePageFetcher(Settings(_env_file=None), extractor=Ext())
    respx.get("https://a.example.edu/blocked").mock(return_value=httpx.Response(403, html="denied"))
    page = await f.fetch("https://a.example.edu/blocked")
    assert page.ok and page.source.retrieval_method == "provider_extract" and page.chunks
    assert [lk.url for lk in page.links] == ["https://a.example.edu/tuition"]
    # policy-blocked domains never use the fallback
    assert (await f.fetch("https://www.google.com/")).source.error_category == ErrorCategory.blocked_url


def test_regressions_found_during_curation():
    from app.validation.merger import funding_is_negative
    from app.validation.normalizer import date_tokens_present
    assert date_tokens_present("December 15", "The deadline for the Fall 2026 semester is December 15th.")
    assert not date_tokens_present("December 15", "due December 1.")
    assert funding_is_negative("There is no university or departmental financial aid for this program")
    assert funding_is_negative("MS students are self-funded")
    assert not funding_is_negative("Partial merit scholarships are available but not guaranteed to all")
    assert not funding_is_negative("Teaching assistantships are available on a competitive basis")


def test_curated_dataset_is_labelled_and_evidenced():
    from app.fallback.loader import CuratedDataset
    ds = CuratedDataset()
    assert len(ds.records) >= 12 and ds.curated_at
    for p in ds.records:
        assert p.fallback_record and p.verification_status.value == "fallback" and p.last_verified_at
        claims = [t.claim for t in p.tuition] + [f.claim for f in p.funding] + [d.claim for d in p.deadlines] + \
            [r.claim for r in p.requirements] + p.required_documents
        assert claims and all(c.status.value == "curated_fallback" and c.evidence for c in claims)
        assert all(e.url.startswith("https://") and ".edu" in e.url for c in claims for e in c.evidence)
