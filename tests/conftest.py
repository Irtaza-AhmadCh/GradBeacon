from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.fallback.loader import CuratedDataset  # noqa: E402
from app.main import create_app  # noqa: E402
from app.providers.email.base import UnconfiguredEmailProvider  # noqa: E402
from app.providers.factory import Providers  # noqa: E402
from app.tools.search_service import SearchService  # noqa: E402
from tests.fakes.fakes import EXTRACTIONS, ROUTES, FakeFetcher, FakeLLM, FakeSearchProvider  # noqa: E402

SECRET = "test-approval-secret-0123456789-abcdefghijklmnop"
FAKE_KEY = "gsk_test_THISISAFAKEKEYFORTESTS0123"


def make_settings(**over) -> Settings:
    base = dict(_env_file=None, app_env="development", app_mode="live", llm_provider="groq", groq_api_key_1=SecretStr(FAKE_KEY),
                tavily_api_key=SecretStr("tvly-fakekeyfortests123"), approval_token_secret=SecretStr(SECRET),
                request_deadline_seconds=60, verify_deadline_seconds=30, professor_deadline_seconds=30,
                rate_research_per_10min=100)
    base.update(over)
    return Settings(**base)


def make_providers(settings: Settings, llm=None, search=None, fetcher=None, email=None, mode="live") -> Providers:
    return Providers(search=SearchService(search or FakeSearchProvider(), settings) if mode == "live" else None,
                     llm=llm if mode == "live" else None, email=email or UnconfiguredEmailProvider(),
                     fetcher=fetcher or FakeFetcher(ROUTES), effective_mode=mode, config_warnings=[])


@pytest.fixture
def curated_fixture(tmp_path) -> CuratedDataset:
    """Tiny curated dataset built from the fixture page (for fallback tests)."""
    import json

    from app.core import clock
    from app.models.common import DegreeLevel
    from app.models.evidence import Claim, Evidence
    from app.models.program import Deadline, ProgramOpportunity

    now = clock.utc_now()
    ev = Evidence(source_id="src_x", chunk_id="src_x#c0", url="https://www.example.edu/cs/ms",
                  quote="The application deadline for Fall 2027 admission is December 15, 2026", retrieved_at=now)
    rec = ProgramOpportunity(program_id="prg_curated01", institution_name="Example State University",
                             program_name="Master of Science in Computer Science", degree_level=DegreeLevel.masters,
                             field="Computer Science", official_program_url="https://www.example.edu/cs/ms",
                             deadlines=[Deadline(term="Fall 2027", applicant_type="all",
                                                 claim=Claim(value="December 15, 2026", status="verified", evidence=[ev]))],
                             has_verified_program_page=True, retrieved_at=now, last_verified_at=now)
    path = tmp_path / "curated.json"
    path.write_text(json.dumps({"dataset_version": "test", "curated_at": "2026-10-17",
                                "records": [rec.model_dump(mode="json")]}), encoding="utf-8")
    return CuratedDataset(path)


@pytest.fixture
def build_app(curated_fixture):
    def _build(llm=None, search=None, fetcher=None, email=None, mode="live", **settings_over):
        settings = make_settings(**settings_over)
        providers = make_providers(settings, llm or FakeLLM(extractions=EXTRACTIONS), search, fetcher, email, mode)
        return create_app(settings, providers, curated_fixture)
    return _build
