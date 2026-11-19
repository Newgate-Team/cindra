"""A local stand-in for Google OAuth + Drive v3, for running Cindra's
whole Google Drive flow without a Google Cloud OAuth client.

Requests are checked against Google's own Drive v3 discovery document
(google_drive_contract.py): an unknown query parameter, a field
selection File doesn't have, or a body key Drive doesn't know gets a
Google-shaped 400, and every response it sends is checked against the
same schemas. It mimics what Cindra depends on: the consent screen
(with a choice of two Google accounts), the code -> token exchange and
its error codes, refresh tokens issued only for offline access, revoke,
drive.file visibility (an app sees only its own files), folders, the
resumable upload protocol including 308 Resume Incomplete, and the
files' webViewLink pages.

Run locally:
    uvicorn devtools.google_drive_simulator:app --port 8766
and point the API/worker at it with
    GOOGLE_API_BASE_URL=http://localhost:8766
    GOOGLE_DRIVE_CLIENT_ID=sim-client.apps.googleusercontent.com
    GOOGLE_DRIVE_CLIENT_SECRET=sim-secret
    GOOGLE_DRIVE_REDIRECT_URI=http://localhost:3000/oauth/google-drive/callback

Fault injection by file name: "[flaky]" -- the first upload request
stores only half the bytes and fails with 503 (the client must resume
from Google's 308 Range); "[full]" -- 403 storageQuotaExceeded.
Open http://localhost:8766/ to see what landed in each simulated Drive.
"""

from __future__ import annotations

import hashlib
import html
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from devtools import google_drive_contract as contract

SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
CLIENT_ID = os.environ.get("GDRIVE_SIM_CLIENT_ID", "sim-client.apps.googleusercontent.com")
CLIENT_SECRET = os.environ.get("GDRIVE_SIM_CLIENT_SECRET", "sim-secret")
REDIRECT_URIS = set(
    os.environ.get(
        "GDRIVE_SIM_REDIRECT_URIS",
        "http://localhost:3000/oauth/google-drive/callback,"
        "https://cindra-chi.vercel.app/oauth/google-drive/callback",
    ).split(",")
)
ACCOUNTS = {
    "simulator@gmail.com": "Симулятор Google",
    "second.account@gmail.com": "Второй аккаунт",
}
_DEFAULT_FILE_FIELDS = ("kind", "id", "name", "mimeType")


@dataclass
class _Grant:
    account: str
    scope: str
    revoked: bool = False


@dataclass
class _Upload:
    account: str
    metadata: dict[str, Any]
    size: int | None
    fields: str | None
    received: bytearray = field(default_factory=bytearray)
    file_id: str | None = None
    failed_once: bool = False


class _State:
    def __init__(self) -> None:
        self.codes: dict[str, dict[str, Any]] = {}
        self.refresh_tokens: dict[str, _Grant] = {}
        self.access_tokens: dict[str, tuple[_Grant, float]] = {}
        self.consented: set[str] = set()
        self.files: dict[str, dict[str, Any]] = {}
        self.contents: dict[str, bytes] = {}
        self.uploads: dict[str, _Upload] = {}


state = _State()
app = FastAPI(title="Google Drive simulator")


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _drive_error(
    status: int, message: str, reason: str, location: str | None = None
) -> JSONResponse:
    error: dict[str, Any] = {"domain": "global", "reason": reason, "message": message}
    if location:
        error.update(location=location, locationType="parameter")
    return JSONResponse(
        {"error": {"code": status, "message": message, "errors": [error]}}, status_code=status
    )


def _oauth_error(status: int, code: str, description: str) -> JSONResponse:
    return JSONResponse({"error": code, "error_description": description}, status_code=status)


# ---- OAuth -------------------------------------------------------------


def _error_page(message: str) -> HTMLResponse:
    # Google shows these on its own page -- it never redirects an
    # unregistered redirect_uri back to the caller.
    return HTMLResponse(
        f"<h1>400. Ошибка</h1><p>{html.escape(message)}</p>", status_code=400
    )


@app.get("/o/oauth2/v2/auth", response_class=HTMLResponse)
def consent(request: Request) -> HTMLResponse:
    q = request.query_params
    if q.get("client_id") != CLIENT_ID:
        return _error_page("invalid_client: The OAuth client was not found.")
    if q.get("redirect_uri") not in REDIRECT_URIS:
        return _error_page(f"redirect_uri_mismatch: {q.get('redirect_uri')}")
    if q.get("response_type") != "code":
        return _error_page("unsupported_response_type")
    scopes = (q.get("scope") or "").split()
    if not scopes or any(s != SCOPE for s in scopes):
        return _error_page(f"invalid_scope: {q.get('scope')}")
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in q.items()
    )
    accounts = "".join(
        f'<label style="display:block;margin:6px 0"><input type="radio" name="account" '
        f'value="{email}"{" checked" if i == 0 else ""}> {html.escape(name)} &lt;{email}&gt;</label>'
        for i, (email, name) in enumerate(ACCOUNTS.items())
    )
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><title>Вход – Google Аккаунты (симулятор)</title>
<body style="font-family:system-ui;max-width:520px;margin:40px auto">
<p style="color:#888">Симулятор Google — не настоящий Google</p>
<h1>Приложение Cindra запрашивает доступ к вашему аккаунту Google</h1>
<form method="post" action="/o/oauth2/v2/approve">{hidden}
<p>Выберите аккаунт:</p>{accounts}
<p>Cindra сможет: <b>просматривать, создавать и удалять только те файлы Google Диска,
которые вы используете в этом приложении</b>.</p>
<button name="decision" value="allow" type="submit">Разрешить</button>
<button name="decision" value="deny" type="submit">Отмена</button>
</form></body>"""
    )


@app.post("/o/oauth2/v2/approve")
async def approve(request: Request) -> Response:
    form = dict(await request.form())
    redirect_uri = form.get("redirect_uri", "")
    if redirect_uri not in REDIRECT_URIS:
        return _error_page("redirect_uri_mismatch")
    params: dict[str, str] = {}
    if form.get("state"):
        params["state"] = form["state"]
    if form.get("decision") != "allow":
        params["error"] = "access_denied"
    else:
        code = "4/sim-" + secrets.token_urlsafe(24)
        state.codes[code] = {
            "account": form.get("account") or next(iter(ACCOUNTS)),
            "redirect_uri": redirect_uri,
            "scope": form.get("scope", ""),
            "offline": form.get("access_type") == "offline",
            "prompt_consent": form.get("prompt") == "consent",
            "expires": time.time() + 600,
        }
        params.update(code=code, scope=form.get("scope", ""))
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}", status_code=302)


def _issue_access(grant: _Grant) -> str:
    token = "ya29.sim-" + secrets.token_urlsafe(24)
    state.access_tokens[token] = (grant, time.time() + 3599)
    return token


@app.post("/token")
async def token_endpoint(request: Request) -> JSONResponse:
    form = dict(await request.form())
    if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
        return _oauth_error(401, "invalid_client", "Unauthorized")
    grant_type = form.get("grant_type")
    if grant_type == "authorization_code":
        code = state.codes.pop(form.get("code", ""), None)  # single use
        if code is None or code["expires"] < time.time():
            return _oauth_error(400, "invalid_grant", "Bad Request")
        if form.get("redirect_uri") != code["redirect_uri"]:
            return _oauth_error(400, "redirect_uri_mismatch", "Bad Request")
        grant = _Grant(code["account"], code["scope"])
        body: dict[str, Any] = {
            "access_token": _issue_access(grant),
            "expires_in": 3599,
            "scope": code["scope"],
            "token_type": "Bearer",
        }
        # Google only hands out a refresh token for offline access, and
        # after the first grant only when consent is shown again.
        first_time = code["account"] not in state.consented
        if code["offline"] and (first_time or code["prompt_consent"]):
            refresh = "1//sim-" + secrets.token_urlsafe(32)
            state.refresh_tokens[refresh] = grant
            body["refresh_token"] = refresh
        state.consented.add(code["account"])
        return JSONResponse(body)
    if grant_type == "refresh_token":
        grant = state.refresh_tokens.get(form.get("refresh_token", ""))
        if grant is None or grant.revoked:
            return _oauth_error(400, "invalid_grant", "Token has been expired or revoked.")
        return JSONResponse(
            {
                "access_token": _issue_access(grant),
                "expires_in": 3599,
                "scope": grant.scope,
                "token_type": "Bearer",
            }
        )
    return _oauth_error(400, "unsupported_grant_type", "Invalid grant_type")


@app.post("/revoke")
async def revoke(request: Request) -> JSONResponse:
    form = dict(await request.form())
    token = form.get("token") or request.query_params.get("token", "")
    grant = state.refresh_tokens.get(token)
    if grant is None and token in state.access_tokens:
        grant = state.access_tokens[token][0]
    if grant is None or grant.revoked:
        return _oauth_error(400, "invalid_token", "Token expired or revoked")
    grant.revoked = True
    return JSONResponse({})


# ---- Drive -------------------------------------------------------------


def _auth(request: Request) -> _Grant | JSONResponse:
    header = request.headers.get("authorization", "")
    entry = state.access_tokens.get(header.removeprefix("Bearer ").strip())
    if not header.startswith("Bearer ") or entry is None:
        return _drive_error(401, "Request is missing required authentication credential.",
                            "required")
    grant, expires = entry
    if grant.revoked or expires < time.time():
        return _drive_error(401, "Invalid Credentials", "authError")
    if SCOPE not in grant.scope.split():
        return _drive_error(403, "Insufficient Permission", "insufficientPermissions")
    return grant


def _params(request: Request, resource: str, method: str, **path: str) -> JSONResponse | None:
    params = {**dict(request.query_params), **path}
    errors = contract.parameter_errors(resource, method, params)
    if errors:
        return _drive_error(400, f"Invalid parameter: {errors[0]}", "invalidParameter")
    return None


def _select(obj: dict[str, Any], tree: dict[str, Any]) -> dict[str, Any]:
    if "*" in tree:
        return obj
    out: dict[str, Any] = {}
    for key, sub in tree.items():
        if key not in obj:
            continue
        value = obj[key]
        if sub and isinstance(value, dict):
            value = _select(value, sub)
        elif sub and isinstance(value, list):
            value = [_select(v, sub) if isinstance(v, dict) else v for v in value]
        out[key] = value
    return out


def _respond(schema_name: str, obj: dict[str, Any], fields: str | None) -> JSONResponse:
    if fields is not None:
        errors = contract.fields_errors(schema_name, fields)
        if errors:
            return _drive_error(400, f"Invalid field selection: {errors[0]}", "invalidParameter",
                                "fields")
        body = _select(obj, contract.parse_fields(fields))
    else:
        body = {k: obj[k] for k in _DEFAULT_FILE_FIELDS if k in obj}
    problems = contract.body_errors(schema_name, body)
    assert not problems, f"simulator built an off-schema {schema_name}: {problems}"
    return JSONResponse(body)


def _file_resource(base: str, file: dict[str, Any]) -> dict[str, Any]:
    link = (
        f"{base}/drive/folders/{file['id']}"
        if file["mimeType"] == FOLDER_MIME
        else f"{base}/file/d/{file['id']}/view"
    )
    resource = {**file, "webViewLink": link}
    resource.pop("owner", None)
    return resource


def _new_file(account: str, metadata: dict[str, Any]) -> dict[str, Any]:
    file_id = "1sim" + secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    file = {
        "kind": "drive#file",
        "id": file_id,
        "name": metadata.get("name") or "Untitled",
        "mimeType": metadata.get("mimeType") or "application/octet-stream",
        "parents": metadata.get("parents") or ["root"],
        "trashed": False,
        "createdTime": now,
        "modifiedTime": now,
        "owner": account,
    }
    state.files[file_id] = file
    return file


def _visible(grant: _Grant, file_id: str) -> dict[str, Any] | None:
    # drive.file: an app only sees files it created, in that account.
    file = state.files.get(file_id)
    return file if file is not None and file["owner"] == grant.account else None


def _metadata_errors(grant: _Grant, metadata: Any) -> JSONResponse | None:
    if not isinstance(metadata, dict):
        return _drive_error(400, "Invalid JSON payload", "parseError")
    errors = contract.body_errors("File", metadata)
    if errors:
        return _drive_error(400, f"Invalid JSON payload: {errors[0]}", "invalid")
    for parent in metadata.get("parents") or []:
        folder = _visible(grant, parent)
        if folder is None or folder["mimeType"] != FOLDER_MIME:
            return _drive_error(404, f"File not found: {parent}.", "notFound", "fileId")
    return None


@app.get("/drive/v3/about")
def about(request: Request) -> Response:
    grant = _auth(request)
    if isinstance(grant, JSONResponse):
        return grant
    if bad := _params(request, "about", "get"):
        return bad
    if "fields" not in request.query_params:
        # Real Drive: about.get has no default field set.
        return _drive_error(400, "The 'fields' parameter is required for this method.",
                            "required", "fields")
    user = {
        "kind": "drive#user",
        "displayName": ACCOUNTS[grant.account],
        "emailAddress": grant.account,
        "me": True,
        "permissionId": hashlib.sha1(grant.account.encode()).hexdigest()[:20],
    }
    return _respond("About", {"kind": "drive#about", "user": user}, request.query_params["fields"])


@app.post("/drive/v3/files")
async def create_metadata_only(request: Request) -> Response:
    grant = _auth(request)
    if isinstance(grant, JSONResponse):
        return grant
    if bad := _params(request, "files", "create"):
        return bad
    try:
        metadata = await request.json()
    except ValueError:
        return _drive_error(400, "Invalid JSON payload", "parseError")
    if bad := _metadata_errors(grant, metadata):
        return bad
    file = _new_file(grant.account, metadata)
    return _respond("File", _file_resource(_base(request), file), request.query_params.get("fields"))


@app.get("/drive/v3/files/{file_id}")
def get_file(file_id: str, request: Request) -> Response:
    grant = _auth(request)
    if isinstance(grant, JSONResponse):
        return grant
    if bad := _params(request, "files", "get", fileId=file_id):
        return bad
    file = _visible(grant, file_id)
    if file is None:
        return _drive_error(404, f"File not found: {file_id}.", "notFound", "fileId")
    return _respond("File", _file_resource(_base(request), file), request.query_params.get("fields"))


@app.post("/upload/drive/v3/files")
async def start_resumable(request: Request) -> Response:
    grant = _auth(request)
    if isinstance(grant, JSONResponse):
        return grant
    if bad := _params(request, "files", "create"):
        return bad
    if request.query_params.get("uploadType") != "resumable":
        return _drive_error(400, "Simulator implements uploadType=resumable only", "badRequest")
    fields = request.query_params.get("fields")
    if fields is not None and (errors := contract.fields_errors("File", fields)):
        return _drive_error(400, f"Invalid field selection: {errors[0]}", "invalidParameter",
                            "fields")
    try:
        metadata = await request.json()
    except ValueError:
        return _drive_error(400, "Invalid JSON payload", "parseError")
    if bad := _metadata_errors(grant, metadata):
        return bad
    if "[full]" in metadata.get("name", ""):
        return _drive_error(403, "The user's Drive storage quota has been exceeded.",
                            "storageQuotaExceeded")
    declared = request.headers.get("x-upload-content-length")
    upload_id = "sim-" + secrets.token_urlsafe(24)
    content_type = request.headers.get("x-upload-content-type")
    state.uploads[upload_id] = _Upload(
        account=grant.account,
        metadata={**metadata, "mimeType": metadata.get("mimeType") or content_type},
        size=int(declared) if declared and declared.isdigit() else None,
        fields=fields,
    )
    query = urlencode({"uploadType": "resumable", "upload_id": upload_id})
    location = f"{_base(request)}/upload/drive/v3/files?{query}"
    return Response(status_code=200, headers={"Location": location})


def _incomplete(upload: _Upload) -> Response:
    headers = {"Range": f"bytes=0-{len(upload.received) - 1}"} if upload.received else {}
    return Response(status_code=308, headers=headers)


def _finish(request: Request, upload: _Upload) -> Response:
    if upload.file_id is None:
        file = _new_file(upload.account, upload.metadata)
        state.contents[file["id"]] = bytes(upload.received)
        file["size"] = str(len(upload.received))
        upload.file_id = file["id"]
    file = state.files[upload.file_id]
    return _respond("File", _file_resource(_base(request), file), upload.fields)


@app.put("/upload/drive/v3/files")
async def upload_bytes(request: Request) -> Response:
    upload = state.uploads.get(request.query_params.get("upload_id", ""))
    if upload is None:
        return _drive_error(404, "Upload session not found or expired", "notFound")
    content_range = request.headers.get("content-range", "")
    body = await request.body()
    if content_range.startswith("bytes */"):
        # Status query: how much has Google got?
        total = int(content_range.split("/")[1])
        if upload.file_id is not None or len(upload.received) == total:
            return _finish(request, upload)
        return _incomplete(upload)
    if upload.file_id is not None:
        return _finish(request, upload)
    start, total = 0, len(body)
    if content_range:
        span, total_text = content_range.removeprefix("bytes ").split("/")
        start, end = (int(x) for x in span.split("-"))
        total = int(total_text)
        if end - start + 1 != len(body):
            return _drive_error(400, "Content-Range does not match the body length", "badRequest")
    if upload.size is not None and total != upload.size:
        return _drive_error(400, "Upload size differs from X-Upload-Content-Length", "badRequest")
    if start != len(upload.received):
        return _drive_error(400, f"Expected offset {len(upload.received)}, got {start}",
                            "badRequest")
    if "[flaky]" in upload.metadata.get("name", "") and not upload.failed_once:
        upload.failed_once = True
        upload.received.extend(body[: len(body) // 2])
        return _drive_error(503, "Backend Error", "backendError")
    upload.received.extend(body)
    if len(upload.received) < total:
        return _incomplete(upload)
    return _finish(request, upload)


# ---- Looking at what landed -------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    rows = []
    for file in state.files.values():
        if file["mimeType"] == FOLDER_MIME:
            continue
        rows.append(
            f"<tr><td>{html.escape(file['owner'])}</td><td><a href='/file/d/{file['id']}/view'>"
            f"{html.escape(file['name'])}</a></td><td>{file.get('size', '?')}</td>"
            f"<td>{file['mimeType']}</td><td>{'в корзине' if file['trashed'] else ''}</td></tr>"
        )
    folders = "".join(
        f"<li><a href='/drive/folders/{f['id']}'>{html.escape(f['name'])}</a> — {f['owner']}"
        f"{' (в корзине)' if f['trashed'] else ''}</li>"
        for f in state.files.values()
        if f["mimeType"] == FOLDER_MIME
    )
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Симулятор Google Drive</title>"
        "<body style='font-family:system-ui;margin:24px'><h1>Симулятор Google Drive</h1>"
        f"<h2>Папки</h2><ul>{folders or '<li>нет</li>'}</ul><h2>Файлы</h2>"
        "<table border=1 cellpadding=6><tr><th>Аккаунт</th><th>Имя</th><th>Байт</th>"
        f"<th>Тип</th><th></th></tr>{''.join(rows)}</table></body>"
    )


@app.get("/drive/folders/{folder_id}", response_class=HTMLResponse)
def folder_page(folder_id: str) -> HTMLResponse:
    folder = state.files.get(folder_id)
    if folder is None:
        return HTMLResponse("<h1>Папка не найдена</h1>", status_code=404)
    items = "".join(
        f"<li><a href='/file/d/{f['id']}/view'>{html.escape(f['name'])}</a></li>"
        for f in state.files.values()
        if folder_id in f["parents"]
    )
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>{html.escape(folder['name'])}</title>"
        f"<body style='font-family:system-ui;margin:24px'><h1>📁 {html.escape(folder['name'])}"
        f"</h1><p>{folder['owner']}</p><ul>{items or '<li>пусто</li>'}</ul></body>"
    )


@app.get("/file/d/{file_id}/view", response_class=HTMLResponse)
def file_page(file_id: str) -> HTMLResponse:
    file = state.files.get(file_id)
    if file is None:
        return HTMLResponse("<h1>Файл не найден</h1>", status_code=404)
    src = f"/sim/files/{file_id}/content"
    preview = (
        f"<img src='{src}' style='max-width:480px'>"
        if file["mimeType"].startswith("image/")
        else f"<video src='{src}' controls style='max-width:480px'></video>"
        if file["mimeType"].startswith("video/")
        else ""
    )
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>{html.escape(file['name'])}</title>"
        f"<body style='font-family:system-ui;margin:24px'><h1>{html.escape(file['name'])}</h1>"
        f"<p>{file['owner']} · {file.get('size', '?')} байт</p>{preview}</body>"
    )


@app.get("/sim/files")
def list_files() -> list[dict[str, Any]]:
    return list(state.files.values())


@app.get("/sim/files/{file_id}/content")
def file_content(file_id: str) -> Response:
    file = state.files.get(file_id)
    if file is None or file_id not in state.contents:
        return Response(status_code=404)
    return Response(state.contents[file_id], media_type=file["mimeType"])


@app.post("/sim/files/{file_id}/trash")
def trash(file_id: str) -> Response:
    state.files[file_id]["trashed"] = True
    return Response(status_code=204)


@app.post("/sim/reset")
def reset() -> Response:
    state.__init__()
    return Response(status_code=204)


@app.post("/sim/expire-uploads")
def expire_uploads() -> Response:
    state.uploads.clear()
    return Response(status_code=204)
