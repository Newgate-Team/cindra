from fastapi import FastAPI

from app.content_pipeline.registry import register_generator
from app.content_pipeline.text_generator import anthropic_text_generator
from app.models import GenerationContentType, SocialPlatform
from app.routers import auth, billing, content, metrics, posts, social_accounts
from app.scheduler.registry import register_publisher
from app.social_integrations import instagram, telegram

register_generator(GenerationContentType.text, anthropic_text_generator)
register_publisher(SocialPlatform.telegram, telegram.publish)
register_publisher(SocialPlatform.instagram, instagram.publish)

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
