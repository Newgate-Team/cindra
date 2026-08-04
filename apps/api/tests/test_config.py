from app.config import Settings


def test_settings_defaults_match_docker_compose() -> None:
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg://cindra:cindra@localhost:5433/cindra"
    assert settings.redis_url == "redis://localhost:6380/0"
