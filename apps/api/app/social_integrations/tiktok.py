"""TikTok Login Kit and Content Posting integration.

Direct Post queries creator info immediately before publishing because
TikTok requires clients to show and respect the creator's current
privacy/interaction choices. Draft Upload skips those post settings and
delivers the video to the creator's TikTok inbox for final editing.
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

_API_BASE = "https://open.tiktokapis.com"
_TOKEN_URL = f"{_API_BASE}/v2/oauth/token/"
_CREATOR_INFO_URL = f"{_API_BASE}/v2/post/publish/creator_info/query/"
_DIRECT_POST_URL = f"{_API_BASE}/v2/post/publish/video/init/"
_DRAFT_UPLOAD_URL = f"{_API_BASE}/v2/post/publish/inbox/video/init/"
_STATUS_URL = f"{_API_BASE}/v2/post/publish/status/fetch/"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
_MAX_CHUNK_SIZE = 64 * 1024 * 1024
_MAX_VIDEO_SIZE = 4 * 1024 * 1024 * 1024


def _handle_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code in _RETRYABLE_STATUS_CODES:
        raise TransientPublishError(f"TikTok API {response.status_code}: {response.text[:500]}")
    try:
        body = response.json()
    except ValueError as exc:
        raise PermanentPublishError(
            f"TikTok API вернул не-JSON ответ ({response.status_code})"
        ) from exc

    if response.status_code >= 400:
        message = body.get("error_description") or body.get("message") or body
        raise PermanentPublishError(f"TikTok API {response.status_code}: {message}")

    error = body.get("error")
    if isinstance(error, dict) and error.get("code") not in (None, "ok"):
        raise PermanentPublishError(
            f"TikTok API: {error.get('message') or error.get('code')}"
        )
    if isinstance(error, str) and error:
        raise PermanentPublishError(
            f"TikTok OAuth: {body.get('error_description') or error}"
        )
    return body


def exchange_code_for_token(
    code: str,
    client_key: str,
    client_secret: str,
    redirect_uri: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def refresh_access_token(
    refresh_token: str,
    client_key: str,
    client_secret: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(
        _TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return _handle_json(response)


def _bearer(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def query_creator_info(
    access_token: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(_CREATOR_INFO_URL, headers=_bearer(access_token), json={}, timeout=20.0)
    return _handle_json(response).get("data", {})


def fetch_publish_status(
    access_token: str, publish_id: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    request = client.post if client is not None else httpx.post
    response = request(
        _STATUS_URL,
        headers=_bearer(access_token),
        json={"publish_id": publish_id},
        timeout=20.0,
    )
    return _handle_json(response).get("data", {})


def ensure_fresh_access_token(
    account: SocialAccount, client: httpx.Client | None = None
) -> str:
    """Refreshes an expiring token and mutates the attached ORM row.

    The scheduler commits the SocialAccount and Post together after the
    publisher returns, so refreshed encrypted values survive worker
    restarts without changing the registry's publisher signature.
    """
    expires_at = account.token_expires_at
    if expires_at is None or expires_at > datetime.now(UTC) + _TOKEN_REFRESH_MARGIN:
        return get_access_token(account)

    current_refresh_token = get_refresh_token(account)
    if not current_refresh_token:
        raise PermanentPublishError("TikTok refresh token отсутствует — подключите аккаунт заново")

    settings = get_settings()
    token = refresh_access_token(
        current_refresh_token,
        settings.tiktok_client_key,
        settings.tiktok_client_secret,
        client,
    )
    account.encrypted_access_token = encrypt_token(token["access_token"])
    account.encrypted_refresh_token = encrypt_token(
        token.get("refresh_token") or current_refresh_token
    )
    account.token_expires_at = datetime.now(UTC) + timedelta(seconds=int(token["expires_in"]))
    return token["access_token"]


def _download_video(
    video_url: str, destination: BinaryIO, client: httpx.Client
) -> tuple[int, str]:
    # CIN-134, generalized in CIN-156: Direct Post's FILE_UPLOAD makes
    # our worker download the URL first, so it's only ever allowed to
    # be our own bucket -- see media_validation.py for why.
    validate_own_media_url(video_url, "TikTok")
    size = 0
    with client.stream("GET", video_url, follow_redirects=False, timeout=120.0) as response:
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise TransientPublishError(
                f"Не удалось скачать видео для TikTok: HTTP {response.status_code}"
            )
        if response.status_code != 200:
            raise PermanentPublishError(
                f"Не удалось скачать видео для TikTok: HTTP {response.status_code}"
            )
        content_type = response.headers.get("content-type", "video/mp4").split(";")[0]
        if not content_type.startswith("video/"):
            raise PermanentPublishError(
                f"URL для TikTok вернул {content_type}, ожидалось video/*"
            )
        for chunk in response.iter_bytes(1024 * 1024):
            size += len(chunk)
            if size > _MAX_VIDEO_SIZE:
                raise PermanentPublishError("Видео превышает лимит TikTok 4 GB")
            destination.write(chunk)
    if size == 0:
        raise PermanentPublishError("Видео для TikTok пустое")
    destination.seek(0)
    return size, content_type


def _options_for_account(account: SocialAccount, post: Post) -> dict[str, Any]:
    tiktok_options = post.platform_options.get("tiktok", {})
    return tiktok_options.get("accounts", {}).get(str(account.id), tiktok_options)


def _upload_source_info(video_size: int) -> tuple[dict[str, Any], int, int]:
    chunk_size = min(video_size, _MAX_CHUNK_SIZE)
    total_chunk_count = max(1, video_size // chunk_size)
    return (
        {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunk_count,
        },
        chunk_size,
        total_chunk_count,
    )


def _init_direct_post(
    access_token: str,
    account: SocialAccount,
    post: Post,
    creator_info: dict[str, Any],
    video_size: int,
    client: httpx.Client,
) -> tuple[str, str, int, int]:
    options = _options_for_account(account, post)
    privacy_level = options.get("privacy_level")
    available_privacy = creator_info.get("privacy_level_options", [])
    if not privacy_level or privacy_level not in available_privacy:
        raise PermanentPublishError(
            "Выберите доступный уровень приватности TikTok непосредственно перед публикацией"
        )

    source_info, chunk_size, total_chunk_count = _upload_source_info(video_size)
    post_info = {
        "title": post.text[:2200],
        "privacy_level": privacy_level,
        "disable_duet": bool(options.get("disable_duet"))
        or bool(creator_info.get("duet_disabled")),
        "disable_comment": bool(options.get("disable_comment"))
        or bool(creator_info.get("comment_disabled")),
        "disable_stitch": bool(options.get("disable_stitch"))
        or bool(creator_info.get("stitch_disabled")),
        "video_cover_timestamp_ms": int(options.get("video_cover_timestamp_ms", 1000)),
        "brand_content_toggle": bool(options.get("brand_content_toggle")),
        "brand_organic_toggle": bool(options.get("brand_organic_toggle")),
        "is_aigc": bool(options.get("is_aigc")),
    }
    payload = {
        "post_info": post_info,
        "source_info": source_info,
    }
    response = client.post(
        _DIRECT_POST_URL,
        headers=_bearer(access_token),
        json=payload,
        timeout=30.0,
    )
    data = _handle_json(response).get("data", {})
    return data["publish_id"], data["upload_url"], chunk_size, total_chunk_count


def _init_draft_upload(
    access_token: str,
    video_size: int,
    client: httpx.Client,
) -> tuple[str, str, int, int]:
    source_info, chunk_size, total_chunk_count = _upload_source_info(video_size)
    response = client.post(
        _DRAFT_UPLOAD_URL,
        headers=_bearer(access_token),
        json={"source_info": source_info},
        timeout=30.0,
    )
    data = _handle_json(response).get("data", {})
    return data["publish_id"], data["upload_url"], chunk_size, total_chunk_count


def _upload_video(
    source: BinaryIO,
    upload_url: str,
    video_size: int,
    chunk_size: int,
    total_chunk_count: int,
    content_type: str,
    client: httpx.Client,
) -> None:
    start = 0
    for index in range(total_chunk_count):
        is_last = index == total_chunk_count - 1
        length = video_size - start if is_last else chunk_size
        chunk = source.read(length)
        end = start + len(chunk) - 1
        response = client.put(
            upload_url,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {start}-{end}/{video_size}",
            },
            content=chunk,
            timeout=120.0,
        )
        expected = {200, 201} if is_last else {200, 206}
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise TransientPublishError(
                f"TikTok upload {response.status_code}: {response.text[:500]}"
            )
        if response.status_code not in expected:
            raise PermanentPublishError(
                f"TikTok upload {response.status_code}: {response.text[:500]}"
            )
        start = end + 1


def publish(
    account: SocialAccount, post: Post, client: httpx.Client | None = None
) -> dict[str, Any]:
    if not post.video_url:
        raise PermanentPublishError("Публикация в TikTok требует video_url")

    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        access_token = ensure_fresh_access_token(account, active_client)
        options = _options_for_account(account, post)
        mode = options.get("mode", "direct_post")
        if mode not in {"direct_post", "draft_upload"}:
            raise PermanentPublishError("Неизвестный режим публикации TikTok")

        with tempfile.SpooledTemporaryFile(max_size=_MAX_CHUNK_SIZE) as video:
            video_size, content_type = _download_video(post.video_url, video, active_client)
            if mode == "draft_upload":
                publish_id, upload_url, chunk_size, total_chunk_count = _init_draft_upload(
                    access_token, video_size, active_client
                )
            else:
                creator_info = query_creator_info(access_token, active_client)
                publish_id, upload_url, chunk_size, total_chunk_count = _init_direct_post(
                    access_token, account, post, creator_info, video_size, active_client
                )
            _upload_video(
                video,
                upload_url,
                video_size,
                chunk_size,
                total_chunk_count,
                content_type,
                active_client,
            )
        return {"id": publish_id, "mode": mode}
    finally:
        if owns_client:
            active_client.close()
