from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GenerationContentType, GenerationJob, GenerationStatus, User


def _auth_headers(client: TestClient) -> dict[str, str]:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _job(
    user: User,
    content_type: GenerationContentType,
    status: GenerationStatus = GenerationStatus.completed,
    topic: str = "осенняя коллекция",
    brand_guide: str | None = None,
    output_payload: dict | None = None,
) -> GenerationJob:
    return GenerationJob(
        user_id=user.id,
        content_type=content_type,
        status=status,
        input_payload={"topic": topic, "brand_guide": brand_guide},
        output_payload=output_payload,
        completed_at=datetime.now(UTC),
    )


def test_feed_includes_completed_image_and_video_jobs(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    db.add(_job(user, GenerationContentType.image, output_payload={"image_url": "https://x/img.png"}))
    db.add(_job(user, GenerationContentType.video, output_payload={"video_url": "https://x/vid.mp4"}))
    db.commit()

    response = client.get("/feed", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    content_types = {item["content_type"] for item in body["items"]}
    assert content_types == {"image", "video"}


def test_feed_excludes_text_jobs(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    db.add(_job(user, GenerationContentType.text, output_payload={"text": "готовый пост"}))
    db.commit()

    response = client.get("/feed", headers=headers)
    assert response.json()["total"] == 0


def test_feed_excludes_failed_and_incomplete_jobs(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    db.add(_job(user, GenerationContentType.image, status=GenerationStatus.failed))
    db.add(_job(user, GenerationContentType.image, status=GenerationStatus.processing))
    db.add(_job(user, GenerationContentType.image, status=GenerationStatus.flagged))
    db.commit()

    response = client.get("/feed", headers=headers)
    assert response.json()["total"] == 0


def test_feed_spans_all_users_not_just_current_user(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)

    other = User(email="eve@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    db.add(_job(other, GenerationContentType.image, output_payload={"image_url": "https://x/img.png"}))
    db.commit()

    response = client.get("/feed", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1  # someone else's job, still shown


def test_feed_does_not_expose_user_identity_or_brand_guide(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    db.add(
        _job(
            user,
            GenerationContentType.image,
            topic="осенний латте",
            brand_guide="секретный бренд-гайд клиента",
            output_payload={
                "image_url": "https://x/img.png",
                "prompt": "Фото на тему: осенний латте. Стиль: секретный бренд-гайд клиента.",
            },
        )
    )
    db.commit()

    response = client.get("/feed", headers=headers)
    item = response.json()["items"][0]
    assert set(item.keys()) == {"id", "content_type", "image_url", "video_url", "caption", "created_at"}
    assert item["caption"] == "осенний латте"
    assert "секретный бренд-гайд" not in item["caption"]


def test_feed_is_paginated(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    for i in range(5):
        db.add(
            _job(
                user,
                GenerationContentType.image,
                topic=f"тема {i}",
                output_payload={"image_url": f"https://x/{i}.png"},
            )
        )
    db.commit()

    response = client.get("/feed?limit=2&offset=0", headers=headers)
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2


def test_feed_newest_first(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    older = _job(user, GenerationContentType.image, topic="старое", output_payload={"image_url": "https://x/1.png"})
    older.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    newer = _job(user, GenerationContentType.image, topic="новое", output_payload={"image_url": "https://x/2.png"})
    newer.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.add_all([older, newer])
    db.commit()

    response = client.get("/feed", headers=headers)
    captions = [item["caption"] for item in response.json()["items"]]
    assert captions == ["новое", "старое"]


def test_feed_prefers_generated_caption_over_raw_topic(client: TestClient, db: Session) -> None:
    # CIN-116: output_payload["text"] (CIN-114's generated caption)
    # takes priority over input_payload["topic"] when both are present.
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    db.add(
        _job(
            user,
            GenerationContentType.image,
            topic="сырой промпт",
            output_payload={"image_url": "https://x/img.png", "text": "Настоящая подпись поста"},
        )
    )
    db.commit()

    response = client.get("/feed", headers=headers)
    assert response.json()["items"][0]["caption"] == "Настоящая подпись поста"


def test_feed_falls_back_to_topic_when_caption_is_missing(client: TestClient, db: Session) -> None:
    # CIN-114's caption generation is best-effort and can be absent.
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    db.add(
        _job(
            user,
            GenerationContentType.image,
            topic="запасной вариант",
            output_payload={"image_url": "https://x/img.png"},
        )
    )
    db.commit()

    response = client.get("/feed", headers=headers)
    assert response.json()["items"][0]["caption"] == "запасной вариант"


def test_feed_requires_auth(client: TestClient) -> None:
    assert client.get("/feed").status_code == 401
