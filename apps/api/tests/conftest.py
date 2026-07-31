from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.main import app
from app.models import User


@pytest.fixture(autouse=True)
def _clean_users_table() -> None:
    with SessionLocal() as session:
        session.execute(text("TRUNCATE TABLE users CASCADE"))
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
    db.commit()
    db.refresh(user)
    return user
