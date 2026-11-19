"""YouTube Data API v3 integration (OAuth + resumable video upload).

Unlike TikTok, YouTube's API has no mandatory "query creator settings
immediately before publish" step and no platform-mandated ban on a
default privacy value -- video-only like TikTok, but otherwise closer
to Instagram's simpler publish() shape.
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

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_UPLOAD_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
# Matches video_projects.py's own upload cap (_MAX_UPLOAD_BYTES) --
# nothing this app ever hands to a publisher exceeds that, so there's
# no reason to allow more here. YouTube itself supports far larger
# files; this bound is about what Cindra actually produces, not a
# YouTube API limit.
_MAX_VIDEO_SIZE = 200 * 1024 * 1024
_DEFAULT_CATEGORY_ID = "22"  # People & Blogs -- a generic, safe default


def _handle_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(f"YouTube API {response.status_code}: {response.text[:500]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise PermanentPublishError(
            f"YouTube API вернул не-JSON ответ ({response.status_code})"
        ) from exc

    if response.status_code >= 400:
        error = body.get("error", {})
        message = error.get("message") if isinstance(error, dict) else error
        raise PermanentPublishError(f"YouTube API {response.status_code}: {message or body}")
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
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
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
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def get_channel_info(access_token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """The authenticated user's own channel -- `mine=true` avoids a
    separate channel-id lookup step, same reasoning as query_creator_info
    in tiktok.py being called with no id argument."""
    request = client.get if client is not None else httpx.get
    response = request(
        _CHANNELS_URL,
        params={"part": "snippet", "mine": "true"},
        headers=_bearer(access_token),
        timeout=20.0,
    )
    items = _handle_json(response).get("items", [])
    if not items:
        raise PermanentPublishError("У аккаунта Google нет ни одного канала YouTube")
    return items[0]


def ensure_fresh_access_token(
    account: SocialAccount, client: httpx.Client | None = None
) -> str:
    """Refreshes an expiring token and mutates the attached ORM row --
    same contract as tiktok.py's ensure_fresh_access_token (the caller
    commits the SocialAccount alongside the Post afterward)."""
    expires_at = account.token_expires_at
    if expires_at is None or expires_at > datetime.now(UTC) + _TOKEN_REFRESH_MARGIN:
        return get_access_token(account)

    current_refresh_token = get_refresh_token(account)
    if not current_refresh_token:
        raise PermanentPublishError(
            "YouTube refresh token отсутствует — подключите аккаунт заново"
        )

    settings = get_settings()
    token = refresh_access_token(
        current_refresh_token,
        settings.youtube_client_id,
        settings.youtube_client_secret,
        client,
    )
    account.encrypted_access_token = encrypt_token(token["access_token"])
    # Google's refresh response doesn't repeat the refresh_token unless
    # it issued a new one -- keep the existing one otherwise, same
    # pattern as tiktok.py.
    account.encrypted_refresh_token = encrypt_token(
        token.get("refresh_token") or current_refresh_token
    )
    account.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(token["expires_in"]))
    return token["access_token"]


def _download_video(
    video_url: str, destination: BinaryIO, client: httpx.Client
) -> tuple[int, str]:
    # Same SSRF reasoning as tiktok.py's _download_video (CIN-134/156):
    # the worker fetches this URL itself, so it must only ever be our
    # own bucket.
    validate_own_media_url(video_url, "YouTube")
    size = 0
    with client.stream("GET", video_url, follow_redirects=False, timeout=120.0) as response:
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise TransientPublishError(
                f"Не удалось скачать видео для YouTube: HTTP {response.status_code}"
            )
        if response.status_code != 200:
            raise PermanentPublishError(
                f"Не удалось скачать видео для YouTube: HTTP {response.status_code}"
            )
        content_type = response.headers.get("content-type", "video/mp4").split(";")[0]
        if not content_type.startswith("video/"):
            raise PermanentPublishError(
                f"URL для YouTube вернул {content_type}, ожидалось video/*"
            )
        for chunk in response.iter_bytes(1024 * 1024):
            size += len(chunk)
            if size > _MAX_VIDEO_SIZE:
                raise PermanentPublishError("Видео превышает лимит для загрузки на YouTube")
            destination.write(chunk)
    if size == 0:
        raise PermanentPublishError("Видео для YouTube пустое")
    destination.seek(0)
    return size, content_type


def _options_for_account(account: SocialAccount, post: Post) -> dict[str, Any]:
    youtube_options = post.platform_options.get("youtube", {})
    return youtube_options.get("accounts", {}).get(str(account.id), youtube_options)


def _init_resumable_upload(
    access_token: str,
    account: SocialAccount,
    post: Post,
    video_size: int,
    content_type: str,
    client: httpx.Client,
) -> str:
    """Step 1 of Google's resumable upload protocol: register the
    upload's metadata and get back a session URL to PUT the actual
    bytes to. A single PUT of the whole body to that session URL
    (below) is a complete, valid use of the protocol -- true chunked/
    resumed uploads are also supported by the same session URL, but
    nothing this app produces is large enough to need that."""
    options = _options_for_account(account, post)
    title = options.get("title") or post.text[:100] or "Cindra video"
    payload = {
        "snippet": {
            "title": title[:100],
            "description": post.text[:5000],
            "categoryId": options.get("category_id", _DEFAULT_CATEGORY_ID),
        },
        "status": {"privacyStatus": options.get("privacy_status", "public")},
    }
    response = client.post(
        _UPLOAD_INIT_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            **_bearer(access_token),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": content_type,
            "X-Upload-Content-Length": str(video_size),
        },
        json=payload,
        timeout=30.0,
    )
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(
            f"YouTube upload init {response.status_code}: {response.text[:500]}"
        )
    if response.status_code >= 400:
        raise PermanentPublishError(
            f"YouTube upload init {response.status_code}: {response.text[:500]}"
        )
    upload_url = response.headers.get("location")
    if not upload_url:
        raise PermanentPublishError("YouTube не вернул адрес для загрузки видео")
    return upload_url


def _upload_video(
    source: BinaryIO,
    upload_url: str,
    video_size: int,
    content_type: str,
    client: httpx.Client,
) -> dict[str, Any]:
    response = client.put(
        upload_url,
        headers={"Content-Type": content_type, "Content-Length": str(video_size)},
        content=source.read(),
        timeout=300.0,
    )
    return _handle_json(response)


def publish(
    account: SocialAccount, post: Post, client: httpx.Client | None = None
) -> dict[str, Any]:
    if not post.video_url:
        raise PermanentPublishError("Публикация на YouTube требует video_url")

    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        access_token = ensure_fresh_access_token(account, active_client)
        with tempfile.SpooledTemporaryFile(max_size=_MAX_VIDEO_SIZE) as video:
            video_size, content_type = _download_video(post.video_url, video, active_client)
            upload_url = _init_resumable_upload(
                access_token, account, post, video_size, content_type, active_client
            )
            result = _upload_video(video, upload_url, video_size, content_type, active_client)
        return {"id": result["id"]}
    finally:
        if owns_client:
            active_client.close()
