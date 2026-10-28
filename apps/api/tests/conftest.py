from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.db import SessionLocal, engine
from app.main import app
from app.models import Subscription, User

celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)


@pytest.fixture(scope="session", autouse=True)
def _test_database_only() -> None:
    # Every test starts by truncating users. Without DATABASE_URL in the
    # environment that hits the dev database from .env -- a pytest run in
    # the middle of a live check wiped the user and the running stream
    # (2026-10-22, 2026-10-28). Stop before the first TRUNCATE instead.
    name = engine.url.database or ""
    if "test" not in name:
        pytest.exit(
            f"pytest очищает таблицы перед каждым тестом, а DATABASE_URL указывает на «{name}». "
            "Нужна отдельная база с «test» в имени, например: "
            "createdb cindra_test && DATABASE_URL=postgresql+psycopg://cindra:cindra@localhost:5433/"
            "cindra_test alembic upgrade head, затем тот же DATABASE_URL=… pytest",
            returncode=4,
        )


@pytest.fixture(autouse=True)
def _clean_tables(_test_database_only: None) -> None:
    with SessionLocal() as session:
        # Everything user-owned goes with the cascade. image_template
        # _previews (CIN-150) is keyed by template id and has no FK to
        # users, so it survives that and has to be named explicitly --
        # otherwise one test's stored previews leak into the next.
        # montage_agents (DaVinci render agents) isn't user-owned either.
        session.execute(
            text(
                "TRUNCATE TABLE users, image_template_previews, montage_agents, "
                "stream_agents CASCADE"
            )
        )
        session.commit()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def user(db: Session) -> User:
    user = User(email="owner@cindra.dev", hashed_password="not-a-real-hash")
    db.add(user)
    db.flush()
    # Mirrors what POST /auth/register does -- every real user has
    # exactly one subscription, so code that assumes that (app/usage.py)
    # doesn't need a None-guard for a state that can't actually occur.
    db.add(Subscription(user_id=user.id))
    db.commit()
    db.refresh(user)
    return user
