"""Google Drive v3 client: saving finished content into the user's own
Drive (OAuth 2.0, scope drive.file).

drive.file rather than drive: the app only ever sees files it created
itself, which is all "put my content there" needs -- and it's a
non-sensitive scope, so publishing the consent screen doesn't require
Google's restricted-scope security assessment.

Every call takes an optional `client`; without one a fresh client comes
from _new_client(), which tests point at devtools/google_drive_simulator.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, BinaryIO
from urllib.parse import urlencode

import httpx

from app.config import get_settings

SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,webViewLink"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
_CHUNK = 1024 * 1024
_RANGE_RE = re.compile(r"bytes=0-(\d+)")


class DriveError(Exception):
    """Permanent: retrying won't help."""


class DriveAuthError(DriveError):
    """The stored grant is revoked/expired -- the user must reconnect."""


class DriveTransientError(DriveError):
    """Rate limit, Google 5xx or a network failure -- retry later."""


class DriveNotFoundError(DriveError):
    pass


class DriveSessionExpiredError(DriveError):
    """The resumable upload session is gone; start a new one."""


def _hosts() -> tuple[str, str, str]:
    base = get_settings().google_api_base_url.rstrip("/")
    if base:
        return base, base, base
    return (
        "https://accounts.google.com",
        "https://oauth2.googleapis.com",
        "https://www.googleapis.com",
    )


def _new_client() -> httpx.Client:
    # Uploads can be hundreds of MB; per-operation read/write timeouts,
    # not a whole-request deadline.
    return httpx.Client(timeout=httpx.Timeout(30.0, read=300.0, write=300.0))


@contextmanager
def _session(client: httpx.Client | None) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
    else:
        with _new_client() as own:
            yield own


def _send(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        return client.request(method, url, **kwargs)
    except httpx.TransportError as exc:
        raise DriveTransientError(f"Google Drive недоступен: {exc}") from exc


def authorization_url(state: str) -> str:
    settings = get_settings()
    accounts, _, _ = _hosts()
    query = urlencode(
        {
            "client_id": settings.google_drive_client_id,
            "redirect_uri": settings.google_drive_redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            # offline + consent, same reason as YouTube's start: without
            # both a reconnect comes back with no refresh_token at all.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{accounts}/o/oauth2/v2/auth?{query}"


def _oauth_error(response: httpx.Response) -> DriveError:
    if response.status_code in _RETRYABLE_STATUS:
        return DriveTransientError(f"Google OAuth {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = body.get("error") if isinstance(body, dict) else None
    if code == "invalid_grant":
        return DriveAuthError("Google отозвал доступ к Drive — подключите Google Drive заново")
    if code in {"invalid_client", "unauthorized_client"}:
        return DriveError("OAuth-клиент Google Drive на сервере настроен неверно")
    detail = body.get("error_description") or code if isinstance(body, dict) else None
    return DriveError(f"Google OAuth {response.status_code}: {detail or response.text[:200]}")


def _token_request(data: dict[str, str], client: httpx.Client | None) -> dict[str, Any]:
    settings = get_settings()
    _, oauth, _ = _hosts()
    with _session(client) as c:
        response = _send(
            c,
            "POST",
            f"{oauth}/token",
            data={
                **data,
                "client_id": settings.google_drive_client_id,
                "client_secret": settings.google_drive_client_secret,
            },
        )
    if response.status_code != 200:
        raise _oauth_error(response)
    return response.json()


def exchange_code(code: str, client: httpx.Client | None = None) -> dict[str, Any]:
    return _token_request(
        {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": get_settings().google_drive_redirect_uri,
        },
        client,
    )


def refresh_access_token(refresh_token: str, client: httpx.Client | None = None) -> str:
    token = _token_request(
        {"refresh_token": refresh_token, "grant_type": "refresh_token"}, client
    )
    return token["access_token"]


def revoke(token: str, client: httpx.Client | None = None) -> None:
    _, oauth, _ = _hosts()
    with _session(client) as c:
        response = _send(c, "POST", f"{oauth}/revoke", data={"token": token})
    if response.status_code != 200:
        raise _oauth_error(response)


def _api_error(response: httpx.Response) -> DriveError:
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}
    if not isinstance(error, dict):
        error = {}
    message = error.get("message") or response.text[:200]
    reasons = {e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)}
    status = response.status_code
    if status == 401:
        return DriveAuthError("Google Drive отклонил доступ — подключите Google Drive заново")
    if status in _RETRYABLE_STATUS or (status == 403 and reasons & _RATE_LIMIT_REASONS):
        return DriveTransientError(f"Google Drive {status}: {message}")
    if status == 403 and "storageQuotaExceeded" in reasons:
        return DriveError("В Google Drive закончилось место — освободите место или смените аккаунт")
    if status == 404:
        return DriveNotFoundError(message)
    return DriveError(f"Google Drive {status}: {message}")


def _api(
    access_token: str,
    method: str,
    path: str,
    client: httpx.Client | None,
    **kwargs: Any,
) -> dict[str, Any]:
    _, _, api = _hosts()
    with _session(client) as c:
        response = _send(
            c,
            method,
            f"{api}/drive/v3/{path}",
            headers={"Authorization": f"Bearer {access_token}"},
            **kwargs,
        )
    if response.status_code != 200:
        raise _api_error(response)
    return response.json()


def get_account(access_token: str, client: httpx.Client | None = None) -> dict[str, Any]:
    about = _api(
        access_token,
        "GET",
        "about",
        client,
        params={"fields": "user(displayName,emailAddress)"},
    )
    return about.get("user", {})


def account_label(user: dict[str, Any]) -> str | None:
    return user.get("emailAddress") or user.get("displayName")


def create_folder(
    access_token: str, name: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    return _api(
        access_token,
        "POST",
        "files",
        client,
        params={"fields": FILE_FIELDS},
        json={"name": name, "mimeType": FOLDER_MIME},
    )


def folder_usable(
    access_token: str, folder_id: str, client: httpx.Client | None = None
) -> bool:
    """False when the user deleted or trashed the folder -- uploading
    into a trashed folder would succeed but land the file in the bin."""
    try:
        folder = _api(
            access_token,
            "GET",
            f"files/{folder_id}",
            client,
            params={"fields": "id,trashed"},
        )
    except DriveNotFoundError:
        return False
    return not folder.get("trashed", False)


def start_upload(
    access_token: str,
    *,
    name: str,
    mime_type: str,
    size: int,
    parent_id: str,
    client: httpx.Client | None = None,
) -> str:
    """Opens a resumable upload session and returns its URI. Resumable
    even for small files: the URI is what lets a retry continue the
    same upload instead of leaving a duplicate file in the user's Drive."""
    _, _, api = _hosts()
    with _session(client) as c:
        response = _send(
            c,
            "POST",
            f"{api}/upload/drive/v3/files",
            params={"uploadType": "resumable", "fields": FILE_FIELDS},
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(size),
            },
            json={"name": name, "parents": [parent_id], "mimeType": mime_type},
        )
    if response.status_code != 200:
        raise _api_error(response)
    location = response.headers.get("location")
    if not location:
        raise DriveTransientError("Google Drive не вернул адрес сессии загрузки")
    return location


def _upload_response(response: httpx.Response) -> dict[str, Any] | int:
    """A finished upload's file, or -- 308 Resume Incomplete -- the
    offset Google wants the next byte from."""
    if response.status_code in (200, 201):
        return response.json()
    if response.status_code == 308:
        match = _RANGE_RE.fullmatch(response.headers.get("range", ""))
        return int(match.group(1)) + 1 if match else 0
    if response.status_code in (404, 410):
        raise DriveSessionExpiredError("Сессия загрузки Google Drive истекла")
    raise _api_error(response)


def upload_status(
    session_url: str, size: int, client: httpx.Client | None = None
) -> dict[str, Any] | int:
    # No Authorization header on session requests: the session URI is
    # itself the credential, so a resume still works after the hour-long
    # access token has expired.
    with _session(client) as c:
        response = _send(
            c,
            "PUT",
            session_url,
            headers={"Content-Range": f"bytes */{size}", "Content-Length": "0"},
        )
    return _upload_response(response)


def upload_from(
    session_url: str,
    stream: BinaryIO,
    size: int,
    offset: int,
    client: httpx.Client | None = None,
) -> dict[str, Any] | int:
    stream.seek(offset)

    def chunks() -> Iterator[bytes]:
        while chunk := stream.read(_CHUNK):
            yield chunk

    headers = {"Content-Length": str(size - offset)}
    if size:
        headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
    with _session(client) as c:
        response = _send(c, "PUT", session_url, headers=headers, content=chunks())
    return _upload_response(response)
