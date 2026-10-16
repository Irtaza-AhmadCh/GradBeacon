"""Build provider adapters from settings, honoring the effective mode (keys never leave adapters)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import Settings
from app.providers.email.base import EmailProvider, UnconfiguredEmailProvider
from app.providers.llm.base import LLMProvider
from app.tools.fetcher import PageFetcher, SafePageFetcher
from app.tools.search_service import SearchService

log = logging.getLogger("gradbeacon.config")


@dataclass
class Providers:
    search: SearchService | None
    llm: LLMProvider | None
    email: EmailProvider
    fetcher: PageFetcher
    effective_mode: str
    config_warnings: list[str]


def build_email_provider(settings: Settings) -> EmailProvider:
    # Sending deferred by the owner. Return a real provider here once implemented.
    return UnconfiguredEmailProvider()


def build_providers(settings: Settings) -> Providers:
    warnings: list[str] = []
    effective = settings.app_mode
    missing = settings.missing_for_live() if settings.app_mode == "live" else []
    if missing:
        log.warning("config_error", extra={"missing_variables": missing})
        warnings.append("Live research disabled: missing configuration " + ", ".join(missing))
        effective = "fallback_only"
    search = llm = None
    extractor = None
    if effective == "live":
        from app.providers.llm.groq_provider import GroqLLMProvider
        from app.providers.search.tavily_provider import TavilySearchProvider

        tavily = TavilySearchProvider(settings.tavily_api_key.get_secret_value())
        extractor = tavily
        search = SearchService(tavily, settings)
        llm = GroqLLMProvider(api_keys=settings.groq_api_keys, model=settings.llm_model,
                              writing_model=settings.writing_model, timeout=settings.llm_timeout_seconds)
    log.info("effective_mode", extra={"effective_mode": effective})
    return Providers(search=search, llm=llm, email=build_email_provider(settings), fetcher=SafePageFetcher(settings, extractor=extractor),
                     effective_mode=effective, config_warnings=warnings)
