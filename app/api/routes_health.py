from __future__ import annotations

from fastapi import APIRouter, Depends

from app import __version__
from app.api.deps import get_ctx
from app.orchestrator.context import AppContext

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(ctx: AppContext = Depends(get_ctx)):
    """Liveness + safe configuration status. Never returns secret values."""
    s = ctx.settings

    def st(v) -> str:
        return "configured" if (v is not None and v.get_secret_value().strip()) else "missing"

    return {
        "status": "ok", "version": __version__, "app_env": s.app_env, "app_mode": s.app_mode,
        "effective_mode": ctx.providers.effective_mode,
        "providers": {"llm": "configured" if s.llm_api_keys else "missing", "search": st(s.tavily_api_key),
                      "email": "configured" if getattr(ctx.providers.email, "configured", False) else "not_implemented",
                      "approval_signing": st(s.approval_token_secret)},
        "email_send_mode": s.email_send_mode,
        "curated_records": len(ctx.curated.records),
        "curated_at": ctx.curated.curated_at,
        "demo_replay_available": ctx.replay_available,
        "jobs_running": ctx.store.running_count(),
        "warnings": ctx.providers.config_warnings,
    }
