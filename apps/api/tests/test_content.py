import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.content_pipeline import registry
from app.content_pipeline.errors import ContentModeratedError
from app.content_pipeline.registry import register_generator
from app.models import GenerationContentType, Subscription, SubscriptionTier, User


@pytest.fixture(autouse=True)
def _fake_generators():
    # Endpoint tests exercise routing/DB/queue wiring, not the real
    # Gemini/Imagen/Veo calls (those are covered offline in
    # test_text_generator.py / test_image_generator.py /
    # test_video_generator.py via MockTransport, and were verified
    # once manually against the live endpoints -- see CIN-53/54/55).
    previous = dict(registry._REGISTRY)
    register_generator(
        GenerationContentType.text, lambda payload: {"text": f"пост про {payload['topic']}"}
    )
    register_generator(
        GenerationContentType.image, lambda payload: {"image_base64": "ZmFrZQ=="}
    )
    register_generator(
        GenerationContentType.video, lambda payload: {"video_uri": "https://example.com/fake.mp4"}
    )
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(previous)


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


def test_generate_image_runs_synchronously_in_eager_mode_and_completes(
    client: TestClient,
) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "platform": "instagram", "content_type": "image"},
        headers=headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["content_type"] == "image"
    assert body["status"] == "completed"
    assert body["output_payload"] == {"image_base64": "ZmFrZQ=="}


def test_generate_video_runs_synchronously_in_eager_mode_and_completes(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    # Free tier's video limit is 0 (see app/plans.py) -- upgrade to
    # pro so this test exercises the generation path itself, not the
    # limit (that's covered separately by test_generate_video_returns_402_on_free_tier).
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    db.execute(
        update(Subscription).where(Subscription.user_id == user.id).values(tier=SubscriptionTier.pro)
    )
    db.commit()

    response = client.post(
        "/content/generate",
        json={"topic": "тема", "platform": "instagram", "content_type": "video"},
        headers=headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["content_type"] == "video"
    assert body["status"] == "completed"
    assert body["output_payload"] == {"video_uri": "https://example.com/fake.mp4"}


def test_generate_returns_402_once_tier_limit_is_reached(client: TestClient) -> None:
    # Free tier's image limit (3/month, see app/plans.py) -- chosen
    # over the text limit (20/month) so this test doesn't need 20
    # requests to hit it.
    headers = _auth_headers(client)
    for _ in range(3):
        response = client.post(
            "/content/generate",
            json={"topic": "тема", "platform": "telegram", "content_type": "image"},
            headers=headers,
        )
        assert response.status_code == 202

    response = client.post(
        "/content/generate",
        json={"topic": "тема", "platform": "telegram", "content_type": "image"},
        headers=headers,
    )
    assert response.status_code == 402


def test_generate_video_returns_402_on_free_tier(client: TestClient) -> None:
    # Free tier's video limit is 0 -- blocked on the very first
    # attempt, unlike text/image which allow a few before blocking.
    headers = _auth_headers(client)
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "platform": "telegram", "content_type": "video"},
        headers=headers,
    )
    assert response.status_code == 402


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
