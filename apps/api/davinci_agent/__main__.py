"""Run the montage agent next to DaVinci Resolve Studio.

    python -m davinci_agent --api https://<cindra api> --token <MONTAGE_AGENT_TOKEN>

Resolve Studio must be running on this machine (headless is fine:
`Resolve -nogui`). Copy this folder to the render machine; Python 3.10+
is the only other requirement. Options can also come from the
environment: CINDRA_API_URL, CINDRA_AGENT_TOKEN, CINDRA_AGENT_NAME,
CINDRA_AGENT_WORKDIR.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from pathlib import Path

from davinci_agent.api_client import CindraApi
from davinci_agent.resolve_driver import ResolveError, ResolveRenderer, connect
from davinci_agent.worker import run

VERSION = "1.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="davinci_agent", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=os.environ.get("CINDRA_API_URL"))
    parser.add_argument("--token", default=os.environ.get("CINDRA_AGENT_TOKEN"))
    parser.add_argument("--name", default=os.environ.get("CINDRA_AGENT_NAME", socket.gethostname()))
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(os.environ.get("CINDRA_AGENT_WORKDIR", Path.home() / "CindraMontage")),
    )
    parser.add_argument("--once", action="store_true", help="take at most one job, then exit")
    args = parser.parse_args(argv)
    if not args.api or not args.token:
        parser.error("нужны --api и --token (или CINDRA_API_URL / CINDRA_AGENT_TOKEN)")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        renderer = ResolveRenderer(connect())
    except ResolveError as exc:
        print(exc, file=sys.stderr)
        return 2
    api = CindraApi(args.api, args.token, args.name, VERSION)
    api.heartbeat()
    logging.getLogger("davinci_agent").info(
        "connected to DaVinci and %s as %r", args.api, args.name
    )
    run(api, renderer, args.work_dir, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
