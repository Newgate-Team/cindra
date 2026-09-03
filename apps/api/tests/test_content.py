from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.content_pipeline import registry
from app.content_pipeline.errors import ContentModeratedError
from app.content_pipeline.registry import register_generator
from app.models import (
    GenerationContentType,
    ImageTemplatePreview,
    SocialPlatform,
    Subscription,
    SubscriptionTier,
    User,
)
from app.social_accounts import upsert_social_account


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


def _account_id(db: Session, platform: SocialPlatform = SocialPlatform.telegram, external_id: str = "-100") -> str:
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    account = upsert_social_account(db, user, platform, external_id, access_token="t")
    return str(account.id)


def test_generate_runs_synchronously_in_eager_mode_and_completes(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _account_id(db)
    response = client.post(
        "/content/generate",
        json={"topic": "утренний кофе", "target_account_ids": [account_id]},
        headers=headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["content_type"] == "text"
    assert body["status"] == "completed"
    assert body["output_payload"] == {"text": "пост про утренний кофе"}


def test_get_generation_job(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _account_id(db, SocialPlatform.instagram, "insta-1")
    created = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id], "content_type": "image"},
        headers=headers,
    ).json()

    response = client.get(f"/content/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_generation_job_not_owned_returns_404(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _account_id(db)
    created = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id]},
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
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    account_id = _account_id(db, SocialPlatform.instagram, "insta-1")
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id], "content_type": "image"},
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
    account_id = _account_id(db, SocialPlatform.instagram, "insta-1")
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
        json={"topic": "тема", "target_account_ids": [account_id], "content_type": "video"},
        headers=headers,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["content_type"] == "video"
    assert body["status"] == "completed"
    assert body["output_payload"] == {"video_uri": "https://example.com/fake.mp4"}


def test_generate_returns_402_once_tier_limit_is_reached(client: TestClient, db: Session) -> None:
    # Free tier's image limit (3/month, see app/plans.py) -- chosen
    # over the text limit (20/month) so this test doesn't need 20
    # requests to hit it.
    headers = _auth_headers(client)
    account_id = _account_id(db)
    for _ in range(3):
        response = client.post(
            "/content/generate",
            json={"topic": "тема", "target_account_ids": [account_id], "content_type": "image"},
            headers=headers,
        )
        assert response.status_code == 202

    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id], "content_type": "image"},
        headers=headers,
    )
    assert response.status_code == 402


def test_generate_video_returns_402_on_free_tier(client: TestClient, db: Session) -> None:
    # Free tier's video limit is 0 -- blocked on the very first
    # attempt, unlike text/image which allow a few before blocking.
    headers = _auth_headers(client)
    account_id = _account_id(db)
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id], "content_type": "video"},
        headers=headers,
    )
    assert response.status_code == 402


def test_generate_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/content/generate", json={"topic": "тема", "target_account_ids": []}
    )
    assert response.status_code == 401


def test_generate_rejects_instagram_text_before_generating(client: TestClient, db: Session) -> None:
    # CIN-106: Instagram's Content Publishing API has no text-only
    # post -- this must be rejected up front (400), not generated and
    # then fail later at publish time.
    headers = _auth_headers(client)
    account_id = _account_id(db, SocialPlatform.instagram, "insta-1")
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id], "content_type": "text"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "instagram" in response.json()["detail"]


def test_generate_rejects_target_account_not_owned(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    other = User(email="mallory@cindra.dev", hashed_password="x")
    db.add(other)
    db.commit()
    other_account = upsert_social_account(db, other, SocialPlatform.telegram, "-999", access_token="t")

    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [str(other_account.id)]},
        headers=headers,
    )
    assert response.status_code == 404


def test_generate_with_multiple_targets_uses_intersection_of_content_types(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    telegram_id = _account_id(db, SocialPlatform.telegram, "-100")
    instagram_id = _account_id(db, SocialPlatform.instagram, "insta-1")

    # text is valid for telegram alone but not for the pair (instagram excludes it)
    response = client.post(
        "/content/generate",
        json={
            "topic": "тема",
            "target_account_ids": [telegram_id, instagram_id],
            "content_type": "text",
        },
        headers=headers,
    )
    assert response.status_code == 400

    response = client.post(
        "/content/generate",
        json={
            "topic": "тема",
            "target_account_ids": [telegram_id, instagram_id],
            "content_type": "image",
        },
        headers=headers,
    )
    assert response.status_code == 202


def test_generate_rejects_more_than_five_attachments(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _account_id(db)
    attachments = [
        {"url": f"https://r2.example/{i}.jpg", "attachment_type": "image"} for i in range(6)
    ]
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id], "attachments": attachments},
        headers=headers,
    )
    assert response.status_code == 422


def test_generate_rejects_two_videos(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _account_id(db)
    attachments = [
        {"url": "https://r2.example/a.mp4", "attachment_type": "video"},
        {"url": "https://r2.example/b.mp4", "attachment_type": "video"},
    ]
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id], "attachments": attachments},
        headers=headers,
    )
    assert response.status_code == 422


def test_generate_accepts_five_mixed_attachments(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    account_id = _account_id(db)
    attachments = [
        {"url": "https://r2.example/1.jpg", "attachment_type": "image"},
        {"url": "https://r2.example/2.jpg", "attachment_type": "image"},
        {"url": "https://r2.example/3.jpg", "attachment_type": "image"},
        {"url": "https://r2.example/1.pdf", "attachment_type": "document"},
        {"url": "https://r2.example/1.mp3", "attachment_type": "audio"},
    ]
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id], "attachments": attachments},
        headers=headers,
    )
    assert response.status_code == 202


def test_upload_attachment_returns_url_and_type(client: TestClient) -> None:
    headers = _auth_headers(client)
    with patch(
        "app.routers.content.upload_bytes",
        return_value="https://media.cindra.example/abc.txt",
    ) as upload:
        response = client.post(
            "/content/attachment",
            files={"file": ("notes.txt", b"some context", "text/plain")},
            headers=headers,
        )
    assert response.status_code == 201
    body = response.json()
    assert body == {
        "url": "https://media.cindra.example/abc.txt",
        "attachment_type": "document",
        "mime_type": "text/plain",
    }
    upload.assert_called_once_with(b"some context", "text/plain", "txt")


def test_upload_attachment_downscales_and_reencodes_image(client: TestClient) -> None:
    import io

    from PIL import Image

    headers = _auth_headers(client)
    original = Image.new("RGB", (2000, 1000), color=(10, 20, 30))
    buf = io.BytesIO()
    original.save(buf, format="PNG")

    with patch(
        "app.routers.content.upload_bytes",
        return_value="https://media.cindra.example/abc.jpg",
    ) as upload:
        response = client.post(
            "/content/attachment",
            files={"file": ("photo.png", buf.getvalue(), "image/png")},
            headers=headers,
        )
    assert response.status_code == 201
    body = response.json()
    assert body["attachment_type"] == "image"
    assert body["mime_type"] == "image/jpeg"

    uploaded_bytes, uploaded_mime, uploaded_ext = upload.call_args[0]
    assert uploaded_mime == "image/jpeg"
    assert uploaded_ext == "jpg"
    with Image.open(io.BytesIO(uploaded_bytes)) as resized:
        assert resized.size == (384, 192)


def test_upload_attachment_rejects_corrupt_image(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/content/attachment",
        files={"file": ("photo.jpg", b"not-actually-an-image", "image/jpeg")},
        headers=headers,
    )
    assert response.status_code == 400


def test_upload_attachment_rejects_unsupported_type(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/content/attachment",
        files={"file": ("virus.exe", b"x", "application/x-msdownload")},
        headers=headers,
    )
    assert response.status_code == 400


def test_upload_attachment_rejects_oversized_file(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/content/attachment",
        files={"file": ("big.jpg", b"x" * (11 * 1024 * 1024), "image/jpeg")},
        headers=headers,
    )
    assert response.status_code == 413


def test_upload_attachment_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/content/attachment", files={"file": ("notes.txt", b"x", "text/plain")}
    )
    assert response.status_code == 401


def test_generate_flagged_content_reports_status(client: TestClient, db: Session) -> None:
    def _rejected(payload: dict) -> dict:
        raise ContentModeratedError("упоминание конкурента")

    register_generator(GenerationContentType.text, _rejected)
    headers = _auth_headers(client)
    account_id = _account_id(db)
    response = client.post(
        "/content/generate",
        json={"topic": "тема", "target_account_ids": [account_id]},
        headers=headers,
    )
    assert response.json()["status"] == "flagged"


def test_generate_rejects_unknown_tone(client: TestClient, db: Session) -> None:
    # CIN-138: tone must be a known preset key.
    headers = _auth_headers(client)
    account_id = _account_id(db)
    response = client.post(
        "/content/generate",
        json={
            "topic": "тема",
            "target_account_ids": [account_id],
            "content_type": "text",
            "tone": "sarcastic",
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "Неизвестный тон" in response.text


def test_generate_rejects_unknown_image_template(client: TestClient, db: Session) -> None:
    # CIN-143: image_template must be a known catalog key.
    headers = _auth_headers(client)
    account_id = _account_id(db)
    response = client.post(
        "/content/generate",
        json={
            "topic": "тема",
            "target_account_ids": [account_id],
            "content_type": "image",
            "image_template": "vaporwave",
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert "Неизвестный шаблон" in response.text


def test_image_templates_catalog_is_served(client: TestClient) -> None:
    # CIN-143: single source of truth for the «Посты» template select --
    # id/title/description only, the English prompt directive stays
    # internal. CIN-150 adds preview_url, null until staff generate one.
    headers = _auth_headers(client)
    response = client.get("/content/image-templates", headers=headers)
    assert response.status_code == 200
    templates = response.json()
    ids = {t["id"] for t in templates}
    assert "product_shot" in ids
    assert "flat_lay" in ids
    assert all(set(t) == {"id", "title", "description", "preview_url"} for t in templates)
    assert all(t["title"] and t["description"] for t in templates)
    assert all(t["preview_url"] is None for t in templates)


def test_every_image_template_has_a_preview_topic() -> None:
    # CIN-150: the preview generator reads this for every entry, so a
    # template added without one would only fail at generation time.
    from app.image_templates import IMAGE_TEMPLATES

    assert len(IMAGE_TEMPLATES) >= 12
    for template_id, template in IMAGE_TEMPLATES.items():
        assert template["preview_topic"].strip(), template_id
        assert template["directive"].startswith("Template:"), template_id


def test_stored_preview_is_returned_in_the_catalog(client: TestClient, db: Session) -> None:
    db.add(
        ImageTemplatePreview(
            template_id="product_shot",
            preview_url="https://media.cindra.example/preview.png",
        )
    )
    db.commit()
    response = client.get("/content/image-templates", headers=_auth_headers(client))
    previews = {t["id"]: t["preview_url"] for t in response.json()}
    assert previews["product_shot"] == "https://media.cindra.example/preview.png"
    assert previews["lifestyle"] is None


def test_preview_generation_is_staff_only(client: TestClient) -> None:
    # It spends a real image generation per template (CIN-150).
    response = client.post("/content/image-templates/previews", headers=_auth_headers(client))
    assert response.status_code == 403


def test_preview_generation_stores_urls_and_reports_failures(
    client: TestClient, db: Session
) -> None:
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    user.is_admin = True
    db.commit()

    calls: list[str] = []

    def fake_generator(payload: dict) -> dict:
        calls.append(payload["image_template"])
        if payload["image_template"] == "diagram":
            raise RuntimeError("модель отказалась")
        return {"image_url": f"https://media.cindra.example/{payload['image_template']}.png"}

    with patch("app.routers.content.nano_banana_image_generator", side_effect=fake_generator):
        response = client.post("/content/image-templates/previews", headers=headers)

    assert response.status_code == 200
    body = response.json()
    # One template failing must not abort the rest of the run.
    assert "diagram" in body["failed"]
    assert "product_shot" in body["generated"]
    assert len(calls) == len(body["generated"]) + len(body["failed"])

    stored = {
        row.template_id: row.preview_url
        for row in db.scalars(select(ImageTemplatePreview)).all()
    }
    assert stored["product_shot"] == "https://media.cindra.example/product_shot.png"
    assert "diagram" not in stored


def test_regenerating_a_preview_replaces_the_row(client: TestClient, db: Session) -> None:
    db.add(ImageTemplatePreview(template_id="venue", preview_url="https://old.example/v.png"))
    db.commit()
    headers = _auth_headers(client)
    user = db.scalar(select(User).where(User.email == "ada@cindra.dev"))
    user.is_admin = True
    db.commit()

    with patch(
        "app.routers.content.nano_banana_image_generator",
        return_value={"image_url": "https://new.example/v.png"},
    ):
        client.post("/content/image-templates/previews", headers=headers)

    rows = db.scalars(
        select(ImageTemplatePreview).where(ImageTemplatePreview.template_id == "venue")
    ).all()
    assert len(rows) == 1
    assert rows[0].preview_url == "https://new.example/v.png"


def test_image_templates_catalog_requires_auth(client: TestClient) -> None:
    assert client.get("/content/image-templates").status_code == 401
