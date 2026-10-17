"""Application settings loaded from environment variables / .env (never logged)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: Literal["development", "production"] = "development"
    app_mode: Literal["live", "fallback_only", "demo_replay"] = "live"
    log_level: str = "INFO"
    allowed_origins: str = ""
    trust_proxy_headers: bool = False
    serve_frontend: bool = True

    # --- LLM (Gemini or Groq, native tool use; both via OpenAI-compatible chat completions) ---
    llm_provider: Literal["gemini", "groq"] = "gemini"
    gemini_api_key_1: SecretStr | None = None
    gemini_api_key_2: SecretStr | None = None
    gemini_api_key_3: SecretStr | None = None
    gemini_api_key_4: SecretStr | None = None
    gemini_api_key_5: SecretStr | None = None
    gemini_api_key_6: SecretStr | None = None
    groq_api_key_1: SecretStr | None = None
    groq_api_key_2: SecretStr | None = None
    groq_api_key_3: SecretStr | None = None
    groq_api_key_4: SecretStr | None = None
    groq_api_key_5: SecretStr | None = None
    groq_api_key_6: SecretStr | None = None
    llm_model: str = "gemini-3.5-flash"
    # CSV; tried in order when every key is out of quota for the current model (quotas are per model)
    llm_fallback_models: str = "gemini-3.8-flash,gemini-flash-latest,gemini-3-flash-preview,gemini-3.1-flash-lite"
    llm_writing_model: str | None = None
    llm_effort_research: Literal["low", "medium", "high"] = "low"
    llm_effort_extraction: Literal["low", "medium", "high"] = "low"
    llm_effort_writing: Literal["low", "medium", "high"] = "medium"
    llm_timeout_seconds: float = 60.0

    # --- Search (Tavily) ---
    search_provider: Literal["tavily"] = "tavily"
    tavily_api_key: SecretStr | None = None

    # --- Email (sending deferred by owner; adapter slot only) ---
    email_provider: Literal["none"] = "none"
    email_send_mode: Literal["disabled", "sandbox", "live"] = "live"
    email_sandbox_to: str | None = None

    # --- Approval tokens ---
    approval_token_secret: SecretStr | None = None
    approval_token_ttl_seconds: int = 600

    # --- Limits / budgets (initial assumptions; tune after measuring) ---
    max_results_hard_cap: int = 15
    default_target_results: int = 8
    max_professors: int = 8
    max_loop_turns: int = 12
    max_search_calls: int = 8
    max_page_fetches: int = 14
    max_llm_calls: int = 40
    first_batch_candidates: int = 4
    max_page_bytes: int = 2_000_000
    max_extraction_chars: int = 12_000  # sized for Groq free-tier tokens-per-minute limits
    fetch_connect_timeout: float = 5.0
    fetch_total_timeout: float = 15.0
    max_redirects: int = 3
    fetch_provider_fallback: bool = True  # use search provider's extract API when a site refuses direct fetch
    request_deadline_seconds: int = 180
    verify_deadline_seconds: int = 60
    professor_deadline_seconds: int = 150
    job_ttl_seconds: int = 3600
    max_jobs: int = 100
    max_concurrent_jobs: int = 4
    rate_research_per_10min: int = 5
    rate_send_per_hour: int = 3
    rate_send_per_day_global: int = 30
    extra_official_domains: str = ""

    # --- Demo replay ---
    demo_replay_path: str = "data/demo_replay.json"
    demo_replay_speedup: float = 4.0

    @field_validator("max_professors")
    @classmethod
    def _cap_professors(cls, v: int) -> int:
        return max(1, min(v, 8))

    @field_validator("max_results_hard_cap")
    @classmethod
    def _cap_results(cls, v: int) -> int:
        return max(1, min(v, 15))

    # ---- derived helpers ----
    @property
    def fallback_models(self) -> list[str]:
        return [m.strip() for m in self.llm_fallback_models.split(",") if m.strip()]

    @property
    def writing_model(self) -> str:
        return self.llm_writing_model or self.llm_model

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def extra_official(self) -> set[str]:
        return {d.strip().lower() for d in self.extra_official_domains.split(",") if d.strip()}

    def secret_values(self) -> list[str]:
        out: list[str] = []
        for name in (*_key_names("gemini"), *_key_names("groq"), "tavily_api_key", "approval_token_secret"):
            v = getattr(self, name)
            if v is not None and v.get_secret_value():
                out.append(v.get_secret_value())
        return out

    def missing_for_live(self) -> list[str]:
        missing = []
        if not self.llm_api_keys:
            missing.append(f"{self.llm_provider.upper()}_API_KEY_1..6 (at least one)")
        if not _present(self.tavily_api_key):
            missing.append("TAVILY_API_KEY")
        return missing

    def _keys(self, provider: str) -> list[str]:
        return [v.get_secret_value().strip() for name in _key_names(provider)
                if (v := getattr(self, name)) is not None and v.get_secret_value().strip()]

    @property
    def groq_api_keys(self) -> list[str]:
        return self._keys("groq")

    @property
    def gemini_api_keys(self) -> list[str]:
        return self._keys("gemini")

    @property
    def llm_api_keys(self) -> list[str]:
        return self._keys(self.llm_provider)


def _key_names(provider: str) -> tuple[str, ...]:
    return tuple(f"{provider}_api_key_{i}" for i in range(1, 7))


def _present(v: SecretStr | None) -> bool:
    return v is not None and bool(v.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
