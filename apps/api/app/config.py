from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://cindra:cindra@localhost:5433/cindra"
    redis_url: str = "redis://localhost:6380/0"
    jwt_secret: str = "dev-only-insecure-secret-change-in-.env"
    social_token_encryption_key: str = "miLbnE1KsbWEH0uvZOPC03XFYh_NydEqOpPk0KVAn18="

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
