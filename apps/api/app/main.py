from fastapi import FastAPI

from app.bootstrap import bootstrap
from app.routers import auth, billing, content, metrics, posts, social_accounts

bootstrap()

app = FastAPI(title="Cindra API")
app.include_router(auth.router)
app.include_router(social_accounts.router)
app.include_router(billing.router)
app.include_router(content.router)
app.include_router(posts.router)
app.include_router(metrics.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
