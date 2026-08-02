from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://cindra:cindra@localhost:5433/cindra"
    redis_url: str = "redis://localhost:6380/0"
    jwt_secret: str = "dev-only-insecure-secret-change-in-.env"
    social_token_encryption_key: str = "miLbnE1KsbWEH0uvZOPC03XFYh_NydEqOpPk0KVAn18="
    # Empty until CIN-53 is resolved -- see that ticket. The client
    # below is real (real endpoint, real request shape); only the
    # credential is missing. Same key covers Imagen (CIN-54) and Veo
    # (CIN-55) -- one Google AI Studio key for the whole stack.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    # Imagen 4 is deprecated by Google, shutting down 2026-08-17 --
    # see gate ticket CIN-58, migration not done yet.
    imagen_model: str = "imagen-4.0-generate-001"
    # Was "veo-3.0-fast-generate-001" (does not exist) until CIN-57
    # caught it -- see that ticket for how the bug slipped through
    # the earlier manual verification.
    veo_model: str = "veo-3.1-fast-generate-preview"
    veo_duration_seconds: str = "8"
    veo_resolution: str = "1080p"
    # Empty until CIN-51 is resolved -- see that gate ticket.
    telegram_bot_token: str = ""
    # Empty until CIN-52 is resolved -- see that gate ticket.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "https://app.cindra.example/oauth/instagram/callback"
    # Comma-separated. Without CORS the browser blocks every request
    # from the web app (blocked at the OPTIONS preflight, not
    # something curl-based manual testing ever exercises) -- found
    # while verifying CIN-60's frontend changes against a real
    # browser for the first time.
    cors_origins: str = "http://localhost:3000"

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
