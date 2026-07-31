import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app


@pytest.fixture(autouse=True)
def _clean_users_table() -> None:
    with SessionLocal() as db:
        db.execute(text("TRUNCATE TABLE users CASCADE"))
        db.commit()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
