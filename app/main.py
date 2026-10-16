"""GradBeacon API — single FastAPI service (run with exactly ONE worker: in-memory job state)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import error_handlers
from app.api.rate_limit import Limits
from app.api.routes_demo import router as demo_router
from app.api.routes_health import router as health_router
from app.api.routes_outreach import router as outreach_router
from app.api.routes_professors import router as professors_router
from app.api.routes_research import router as research_router
from app.core.config import Settings, get_settings
from app.core.ids import new_id
from app.core.logging import configure_logging
from app.fallback.loader import CuratedDataset
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import JobStore
from app.providers.factory import Providers, build_providers
from app.scoring.scorer import Scorer

log = logging.getLogger("gradbeacon")
MAX_BODY_BYTES = 32 * 1024
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def create_app(settings: Settings | None = None, providers: Providers | None = None,
               curated: CuratedDataset | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.secret_values())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def sweeper():
            while True:
                await asyncio.sleep(60)
                app.state.ctx.store.sweep()
        task = asyncio.create_task(sweeper())
        log.info("startup", extra={"version": __version__, "effective_mode": app.state.ctx.providers.effective_mode,
                                   "curated_records": len(app.state.ctx.curated.records)})
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(Exception):
                await app.state.ctx.providers.fetcher.aclose()

    app = FastAPI(title="GradBeacon API", version=__version__, lifespan=lifespan,
                  description="Evidence-first US graduate-opportunity research agent with human approval gates. "
                              "Fit scores are profile-fit scores, not admission probabilities.")
    app.state.ctx = AppContext(
        settings=settings, providers=providers or build_providers(settings),
        store=JobStore(settings.job_ttl_seconds, settings.max_jobs, settings.max_concurrent_jobs),
        curated=curated or CuratedDataset(), scorer=Scorer())
    app.state.limits = Limits(settings.rate_research_per_10min, settings.rate_send_per_hour,
                              settings.rate_send_per_day_global)

    @app.middleware("http")
    async def request_guard(request: Request, call_next):
        request.state.request_id = new_id("req")
        if request.method in ("POST", "PUT", "PATCH"):
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
                return JSONResponse({"error": {"code": "too_large", "message": "Request body too large",
                                               "request_id": request.state.request_id}}, status_code=413,
                                    headers={"X-Request-ID": request.state.request_id})
            body = await request.body()
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse({"error": {"code": "too_large", "message": "Request body too large",
                                               "request_id": request.state.request_id}}, status_code=413,
                                    headers={"X-Request-ID": request.state.request_id})
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    if settings.origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_methods=["GET", "POST", "PUT"],
                           allow_headers=["Content-Type", "Last-Event-ID"], allow_credentials=False)
    error_handlers.install(app)
    app.include_router(health_router)
    app.include_router(research_router)
    app.include_router(professors_router)
    app.include_router(outreach_router)
    app.include_router(demo_router)

    if settings.serve_frontend and FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"name": "GradBeacon API", "version": __version__, "docs": "/docs", "health": "/health"}
    return app


app = create_app()
