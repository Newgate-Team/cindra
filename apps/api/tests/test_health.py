from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_database_and_redis() -> None:
    response = client.get("/health/ready")
    body = response.json()
    assert response.status_code == 200  # tests run with a real database
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] in {"ok", "unavailable"}  # reported, never fatal


def test_ready_is_503_without_the_database(monkeypatch) -> None:
    from app import main

    class Broken:
        def __enter__(self):
            raise RuntimeError("no database")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(main, "SessionLocal", lambda: Broken())
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


def test_every_response_carries_a_request_id() -> None:
    response = client.get("/health")
    assert len(response.headers["x-request-id"]) >= 8
    echoed = client.get("/health", headers={"x-request-id": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"
