import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.content_pipeline import registry
from app.content_pipeline.errors import ContentModeratedError
from app.content_pipeline.registry import register_generator
from app.models import GenerationContentType


@pytest.fixture(autouse=True)
def _fake_text_generator():
    # Endpoint tests exercise routing/DB/queue wiring, not the real
    # Anthropic call (that's covered offline in test_text_generator.py
    # via MockTransport, and was verified once manually against the
    # live endpoint -- see CIN-49).
    previous = registry._REGISTRY.get(GenerationContentType.text)
    register_generator(
        GenerationContentType.text, lambda payload: {"text": f"пост про {payload['topic']}"}
    )
    yield
    if previous is not None:
        register_generator(GenerationContentType.text, previous)


def _auth_headers(client: TestClient) -> dict[str, str]:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_generate_runs_synchronously_in_eager_mode_and_completes(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/content/generate",
        json={"topic": "утренний кофе", "platform": "telegram"},
        headers=headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["content_type"] == "text"
    assert body["status"] == "completed"
    assert body["output_payload"] == {"text": "пост про утренний кофе"}


def test_get_generation_job(client: TestClient) -> None:
    headers = _auth_headers(client)
    created = client.post(
        "/content/generate",
        json={"topic": "тема", "platform": "instagram"},
        headers=headers,
    ).json()

    response = client.get(f"/content/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_generation_job_not_owned_returns_404(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    created = client.post(
        "/content/generate",
        json={"topic": "тема", "platform": "instagram"},
        headers=headers,
    ).json()

    other_payload = {"email": "eve@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=other_payload)
    other_token = client.post("/auth/login", json=other_payload).json()["access_token"]

    response = client.get(
        f"/content/{created['id']}", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert response.status_code == 404


def test_generate_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/content/generate", json={"topic": "тема", "platform": "telegram"}
    )
    assert response.status_code == 401


def test_generate_flagged_content_reports_status(client: TestClient) -> None:
    def _rejected(payload: dict) -> dict:
        raise ContentModeratedError("упоминание конкурента")

    register_generator(GenerationContentType.text, _rejected)
    headers = _auth_headers(client)
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "platform": "telegram"},
        headers=headers,
    )
    assert response.json()["status"] == "flagged"
