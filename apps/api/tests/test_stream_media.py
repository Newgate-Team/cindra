import time
from pathlib import Path

import av
import numpy as np
import pytest

from stream_agent.broadcaster import Broadcaster, _redact, twitch_ingest
from stream_agent.media import (
    AudioRing,
    Layer,
    blend,
    letterbox,
    render_caption,
    render_reaction,
    resample_24k_to_48k,
)


def test_audio_ring_pads_with_silence_and_drops_backlog() -> None:
    ring = AudioRing(max_seconds=0.01)  # 480 samples
    ring.write(np.arange(100, dtype=np.int16).tobytes())
    out = ring.read(150)
    assert out[:100].tolist() == list(range(100)) and not out[100:].any()
    ring.write(np.ones(1000, dtype=np.int16).tobytes())
    assert len(ring) == 480  # oldest dropped: voice stays in sync with lips


def test_resample_doubles_rate_and_keeps_samples() -> None:
    pcm = np.array([0, 100, 200], dtype=np.int16).tobytes()
    out = np.frombuffer(resample_24k_to_48k(pcm), dtype=np.int16).tolist()
    assert out == [0, 50, 100, 150, 200, 200]


def test_caption_has_cyrillic_glyphs_and_stays_inside_the_frame() -> None:
    layer = render_caption("Ронни", "очень длинное сообщение " * 30, 1280, 720)
    h, w = layer.pixels.shape[:2]
    assert 0 <= layer.x and layer.x + w <= 1280 and 0 <= layer.y and layer.y + h <= 720
    assert h < 720 // 3  # three lines at most, the avatar stays visible
    # Text pixels exist (white) -- a missing Cyrillic font would draw boxes
    # or nothing; DejaVu (CI) / Arial Unicode (macOS) both have Cyrillic.
    assert (layer.pixels[..., :3] > 240).all(axis=2).sum() > 200


def test_reactions_render_only_for_known_emoji() -> None:
    assert render_reaction("🔥", 1280, 720).pixels.shape[2] == 4
    assert render_reaction("🦄", 1280, 720) is None


def test_blend_is_alpha_correct_and_clipped() -> None:
    frame = np.zeros((10, 10, 4), dtype=np.uint8)
    pixels = np.zeros((4, 4, 4), dtype=np.uint8)
    pixels[...] = (255, 0, 0, 128)
    blend(frame, Layer(pixels, 8, 8))  # half off-screen
    assert frame[9, 9, 0] == 128 and frame[7, 7, 0] == 0


def test_letterbox_keeps_aspect_and_even_sizes() -> None:
    assert letterbox(np.zeros((360, 640, 4)), 1280, 720) == (0, 0, 1280, 720)
    x, _, w, h = letterbox(np.zeros((1920, 1080, 4)), 1280, 720)  # portrait avatar
    assert (w % 2, h % 2) == (0, 0) and h == 720 and x > 0


def test_stream_keys_never_reach_the_log() -> None:
    assert _redact(twitch_ingest("live_123_SECRET")).endswith("/***")
    assert "SECRET" not in _redact(twitch_ingest("live_123_SECRET"))


def test_broadcast_keeps_a_steady_frame_rate_whatever_the_source_does(tmp_path: Path) -> None:
    out = tmp_path / "out.flv"
    calls = {"n": 0}
    red = np.zeros((360, 640, 4), dtype=np.uint8)
    red[..., 0], red[..., 3] = 220, 255

    def picture():
        calls["n"] += 1
        # The avatar delivers a frame only now and then (and none at first).
        return red if calls["n"] > 5 and calls["n"] % 7 == 0 else None

    marker = np.zeros((40, 40, 4), dtype=np.uint8)
    marker[...] = (0, 255, 0, 255)
    caster = Broadcaster(
        str(out), picture=picture, audio=lambda n: np.zeros(n, dtype=np.int16),
        layers=lambda: [Layer(marker, 10, 10)], width=320, height=180, fps=20,
    )
    caster.start()
    time.sleep(2.0)
    caster.stop()
    assert caster.healthy() and caster.reconnects == 0

    with av.open(str(out)) as container:
        frames = list(container.decode(container.streams.video[0]))
        assert 34 <= len(frames) <= 46  # ~2 s at 20 fps, independent of the source
        last = frames[-1].to_ndarray(format="rgb24")
        assert last[90, 160, 0] > 150  # the held red picture
        assert last[25, 25, 1] > 200 and last[25, 25, 0] < 80  # overlay on top
    with av.open(str(out)) as container:
        audio = container.streams.audio[0]
        assert (audio.sample_rate, audio.channels) == (48000, 2)


def test_an_unreachable_output_turns_unhealthy(tmp_path: Path) -> None:
    caster = Broadcaster(
        str(tmp_path / "missing-dir" / "out.flv"), picture=lambda: None,
        audio=lambda n: np.zeros(n, dtype=np.int16), give_up_after=0.0,
    )
    caster.start()
    deadline = time.time() + 5
    while caster.healthy() and time.time() < deadline:
        time.sleep(0.05)
    caster.stop()
    assert not caster.healthy()


@pytest.mark.parametrize("rate", [24000])
def test_speech_rate_constant_matches_the_api(rate: int) -> None:
    from app.content_pipeline.stream_speech import SAMPLE_RATE
    from stream_agent.liveavatar import SPEECH_RATE

    assert SAMPLE_RATE == SPEECH_RATE == rate
