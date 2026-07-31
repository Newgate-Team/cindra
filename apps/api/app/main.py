from fastapi import FastAPI

from app.routers import auth, billing, social_accounts

app = FastAPI(title="Cindra API")
app.include_router(auth.router)
app.include_router(social_accounts.router)
app.include_router(billing.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
