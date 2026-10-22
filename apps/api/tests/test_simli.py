"""Simli: the key and faces through the API, and the stream agent's
session protocol over real HTTP + WebSocket -- all against
devtools/simli_simulator (REST checked against Simli's own OpenAPI)."""

import asyncio
import socket
import threading
import time
from unittest.mock import patch

import aiohttp
import numpy as np
import pytest
import uvicorn
from fastapi.testclient import TestClient

from devtools import simli_simulator as simulator
from stream_agent.media import resample_pcm
from stream_agent.simli import SIMLI_RATE, SimliAuthError, SimliSession
from stream_agent.stage import vendor_session_factory

_KEY = "simli_sim_owner"
_FACE = "3bad681f-1b4a-4507-bc54-fbc675c191b7"  # preset "Victor"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch) -> None:
    simulator.state.__init__()
    monkeypatch.setenv("SIMLI_SIM_MAX_SESSION", "1200")


@pytest.fixture(scope="module")
def sim_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(simulator.app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _login(client: TestClient) -> dict:
    client.post("/auth/register", json={"email": "o@cindra.dev", "password": "supersecret1",
                                        "role": "agency"})
    token = client.post("/auth/login", json={"email": "o@cindra.dev",
                                             "password": "supersecret1"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_connect_list_faces_and_offer_simli_for_streams(client: TestClient) -> None:
    with TestClient(simulator.app, base_url="https://api.simli.ai") as sim, \
            patch("app.tool_integrations.simli.httpx.get", sim.get):
        headers = _login(client)
        bad = client.put("/integrations/simli", json={"api_key": "sk-not-simli"}, headers=headers)
        assert bad.status_code == 400 and "app.simli.com" in bad.json()["detail"]
        ok = client.put("/integrations/simli", json={"api_key": _KEY}, headers=headers)
        assert ok.status_code == 200 and ok.json()["provider"] == "simli"
        assert client.get("/integrations/simli/account", headers=headers).json() == {
            "active_sessions": 0
        }
        own = client.get("/integrations/simli/avatars", headers=headers).json()["items"]
        assert own[0]["name"].startswith("Моё лицо от 2026-09-30") and own[0]["ready"]
        presets = client.get("/integrations/simli/avatars?ownership=public",
                             headers=headers).json()["items"]
        assert len(presets) == 20 and presets[0]["name"] == "Victor"
        assert all(p["preview_url"].startswith("https://") for p in presets)
        providers = client.get("/streams/providers", headers=headers).json()
        assert {"id": "simli", "label": "Simli", "connected": True} in providers
        assert client.delete("/integrations/simli", headers=headers).status_code == 204


def test_session_protocol_over_real_http_and_websocket(sim_url) -> None:
    async def scenario():
        async with aiohttp.ClientSession() as http:
            session = SimliSession(_KEY, _FACE, http=http, base_url=sim_url)
            info = await session.start_session()
            assert session.max_duration == 1200  # 3600 refused by the plan cap
            assert {"livekit_url", "livekit_token"} <= info.keys()
            avatar = next(iter(simulator.state.avatars.values()))

            sent: list[bytes] = []
            real = session._ws.send_bytes

            async def spy(data: bytes) -> None:
                sent.append(data)
                await real(data)

            session._ws.send_bytes = spy
            speech = (np.sin(np.arange(int(24000 * 1.6)) / 8) * 9000).astype(np.int16).tobytes()
            await session.speak(speech)  # 1.6 s at 24 kHz
            assert session.speaking
            deadline = time.monotonic() + 8
            while session.speaking and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            assert not session.speaking  # SPEAK ... SILENT came back
            # 16 kHz, in half-second chunks.
            assert sum(len(c) for c in sent) == pytest.approx(SIMLI_RATE * 2 * 1.6, abs=4)
            assert max(len(c) for c in sent) <= SIMLI_RATE
            await session.close()
            await asyncio.sleep(0.3)
            assert avatar.ended  # closing the socket ends a Simli session

    asyncio.run(scenario())


def test_a_bad_key_is_reported_as_such(sim_url) -> None:
    async def scenario():
        async with aiohttp.ClientSession() as http:
            with pytest.raises(SimliAuthError):
                await SimliSession("nope", _FACE, http=http, base_url=sim_url).start_session()

    asyncio.run(scenario())


def test_simulator_refuses_what_simli_would(sim_url) -> None:
    with TestClient(simulator.app) as sim:
        typo = sim.post("/compose/token", headers={"x-simli-api-key": _KEY},
                        json={"faceId": _FACE, "maxSessionLenght": 60})
        assert typo.status_code == 422


def test_the_stage_picks_the_streams_vendor() -> None:
    job = {"avatar_provider": "simli", "avatar_api_key": _KEY, "avatar": {"avatar_id": _FACE}}
    factory = vendor_session_factory(job, {"simli": "http://sim"})

    async def build():  # sessions are made inside the stage's event loop
        return factory(object())

    assert isinstance(asyncio.run(build()), SimliSession)
    with pytest.raises(ValueError):
        vendor_session_factory({**job, "avatar_provider": "other"}, {})


def test_resampling_keeps_duration_and_shape() -> None:
    tone = (np.sin(np.arange(24000) * 2 * np.pi * 440 / 24000) * 10000).astype(np.int16)
    out = np.frombuffer(resample_pcm(tone.tobytes(), 24000, 16000), dtype=np.int16)
    assert out.size == 16000
    assert abs(int(np.abs(out).max()) - 10000) < 300
