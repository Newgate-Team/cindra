import io
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.media_storage import upload_bytes
from app.content_pipeline.video_generator import VideoGenerationFailedError
from app.db import SessionLocal
from app.integrations import get_secret
from app.models import Integration
from app.tool_integrations import heygen

_POLL_INTERVAL_SECONDS = 10.0
_MAX_POLL_ATTEMPTS = 120  # ~20 minutes -- a long script on Avatar IV can take a while
_MAX_VIDEO_BYTES = 300 * 1024 * 1024
_DOWNLOAD_CHUNK = 1024 * 1024

# Same SSRF shape seedance_generator closes for fal.ai: the finished
# file's URL comes from the provider's response, so only HeyGen's own
# file hosts are fetched (docs examples: files.heygen.com,
# resource.heygen.ai), and a redirect away from them isn't followed.
# Not yet observed on a real render -- if HeyGen ever serves from
# another CDN, the failure names the host so this list is a one-line fix.
_ALLOWED_RESULT_HOSTS = ("heygen.com", "heygen.ai")


def _is_allowed_result_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    return any(
        parts.hostname == host or parts.hostname.endswith("." + host)
        for host in _ALLOWED_RESULT_HOSTS
    )


def _api_key(integration_id: str) -> str:
    # Read at execution time, not copied into the job at enqueue, so
    # the secret never sits in generation_jobs and disconnecting
    # HeyGen stops any job that hasn't started rendering yet.
    with SessionLocal() as db:
        integration = db.get(Integration, uuid.UUID(integration_id))
        if integration is None:
            raise VideoGenerationFailedError(
                "HeyGen отключён — подключите его заново на странице «Инструменты»"
            )
        return get_secret(integration)


def _download(url: str, client: httpx.Client) -> bytes:
    if not _is_allowed_result_url(url):
        raise VideoGenerationFailedError(
            f"HeyGen вернул видео с неожиданного хоста ({urlsplit(url).hostname}) — "
            "скачивание отклонено"
        )
    buffer = io.BytesIO()
    try:
        # No X-Api-Key here: the file host must never see the user's key.
        with client.stream("GET", url, follow_redirects=False, timeout=300.0) as response:
            if response.status_code != 200:
                raise TransientGenerationError(
                    f"Скачивание видео из HeyGen: HTTP {response.status_code}"
                )
            for chunk in response.iter_bytes(_DOWNLOAD_CHUNK):
                buffer.write(chunk)
                if buffer.tell() > _MAX_VIDEO_BYTES:
                    raise VideoGenerationFailedError("Видео из HeyGen слишком большое")
    except httpx.TransportError as exc:
        raise TransientGenerationError(f"Скачивание видео из HeyGen: {exc}") from exc
    if buffer.tell() == 0:
        raise TransientGenerationError("HeyGen отдал пустой файл видео")
    return buffer.getvalue()


def _render(
    api_key: str,
    payload: dict[str, Any],
    client: httpx.Client,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    video_id = heygen.create_avatar_video(
        api_key,
        avatar_id=payload["avatar_id"],
        voice_id=payload["voice_id"],
        script=payload["script"],
        aspect_ratio=payload.get("aspect_ratio", "9:16"),
        idempotency_key=payload["heygen_request_id"],
        title=payload.get("title"),
        client=client,
    )
    for _ in range(_MAX_POLL_ATTEMPTS):
        sleep(_POLL_INTERVAL_SECONDS)
        video = heygen.get_video(api_key, video_id, client=client)
        status = video.get("status")
        if status == "completed":
            return {**video, "id": video.get("id") or video_id}
        if status == "failed":
            reason = video.get("failure_message") or video.get("failure_code") or "причина не указана"
            raise VideoGenerationFailedError(f"HeyGen не смог сгенерировать видео: {reason}")
    raise VideoGenerationFailedError(
        f"HeyGen не закончил рендер за {_MAX_POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS / 60:.0f} минут"
    )


def heygen_video_generator(
    payload: dict[str, Any],
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Avatar video rendered with the user's own HeyGen account.

    A Celery retry re-runs this whole function, including the create
    call -- payload["heygen_request_id"] is sent as HeyGen's
    Idempotency-Key, so the retry gets the original render back
    instead of starting (and charging the user's wallet for) another.
    """
    api_key = _api_key(payload["heygen_integration_id"])
    owns_client = client is None
    active_client = client or httpx.Client()
    try:
        try:
            video = _render(api_key, payload, active_client, sleep)
        except heygen.HeyGenTransientError as exc:
            raise TransientGenerationError(str(exc)) from exc
        except heygen.HeyGenInsufficientCreditError as exc:
            raise VideoGenerationFailedError(
                "На балансе HeyGen API недостаточно средств — пополните кошелёк в HeyGen "
                "и запустите генерацию снова"
            ) from exc
        except heygen.HeyGenAuthError as exc:
            raise VideoGenerationFailedError(
                "HeyGen отклонил ключ — подключите HeyGen заново на странице «Инструменты»"
            ) from exc
        except heygen.HeyGenError as exc:
            raise VideoGenerationFailedError(str(exc)) from exc

        video_url = video.get("video_url")
        if not video_url:
            raise VideoGenerationFailedError("HeyGen вернул готовое видео без ссылки на файл")
        content = _download(video_url, active_client)
        try:
            stored_url = upload_bytes(content, "video/mp4", "mp4")
        except Exception as exc:
            # Any storage failure: the render already happened (and was paid for from the
            # user's wallet) -- point them at it in HeyGen rather than
            # letting the money look lost.
            where = video.get("video_page_url") or f"id {video['id']}"
            raise VideoGenerationFailedError(
                f"Видео готово в HeyGen ({where}), но не удалось сохранить его в Cindra: {exc}"
            ) from exc
    finally:
        if owns_client:
            active_client.close()

    return {
        "video_url": stored_url,
        "script": payload["script"],
        "heygen_video_id": video["id"],
    }
