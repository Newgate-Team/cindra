from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://cindra:cindra@localhost:5433/cindra"
    redis_url: str = "redis://localhost:6380/0"
    jwt_secret: str = "dev-only-insecure-secret-change-in-.env"
    social_token_encryption_key: str = "miLbnE1KsbWEH0uvZOPC03XFYh_NydEqOpPk0KVAn18="
    # Empty until CIN-53 is resolved -- see that ticket. The client
    # below is real (real endpoint, real request shape); only the
    # credential is missing.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    # Empty until CIN-51 is resolved -- see that gate ticket.
    telegram_bot_token: str = ""
    # Empty until CIN-52 is resolved -- see that gate ticket.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "https://app.cindra.example/oauth/instagram/callback"

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
