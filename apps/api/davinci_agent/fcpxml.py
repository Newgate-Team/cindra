"""Builds the montage as an FCPXML timeline that Resolve imports.

FCPXML rather than assembling the timeline call-by-call through the
scripting API: it's one artifact Resolve imports in a single call
(MediaPool.ImportTimelineFromFile), it can be checked on the free
Resolve by importing it by hand (the API itself is Studio-only), and a
human editor can open the very same file to adjust the cut.

Version 1.8 with the media path as the asset's `src` attribute -- the
long-established form Resolve's importer reads (later versions moved
it into <media-rep>).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from davinci_agent.media_probe import MediaInfo


@dataclass(frozen=True)
class Clip:
    path: Path
    info: MediaInfo
    name: str
    # Stills only -- videos run for their own length.
    still_seconds: Fraction | None = None


@dataclass(frozen=True)
class Music:
    path: Path
    info: MediaInfo
    volume_db: float = -18.0


def _time(frames: int, fps: int) -> str:
    return f"{frames}/{fps}s" if frames % fps else f"{frames // fps}s"


def _rational(seconds: Fraction) -> str:
    return f"{seconds.numerator}/{seconds.denominator}s" if seconds.denominator != 1 else (
        f"{seconds.numerator}s"
    )


def clip_frames(clip: Clip, fps: int) -> int:
    if clip.still_seconds is not None:
        return max(1, round(clip.still_seconds * fps))
    if clip.info.duration is None:
        raise ValueError(f"{clip.name}: у видео нет длительности")
    # Floor, never round up: a clip one frame longer than its file
    # would end on an offline frame in Resolve.
    return max(1, math.floor(clip.info.duration * fps))


def build_fcpxml(
    *,
    project_name: str,
    width: int,
    height: int,
    fps: int,
    clips: list[Clip],
    music: Music | None = None,
    media_src: Callable[[Path], str] | None = None,
) -> str:
    """`media_src` turns a file into the asset's src; by default its
    absolute file:// URL (the agent imports right where it downloaded)."""
    src = media_src or (lambda path: path.resolve().as_uri())
    if not clips:
        raise ValueError("Монтаж без клипов")

    root = ET.Element("fcpxml", version="1.8")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        id="fmt_seq",
        name="FFVideoFormatRateUndefined",
        frameDuration=f"1/{fps}s",
        width=str(width),
        height=str(height),
    )

    lengths = [clip_frames(clip, fps) for clip in clips]
    total = sum(lengths)

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="Cindra")
    project = ET.SubElement(event, "project", name=project_name)
    sequence = ET.SubElement(
        project,
        "sequence",
        format="fmt_seq",
        duration=_time(total, fps),
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")

    offset = 0
    first_element = None
    for index, (clip, frames) in enumerate(zip(clips, lengths, strict=True), start=1):
        asset_id = f"asset_{index}"
        # The source's own size and no frameDuration: Resolve reads the
        # real frame rate from the file instead of trusting a guess here.
        fmt_id = f"fmt_{index}"
        ET.SubElement(
            resources,
            "format",
            id=fmt_id,
            name="FFVideoFormatRateUndefined",
            width=str(clip.info.width or width),
            height=str(clip.info.height or height),
        )
        if clip.still_seconds is not None:
            ET.SubElement(
                resources,
                "asset",
                id=asset_id,
                name=clip.name,
                src=src(clip.path),
                start="0s",
                duration="0s",
                hasVideo="1",
                format=fmt_id,
            )
            element = ET.SubElement(
                spine,
                "video",
                ref=asset_id,
                name=clip.name,
                offset=_time(offset, fps),
                start="0s",
                duration=_time(frames, fps),
            )
        else:
            attrs = {
                "id": asset_id,
                "name": clip.name,
                "src": src(clip.path),
                "start": "0s",
                "duration": _rational(clip.info.duration),
                "hasVideo": "1" if clip.info.has_video else "0",
                "format": fmt_id,
            }
            if clip.info.has_audio:
                attrs.update(hasAudio="1", audioSources="1", audioChannels="2", audioRate="48000")
            ET.SubElement(resources, "asset", attrs)
            element = ET.SubElement(
                spine,
                "asset-clip",
                ref=asset_id,
                name=clip.name,
                offset=_time(offset, fps),
                start="0s",
                duration=_time(frames, fps),
                tcFormat="NDF",
            )
        if first_element is None:
            first_element = element
        offset += frames

    if music is not None:
        if music.info.duration is None:
            raise ValueError("У музыки нет длительности")
        music_frames = min(total, math.floor(music.info.duration * fps))
        ET.SubElement(
            resources,
            "asset",
            id="asset_music",
            name="Музыка",
            src=src(music.path),
            start="0s",
            duration=_rational(music.info.duration),
            hasAudio="1",
            audioSources="1",
            audioChannels="2",
            audioRate="48000",
        )
        # A connected clip: child of the first spine element, below the
        # story (lane -1), timed from that element's own start.
        connected = ET.SubElement(
            first_element,
            "asset-clip",
            ref="asset_music",
            name="Музыка",
            lane="-1",
            offset="0s",
            start="0s",
            duration=_time(music_frames, fps),
        )
        ET.SubElement(connected, "adjust-volume", amount=f"{music.volume_db:g}dB")

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"
