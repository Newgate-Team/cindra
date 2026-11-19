"""LinkedIn OAuth + posting (member-context "Share on LinkedIn").

Text and image posts only -- LinkedIn's video upload is a multi-part
initialize/upload-parts/finalize protocol (unlike a single resumable
PUT like YouTube's), a genuinely larger piece of work deliberately
scoped out of this first pass (see publish_matrix.py's comment on the
same exclusion). publish() raises a clear PermanentPublishError for a
video post rather than silently mishandling it.

Built to LinkedIn's documented REST API shape (OpenID Connect userinfo
for identity, POST /rest/posts for publishing, POST /rest/images for
image upload) -- flagging this because, unlike TikTok/Instagram/
YouTube's APIs (verified against real production calls earlier in this
codebase's history), this integration has not yet been exercised
against a real LinkedIn app. Whoever configures real
LINKEDIN_CLIENT_ID/SECRET should double-check exact field names/
endpoints against LinkedIn's current Developer Portal docs before
relying on this beyond the mocked test coverage.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO

import httpx

from app.config import get_settings
from app.models import Post, SocialAccount
from app.social_accounts import get_access_token, get_refresh_token
from app.social_integrations.errors import PermanentPublishError, TransientPublishError
from app.social_integrations.media_validation import validate_own_media_url
from app.token_crypto import encrypt_token

_AUTH_BASE = "https://www.linkedin.com/oauth/v2"
_TOKEN_URL = f"{_AUTH_BASE}/accessToken"
# The OIDC userinfo endpoint -- not versioned/under /rest, a separate
# LinkedIn API surface from the REST posting API below.
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
_API_BASE = "https://api.linkedin.com/rest"
_POSTS_URL = f"{_API_BASE}/posts"
_IMAGES_URL = f"{_API_BASE}/images"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
# Same reasoning as youtube.py's cap -- matches what this app actually
# produces (video_projects.py's own upload cap), not a LinkedIn limit.
_MAX_IMAGE_SIZE = 200 * 1024 * 1024


def _handle_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(f"LinkedIn API {response.status_code}: {response.text[:500]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise PermanentPublishError(
            f"LinkedIn API вернул не-JSON ответ ({response.status_code})"
        ) from exc

    if response.status_code >= 400:
        message = body.get("message") if isinstance(body, dict) else body
        raise PermanentPublishError(f"LinkedIn API {response.status_code}: {message or body}")
    return body


def exchange_code_for_token(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def refresh_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Only works for apps LinkedIn has separately granted refresh-token
    access to -- most apps get a long-lived (60 day) access token and
    NO refresh token at all. ensure_fresh_access_token below handles
    both cases: this is only ever called when a refresh token was
    actually stored in the first place."""
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def get_member_info(access_token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """OIDC userinfo -- `sub` is the member's own id, the value this
    app stores as SocialAccount.external_account_id and embeds into
    the `urn:li:person:{sub}` author URN every post needs."""
    request = client.get if client is not None else httpx.get
    response = request(
        _USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20.0,
    )
    return _handle_json(response)


def _rest_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": get_settings().linkedin_api_version,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def ensure_fresh_access_token(
    account: SocialAccount, client: httpx.Client | None = None
) -> str:
    """Same refresh-on-demand contract as tiktok.py/youtube.py's own
    ensure_fresh_access_token. Falls straight to "reconnect" when no
    refresh token was ever stored -- the common case for LinkedIn, see
    refresh_access_token's docstring."""
    expires_at = account.token_expires_at
    if expires_at is None or expires_at > datetime.now(UTC) + _TOKEN_REFRESH_MARGIN:
        return get_access_token(account)

    current_refresh_token = get_refresh_token(account)
    if not current_refresh_token:
        raise PermanentPublishError(
            "Срок действия токена LinkedIn истёк — подключите аккаунт заново"
        )

    settings = get_settings()
    token = refresh_access_token(
        current_refresh_token,
        settings.linkedin_client_id,
        settings.linkedin_client_secret,
        client,
    )
    account.encrypted_access_token = encrypt_token(token["access_token"])
    account.encrypted_refresh_token = encrypt_token(
        token.get("refresh_token") or current_refresh_token
    )
    account.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(token["expires_in"]))
    return token["access_token"]


def _download_image(
    image_url: str, destination: BinaryIO, client: httpx.Client
) -> tuple[int, str]:
    # Same SSRF reasoning as tiktok.py/youtube.py's own _download_video:
    # the worker fetches this URL itself, so it must only ever be our
    # own bucket.
    validate_own_media_url(image_url, "LinkedIn")
    size = 0
    with client.stream("GET", image_url, follow_redirects=False, timeout=60.0) as response:
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise TransientPublishError(
                f"Не удалось скачать изображение для LinkedIn: HTTP {response.status_code}"
            )
        if response.status_code != 200:
            raise PermanentPublishError(
                f"Не удалось скачать изображение для LinkedIn: HTTP {response.status_code}"
            )
        content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
        if not content_type.startswith("image/"):
            raise PermanentPublishError(
                f"URL для LinkedIn вернул {content_type}, ожидалось image/*"
            )
        for chunk in response.iter_bytes(1024 * 1024):
            size += len(chunk)
            if size > _MAX_IMAGE_SIZE:
                raise PermanentPublishError("Изображение превышает лимит для загрузки в LinkedIn")
            destination.write(chunk)
    if size == 0:
        raise PermanentPublishError("Изображение для LinkedIn пустое")
    destination.seek(0)
    return size, content_type


def _init_image_upload(
    access_token: str, member_urn: str, client: httpx.Client
) -> tuple[str, str]:
    response = client.post(
        f"{_IMAGES_URL}?action=initializeUpload",
        headers=_rest_headers(access_token),
        json={"initializeUploadRequest": {"owner": member_urn}},
        timeout=30.0,
    )
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(
            f"LinkedIn image init {response.status_code}: {response.text[:500]}"
        )
    if response.status_code >= 400:
        raise PermanentPublishError(
            f"LinkedIn image init {response.status_code}: {response.text[:500]}"
        )
    value = response.json().get("value", {})
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        raise PermanentPublishError("LinkedIn не вернул адрес или id для загрузки изображения")
    return upload_url, image_urn


def _upload_image(
    source: BinaryIO, upload_url: str, content_type: str, client: httpx.Client
) -> None:
    response = client.put(
        upload_url,
        headers={"Content-Type": content_type},
        content=source.read(),
        timeout=120.0,
    )
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(
            f"LinkedIn image upload {response.status_code}: {response.text[:500]}"
        )
    if response.status_code not in (200, 201):
        raise PermanentPublishError(
            f"LinkedIn image upload {response.status_code}: {response.text[:500]}"
        )


def publish(
    account: SocialAccount, post: Post, client: httpx.Client | None = None
) -> dict[str, Any]:
    if post.video_url:
        raise PermanentPublishError("Публикация видео в LinkedIn пока не поддерживается")
    if not post.text:
        raise PermanentPublishError("Публикация в LinkedIn требует текста")

    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        access_token = ensure_fresh_access_token(account, active_client)
        member_urn = f"urn:li:person:{account.external_account_id}"

        body: dict[str, Any] = {
            "author": member_urn,
            "commentary": post.text[:3000],
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        if post.image_url:
            with tempfile.SpooledTemporaryFile(max_size=_MAX_IMAGE_SIZE) as image:
                _image_size, content_type = _download_image(post.image_url, image, active_client)
                upload_url, image_urn = _init_image_upload(access_token, member_urn, active_client)
                _upload_image(image, upload_url, content_type, active_client)
            body["content"] = {"media": {"id": image_urn}}

        response = active_client.post(
            _POSTS_URL, headers=_rest_headers(access_token), json=body, timeout=30.0
        )
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise TransientPublishError(
                f"LinkedIn post {response.status_code}: {response.text[:500]}"
            )
        if response.status_code >= 400:
            raise PermanentPublishError(
                f"LinkedIn post {response.status_code}: {response.text[:500]}"
            )
        # A successful create returns 201 with an empty body -- the new
        # post's own id comes back in this response header instead.
        post_id = response.headers.get("x-restli-id", "")
        return {"id": post_id}
    finally:
        if owns_client:
            active_client.close()
