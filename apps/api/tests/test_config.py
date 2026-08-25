import re
from pathlib import Path

from app.config import Settings

_COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.yml"


def _published_port(service_image: str) -> str:
    """Read the host port docker-compose publishes for a service.

    Parsed with a regex rather than PyYAML so this stays dependency-free
    (PyYAML isn't in the project's requirements).
    """
    compose = _COMPOSE.read_text()
    block = compose.split(service_image, 1)[1]
    match = re.search(r'ports:\s*\n\s*-\s*"(\d+):\d+"', block)
    assert match is not None, f"no published port found after {service_image}"
    return match.group(1)


def _default(field: str) -> str:
    """The declared default, independent of the ambient environment.

    Deliberately NOT Settings(...) -- pydantic-settings reads os.environ
    regardless of _env_file, so instantiating here asserted whatever
    DATABASE_URL happened to be exported (CIN-139: this test failed
    locally for anyone running Postgres on another port, and passed in
    CI only because CI exports exactly the default).
    """
    return Settings.model_fields[field].default


def test_settings_defaults_match_docker_compose() -> None:
    assert _default("database_url") == (
        f"postgresql+psycopg://cindra:cindra@localhost:{_published_port('postgres:16-alpine')}/cindra"
    )
    assert _default("redis_url") == f"redis://localhost:{_published_port('redis:7-alpine')}/0"
