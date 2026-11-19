"""A local stand-in for Simli (api.simli.ai), for running a live stream
on the cheaper avatar vendor without a Simli key.

REST is checked against Simli's own OpenAPI (simli_subset.json) both
ways. The session WebSocket follows docs.simli.com's AsyncAPI: first
message {"livekit_url", "livekit_token"}, then binary PCM16 at 16 kHz
in, "SPEAK"/"SILENT" out, "SKIP" interrupts, "DONE" ends after the
audio. With LIVEAVATAR_SIM_LIVEKIT_URL set the same fake avatar as the
LiveAvatar simulator joins a real (local) LiveKit room.

Run: uvicorn devtools.simli_simulator:app --port 8769 and set
SIMLI_API_BASE_URL=http://localhost:8769. Keys starting "simli_sim_"
are valid. SIMLI_SIM_MAX_SESSION caps maxSessionLength (default 3600).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from devtools import liveavatar_sim_media as media
from devtools.openapi_contract import OpenApiContract

contract = OpenApiContract(Path(__file__).with_name("simli_subset.json"), "urn:simli-v1")

_OWN_FACES = [
    ("7a1c2b3d-0000-4000-8000-0000000000f1", 2, "2026-09-30T12:00:00Z"),
]


class _State:
    def __init__(self) -> None:
        self.tokens: dict[str, dict[str, Any]] = {}
        self.avatars: dict[str, media.SimAvatar] = {}
        self.started = 0


state = _State()
app = FastAPI(title="Simli simulator")


def _key_ok(request: Request) -> bool:
    return request.headers.get("x-simli-api-key", "").startswith("simli_sim_")


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "Invalid API key", "code": "INVALID_API_KEY"}, status_code=401)


def _checked(path: str, method: str, status: int, body: Any) -> JSONResponse:
    problems = contract.response_errors(path, method, status, body)
    assert not problems, f"simulator built an off-spec response for {path}: {problems}"
    return JSONResponse(body, status_code=status)


@app.get("/ratelimiter/sessions")
def sessions(request: Request) -> Response:
    if not _key_ok(request):
        return _unauthorized()
    live = sum(1 for a in state.avatars.values() if a.ended is None)
    return _checked("/ratelimiter/sessions", "get", 200, {"currentUsage": live})


@app.get("/faces")
def faces(request: Request) -> Response:
    if not _key_ok(request):
        return _unauthorized()
    body = [
        {"id": fid, "owner_id": "sim-owner", "simli_version": version,
         "created_at": created, "updated_at": created}
        for fid, version, created in _OWN_FACES
    ]
    return _checked("/faces", "get", 200, body)


@app.post("/compose/token")
async def compose_token(request: Request) -> Response:
    if not _key_ok(request):
        return _unauthorized()
    body = await request.json()
    errors = contract.request_body_errors("/compose/token", "post", body)
    known = contract.spec["components"]["schemas"]["StartStreamingSessionRequest"]["properties"]
    errors += [f"unknown field {k!r}" for k in body if k not in known]
    if errors:
        return JSONResponse({"detail": [{"msg": e} for e in errors]}, status_code=422)
    cap = int(os.environ.get("SIMLI_SIM_MAX_SESSION", "3600"))
    if body.get("maxSessionLength", 3600) > cap:
        return _checked("/compose/token", "post", 400,
                        {"session_token": "FAIL TOKEN",
                         "detail": f"maxSessionLength over your plan's {cap}s"})
    token = "simli_sess_" + secrets.token_urlsafe(24)
    state.tokens[token] = {"face": body["faceId"], "max": body.get("maxSessionLength", 3600),
                           "used": False, "issued": time.time()}
    return _checked("/compose/token", "post", 200, {"session_token": token})


@app.websocket("/compose/webrtc/livekit")
async def session_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    token = websocket.query_params.get("session_token", "")
    issued = state.tokens.get(token)
    if issued is None or issued["used"]:
        await websocket.send_text("ERROR: INVALID_SESSION_TOKEN")
        await websocket.close()
        return
    issued["used"] = True
    state.started += 1
    room = f"simli-{token[-10:]}"
    avatar = media.SimAvatar(token, state.started, issued["max"], room, dialect="simli")
    avatar.ws = websocket
    state.avatars[token] = avatar
    config = media.livekit_config()
    if config:
        info = {"livekit_url": config[0],
                "livekit_token": media.mint(f"viewer-{token[-8:]}", room, publish=False)}
        task = asyncio.create_task(avatar.run(config[0]))
    else:
        info = {"livekit_url": "ws://protocol-only.invalid", "livekit_token": "sim"}
        task = asyncio.create_task(avatar.run_protocol_only())
    await websocket.send_text(json.dumps(info))
    try:
        while avatar.ended is None:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            await avatar.on_simli(message.get("bytes") or message.get("text") or "")
    except WebSocketDisconnect:
        pass
    finally:
        # Closing the socket ends a Simli session.
        if avatar.ended is None:
            avatar.ended = "client closed the socket"
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5)
