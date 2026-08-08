import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Post, PostStatus, SocialPlatform, User
from app.scheduler import registry
from app.scheduler.registry import register_publisher
from app.social_accounts import upsert_social_account


@pytest.fixture(autouse=True)
def _fake_telegram_publisher():
    previous = registry._REGISTRY.get(SocialPlatform.telegram)
    register_publisher(SocialPlatform.telegram, lambda account, post: {"message_id": 7})
    yield
    if previous is not None:
        register_publisher(SocialPlatform.telegram, previous)


def _auth_headers(client: TestClient) -> dict[str, str]:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _connected_account_id(client: TestClient, headers: dict[str, str], db: Session) -> str:
    # CIN-128: connecting a Telegram channel is now a two-step flow --
    # start-verification issues a code that has to appear in the
    # chat's description (proof the caller can actually edit it, i.e.
    # is really an admin) before /connect will accept it.
    from unittest.mock import patch

    chat = {"id": -100123, "title": "My Channel"}
    with patch("app.routers.social_accounts.get_chat", return_value=chat):
        start = client.post(
            "/social-accounts/telegram/start-verification",
            json={"chat_id": "@mychannel"},
            headers=headers,
        )
    token = start.json()["verification_token"]
    code = start.json()["code"]

    with (
        patch(
            "app.routers.social_accounts.get_chat",
            return_value={**chat, "description": code},
        ),
        patch(
            "app.routers.social_accounts.get_me",
            return_value={"id": 999, "username": "cindra_bot"},
        ),
        patch(
            "app.routers.social_accounts.get_chat_member",
            return_value={"status": "member"},
        ),
    ):
        response = client.post(
            "/social-accounts/telegram/connect",
            json={"verification_token": token},
            headers=headers,
        )
    return response.json()["id"]


def test_create_post_without_scheduled_for_publishes_immediately(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)

    response = client.post(
        "/posts",
        json={"social_account_ids": [account_id], "text": "Готовый пост"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "published"
    assert body[0]["platform_message_id"] == "7"


def test_create_post_with_video_url_passes_it_through(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)

    response = client.post(
        "/posts",
        json={
            "social_account_ids": [account_id],
            "text": "Готовое видео",
            "video_url": "https://media.cindra.example/x.mp4",
        },
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()[0]["video_url"] == "https://media.cindra.example/x.mp4"


def test_create_post_scheduled_in_future_stays_scheduled(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    response = client.post(
        "/posts",
        json={"social_account_ids": [account_id], "text": "Пост на потом", "scheduled_for": future},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()[0]["status"] == "scheduled"


def test_create_post_for_someone_elses_account_returns_404(
    client: TestClient, db: Session
) -> None:
    other = User(email="eve@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    other_account = upsert_social_account(
        db, other, SocialPlatform.telegram, "-999", access_token="t"
    )

    headers = _auth_headers(client)
    response = client.post(
        "/posts",
        json={"social_account_ids": [str(other_account.id)], "text": "тест"},
        headers=headers,
    )
    assert response.status_code == 404


def test_get_post(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    created = client.post(
        "/posts", json={"social_account_ids": [account_id], "text": "тест"}, headers=headers
    ).json()[0]

    response = client.get(f"/posts/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_list_posts_scoped_to_owner(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    client.post(
        "/posts", json={"social_account_ids": [account_id], "text": "мой пост"}, headers=headers
    )

    other = User(email="eve2@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    other_account = upsert_social_account(
        db, other, SocialPlatform.telegram, "-777", access_token="t"
    )
    db.add(
        Post(
            user_id=other.id,
            social_account_id=other_account.id,
            text="чужой пост",
            status=PostStatus.published,
            scheduled_for=datetime.now(UTC),
        )
    )
    db.commit()

    response = client.get("/posts", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    texts = [p["text"] for p in body["items"]]
    assert texts == ["мой пост"]


def test_list_posts_requires_auth(client: TestClient) -> None:
    assert client.get("/posts").status_code == 401


def test_list_posts_is_paginated(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    for i in range(5):
        client.post(
            "/posts", json={"social_account_ids": [account_id], "text": f"пост {i}"}, headers=headers
        )

    response = client.get("/posts?limit=2&offset=0", headers=headers)
    body = response.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2

    response = client.get("/posts?limit=2&offset=4", headers=headers)
    body = response.json()
    assert len(body["items"]) == 1  # only 1 left at offset 4 of 5


def test_list_posts_default_limit_is_20(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    client.post("/posts", json={"social_account_ids": [account_id], "text": "тест"}, headers=headers)

    response = client.get("/posts", headers=headers)
    body = response.json()
    assert body["limit"] == 20
    assert body["offset"] == 0


def test_list_posts_rejects_limit_over_max(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.get("/posts?limit=101", headers=headers)
    assert response.status_code == 422


def test_create_post_requires_auth(client: TestClient) -> None:
    response = client.post("/posts", json={"social_account_ids": ["x"], "text": "тест"})
    assert response.status_code == 401


def test_create_post_returns_402_once_publication_limit_reached(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)

    for _ in range(10):  # free tier limit, see app/plans.py
        response = client.post(
            "/posts", json={"social_account_ids": [account_id], "text": "тест"}, headers=headers
        )
        assert response.status_code == 201

    response = client.post(
        "/posts", json={"social_account_ids": [account_id], "text": "тест"}, headers=headers
    )
    assert response.status_code == 402


def test_create_post_fans_out_to_multiple_accounts(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    second_account = upsert_social_account(
        db,
        db.query(User).filter(User.email == "ada@cindra.dev").one(),
        SocialPlatform.telegram,
        "-200",
        access_token="t2",
    )

    response = client.post(
        "/posts",
        json={"social_account_ids": [account_id, str(second_account.id)], "text": "фан-аут"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body) == 2
    assert {p["status"] for p in body} == {"published"}
    assert body[0]["id"] != body[1]["id"]

    # Both rows share generation_job_id when provided -- checked via DB
    # directly since generation_job_id isn't in PostOut.
    posts = db.query(Post).filter(Post.text == "фан-аут").all()
    assert len(posts) == 2
    assert {p.social_account_id for p in posts} == {uuid.UUID(account_id), second_account.id}


def test_create_post_fan_out_limit_check_is_atomic_for_whole_batch(
    client: TestClient, db: Session
) -> None:
    # Free tier's publication limit is 10/month (see app/plans.py).
    # Use up 9, then try to fan out to 2 accounts at once -- neither
    # should be created, since the batch can't fully fit.
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    second_account = upsert_social_account(
        db,
        db.query(User).filter(User.email == "ada@cindra.dev").one(),
        SocialPlatform.telegram,
        "-200",
        access_token="t2",
    )

    for _ in range(9):
        response = client.post(
            "/posts", json={"social_account_ids": [account_id], "text": "тест"}, headers=headers
        )
        assert response.status_code == 201

    response = client.post(
        "/posts",
        json={"social_account_ids": [account_id, str(second_account.id)], "text": "не влезет"},
        headers=headers,
    )
    assert response.status_code == 402
    assert db.query(Post).filter(Post.text == "не влезет").count() == 0


def _generation_job_id(db: Session, user: User) -> str:
    from app.models import GenerationContentType, GenerationJob, GenerationStatus

    job = GenerationJob(
        user_id=user.id,
        content_type=GenerationContentType.image,
        status=GenerationStatus.completed,
        input_payload={"topic": "тест"},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return str(job.id)


def test_create_post_retry_with_same_generation_job_and_account_is_not_duplicated(
    client: TestClient, db: Session
) -> None:
    # CIN-122: a retry after a dropped response (CIN-120) must not
    # publish the same generated content twice to the same account.
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    owner = db.query(User).filter(User.email == "ada@cindra.dev").one()
    job_id = _generation_job_id(db, owner)
    payload = {
        "social_account_ids": [account_id],
        "text": "сторис",
        "generation_job_id": job_id,
    }

    first = client.post("/posts", json=payload, headers=headers)
    second = client.post("/posts", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()[0]["id"] == second.json()[0]["id"]
    assert db.query(Post).filter(Post.generation_job_id == job_id).count() == 1


def test_create_post_retry_does_not_double_charge_usage_limit(
    client: TestClient, db: Session
) -> None:
    from app.models import UsageEvent

    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    owner = db.query(User).filter(User.email == "ada@cindra.dev").one()
    job_id = _generation_job_id(db, owner)
    payload = {
        "social_account_ids": [account_id],
        "text": "сторис",
        "generation_job_id": job_id,
    }

    client.post("/posts", json=payload, headers=headers)
    client.post("/posts", json=payload, headers=headers)
    client.post("/posts", json=payload, headers=headers)

    assert db.query(UsageEvent).filter(UsageEvent.user_id == owner.id).count() == 1


def test_create_post_retry_only_dispatches_publish_once(client: TestClient, db: Session) -> None:
    calls = []
    register_publisher(SocialPlatform.telegram, lambda account, post: calls.append(post.id) or {"message_id": 7})

    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    owner = db.query(User).filter(User.email == "ada@cindra.dev").one()
    job_id = _generation_job_id(db, owner)
    payload = {
        "social_account_ids": [account_id],
        "text": "сторис",
        "generation_job_id": job_id,
    }

    client.post("/posts", json=payload, headers=headers)
    client.post("/posts", json=payload, headers=headers)

    assert len(calls) == 1


def test_create_post_fan_out_retry_only_creates_missing_accounts(
    client: TestClient, db: Session
) -> None:
    # A retry that adds a new target account alongside ones already
    # published for this job should only create/dispatch the new one.
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    owner = db.query(User).filter(User.email == "ada@cindra.dev").one()
    second_account = upsert_social_account(db, owner, SocialPlatform.telegram, "-201", access_token="t2")
    job_id = _generation_job_id(db, owner)

    first = client.post(
        "/posts",
        json={"social_account_ids": [account_id], "text": "сторис", "generation_job_id": job_id},
        headers=headers,
    )
    second = client.post(
        "/posts",
        json={
            "social_account_ids": [account_id, str(second_account.id)],
            "text": "сторис",
            "generation_job_id": job_id,
        },
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()[0]["id"] in {p["id"] for p in second.json()}
    assert db.query(Post).filter(Post.generation_job_id == job_id).count() == 2


def test_create_post_without_generation_job_id_is_never_deduped(
    client: TestClient, db: Session
) -> None:
    # Manual posts (Calendar's create form) have no generation_job_id --
    # the CIN-122 dedup only applies to generated content, not this
    # separate flow.
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    payload = {"social_account_ids": [account_id], "text": "ручной пост"}

    client.post("/posts", json=payload, headers=headers)
    client.post("/posts", json=payload, headers=headers)

    assert db.query(Post).filter(Post.text == "ручной пост").count() == 2


def _scheduled_post(client: TestClient, headers: dict[str, str], db: Session) -> dict:
    account_id = _connected_account_id(client, headers, db)
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    return client.post(
        "/posts",
        json={"social_account_ids": [account_id], "text": "черновик", "scheduled_for": future},
        headers=headers,
    ).json()[0]


def test_update_post_changes_text_and_scheduled_for(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    post = _scheduled_post(client, headers, db)
    new_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    response = client.patch(
        f"/posts/{post['id']}",
        json={"text": "новый текст", "scheduled_for": new_time},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "новый текст"
    assert body["status"] == "scheduled"


def test_update_post_partial_only_changes_provided_fields(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    post = _scheduled_post(client, headers, db)

    response = client.patch(
        f"/posts/{post['id']}", json={"text": "только текст поменялся"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "только текст поменялся"
    assert body["scheduled_for"] == post["scheduled_for"]


def test_update_already_published_post_returns_400(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    published = client.post(
        "/posts", json={"social_account_ids": [account_id], "text": "уже вышел"}, headers=headers
    ).json()[0]
    assert published["status"] == "published"

    response = client.patch(
        f"/posts/{published['id']}", json={"text": "поздно"}, headers=headers
    )
    assert response.status_code == 400


def test_update_someone_elses_post_returns_404(client: TestClient, db: Session) -> None:
    other = User(email="eve3@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    other_account = upsert_social_account(
        db, other, SocialPlatform.telegram, "-555", access_token="t"
    )
    other_post = Post(
        user_id=other.id,
        social_account_id=other_account.id,
        text="чужой",
        status=PostStatus.scheduled,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(other_post)
    db.commit()

    headers = _auth_headers(client)
    response = client.patch(
        f"/posts/{other_post.id}", json={"text": "хочу поменять чужое"}, headers=headers
    )
    assert response.status_code == 404


def test_cancel_scheduled_post_deletes_it(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    post = _scheduled_post(client, headers, db)

    response = client.delete(f"/posts/{post['id']}", headers=headers)
    assert response.status_code == 204
    assert client.get(f"/posts/{post['id']}", headers=headers).status_code == 404


def test_cancel_already_published_post_returns_400(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    published = client.post(
        "/posts", json={"social_account_ids": [account_id], "text": "уже вышел"}, headers=headers
    ).json()[0]

    response = client.delete(f"/posts/{published['id']}", headers=headers)
    assert response.status_code == 400


def test_create_post_with_past_scheduled_for_returns_400(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    response = client.post(
        "/posts",
        json={"social_account_ids": [account_id], "text": "тест", "scheduled_for": past},
        headers=headers,
    )
    assert response.status_code == 400
    assert "прошлом" in response.json()["detail"]


def test_create_post_includes_platform_and_account_label(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)

    response = client.post(
        "/posts", json={"social_account_ids": [account_id], "text": "тест"}, headers=headers
    )
    body = response.json()[0]
    assert body["platform"] == "telegram"
    assert body["account_label"] == "My Channel"


def test_list_posts_includes_platform_and_account_label(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    client.post("/posts", json={"social_account_ids": [account_id], "text": "тест"}, headers=headers)

    response = client.get("/posts", headers=headers)
    body = response.json()
    assert body["items"][0]["platform"] == "telegram"
    assert body["items"][0]["account_label"] == "My Channel"


def test_get_post_includes_platform_and_account_label(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _connected_account_id(client, headers, db)
    created = client.post(
        "/posts", json={"social_account_ids": [account_id], "text": "тест"}, headers=headers
    ).json()[0]

    response = client.get(f"/posts/{created['id']}", headers=headers)
    body = response.json()
    assert body["platform"] == "telegram"
    assert body["account_label"] == "My Channel"


def test_update_post_includes_platform_and_account_label(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    post = _scheduled_post(client, headers, db)

    response = client.patch(
        f"/posts/{post['id']}", json={"text": "новый текст"}, headers=headers
    )
    body = response.json()
    assert body["platform"] == "telegram"
    assert body["account_label"] == "My Channel"


def test_reschedule_post_to_the_past_returns_400(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    post = _scheduled_post(client, headers, db)
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    response = client.patch(
        f"/posts/{post['id']}", json={"scheduled_for": past}, headers=headers
    )
    assert response.status_code == 400
    assert "прошлом" in response.json()["detail"]
