from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import Post, SocialPlatform, User
from app.social_accounts import upsert_social_account
from app.social_integrations import instagram
from app.social_integrations.errors import PermanentPublishError, TransientPublishError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_exchange_code_for_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v21.0/oauth/access_token"
        assert "code=auth-code" in str(request.url)
        return httpx.Response(200, json={"access_token": "short-lived-token"})

    token = instagram.exchange_code_for_token(
        "auth-code", "https://app.example/callback", "app-id", "app-secret", client=_client(handler)
    )
    assert token == "short-lived-token"


def test_exchange_code_for_token_permanent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "Missing or invalid client id.", "code": 101}}
        )

    with pytest.raises(PermanentPublishError):
        instagram.exchange_code_for_token(
            "bad", "https://app.example/callback", "", "", client=_client(handler)
        )


def test_get_long_lived_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "grant_type=fb_exchange_token" in str(request.url)
        return httpx.Response(200, json={"access_token": "long-lived-token"})

    token = instagram.get_long_lived_token(
        "short-lived-token", "app-id", "app-secret", client=_client(handler)
    )
    assert token == "long-lived-token"


def test_discover_connected_accounts_found_on_first_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v21.0/me/accounts":
            return httpx.Response(
                200,
                json={"data": [{"id": "page-1", "name": "Cindra", "access_token": "page-token-1"}]},
            )
        assert request.url.path == "/v21.0/page-1"
        return httpx.Response(
            200,
            json={"instagram_business_account": {"id": "ig-42", "username": "mybrand"}},
        )

    accounts = instagram.discover_connected_accounts("token", client=_client(handler))
    assert accounts["instagram"] == {"id": "ig-42", "username": "mybrand"}
    assert accounts["facebook_page"] == {
        "id": "page-1",
        "name": "Cindra",
        "access_token": "page-token-1",
    }


def test_discover_connected_accounts_skips_pages_without_ig() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v21.0/me/accounts":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "page-1", "name": "No IG", "access_token": "token-1"},
                        {"id": "page-2", "name": "Has IG", "access_token": "token-2"},
                    ]
                },
            )
        if request.url.path == "/v21.0/page-1":
            return httpx.Response(200, json={})  # no instagram_business_account
        return httpx.Response(
            200, json={"instagram_business_account": {"id": "ig-99", "username": "other"}}
        )

    accounts = instagram.discover_connected_accounts("token", client=_client(handler))
    assert accounts["instagram"]["id"] == "ig-99"
    assert accounts["facebook_page"]["id"] == "page-2"


def test_discover_connected_accounts_none_linked_is_permanent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v21.0/me/accounts":
            return httpx.Response(
                200, json={"data": [{"id": "page-1", "name": "Cindra", "access_token": "t"}]}
            )
        return httpx.Response(200, json={})

    with pytest.raises(PermanentPublishError):
        instagram.discover_connected_accounts("token", client=_client(handler))


def test_create_and_publish_media() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v21.0/ig-42/media":
            assert "image_url=" in str(request.url)
            return httpx.Response(200, json={"id": "container-1"})
        assert request.url.path == "/v21.0/ig-42/media_publish"
        assert "creation_id=container-1" in str(request.url)
        return httpx.Response(200, json={"id": "media-1"})

    creation_id = instagram.create_media_container(
        "ig-42", "https://example.com/x.jpg", "caption", "token", client=_client(handler)
    )
    assert creation_id == "container-1"
    result = instagram.publish_media("ig-42", creation_id, "token", client=_client(handler))
    assert result == {"id": "media-1"}


def test_rate_limit_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    with pytest.raises(TransientPublishError):
        instagram.create_media_container(
            "ig-42", "https://example.com/x.jpg", "caption", "token", client=_client(handler)
        )


def test_publish_without_image_url_is_a_permanent_error(db: Session, user: User) -> None:
    account = upsert_social_account(
        db, user, SocialPlatform.instagram, "ig-42", access_token="token"
    )
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text="без картинки",
        scheduled_for=datetime.now(UTC),
    )

    with pytest.raises(PermanentPublishError):
        instagram.publish(account, post)


def test_publish_with_image_url_creates_then_publishes_container(
    db: Session, user: User
) -> None:
    account = upsert_social_account(
        db, user, SocialPlatform.instagram, "ig-42", access_token="token"
    )
    post = Post(
        user_id=user.id,
        social_account_id=account.id,
        text="caption",
        image_url="https://example.com/x.jpg",
        scheduled_for=datetime.now(UTC),
    )

    with (
        patch("app.social_integrations.instagram.create_media_container", return_value="container-1") as create,
        patch("app.social_integrations.instagram.publish_media", return_value={"id": "media-1"}) as pub,
    ):
        result = instagram.publish(account, post)

    create.assert_called_once_with("ig-42", "https://example.com/x.jpg", "caption", "token")
    pub.assert_called_once_with("ig-42", "container-1", "token")
    assert result == {"id": "media-1"}
