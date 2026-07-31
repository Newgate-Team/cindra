from fastapi import FastAPI

from app.routers import auth

app = FastAPI(title="Cindra API")
app.include_router(auth.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
