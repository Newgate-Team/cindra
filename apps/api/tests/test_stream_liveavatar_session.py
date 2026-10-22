"""stream_agent's LiveAvatar session against devtools/liveavatar_simulator
over real HTTP + WebSocket (simulator in protocol-only mode: no LiveKit,
so the room/video part is covered by the local end-to-end run instead)."""

import asyncio
import json
import socket
import threading
import time

import aiohttp
import pytest
import uvicorn

from devtools import liveavatar_simulator as simulator
from stream_agent.liveavatar import LiveAvatarAuthError, LiveAvatarSession

_AVATAR = simulator._PUBLIC[0][0]


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


@pytest.fixture(autouse=True)
def _plan_cap(monkeypatch) -> None:
    monkeypatch.setenv("LIVEAVATAR_SIM_MAX_SESSION", "1200")  # Essential: 20 min


def _run(coro):
    return asyncio.run(coro)


def test_session_lifecycle_and_speech_protocol(sim_url) -> None:
    async def scenario():
        async with aiohttp.ClientSession() as http:
            session = LiveAvatarSession("la_sim_agent", _AVATAR, http=http, base_url=sim_url)
            started = await session.start_session()
            # 3600 refused by the plan cap, 1200 accepted.
            assert session.max_duration == 1200
            await session.connect_control(started["ws_url"])
            sim_avatar = simulator.state.avatars[session.session_id]

            pcm = b"\x10\x00" * int(24000 * 2.5)  # 2.5 s of speech
            await session.speak(pcm)
            assert session.speaking
            await asyncio.sleep(0.3)
            assert sim_avatar.talking or sim_avatar.audio  # the avatar got it
            deadline = time.monotonic() + 8
            while session.speaking and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            assert not session.speaking  # speak_ended for *our* utterance came back
            await session.close()
            return session.session_id

    session_id = _run(scenario())
    assert simulator.state.sessions[session_id]["stopped"] == "USER_CLOSED"


def test_speech_is_sent_in_chunks_of_at_most_a_second(sim_url, monkeypatch) -> None:
    sent: list[dict] = []

    async def scenario():
        async with aiohttp.ClientSession() as http:
            session = LiveAvatarSession("la_sim_agent", _AVATAR, http=http, base_url=sim_url)
            started = await session.start_session()
            await session.connect_control(started["ws_url"])
            real_send = session._ws.send_str

            async def spy(data: str) -> None:
                sent.append(json.loads(data))
                await real_send(data)

            monkeypatch.setattr(session._ws, "send_str", spy)
            await session.speak(b"\x00\x00" * int(24000 * 2.2))
            await session.close()

    _run(scenario())
    speaks = [m for m in sent if m["type"] == "agent.speak"]
    assert len(speaks) == 3 and sent[len(speaks)]["type"] == "agent.speak_end"
    assert "event_id" in speaks[0]  # the first chunk names the utterance
    assert all(len(m["audio"]) * 3 // 4 <= 48000 for m in speaks)


def test_a_bad_key_is_reported_as_such(sim_url) -> None:
    async def scenario():
        async with aiohttp.ClientSession() as http:
            session = LiveAvatarSession("heygen-key", _AVATAR, http=http, base_url=sim_url)
            with pytest.raises(LiveAvatarAuthError):
                await session.start_session()

    _run(scenario())


def test_the_session_ends_at_its_cap(sim_url, monkeypatch) -> None:
    monkeypatch.setenv("LIVEAVATAR_SIM_MAX_SESSION", "120")  # Free plan

    async def scenario():
        async with aiohttp.ClientSession() as http:
            session = LiveAvatarSession("la_sim_agent", _AVATAR, http=http, base_url=sim_url)
            started = await session.start_session()
            assert session.max_duration == 120  # stepped down 3600 -> 1200 -> 120
            await session.connect_control(started["ws_url"])
            simulator.state.avatars[session.session_id].ended = "MAX_DURATION_REACHED"
            deadline = time.monotonic() + 5
            while session.alive and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            assert not session.alive  # the stage would now hand over
            await session.close()

    _run(scenario())
