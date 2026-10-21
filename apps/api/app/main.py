from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.bootstrap import bootstrap
from app.config import get_settings
from app.routers import (
    ab_tests,
    analytics,
    auth,
    billing,
    content,
    feed,
    integrations,
    metrics,
    montage,
    posts,
    social_accounts,
    team,
    video_projects,
)

bootstrap()

app = FastAPI(title="Cindra API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
app.include_router(montage.router)

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
    return {"status": "ok"}
