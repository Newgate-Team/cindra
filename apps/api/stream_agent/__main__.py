"""Run the live-stream agent on a machine that can encode video.

    pip install -e ".[stream-agent]"
    python -m stream_agent --api https://<cindra api> --token <STREAM_AGENT_TOKEN>

One process hosts one stream at a time (a 720p30 encode is a few CPU
cores' worth of work); run more processes, or machines, for more
streams. Options can also come from the environment: CINDRA_API_URL,
CINDRA_STREAM_TOKEN, CINDRA_AGENT_NAME.

For local runs: --rtmp-url overrides Twitch's ingest (a "{key}"
placeholder gets the stream key) and LIVEAVATAR_API_BASE_URL points
the avatar at devtools/liveavatar_simulator.py.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from typing import Any

from stream_agent.api_client import StreamApi
from stream_agent.broadcaster import twitch_ingest
from stream_agent.liveavatar import DEFAULT_BASE_URL
from stream_agent.runner import run
from stream_agent.stage import AvatarStage

VERSION = "1.0"


def stage_factory(rtmp_url: str | None, liveavatar_url: str):
    def make(job: dict[str, Any]) -> AvatarStage:
        if job["avatar_provider"] != "liveavatar":
            raise RuntimeError(f"Сервис аватара {job['avatar_provider']!r} не поддерживается агентом")
        url = rtmp_url.format(key=job["stream_key"]) if rtmp_url else twitch_ingest(job["stream_key"])
        return AvatarStage(job, rtmp_url=url, liveavatar_base_url=liveavatar_url)

    return make


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stream_agent", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=os.environ.get("CINDRA_API_URL"))
    parser.add_argument("--token", default=os.environ.get("CINDRA_STREAM_TOKEN"))
    parser.add_argument("--name", default=os.environ.get("CINDRA_AGENT_NAME", socket.gethostname()))
    parser.add_argument("--rtmp-url", default=os.environ.get("STREAM_RTMP_URL"))
    parser.add_argument(
        "--liveavatar-url", default=os.environ.get("LIVEAVATAR_API_BASE_URL", DEFAULT_BASE_URL)
    )
    parser.add_argument("--once", action="store_true", help="host at most one stream, then exit")
    args = parser.parse_args(argv)
    if not args.api or not args.token:
        parser.error("нужны --api и --token (или CINDRA_API_URL / CINDRA_STREAM_TOKEN)")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    api = StreamApi(args.api, args.token, args.name, VERSION)
    logging.getLogger("stream_agent").info("waiting for streams from %s as %r", args.api, args.name)
    run(api, stage_factory(args.rtmp_url, args.liveavatar_url), once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
