from __future__ import annotations

from fastapi import Request

from app.orchestrator.context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def client_ip(request: Request) -> str:
    ctx: AppContext = request.app.state.ctx
    if ctx.settings.trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
