"""
SGOS Backend — FastAPI Application
Slim app factory: registers middleware and routers.
All business logic lives in routers/ modules.
"""
import hmac
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from config import settings
from database import init_db
from reddit_ingest import TARGET_SUBREDDITS
from voice_profile import init_voice_tables
from creators import init_creator_tables
from vector_search import init_vector_tables
from boards import init_board_tables
from idea_generation import init_ideas_table
from firecrawl_scrape import ensure_scraped_at_column
from observability import log, metrics
from scheduler import init_scheduler

from routers import research, search, ingestion, voice, creators
from routers import alerts, boards, content, scrape, media, analytics, chat
from routers import pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all database tables on startup."""
    init_db()
    init_voice_tables()
    init_creator_tables()
    init_vector_tables()
    init_board_tables()
    init_ideas_table()
    ensure_scraped_at_column()
    log.info("sgos.startup", version=settings.version, db=settings.db_path, subreddits=len(TARGET_SUBREDDITS))
    if settings.api_key:
        log.info("auth.enabled")
    else:
        log.warn("auth.disabled", hint="Set SGOS_API_KEY for production")
        log.error("auth.production_risk", detail="Server has NO authentication — any network-accessible deployment is fully open")

    # Start background ingestion scheduler
    init_scheduler()
    log.info("scheduler.started")

    yield

    # Graceful shutdown — stop scheduler
    from scheduler import scheduler
    scheduler.stop()
    log.info("sgos.shutdown")


app = FastAPI(
    title="SGOS Backend",
    description="StraughterG-OS Intelligence Backend \u2014 Research Engine, Trend Detection, Voice Profiles",
    version=settings.version,
    lifespan=lifespan,
)


# ─── Middleware Stack (executed bottom-to-top: CORS \u2192 CSRF \u2192 Auth) ─────

# 1. CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. CSRF protection — require Origin header for state-changing requests
@app.middleware("http")
async def csrf_middleware(request: Request, call_next):
    """Reject cross-origin state-changing requests without valid Origin."""
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if request.url.path in ("/health", "/metrics"):
            return await call_next(request)
        origin = request.headers.get("origin")
        if not origin:
            # Fall back to Referer for form submissions
            referer = request.headers.get("referer", "")
            origin = referer.rstrip("/") if referer else ""
        if origin and origin not in set(settings.allowed_origins):
            return JSONResponse(status_code=403, content={"error": "Cross-origin request blocked"})
    return await call_next(request)

# 3. Bearer token auth
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Simple Bearer token auth. Disabled when SGOS_API_KEY is empty (dev mode)."""
    if request.url.path in ("/health", "/metrics"):
        return await call_next(request)
    if settings.api_key:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        provided = auth_header[7:]
        if not hmac.compare_digest(provided, settings.api_key):
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return await call_next(request)

# 4. Request tracing — latency + metrics collection
@app.middleware("http")
async def request_tracing(request: Request, call_next):
    """Record request latency and log structured events."""
    start = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000

    # Skip metrics noise
    if request.url.path not in ("/metrics",):
        metrics.record_request(
            path=request.url.path,
            method=request.method,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        if duration_ms > 5000:
            log.warn("request.slow",
                     method=request.method, path=request.url.path,
                     status=response.status_code, duration_ms=round(duration_ms, 1))

    return response


# ─── Observability Endpoints ──────────────────────────────────────────────

@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus-compatible text metrics."""
    return PlainTextResponse(content=metrics.prometheus_text(), media_type="text/plain")


@app.get("/metrics/json")
async def metrics_json():
    """JSON metrics snapshot."""
    return metrics.snapshot()


@app.get("/scheduler/status")
async def scheduler_status():
    """Ingestion scheduler status."""
    from scheduler import scheduler
    return scheduler.status()


# ─── Register Routers ─────────────────────────────────────────────────────────

app.include_router(research.router)
app.include_router(search.router)
app.include_router(ingestion.router)
app.include_router(voice.router)
app.include_router(creators.router)
app.include_router(alerts.router)
app.include_router(boards.router)
app.include_router(content.router)
app.include_router(scrape.router)
app.include_router(media.router)
app.include_router(analytics.router)
app.include_router(chat.router)
app.include_router(pipeline.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
