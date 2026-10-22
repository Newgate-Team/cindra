"""Talks to Cindra's /streams/agent/* endpoints. urllib only; the
transport is a plain function so tests can route it into the real API
in-process (same arrangement as davinci_agent.api_client)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ApiError(Exception):
    pass


class TemporaryApiError(ApiError):
    """5xx / network trouble -- skip this beat and try again."""


class StreamLost(Exception):
    """The API handed the stream to another agent (this one went quiet
    too long) or it's no longer live -- stop broadcasting, don't report."""


@dataclass
class HttpResponse:
    status: int
    body: bytes


Send = Callable[[str, str, dict[str, str], bytes | None], HttpResponse]


def urllib_send(method: str, url: str, headers: dict[str, str], body: bytes | None) -> HttpResponse:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return HttpResponse(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise TemporaryApiError(f"Cindra API недоступен: {exc}") from exc


class StreamApi:
    def __init__(
        self, base_url: str, token: str, agent_name: str, version: str, send: Send = urllib_send
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self.agent_name = agent_name
        self._version = version
        self._send = send

    def _call(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = self._send(
            method,
            f"{self._base}{path}",
            {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            json.dumps(payload).encode(),
        )
        if response.status == 409:
            raise StreamLost(response.body.decode(errors="replace"))
        if response.status >= 500:
            raise TemporaryApiError(f"Cindra API {response.status} on {path}")
        if response.status >= 400:
            detail = response.body.decode(errors="replace")[:500]
            raise ApiError(f"Cindra API {response.status} on {path}: {detail}")
        if response.status == 204 or not response.body:
            return None
        return json.loads(response.body)

    def claim(self) -> dict[str, Any] | None:
        return self._call(
            "POST", "/streams/agent/claim",
            {"agent_name": self.agent_name, "version": self._version},
        )

    def heartbeat(self, stream_id: str, stats: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "POST", f"/streams/agent/{stream_id}/heartbeat",
            {"agent_name": self.agent_name, "stats": stats},
        )

    def reply(
        self,
        stream_id: str,
        mode: str,
        *,
        target: dict[str, str] | None = None,
        notice: dict[str, Any] | None = None,
        recent_chat: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST", f"/streams/agent/{stream_id}/reply",
            {
                "agent_name": self.agent_name,
                "mode": mode,
                "target": target,
                "notice": notice,
                "recent_chat": recent_chat or [],
            },
        )

    def event(self, stream_id: str, kind: str, text: str) -> None:
        self._call(
            "POST", f"/streams/agent/{stream_id}/event",
            {"agent_name": self.agent_name, "kind": kind, "text": text[:2000]},
        )

    def finish(self, stream_id: str, error: str | None = None) -> None:
        self._call(
            "POST", f"/streams/agent/{stream_id}/finish",
            {"agent_name": self.agent_name, "error": error[:2000] if error else None},
        )
