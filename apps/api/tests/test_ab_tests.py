import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_pipeline import registry
from app.content_pipeline.registry import register_generator
from app.models import (
    ABTest,
    GenerationContentType,
    GenerationJob,
    UsageEvent,
    UsageEventType,
    User,
)


@pytest.fixture(autouse=True)
def _fake_text_generator():
    # Same reasoning as test_content.py's _fake_generators -- exercise
    # routing/DB/queue wiring, not the real Gemini call.
    previous = dict(registry._REGISTRY)
    register_generator(
        GenerationContentType.text, lambda payload: {"text": f"вариант про {payload['topic']}"}
    )
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(previous)


def _auth_headers(client: TestClient, email: str = "ada@cindra.dev") -> dict[str, str]:
    payload = {"email": email, "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_requires_auth(client: TestClient) -> None:
    response = client.post("/ab-tests", json={"topic": "тема", "variant_count": 2})
    assert response.status_code == 401


def test_create_generates_variant_count_completed_jobs(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/ab-tests", json={"topic": "осенняя коллекция кофе", "variant_count": 3}, headers=headers
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["variants"]) == 3
    assert body["winner_generation_job_id"] is None
    for variant in body["variants"]:
        assert variant["status"] == "completed"
        assert variant["output_payload"] == {"text": "вариант про осенняя коллекция кофе"}

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    jobs = db.scalars(
        select(GenerationJob).where(GenerationJob.ab_test_id == uuid.UUID(body["id"]))
    ).all()
    assert len(jobs) == 3
    assert all(job.user_id == user.id for job in jobs)


@pytest.mark.parametrize("variant_count", [1, 6])
def test_create_rejects_variant_count_out_of_bounds(client: TestClient, variant_count: int) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/ab-tests", json={"topic": "тема", "variant_count": variant_count}, headers=headers
    )
    assert response.status_code == 422


def test_create_rejects_unknown_tone(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/ab-tests",
        json={"topic": "тема", "variant_count": 2, "tone": "не-существующий-тон"},
        headers=headers,
    )
    assert response.status_code == 422


def test_create_records_one_usage_event_per_variant(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    client.post("/ab-tests", json={"topic": "тема", "variant_count": 3}, headers=headers)

    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    events = db.scalars(
        select(UsageEvent).where(
            UsageEvent.user_id == user.id,
            UsageEvent.event_type == UsageEventType.generation,
            UsageEvent.content_type == GenerationContentType.text,
        )
    ).all()
    assert len(events) == 3


def test_create_over_quota_is_all_or_nothing(client: TestClient, db: Session) -> None:
    # Free tier: 20 text generations/month (app/plans.py). Burn 19, then
    # ask for 2 more in one AB test -- the whole batch must be refused,
    # not "1 fits, 1 doesn't".
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    db.add_all(
        [
            UsageEvent(
                user_id=user.id,
                event_type=UsageEventType.generation,
                content_type=GenerationContentType.text,
            )
            for _ in range(19)
        ]
    )
    db.commit()

    response = client.post(
        "/ab-tests", json={"topic": "тема", "variant_count": 2}, headers=headers
    )
    assert response.status_code == 402

    assert db.scalar(select(ABTest).where(ABTest.user_id == user.id)) is None
    total_events = db.scalars(
        select(UsageEvent).where(
            UsageEvent.user_id == user.id, UsageEvent.event_type == UsageEventType.generation
        )
    ).all()
    assert len(total_events) == 19  # unchanged -- nothing extra recorded


def test_get_returns_the_test_with_its_variants(client: TestClient) -> None:
    headers = _auth_headers(client)
    created = client.post(
        "/ab-tests", json={"topic": "тема", "variant_count": 2}, headers=headers
    ).json()

    response = client.get(f"/ab-tests/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert len(response.json()["variants"]) == 2


def test_get_404_for_unknown_test(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.get(f"/ab-tests/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


def test_get_404_for_another_users_test(client: TestClient) -> None:
    owner_headers = _auth_headers(client, "owner@cindra.dev")
    created = client.post(
        "/ab-tests", json={"topic": "тема", "variant_count": 2}, headers=owner_headers
    ).json()

    outsider_headers = _auth_headers(client, "outsider@cindra.dev")
    response = client.get(f"/ab-tests/{created['id']}", headers=outsider_headers)
    assert response.status_code == 404


def test_set_winner_succeeds(client: TestClient) -> None:
    headers = _auth_headers(client)
    created = client.post(
        "/ab-tests", json={"topic": "тема", "variant_count": 2}, headers=headers
    ).json()
    winner_id = created["variants"][0]["id"]

    response = client.post(
        f"/ab-tests/{created['id']}/winner",
        json={"generation_job_id": winner_id},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["winner_generation_job_id"] == winner_id


def test_set_winner_rejects_a_job_not_in_this_test(client: TestClient) -> None:
    headers = _auth_headers(client)
    test_a = client.post(
        "/ab-tests", json={"topic": "тема A", "variant_count": 2}, headers=headers
    ).json()
    test_b = client.post(
        "/ab-tests", json={"topic": "тема B", "variant_count": 2}, headers=headers
    ).json()

    response = client.post(
        f"/ab-tests/{test_a['id']}/winner",
        json={"generation_job_id": test_b["variants"][0]["id"]},
        headers=headers,
    )
    assert response.status_code == 400


def test_set_winner_requires_ownership(client: TestClient) -> None:
    owner_headers = _auth_headers(client, "owner2@cindra.dev")
    created = client.post(
        "/ab-tests", json={"topic": "тема", "variant_count": 2}, headers=owner_headers
    ).json()

    outsider_headers = _auth_headers(client, "outsider2@cindra.dev")
    response = client.post(
        f"/ab-tests/{created['id']}/winner",
        json={"generation_job_id": created["variants"][0]["id"]},
        headers=outsider_headers,
    )
    assert response.status_code == 404
