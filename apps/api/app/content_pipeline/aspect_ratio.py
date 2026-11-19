"""Aspect-ratio policy for generated media (CIN-145).

Platforms are strict about geometry: Stories and TikTok are vertical
9:16 surfaces, the Instagram feed crops anything wider than square.
Before this, everything was generated in the model's default landscape
and looked wrong (or got cropped) at publish time.

Field names differ per API and are set by the callers: images go into
the Interactions API's `response_format.aspect_ratio` (supports 1:1 /
9:16 / 16:9 among others), video into Veo's `parameters.aspectRatio`
(16:9 or 9:16 only) -- both shapes verified against the official docs
at ai.google.dev on 2026-09-01.

Returning None means "don't send the field" -- the model's default,
identical to the behavior before CIN-145.
"""

from typing import Any


def image_aspect_ratio(payload: dict[str, Any]) -> str | None:
    # Stories are composited onto a vertical canvas (CIN-123), so a
    # landscape source wastes most of the frame.
    if payload.get("content_kind") == "story":
        return "9:16"
    # The Instagram feed displays at most square-ish crops -- square is
    # the one geometry that survives every placement uncut.
    if payload.get("platform") == "instagram":
        return "1:1"
    return None


def video_aspect_ratio(payload: dict[str, Any]) -> str | None:
    # The video studio pins its own ratio explicitly (veo_auto produces
    # a vertical short) -- that beats any platform-derived guess.
    explicit = payload.get("aspect_ratio")
    if explicit:
        return explicit
    if payload.get("content_kind") == "story" or payload.get("platform") == "tiktok":
        return "9:16"
    return None
