from fastapi import FastAPI

from app.content_pipeline.registry import register_generator
from app.content_pipeline.text_generator import anthropic_text_generator
from app.models import GenerationContentType
from app.routers import auth, billing, content, social_accounts

register_generator(GenerationContentType.text, anthropic_text_generator)

app = FastAPI(title="Cindra API")
app.include_router(auth.router)
app.include_router(social_accounts.router)
app.include_router(billing.router)
app.include_router(content.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
