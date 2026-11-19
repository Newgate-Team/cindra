from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Subscription, SubscriptionTier, UsageEvent, UsageEventType, User

_RENDERED_URL = "https://media.cindra.example/card.png"


@pytest.fixture(autouse=True)
def _media_storage_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # The endpoint refuses to render without R2 configured, and the
    # test settings have no credentials -- upload_bytes itself is
    # patched per test, so a placeholder account id is enough.
    monkeypatch.setattr(get_settings(), "r2_account_id", "test-account")


def _auth_headers(client: TestClient, email: str = "layout@cindra.dev") -> dict[str, str]:
    payload = {"email": email, "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _render_body(**overrides) -> dict:
    body = {
        "template_id": "quote_card",
        "canvas_format": "square",
        "values": {"quote": "Текст карточки", "author": "Автор"},
    }
    body.update(overrides)
    return body


def test_catalog_lists_templates_with_slots(client: TestClient) -> None:
    response = client.get("/content/layout-templates", headers=_auth_headers(client))
    assert response.status_code == 200
    templates = response.json()
    by_id = {t["id"]: t for t in templates}
    assert "quote_card" in by_id
    quote = by_id["quote_card"]
    assert quote["supports_image"] is False
    assert [s["name"] for s in quote["slots"]] == ["quote", "author"]
    assert by_id["photo_quote"]["supports_image"] is True
    # The render spec is internal -- the UI has no business with it.
    assert "blocks" not in quote


def test_catalog_requires_auth(client: TestClient) -> None:
    assert client.get("/content/layout-templates").status_code == 401


def test_preview_returns_png(client: TestClient) -> None:
    response = client.get(
        "/content/layout-templates/stat_card/preview", headers=_auth_headers(client)
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    assert "max-age" in response.headers.get("cache-control", "")


def test_preview_of_unknown_template_is_404(client: TestClient) -> None:
    response = client.get(
        "/content/layout-templates/nope/preview", headers=_auth_headers(client)
    )
    assert response.status_code == 404


def test_preview_is_not_metered(client: TestClient, db: Session) -> None:
    # Previews are demo renders with no storage -- charging for them
    # would make the gallery cost quota just to browse.
    headers = _auth_headers(client)
    client.get("/content/layout-templates/quote_card/preview", headers=headers)
    user_id = db.scalar(select(User.id).where(User.email == "layout@cindra.dev"))
    assert db.scalars(select(UsageEvent).where(UsageEvent.user_id == user_id)).all() == []


def test_render_uploads_and_records_usage(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    with patch(
        "app.routers.content.upload_bytes", return_value=_RENDERED_URL
    ) as upload:
        response = client.post("/content/layout-render", json=_render_body(), headers=headers)
    assert response.status_code == 200
    assert response.json()["image_url"] == _RENDERED_URL
    assert upload.call_args[0][1] == "image/png"

    user_id = db.scalar(select(User.id).where(User.email == "layout@cindra.dev"))
    events = db.scalars(select(UsageEvent).where(UsageEvent.user_id == user_id)).all()
    assert [e.event_type for e in events] == [UsageEventType.layout_render]


def test_render_without_media_storage_configured_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Otherwise boto3 fails deep inside with "Invalid endpoint:
    # https://.r2.cloudflarestorage.com" and the user sees a bare 500
    # for what is a server configuration gap (seen live before the fix).
    monkeypatch.setattr(get_settings(), "r2_account_id", "")
    response = client.post(
        "/content/layout-render", json=_render_body(), headers=_auth_headers(client)
    )
    assert response.status_code == 503
    assert "хранилище" in response.json()["detail"]


def test_render_rejects_unknown_template(client: TestClient) -> None:
    response = client.post(
        "/content/layout-render",
        json=_render_body(template_id="nope"),
        headers=_auth_headers(client),
    )
    assert response.status_code == 400
    assert "Неизвестный шаблон" in response.json()["detail"]


def test_render_rejects_missing_required_slot(client: TestClient) -> None:
    response = client.post(
        "/content/layout-render",
        json=_render_body(values={"author": "Только автор"}),
        headers=_auth_headers(client),
    )
    assert response.status_code == 400


def test_failed_render_does_not_consume_quota(client: TestClient, db: Session) -> None:
    # CIN-139's rule applied to a synchronous endpoint: the quota is
    # only recorded once the render actually produced something.
    headers = _auth_headers(client)
    client.post(
        "/content/layout-render", json=_render_body(template_id="nope"), headers=headers
    )
    user_id = db.scalar(select(User.id).where(User.email == "layout@cindra.dev"))
    assert db.scalars(select(UsageEvent).where(UsageEvent.user_id == user_id)).all() == []


def test_render_rejects_oversized_accent_and_values(client: TestClient) -> None:
    headers = _auth_headers(client)
    assert (
        client.post(
            "/content/layout-render", json=_render_body(accent="red"), headers=headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/content/layout-render",
            json=_render_body(values={"quote": "x" * 1001}),
            headers=headers,
        ).status_code
        == 422
    )


def test_render_quota_runs_out_on_free_tier(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user_id = db.scalar(select(User.id).where(User.email == "layout@cindra.dev"))
    for _ in range(30):  # the whole Free allowance
        db.add(UsageEvent(user_id=user_id, event_type=UsageEventType.layout_render))
    db.commit()

    response = client.post("/content/layout-render", json=_render_body(), headers=headers)
    assert response.status_code == 402
    assert "карточек по шаблону" in response.json()["detail"]


def test_business_tier_has_no_render_ceiling(client: TestClient, db: Session) -> None:
    headers = _auth_headers(client)
    user_id = db.scalar(select(User.id).where(User.email == "layout@cindra.dev"))
    db.execute(
        update(Subscription)
        .where(Subscription.user_id == user_id)
        .values(tier=SubscriptionTier.business)
    )
    for _ in range(60):
        db.add(UsageEvent(user_id=user_id, event_type=UsageEventType.layout_render))
    db.commit()

    with patch("app.routers.content.upload_bytes", return_value=_RENDERED_URL):
        response = client.post("/content/layout-render", json=_render_body(), headers=headers)
    assert response.status_code == 200


@pytest.mark.parametrize("canvas_format", ["square", "story", "landscape"])
def test_render_accepts_every_canvas_format(client: TestClient, canvas_format: str) -> None:
    with patch("app.routers.content.upload_bytes", return_value=_RENDERED_URL):
        response = client.post(
            "/content/layout-render",
            json=_render_body(canvas_format=canvas_format),
            headers=_auth_headers(client),
        )
    assert response.status_code == 200


def _png_bytes(width: int = 3000, height: int = 2000) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_background_upload_downscales_for_the_canvas(client: TestClient) -> None:
    # CIN-151: the attachment path shrinks to 384px for the model's
    # tile budget -- unusable behind a 1080x1920 card, so backgrounds
    # get their own, much larger bound.
    from io import BytesIO

    from PIL import Image

    stored: dict = {}

    def fake_upload(data: bytes, content_type: str, extension: str) -> str:
        stored["data"] = data
        stored["extension"] = extension
        return "https://media.cindra.example/bg.jpg"

    with patch("app.routers.content.upload_bytes", side_effect=fake_upload):
        response = client.post(
            "/content/layout-background",
            files={"file": ("photo.png", _png_bytes(), "image/png")},
            headers=_auth_headers(client),
        )

    assert response.status_code == 201
    assert response.json()["background_url"] == "https://media.cindra.example/bg.jpg"
    assert stored["extension"] == "jpg"
    stored_image = Image.open(BytesIO(stored["data"]))
    assert max(stored_image.size) == 2160
    assert max(stored_image.size) > 384


def test_background_upload_rejects_non_images(client: TestClient) -> None:
    response = client.post(
        "/content/layout-background",
        files={"file": ("notes.txt", b"just text", "text/plain")},
        headers=_auth_headers(client),
    )
    assert response.status_code == 400
    assert "изображение" in response.json()["detail"]


def test_background_upload_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/content/layout-background",
        files={"file": ("photo.png", _png_bytes(10, 10), "image/png")},
    )
    assert response.status_code == 401


def test_background_upload_without_storage_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "r2_account_id", "")
    response = client.post(
        "/content/layout-background",
        files={"file": ("photo.png", _png_bytes(10, 10), "image/png")},
        headers=_auth_headers(client),
    )
    assert response.status_code == 503
