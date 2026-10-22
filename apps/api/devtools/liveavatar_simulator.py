"""A local stand-in for HeyGen LiveAvatar (api.liveavatar.com), for
running Cindra's live-stream setup without a LiveAvatar key.

Every request is validated against LiveAvatar's own OpenAPI spec
(liveavatar_subset.json via OpenApiContract) and every response is
built to pass it. Mimics what Cindra relies on: X-API-KEY auth, credits,
own avatars (one ready, one still training) and public ones, and the
session lifecycle token -> start -> keep-alive -> stop with the plan's
session-length cap.

Run locally:
    uvicorn devtools.liveavatar_simulator:app --port 8767
and point the API/agent at it with LIVEAVATAR_API_BASE_URL=http://localhost:8767.

Keys: anything starting with "la_sim_" is valid; "la_sim_broke" has no
credits left. LIVEAVATAR_SIM_MAX_SESSION (seconds, default 1200 -- the
Essential plan's 20 minutes) caps max_session_duration.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from devtools import liveavatar_sim_media as media
from devtools.openapi_contract import OpenApiContract

contract = OpenApiContract(
    Path(__file__).with_name("liveavatar_subset.json"), "urn:liveavatar-v1"
)

_OWN = [
    ("9f3c2b1a-0000-4000-8000-000000000001", "Мой цифровой двойник", "VIDEO", "ACTIVE", "Мой голос"),
    ("9f3c2b1a-0000-4000-8000-000000000002", "Новый образ", "VIDEO", "INIT", None),
]
_PUBLIC = [
    ("5d1e7a40-0000-4000-8000-00000000000a", "Wayne", "VIDEO", "ACTIVE", "Wayne"),
    ("5d1e7a40-0000-4000-8000-00000000000b", "Анна — студия", "VIDEO", "ACTIVE", "Anna"),
    ("5d1e7a40-0000-4000-8000-00000000000c", "Kate — casual", "IMAGE", "ACTIVE", "Kate"),
]


class _State:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, str] = {}  # session_token -> session_id
        self.avatars: dict[str, media.SimAvatar] = {}
        self.started = 0


state = _State()
app = FastAPI(title="LiveAvatar simulator")


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _envelope(data: Any, status: int = 200, message: str = "success") -> dict[str, Any]:
    return {"code": 100 if status < 400 else 400, "data": data, "message": message}


def _reply(path: str, method: str, status: int, data: Any) -> JSONResponse:
    body = _envelope(data, status)
    problems = contract.response_errors(path, method, status, body)
    assert not problems, f"simulator built an off-spec response for {path}: {problems}"
    return JSONResponse(body, status_code=status)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"code": status * 100, "data": None, "message": message},
                        status_code=status)


def _key(request: Request) -> str | JSONResponse:
    key = request.headers.get("x-api-key", "")
    if not key.startswith("la_sim_"):
        return _error(401, "Invalid API key")
    return key


def _query_errors(request: Request, path: str, method: str) -> JSONResponse | None:
    errors = contract.parameter_errors(path, method, dict(request.query_params), {})
    if errors:
        return JSONResponse({"detail": [{"msg": e} for e in errors]}, status_code=422)
    return None


def _avatar(entry: tuple, base: str) -> dict[str, Any]:
    avatar_id, name, kind, status, voice = entry
    now = "2026-10-01T10:00:00Z"
    return {
        "id": avatar_id,
        "type": kind,
        "status": status,
        "name": name,
        "preview_url": f"{base}/files/avatar/{avatar_id}.svg",
        "default_voice": {"id": f"voice-{avatar_id[-4:]}", "name": voice} if voice else None,
        "is_1080p": False,
        "created_at": now,
        "updated_at": now,
        "is_expired": False,
        "error_message": "",
    }


def _page(request: Request, entries: list[tuple], path: str) -> Response:
    if isinstance(key := _key(request), JSONResponse):
        return key
    if bad := _query_errors(request, path, "get"):
        return bad
    page = int(request.query_params.get("page", 1))
    size = int(request.query_params.get("page_size", 20))
    chunk = entries[(page - 1) * size : page * size]
    more = page * size < len(entries)
    data = {
        "count": len(entries),
        "results": [_avatar(e, _base(request)) for e in chunk],
        "next": f"{_base(request)}{path}?page={page + 1}&page_size={size}" if more else None,
        "previous": None,
    }
    if data["next"] is None:
        del data["next"]  # their schema types it as a string when present
    del data["previous"]
    return _reply(path, "get", 200, data)


@app.get("/v1/avatars")
def own_avatars(request: Request) -> Response:
    return _page(request, _OWN, "/v1/avatars")


@app.get("/v1/avatars/public")
def public_avatars(request: Request) -> Response:
    return _page(request, _PUBLIC, "/v1/avatars/public")


@app.get("/v1/users/credits")
def credits(request: Request) -> Response:
    if isinstance(key := _key(request), JSONResponse):
        return key
    left = "0" if key == "la_sim_broke" else "1100.00"
    return _reply("/v1/users/credits", "get", 200, {"credits_left": left})


@app.post("/v1/sessions/token")
async def session_token(request: Request) -> Response:
    if isinstance(key := _key(request), JSONResponse):
        return key
    body = await request.json()
    errors = contract.request_body_errors("/v1/sessions/token", "post", body)
    known = contract.spec["components"]["schemas"]["LiteSDKSessionTokenConfigDataSchema"][
        "properties"
    ]
    errors += [f"unknown field {k!r}" for k in body if k not in known]
    if errors:
        return JSONResponse({"detail": [{"msg": e} for e in errors]}, status_code=422)
    if key == "la_sim_broke":
        return _error(402, "Insufficient credits")
    cap = int(os.environ.get("LIVEAVATAR_SIM_MAX_SESSION", "1200"))
    requested = body.get("max_session_duration") or cap
    if requested > cap:
        return _error(400, f"max_session_duration must be <= {cap} for your plan")
    if body["avatar_id"] not in {e[0] for e in _OWN + _PUBLIC if e[3] == "ACTIVE"}:
        return _error(404, "Avatar not found or not ready")
    session_id = str(uuid.uuid4())
    token = "sess_" + secrets.token_urlsafe(24)
    state.tokens[token] = session_id
    state.sessions[session_id] = {
        "mode": body.get("mode", "FULL"),
        "avatar_id": body["avatar_id"],
        "max": requested,
        "started_at": None,
        "stopped": None,
        "last_alive": time.time(),
    }
    return _reply("/v1/sessions/token", "post", 200,
                  {"session_id": session_id, "session_token": token})


@app.post("/v1/sessions/start")
async def session_start(request: Request) -> Response:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    session_id = state.tokens.get(token)
    if session_id is None:
        return _error(401, "Invalid session token")
    session = state.sessions[session_id]
    if session["started_at"] is not None:
        return _error(400, "Session already started")
    session["started_at"] = time.time()
    state.started += 1
    base = _base(request).replace("http", "ws", 1)
    room = f"liveavatar-{session_id}"
    config = media.livekit_config()
    data = {
        "session_id": session_id,
        "livekit_url": config[0] if config else "ws://localhost:7880",
        "livekit_client_token": (
            media.mint(f"viewer-{session_id[:8]}", room, publish=False)
            if config else "sim-client-" + session_id
        ),
        "livekit_agent_token": "sim-agent-" + session_id,
        "max_session_duration": session["max"],
        "ws_url": f"{base}/v1/sessions/{session_id}/ws",
    }
    # Media mode: a fake avatar actually joins the room. Without LiveKit,
    # only the control WebSocket and its timing are simulated.
    avatar = media.SimAvatar(session_id, state.started, session["max"], room)
    state.avatars[session_id] = avatar
    loop = asyncio.get_running_loop()
    loop.create_task(avatar.run(config[0]) if config else avatar.run_protocol_only())
    return _reply("/v1/sessions/start", "post", 201, data)


async def _session_from(request: Request, path: str) -> tuple[str, dict] | JSONResponse:
    raw = await request.body()
    body = json.loads(raw) if raw else {}
    errors = contract.request_body_errors(path, "post", body)
    if errors:
        return JSONResponse({"detail": [{"msg": e} for e in errors]}, status_code=422)
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    session_id = body.get("session_id") or state.tokens.get(token)
    if session_id not in state.sessions:
        return _error(404, "Session not found")
    if not (token in state.tokens or request.headers.get("x-api-key", "").startswith("la_sim_")):
        return _error(401, "Unauthorized")
    return session_id, body


@app.post("/v1/sessions/keep-alive")
async def keep_alive(request: Request) -> Response:
    found = await _session_from(request, "/v1/sessions/keep-alive")
    if isinstance(found, JSONResponse):
        return found
    session_id, _ = found
    if state.sessions[session_id]["stopped"]:
        return _error(400, "Session already stopped")
    state.sessions[session_id]["last_alive"] = time.time()
    return _reply("/v1/sessions/keep-alive", "post", 200, None)


@app.post("/v1/sessions/stop")
async def stop(request: Request) -> Response:
    found = await _session_from(request, "/v1/sessions/stop")
    if isinstance(found, JSONResponse):
        return found
    session_id, body = found
    state.sessions[session_id]["stopped"] = body.get("reason", "UNKNOWN")
    if (avatar := state.avatars.get(session_id)) is not None and avatar.ended is None:
        avatar.ended = body.get("reason", "USER_CLOSED")
    return _reply("/v1/sessions/stop", "post", 200, None)


@app.websocket("/v1/sessions/{session_id}/ws")
async def session_ws(websocket: WebSocket, session_id: str) -> None:
    avatar = state.avatars.get(session_id)
    await websocket.accept()
    if avatar is None or avatar.ended:
        await websocket.close(code=4404)
        return
    avatar.ws = websocket  # one socket per session: a new one replaces the old
    await avatar.emit("session.state_updated", state="new")
    await avatar.emit("session.state_updated", state="connected")
    try:
        while avatar.ended is None:
            await avatar.on_command(await websocket.receive_text())
    except WebSocketDisconnect:
        pass


@app.get("/files/avatar/{name}")
def avatar_preview(name: str) -> Response:
    letter = "Ж" if name.endswith("1.svg") else "A"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320">'
        '<rect width="320" height="320" fill="#2b2f3a"/>'
        '<circle cx="160" cy="130" r="70" fill="#e0a37a"/>'
        '<rect x="70" y="210" width="180" height="110" rx="50" fill="#4b6cb7"/>'
        f'<text x="160" y="150" font-size="48" text-anchor="middle" fill="#2b2f3a">{letter}</text>'
        "</svg>"
    )
    return Response(svg, media_type="image/svg+xml")
