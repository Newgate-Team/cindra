"""Small media building blocks for the stream: an audio buffer that
never makes the encoder wait, and the on-screen overlays (the chat
message being answered, the avatar's reaction).

Overlays are rendered once per change with Pillow and alpha-blended
into each frame with numpy -- re-drawing text 30 times a second would
cost more CPU than the encoder.
"""

from __future__ import annotations

import os
import textwrap
import threading
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUTPUT_RATE = 48000
_ASSETS = Path(__file__).with_name("assets")
REACTION_FILES = {
    "👋": "1f44b", "😂": "1f602", "🔥": "1f525", "❤️": "2764", "🤔": "1f914",
    "😮": "1f62e", "👍": "1f44d", "🎉": "1f389", "🙏": "1f64f",
}
_FONT_CANDIDATES = (
    os.environ.get("STREAM_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Debian/Ubuntu: fonts-dejavu-core
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS
    "/Library/Fonts/Arial Unicode.ttf",
)


class AudioRing:
    """Mono s16 PCM at 48 kHz. The encoder pulls a fixed amount every
    frame and gets silence for whatever isn't there -- the broadcast
    clock never waits for the avatar. If audio piles up (the encoder
    stalled), the oldest is dropped to keep lips and voice in sync."""

    def __init__(self, max_seconds: float = 1.0) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._max = int(OUTPUT_RATE * max_seconds) * 2

    def write(self, pcm: bytes) -> None:
        with self._lock:
            self._buf += pcm
            if len(self._buf) > self._max:
                del self._buf[: len(self._buf) - self._max]

    def read(self, samples: int) -> np.ndarray:
        wanted = samples * 2
        with self._lock:
            chunk = bytes(self._buf[:wanted])
            del self._buf[:wanted]
        out = np.zeros(samples, dtype=np.int16)
        if chunk:
            data = np.frombuffer(chunk, dtype=np.int16)
            out[: len(data)] = data
        return out

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf) // 2


def resample_24k_to_48k(pcm: bytes) -> bytes:
    """2x upsampling with linear interpolation -- plenty for speech."""
    data = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    if data.size == 0:
        return b""
    nxt = np.append(data[1:], data[-1])
    out = np.empty(data.size * 2, dtype=np.float32)
    out[0::2] = data
    out[1::2] = (data + nxt) / 2
    return out.astype(np.int16).tobytes()


@cache
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size)
    # Pillow's built-in bitmap font has no Cyrillic -- better a broken
    # caption than a crashed broadcast, but install fonts-dejavu-core.
    return ImageFont.load_default(size)


@dataclass(frozen=True)
class Layer:
    pixels: np.ndarray  # H x W x 4, uint8, straight alpha
    x: int
    y: int


def render_caption(author: str, message: str, width: int, height: int) -> Layer:
    """The chat message being answered, as a translucent card at the
    bottom of the screen: "author: message", at most three lines."""
    size = max(18, height // 30)
    font, bold = _font(size), _font(int(size * 1.1))
    chars_per_line = max(20, int(width * 0.8 / (size * 0.55)))
    lines = textwrap.wrap(message, chars_per_line)[:3]
    if len(textwrap.wrap(message, chars_per_line)) > 3:
        lines[-1] = lines[-1][: chars_per_line - 1] + "…"
    pad = size // 2
    card_h = pad * 2 + int(size * 1.3) * (len(lines) + 1)
    card_w = int(width * 0.86)
    image = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, card_w - 1, card_h - 1), radius=pad, fill=(15, 15, 20, 190))
    draw.text((pad, pad), author[:40], font=bold, fill=(255, 196, 120, 255))
    for i, line in enumerate(lines, start=1):
        draw.text((pad, pad + int(size * 1.3) * i), line, font=font, fill=(255, 255, 255, 255))
    return Layer(np.asarray(image), (width - card_w) // 2, height - card_h - height // 20)


def render_reaction(reaction: str, width: int, height: int) -> Layer | None:
    code = REACTION_FILES.get(reaction)
    if code is None:
        return None
    side = max(64, height // 6)
    image = Image.open(_ASSETS / "reactions" / f"{code}.png").convert("RGBA")
    image = image.resize((side, side), Image.Resampling.LANCZOS)
    return Layer(np.asarray(image), width - side - width // 30, height // 20)


def blend(frame: np.ndarray, layer: Layer) -> None:
    """Alpha-blends `layer` into an RGBA frame in place (clipped)."""
    h, w = frame.shape[:2]
    lh, lw = layer.pixels.shape[:2]
    x0, y0 = max(layer.x, 0), max(layer.y, 0)
    x1, y1 = min(layer.x + lw, w), min(layer.y + lh, h)
    if x0 >= x1 or y0 >= y1:
        return
    src = layer.pixels[y0 - layer.y : y1 - layer.y, x0 - layer.x : x1 - layer.x].astype(np.uint16)
    dst = frame[y0:y1, x0:x1, :3].astype(np.uint16)
    alpha = src[..., 3:4]
    frame[y0:y1, x0:x1, :3] = ((src[..., :3] * alpha + dst * (255 - alpha)) // 255).astype(np.uint8)


def letterbox(source: np.ndarray, width: int, height: int) -> tuple[int, int, int, int]:
    """Where a source frame goes inside width x height keeping its
    aspect ratio: (x, y, w, h), even sizes for the encoder."""
    sh, sw = source.shape[:2]
    scale = min(width / sw, height / sh)
    w, h = int(sw * scale) & ~1, int(sh * scale) & ~1
    return (width - w) // 2, (height - h) // 2, w, h
