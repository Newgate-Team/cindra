"""Durations and dimensions read straight from file headers.

The timeline file has to state every clip's length before Resolve sees
the media, and the render machine may have no ffprobe -- so this parses
the containers Cindra actually produces/accepts: MP4/MOV/M4A (ISO base
media), WAV, MP3, and PNG/JPEG/WebP stills.
"""

from __future__ import annotations

import mmap
import os
import struct
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class UnsupportedMediaError(Exception):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration: Fraction | None  # seconds; None for stills
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None


def probe(path: Path) -> MediaInfo:
    # Mapped, not read whole: the API's timeline package probes clips of
    # up to hundreds of MB too, and only headers (and MP3 frame headers)
    # are ever touched.
    with path.open("rb") as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            raise UnsupportedMediaError(f"{path.name}: неподдерживаемый формат файла")
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return _probe(path, data)


def _probe(path: Path, data) -> MediaInfo:
    head = data[:16]
    if head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip"):
        return _probe_isobmff(data)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return _probe_wav(data)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return _probe_webp(data)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return MediaInfo(None, True, False, width, height)
    if head[:3] == b"\xff\xd8\xff":
        return _probe_jpeg(data)
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return _probe_mp3(data)
    raise UnsupportedMediaError(f"{path.name}: неподдерживаемый формат файла")


def _boxes(data: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            raise UnsupportedMediaError("повреждённый MP4: неверный размер блока")
        yield kind, pos + header, min(pos + size, end)
        pos += size


def _probe_isobmff(data: bytes) -> MediaInfo:
    moov = next(((s, e) for kind, s, e in _boxes(data, 0, len(data)) if kind == b"moov"), None)
    if moov is None:
        raise UnsupportedMediaError("MP4 без блока moov")
    duration = None
    has_video = has_audio = False
    width = height = None
    for kind, start, end in _boxes(data, *moov):
        if kind == b"mvhd":
            version = data[start]
            if version == 1:
                timescale, length = struct.unpack(">IQ", data[start + 20 : start + 32])
            else:
                timescale, length = struct.unpack(">II", data[start + 12 : start + 20])
            duration = Fraction(length, timescale)
        elif kind == b"trak":
            handler, dims = _track(data, start, end)
            if handler == b"vide":
                has_video = True
                width, height = dims
            elif handler == b"soun":
                has_audio = True
    if duration is None:
        raise UnsupportedMediaError("MP4 без длительности (нет mvhd)")
    return MediaInfo(duration, has_video, has_audio, width, height)


def _track(data: bytes, start: int, end: int) -> tuple[bytes | None, tuple[int | None, int | None]]:
    handler = None
    dims: tuple[int | None, int | None] = (None, None)
    for kind, s, e in _boxes(data, start, end):
        if kind == b"tkhd":
            # Width/height are the last two 16.16 fixed-point fields.
            w, h = struct.unpack(">II", data[e - 8 : e])
            dims = (w >> 16, h >> 16)
        elif kind == b"mdia":
            for sub, ss, _se in _boxes(data, s, e):
                if sub == b"hdlr":
                    handler = data[ss + 8 : ss + 12]
    return handler, dims


def _probe_wav(data: bytes) -> MediaInfo:
    byte_rate = None
    for kind, start, end in _riff_chunks(data):
        if kind == b"fmt ":
            byte_rate = struct.unpack("<I", data[start + 8 : start + 12])[0]
        elif kind == b"data" and byte_rate:
            return MediaInfo(Fraction(end - start, byte_rate), False, True)
    raise UnsupportedMediaError("WAV без блока fmt/data")


def _riff_chunks(data: bytes):
    pos = 12
    while pos + 8 <= len(data):
        kind, size = struct.unpack("<4sI", data[pos : pos + 8])
        yield kind, pos + 8, min(pos + 8 + size, len(data))
        pos += 8 + size + (size & 1)


_MP3_BITRATES = {
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_MP3_RATES = {1: [44100, 48000, 32000], 2: [22050, 24000, 16000], 25: [11025, 12000, 8000]}


def _xing_samples(data: bytes, pos: int, version: int, mono: bool, per_frame: int) -> int | None:
    """Exact sample count from the Xing/Info + LAME header in the first
    frame, when present: frame counting alone overshoots by the
    encoder's delay/padding plus the header frame itself (~0.1s on a
    LAME VBR file) -- enough for Resolve to show the tail as offline
    when the music clip is cut to "the whole file"."""
    side_info = (17 if mono else 32) if version == 1 else (9 if mono else 17)
    tag = pos + 4 + side_info
    if data[tag : tag + 4] not in (b"Xing", b"Info"):
        return None
    flags = struct.unpack(">I", data[tag + 4 : tag + 8])[0]
    if not flags & 0x1:
        return None
    frames = struct.unpack(">I", data[tag + 8 : tag + 12])[0]
    samples = frames * per_frame
    # LAME extension sits 120 bytes after the tag when the TOC (flag
    # 0x4) and quality (0x8) fields are present -- they always are for
    # LAME/libmp3lame; encoder delay and padding are 12 bits each.
    offset = tag + 8 + 4 * bool(flags & 0x1) + 4 * bool(flags & 0x2) + 100 * bool(flags & 0x4)
    offset += 4 * bool(flags & 0x8)
    lame = data[offset : offset + 24]
    if len(lame) == 24 and lame[:4] in (b"LAME", b"Lavf", b"Lavc", b"L3.9"):
        b0, b1, b2 = lame[21], lame[22], lame[23]
        delay, padding = (b0 << 4) | (b1 >> 4), ((b1 & 0x0F) << 8) | b2
        samples -= delay + padding
    return samples if samples > 0 else None


def _probe_mp3(data: bytes) -> MediaInfo:
    pos = 0
    if data[:3] == b"ID3":
        size = data[6:10]
        pos = 10 + ((size[0] << 21) | (size[1] << 14) | (size[2] << 7) | size[3])
    samples = 0
    rate = None
    first_frame = True
    while pos + 4 <= len(data):
        b1, b2 = data[pos + 1], data[pos + 2]
        if data[pos] != 0xFF or b1 & 0xE0 != 0xE0:
            break
        version = {3: 1, 2: 2, 0: 25}.get((b1 >> 3) & 0x03)
        layer = (b1 >> 1) & 0x03
        bitrate_index, rate_index = b2 >> 4, (b2 >> 2) & 0x03
        if version is None or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
            break  # not a Layer III frame -- e.g. a trailing ID3v1 tag
        bitrate = _MP3_BITRATES[(1 if version == 1 else 2, 3)][bitrate_index] * 1000
        rate = _MP3_RATES[version][rate_index]
        padding = (b2 >> 1) & 0x01
        per_frame = 1152 if version == 1 else 576
        if first_frame:
            first_frame = False
            mono = (data[pos + 3] >> 6) == 3
            exact = _xing_samples(data, pos, version, mono, per_frame)
            if exact is not None:
                return MediaInfo(Fraction(exact, rate), False, True)
        length = (per_frame // 8) * bitrate // rate + padding
        samples += per_frame
        pos += length
    if not samples or rate is None:
        raise UnsupportedMediaError("MP3 без аудиокадров")
    return MediaInfo(Fraction(samples, rate), False, True)


def _probe_jpeg(data: bytes) -> MediaInfo:
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            return MediaInfo(None, True, False, width, height)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        pos += 2 + struct.unpack(">H", data[pos + 2 : pos + 4])[0]
    raise UnsupportedMediaError("JPEG без размеров кадра")


def _probe_webp(data: bytes) -> MediaInfo:
    kind = data[12:16]
    if kind == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
    elif kind == b"VP8 ":
        width, height = (v & 0x3FFF for v in struct.unpack("<HH", data[26:30]))
    elif kind == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    else:
        raise UnsupportedMediaError("WebP неизвестного вида")
    return MediaInfo(None, True, False, width, height)
