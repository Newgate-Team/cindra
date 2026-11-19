import logging
import time
import uuid
from pathlib import Path

import redis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.bootstrap import bootstrap
from app.config import get_settings
from app.db import SessionLocal
from app.routers import (
    ab_tests,
    analytics,
    auth,
    billing,
    content,
    feed,
    google_drive,
    integrations,
    liveavatar,
    metrics,
    montage,
    posts,
    simli,
    social_accounts,
    streams,
    team,
    twitch,
    video_projects,
)

bootstrap()

logger = logging.getLogger("cindra.http")


def _init_sentry() -> None:
    """Report unhandled errors to Sentry when SENTRY_DSN is set.

    Imported lazily: the SDK is only needed in deployments that opt in,
    and a missing package must not stop the API from starting. PII is
    off -- request bodies here carry passwords, tokens and user content.
    """
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; error reporting is off")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        send_default_pii=False,
        traces_sample_rate=0.0,
    )


_init_sentry()

app = FastAPI(title="Cindra API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log(request: Request, call_next) -> Response:
    # One line per request with an id the client also gets back
    # (X-Request-ID): a user's "it failed at 14:03" becomes a grep.
    # The path only -- query strings carry OAuth codes and tokens.
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("%s %s -> 500 (%s)", request.method, request.url.path, request_id)
        return JSONResponse(
            {"detail": "Внутренняя ошибка сервера"},
            status_code=500,
            headers={"X-Request-ID": request_id},
        )
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "%s %s -> %s %.0fms (%s)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(auth.router)
app.include_router(social_accounts.router)
app.include_router(billing.router)
app.include_router(content.router)
app.include_router(posts.router)
app.include_router(feed.router)
app.include_router(metrics.router)
app.include_router(video_projects.router)
app.include_router(analytics.router)
app.include_router(ab_tests.router)
app.include_router(team.router)
app.include_router(integrations.router)
app.include_router(google_drive.router)
app.include_router(liveavatar.router)
app.include_router(simli.router)
app.include_router(twitch.router)
app.include_router(montage.router)
app.include_router(streams.router)

if get_settings().media_local_dir:
    # Dev-only counterpart of media_storage's local-dir mode.
    Path(get_settings().media_local_dir).mkdir(parents=True, exist_ok=True)
    app.mount(
        "/dev-media",
        StaticFiles(directory=get_settings().media_local_dir),
        name="dev-media",
    )


@app.get("/health")
def health() -> dict[str, str]:
    # Liveness only: the process answers. Kept free of dependencies so a
    # database blip makes the platform stop routing traffic (see
    # /health/ready), not restart a perfectly healthy process.
    return {"status": "ok"}


@app.get("/health/ready")
def ready() -> JSONResponse:
    """Readiness: can this instance actually serve requests?

    The database is required (503 without it). Redis only carries the
    background work and the rate limits -- both degrade rather than
    fail -- so it is reported but does not fail the check.
    """
    checks: dict[str, str] = {}
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("readiness: database check failed")
        checks["database"] = "unavailable"
    try:
        redis.Redis.from_url(
            get_settings().redis_url, socket_connect_timeout=0.5, socket_timeout=0.5
        ).ping()
        checks["redis"] = "ok"
    except Exception:  # noqa: BLE001
        checks["redis"] = "unavailable"
    ok = checks["database"] == "ok"
    return JSONResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks},
        status_code=200 if ok else 503,
    )
