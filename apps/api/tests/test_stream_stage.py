"""Session rotation in AvatarStage, with fake LiveAvatar sessions (no
network) and the real broadcaster writing to a file."""

import time
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from stream_agent import stage as stage_module
from stream_agent.liveavatar import LiveAvatarAuthError
from stream_agent.media import AudioRing
from stream_agent.stage import AvatarStage


class FakeSession:
    made: ClassVar[list["FakeSession"]] = []
    fail_next: ClassVar[list[Exception]] = []

    def __init__(self, http) -> None:
        self.number = len(FakeSession.made) + 1
        self.latest_frame = np.full((90, 160, 4), (self.number * 40, 0, 0, 255), np.uint8)
        self.audio = AudioRing()
        self.max_duration = 30
        self.opened = time.monotonic()
        self.alive = True
        self.ready = False
        self.speaking = False
        self.closed = False
        self.spoken: list[bytes] = []
        self.session_id = f"s{self.number}"
        FakeSession.made.append(self)

    async def open(self) -> None:
        if FakeSession.fail_next:
            raise FakeSession.fail_next.pop(0)
        self.ready = True

    async def close(self, reason: str = "USER_CLOSED") -> None:
        self.closed, self.alive = True, False

    async def speak(self, pcm: bytes) -> None:
        self.spoken.append(pcm)

    def age(self) -> float:
        return time.monotonic() - self.opened


@pytest.fixture
def make_stage(tmp_path: Path):
    FakeSession.made, FakeSession.fail_next = [], []
    stages: list[AvatarStage] = []

    def make(**kwargs) -> AvatarStage:
        stage = AvatarStage(
            {"avatar_api_key": "k", "avatar": {"avatar_id": "a"}},
            rtmp_url=str(tmp_path / f"out{len(stages)}.flv"),
            vendor_urls={},
            width=160, height=90, fps=10,
            make_session=FakeSession, **kwargs,
        )
        stages.append(stage)
        return stage

    yield make
    for stage in stages:
        stage.stop()


def _wait(condition, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while not condition():
        assert time.time() < deadline, "timed out"
        time.sleep(0.05)


def test_next_session_takes_over_in_a_pause(make_stage, monkeypatch) -> None:
    monkeypatch.setattr(stage_module, "_FORCE_HANDOVER_AT", 0.5)
    stage = make_stage(handover_lead=90)
    stage.start()
    first = stage.current
    first.speaking = True
    first.max_duration = 6  # lead = max/3 = 2 s -> next starts at ~4 s
    _wait(lambda: len(FakeSession.made) == 2 and FakeSession.made[1].ready, timeout=10)
    time.sleep(1.5)
    assert stage.current is first  # still talking: no cut mid-sentence
    first.speaking = False
    _wait(lambda: stage.current is FakeSession.made[1])
    assert first.closed and stage.handovers == 1
    assert stage.broadcaster.frames > 0 and stage.healthy()


def test_handover_is_forced_right_before_the_cap(make_stage, monkeypatch) -> None:
    monkeypatch.setattr(stage_module, "_FORCE_HANDOVER_AT", 4.0)
    stage = make_stage()
    stage.start()
    first = stage.current
    first.speaking = True  # never goes quiet
    first.max_duration = 6
    _wait(lambda: stage.current is not first, timeout=10)
    assert first.closed


def test_a_dead_session_is_replaced_and_the_picture_holds(make_stage) -> None:
    stage = make_stage()
    stage.start()
    first = stage.current
    first.alive = False  # vendor dropped it
    _wait(lambda: stage.current is not first)
    assert stage.healthy() and stage.handovers == 1


def test_a_rejected_key_breaks_the_stage_instead_of_retrying_forever(make_stage) -> None:
    stage = make_stage()
    stage.start()
    FakeSession.fail_next = [LiveAvatarAuthError("401")]
    stage.current.alive = False
    _wait(lambda: not stage.healthy())
    assert "ключ" in stage.problem


def test_speech_goes_to_the_current_session_and_extends_the_caption(make_stage) -> None:
    stage = make_stage()
    stage.start()
    stage.show(author="ann", message="Привет!", reaction="🔥")
    pcm = b"\x00\x00" * 24000  # 1 s
    stage.speak("Привет, ann!", "happy", pcm)
    _wait(lambda: stage.current.spoken == [pcm])
    assert {"caption", "reaction"} <= set(stage._layers)
    caption_left = stage._layers["caption"][1] - time.monotonic()
    assert 2.0 < caption_left <= 3.1  # speech (1 s) + tail (2 s)
