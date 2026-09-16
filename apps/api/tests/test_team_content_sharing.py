from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content_pipeline import registry
from app.content_pipeline.registry import register_generator
from app.models import (
    GenerationContentType,
    Post,
    PostStatus,
    SocialPlatform,
    User,
)
from app.social_accounts import upsert_social_account


@pytest.fixture(autouse=True)
def _fake_text_generator():
    # Same reasoning as test_content.py/test_ab_tests.py -- exercise
    # routing/DB/ownership wiring, not the real Gemini call.
    previous = dict(registry._REGISTRY)
    register_generator(
        GenerationContentType.text, lambda payload: {"text": f"пост про {payload['topic']}"}
    )
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(previous)


def _register_and_login(client: TestClient, email: str, role: str = "agency") -> str:
    client.post(
        "/auth/register", json={"email": email, "password": "supersecret1", "role": role}
    )
    return client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]


def _login(client: TestClient, email: str) -> dict[str, str]:
    token = client.post(
        "/auth/login", json={"email": email, "password": "supersecret1"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _make_team(
    client: TestClient, db: Session, owner_email: str, member_email: str
) -> tuple[User, User, dict[str, str]]:
    owner_token = _register_and_login(client, owner_email)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    team_id = client.post("/team", json={"name": "Команда"}, headers=owner_headers).json()["id"]

    _register_and_login(client, member_email, role="solo")
    owner = db.scalar(select(User).where(User.email == owner_email))
    member = db.scalar(select(User).where(User.email == member_email))
    member.team_id = team_id
    db.commit()
    db.refresh(owner)
    db.refresh(member)
    return owner, member, owner_headers


# ---- GenerationJob (content.py) ----


def test_generate_content_can_target_a_teammates_social_account(
    client: TestClient, db: Session
) -> None:
    owner, _member, _owner_headers = _make_team(
        client, db, "gen-owner@cindra.dev", "gen-member@cindra.dev"
    )
    account = upsert_social_account(
        db, owner, SocialPlatform.telegram, "gen-chat", access_token="t"
    )

    response = client.post(
        "/content/generate",
        json={"topic": "тест", "target_account_ids": [str(account.id)]},
        headers=_login(client, "gen-member@cindra.dev"),
    )
    assert response.status_code == 202


def test_generate_content_rejects_another_teams_social_account(
    client: TestClient, db: Session
) -> None:
    owner_a, _member_a, _ = _make_team(client, db, "gen-a-owner@cindra.dev", "gen-a-member@cindra.dev")
    _owner_b, _member_b, _ = _make_team(client, db, "gen-b-owner@cindra.dev", "gen-b-member@cindra.dev")
    account = upsert_social_account(
        db, owner_a, SocialPlatform.telegram, "gen-a-chat", access_token="t"
    )

    response = client.post(
        "/content/generate",
        json={"topic": "тест", "target_account_ids": [str(account.id)]},
        headers=_login(client, "gen-b-member@cindra.dev"),
    )
    assert response.status_code == 404


def test_get_generation_job_visible_to_teammate(client: TestClient, db: Session) -> None:
    _owner, _member, owner_headers = _make_team(
        client, db, "job-owner@cindra.dev", "job-member@cindra.dev"
    )
    job = client.post(
        "/content/generate", json={"topic": "тест"}, headers=owner_headers
    ).json()

    response = client.get(
        f"/content/{job['id']}", headers=_login(client, "job-member@cindra.dev")
    )
    assert response.status_code == 200


def test_get_generation_job_404_for_another_team(client: TestClient, db: Session) -> None:
    _owner_a, _member_a, owner_a_headers = _make_team(
        client, db, "job-a-owner@cindra.dev", "job-a-member@cindra.dev"
    )
    _owner_b, _member_b, _ = _make_team(
        client, db, "job-b-owner@cindra.dev", "job-b-member@cindra.dev"
    )
    job = client.post(
        "/content/generate", json={"topic": "тест"}, headers=owner_a_headers
    ).json()

    response = client.get(
        f"/content/{job['id']}", headers=_login(client, "job-b-member@cindra.dev")
    )
    assert response.status_code == 404


# ---- Post (posts.py) ----


def test_posts_list_shows_teammates_posts(client: TestClient, db: Session) -> None:
    owner, _member, _owner_headers = _make_team(
        client, db, "post-owner@cindra.dev", "post-member@cindra.dev"
    )
    account = upsert_social_account(
        db, owner, SocialPlatform.telegram, "post-chat", access_token="t"
    )
    now = datetime.now(UTC)
    db.add(
        Post(
            user_id=owner.id,
            social_account_id=account.id,
            text="тест",
            status=PostStatus.published,
            scheduled_for=now,
            published_at=now,
        )
    )
    db.commit()

    response = client.get("/posts", headers=_login(client, "post-member@cindra.dev"))
    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_posts_list_excludes_other_teams_posts(client: TestClient, db: Session) -> None:
    owner_a, _member_a, _ = _make_team(
        client, db, "post-a-owner@cindra.dev", "post-a-member@cindra.dev"
    )
    _owner_b, _member_b, _ = _make_team(
        client, db, "post-b-owner@cindra.dev", "post-b-member@cindra.dev"
    )
    account = upsert_social_account(
        db, owner_a, SocialPlatform.telegram, "post-a-chat", access_token="t"
    )
    now = datetime.now(UTC)
    db.add(
        Post(
            user_id=owner_a.id,
            social_account_id=account.id,
            text="тест",
            status=PostStatus.published,
            scheduled_for=now,
            published_at=now,
        )
    )
    db.commit()

    response = client.get("/posts", headers=_login(client, "post-b-member@cindra.dev"))
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_create_post_can_target_teammates_account_and_generation_job(
    client: TestClient, db: Session
) -> None:
    owner, _member, owner_headers = _make_team(
        client, db, "cp-owner@cindra.dev", "cp-member@cindra.dev"
    )
    account = upsert_social_account(
        db, owner, SocialPlatform.telegram, "cp-chat", access_token="t"
    )
    job = client.post(
        "/content/generate", json={"topic": "тест"}, headers=owner_headers
    ).json()

    response = client.post(
        "/posts",
        json={
            "social_account_ids": [str(account.id)],
            "generation_job_id": job["id"],
            "text": "пост от участника команды",
            "content_kind": "post",
        },
        headers=_login(client, "cp-member@cindra.dev"),
    )
    assert response.status_code == 201
    assert response.json()[0]["platform"] == "telegram"


def test_create_post_rejects_another_teams_generation_job(
    client: TestClient, db: Session
) -> None:
    _owner_a, _member_a, owner_a_headers = _make_team(
        client, db, "cp-a-owner@cindra.dev", "cp-a-member@cindra.dev"
    )
    _owner_b, _member_b, _ = _make_team(
        client, db, "cp-b-owner@cindra.dev", "cp-b-member@cindra.dev"
    )
    account_b = upsert_social_account(
        db, _owner_b, SocialPlatform.telegram, "cp-b-chat", access_token="t"
    )
    job_a = client.post(
        "/content/generate", json={"topic": "тест"}, headers=owner_a_headers
    ).json()

    response = client.post(
        "/posts",
        json={
            "social_account_ids": [str(account_b.id)],
            "generation_job_id": job_a["id"],
            "text": "не должно пройти",
            "content_kind": "post",
        },
        headers=_login(client, "cp-b-member@cindra.dev"),
    )
    assert response.status_code == 404


def test_get_and_update_post_visible_to_teammate(client: TestClient, db: Session) -> None:
    owner, _member, _owner_headers = _make_team(
        client, db, "up-owner@cindra.dev", "up-member@cindra.dev"
    )
    account = upsert_social_account(
        db, owner, SocialPlatform.telegram, "up-chat", access_token="t"
    )
    post = Post(
        user_id=owner.id,
        social_account_id=account.id,
        text="черновик",
        status=PostStatus.scheduled,
        scheduled_for=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    member_headers = _login(client, "up-member@cindra.dev")
    get_response = client.get(f"/posts/{post.id}", headers=member_headers)
    assert get_response.status_code == 200

    update_response = client.patch(
        f"/posts/{post.id}", json={"text": "обновлено участником"}, headers=member_headers
    )
    assert update_response.status_code == 200
    assert update_response.json()["text"] == "обновлено участником"


def test_get_post_404_for_another_team(client: TestClient, db: Session) -> None:
    owner_a, _member_a, _ = _make_team(
        client, db, "gp-a-owner@cindra.dev", "gp-a-member@cindra.dev"
    )
    _owner_b, _member_b, _ = _make_team(
        client, db, "gp-b-owner@cindra.dev", "gp-b-member@cindra.dev"
    )
    account = upsert_social_account(
        db, owner_a, SocialPlatform.telegram, "gp-a-chat", access_token="t"
    )
    now = datetime.now(UTC)
    post = Post(
        user_id=owner_a.id,
        social_account_id=account.id,
        text="тест",
        status=PostStatus.published,
        scheduled_for=now,
        published_at=now,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    response = client.get(
        f"/posts/{post.id}", headers=_login(client, "gp-b-member@cindra.dev")
    )
    assert response.status_code == 404


# ---- VideoProject ----


def test_video_projects_visible_to_teammate(client: TestClient, db: Session) -> None:
    _owner, _member, owner_headers = _make_team(
        client, db, "vp-owner@cindra.dev", "vp-member@cindra.dev"
    )
    project = client.post(
        "/video-projects", json={"topic": "видео команды"}, headers=owner_headers
    ).json()

    member_headers = _login(client, "vp-member@cindra.dev")
    list_response = client.get("/video-projects", headers=member_headers)
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1

    get_response = client.get(f"/video-projects/{project['id']}", headers=member_headers)
    assert get_response.status_code == 200


def test_video_project_404_for_another_team(client: TestClient, db: Session) -> None:
    _owner_a, _member_a, owner_a_headers = _make_team(
        client, db, "vp-a-owner@cindra.dev", "vp-a-member@cindra.dev"
    )
    _owner_b, _member_b, _ = _make_team(
        client, db, "vp-b-owner@cindra.dev", "vp-b-member@cindra.dev"
    )
    project = client.post(
        "/video-projects", json={"topic": "видео A"}, headers=owner_a_headers
    ).json()

    response = client.get(
        f"/video-projects/{project['id']}", headers=_login(client, "vp-b-member@cindra.dev")
    )
    assert response.status_code == 404


# ---- ABTest ----


def test_ab_test_visible_to_teammate_and_winner_settable(
    client: TestClient, db: Session
) -> None:
    _owner, _member, owner_headers = _make_team(
        client, db, "ab-owner@cindra.dev", "ab-member@cindra.dev"
    )
    test = client.post(
        "/ab-tests", json={"topic": "тест", "variant_count": 2}, headers=owner_headers
    ).json()

    member_headers = _login(client, "ab-member@cindra.dev")
    get_response = client.get(f"/ab-tests/{test['id']}", headers=member_headers)
    assert get_response.status_code == 200

    winner_response = client.post(
        f"/ab-tests/{test['id']}/winner",
        json={"generation_job_id": test["variants"][0]["id"]},
        headers=member_headers,
    )
    assert winner_response.status_code == 200
    assert winner_response.json()["winner_generation_job_id"] == test["variants"][0]["id"]


def test_ab_test_404_for_another_team(client: TestClient, db: Session) -> None:
    _owner_a, _member_a, owner_a_headers = _make_team(
        client, db, "ab-a-owner@cindra.dev", "ab-a-member@cindra.dev"
    )
    _owner_b, _member_b, _ = _make_team(
        client, db, "ab-b-owner@cindra.dev", "ab-b-member@cindra.dev"
    )
    test = client.post(
        "/ab-tests", json={"topic": "тест A", "variant_count": 2}, headers=owner_a_headers
    ).json()

    response = client.get(
        f"/ab-tests/{test['id']}", headers=_login(client, "ab-b-member@cindra.dev")
    )
    assert response.status_code == 404


# ---- Analytics ----


def test_analytics_combines_the_whole_teams_activity(client: TestClient, db: Session) -> None:
    owner, member, _owner_headers = _make_team(
        client, db, "an-owner@cindra.dev", "an-member@cindra.dev"
    )
    account = upsert_social_account(
        db, owner, SocialPlatform.telegram, "an-chat", access_token="t"
    )
    now = datetime.now(UTC)
    # One post from the owner, one from the member -- directly via DB
    # so this test is about analytics' own aggregation, not posts.py.
    db.add_all(
        [
            Post(
                user_id=owner.id,
                social_account_id=account.id,
                text="от владельца",
                status=PostStatus.published,
                scheduled_for=now,
                published_at=now,
            ),
            Post(
                user_id=member.id,
                social_account_id=account.id,
                text="от участника",
                status=PostStatus.published,
                scheduled_for=now,
                published_at=now,
            ),
        ]
    )
    db.commit()

    response = client.get(
        "/analytics/summary", headers=_login(client, "an-member@cindra.dev")
    )
    assert response.status_code == 200
    assert response.json()["posts_total"] == 2
    assert response.json()["posts_published"] == 2


def test_analytics_excludes_other_teams_activity(client: TestClient, db: Session) -> None:
    owner_a, _member_a, _ = _make_team(
        client, db, "an-a-owner@cindra.dev", "an-a-member@cindra.dev"
    )
    _owner_b, _member_b, _ = _make_team(
        client, db, "an-b-owner@cindra.dev", "an-b-member@cindra.dev"
    )
    account = upsert_social_account(
        db, owner_a, SocialPlatform.telegram, "an-a-chat", access_token="t"
    )
    now = datetime.now(UTC)
    db.add(
        Post(
            user_id=owner_a.id,
            social_account_id=account.id,
            text="от команды A",
            status=PostStatus.published,
            scheduled_for=now,
            published_at=now,
        )
    )
    db.commit()

    response = client.get(
        "/analytics/summary", headers=_login(client, "an-b-member@cindra.dev")
    )
    assert response.status_code == 200
    assert response.json()["posts_total"] == 0
