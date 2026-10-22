"""The encoder: one long-lived RTMP connection fed at a fixed frame rate.

Twitch drops a broadcast whose video stalls, so the output never waits
for the avatar: every tick it takes whatever picture is current (or
repeats the last one), whatever audio has arrived (or silence), blends
the overlays in, and sends a frame. Swapping avatar sessions, a vendor
hiccup, a slow reply -- none of it reaches the viewer as a dropped
stream. If the RTMP connection itself breaks, it reconnects with
backoff; only after `give_up_after` seconds without a working
connection does it report itself unhealthy.
"""

from __future__ import annotations

import fractions
import logging
import threading
import time
from collections.abc import Callable

import av
import numpy as np

from stream_agent.media import OUTPUT_RATE, Layer, blend, letterbox

log = logging.getLogger("stream_agent.broadcast")

PictureSource = Callable[[], np.ndarray | None]
AudioSource = Callable[[int], np.ndarray]
LayerSource = Callable[[], list[Layer]]


def twitch_ingest(stream_key: str) -> str:
    # Twitch's global ingest; it routes to the nearest point of presence.
    return f"rtmp://ingest.global-contribute.live-video.net/app/{stream_key}"


class Broadcaster:
    def __init__(
        self,
        url: str,
        *,
        picture: PictureSource,
        audio: AudioSource,
        layers: LayerSource = list,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        video_kbps: int = 3000,
        audio_kbps: int = 128,
        give_up_after: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self._picture, self._audio, self._layers = picture, audio, layers
        self.width, self.height, self.fps = width, height, fps
        self._video_kbps, self._audio_kbps = video_kbps, audio_kbps
        self._give_up_after = give_up_after
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failed = False
        self.frames = 0
        self.reconnects = 0
        self.late_frames = 0
        self._last_source: np.ndarray | None = None
        self._scaled: np.ndarray = self._blank()

    def _blank(self) -> np.ndarray:
        canvas = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        canvas[..., :3] = (18, 18, 24)
        canvas[..., 3] = 255
        return canvas

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="broadcast", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)

    def healthy(self) -> bool:
        return not self._failed

    # -- encoding ---------------------------------------------------------

    def _open(self):
        fmt = "flv" if self.url.startswith(("rtmp://", "rtmps://")) or self.url.endswith(".flv") else None
        container = av.open(self.url, mode="w", format=fmt)
        video = container.add_stream("libx264", rate=self.fps)
        video.width, video.height, video.pix_fmt = self.width, self.height, "yuv420p"
        video.bit_rate = self._video_kbps * 1000
        # Twitch: CBR, a keyframe every 2 seconds.
        video.options = {
            "preset": "veryfast",
            "tune": "zerolatency",
            "g": str(self.fps * 2),
            "keyint_min": str(self.fps * 2),
            "sc_threshold": "0",
            "maxrate": f"{self._video_kbps}k",
            "bufsize": f"{self._video_kbps * 2}k",
            "nal-hrd": "cbr",
        }
        audio = container.add_stream("aac", rate=OUTPUT_RATE)
        audio.layout = "stereo"
        audio.bit_rate = self._audio_kbps * 1000
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=OUTPUT_RATE)
        return container, video, audio, resampler

    def _compose(self) -> np.ndarray:
        source = self._picture()
        if source is not None and source is not self._last_source:
            self._last_source = source
            x, y, w, h = letterbox(source, self.width, self.height)
            scaled = (
                av.VideoFrame.from_ndarray(np.ascontiguousarray(source), format="rgba")
                .reformat(width=w, height=h, format="rgba")
                .to_ndarray()
            )
            canvas = self._blank()
            canvas[y : y + h, x : x + w] = scaled
            self._scaled = canvas
        frame = self._scaled
        layers = self._layers()
        if layers:
            frame = frame.copy()
            for layer in layers:
                blend(frame, layer)
        return frame

    def _run(self) -> None:
        backoff = 1.0
        last_ok = self._clock()
        samples_per_frame = OUTPUT_RATE // self.fps
        while not self._stop.is_set():
            try:
                container, video, audio, resampler = self._open()
            except (av.FFmpegError, OSError) as exc:
                log.warning("cannot open %s: %s", _redact(self.url), exc)
                if self._clock() - last_ok > self._give_up_after:
                    self._failed = True
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
                continue
            log.info("broadcasting to %s", _redact(self.url))
            backoff = 1.0
            index = 0
            started = self._clock()
            try:
                while not self._stop.is_set():
                    due = started + index / self.fps
                    delay = due - self._clock()
                    if delay > 0:
                        self._stop.wait(delay)
                    elif delay < -1.0:
                        # The machine can't keep up: re-anchor the clock
                        # instead of bursting frames to catch up.
                        self.late_frames += 1
                        started = self._clock() - index / self.fps
                    picture = av.VideoFrame.from_ndarray(self._compose(), format="rgba")
                    picture.pts, picture.time_base = index, fractions.Fraction(1, self.fps)
                    for packet in video.encode(picture):
                        container.mux(packet)
                    mono = self._audio(samples_per_frame).reshape(1, -1)
                    sound = av.AudioFrame.from_ndarray(mono, format="s16", layout="mono")
                    sound.sample_rate = OUTPUT_RATE
                    sound.pts = index * samples_per_frame
                    sound.time_base = fractions.Fraction(1, OUTPUT_RATE)
                    for resampled in resampler.resample(sound):
                        for packet in audio.encode(resampled):
                            container.mux(packet)
                    index += 1
                    self.frames += 1
                    last_ok = self._clock()
                for stream in (video, audio):
                    for packet in stream.encode(None):
                        container.mux(packet)
            except (av.FFmpegError, OSError) as exc:
                log.warning("broadcast connection lost: %s", exc)
                self.reconnects += 1
                if self._clock() - last_ok > self._give_up_after:
                    self._failed = True
            finally:
                try:
                    container.close()
                except (av.FFmpegError, OSError):
                    pass
            if not self._stop.is_set():
                self._stop.wait(backoff)


def _redact(url: str) -> str:
    """Never log a stream key: it lets anyone broadcast as the channel."""
    head, _, _ = url.rpartition("/")
    return f"{head}/***" if url.startswith(("rtmp", "srt")) else url
