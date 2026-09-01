import time
from collections.abc import Callable
from typing import Any

import httpx

from app.content_pipeline.seedance_generator import seedance_video_generator
from app.content_pipeline.video_generator import veo_video_generator


def dispatch_video_generator(
    payload: dict[str, Any],
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Route a video job to its provider (CIN-144).

    The registry maps exactly one generator per content type, but the
    provider is a per-job decision: the video studio prefers Seedance
    (30s, native audio) when fal.ai is configured, while /content video
    jobs and unconfigured installs stay on Veo. The router records the
    choice in input_payload at enqueue time, so the job itself shows
    which provider actually ran it.
    """
    if payload.get("provider") == "seedance":
        return seedance_video_generator(payload, client=client, sleep=sleep)
    return veo_video_generator(payload, client=client, sleep=sleep)
