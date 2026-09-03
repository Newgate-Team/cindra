from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.db import SessionLocal
from app.main import app
from app.models import Subscription, User

celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with SessionLocal() as session:
        # Everything user-owned goes with the cascade. image_template
        # _previews (CIN-150) is keyed by template id and has no FK to
        # users, so it survives that and has to be named explicitly --
        # otherwise one test's stored previews leak into the next.
        session.execute(text("TRUNCATE TABLE users, image_template_previews CASCADE"))
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
