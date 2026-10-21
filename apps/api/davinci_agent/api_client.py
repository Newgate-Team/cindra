"""Talks to Cindra's /montage/agent/* endpoints and downloads media.

urllib only (no requests/httpx): the render machine shouldn't need a
pip install. The transport is a plain function so the tests can route
it into the real API in-process instead of over the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ApiError(Exception):
    pass


class JobLost(Exception):
    """The API gave the job to another agent (this one went quiet too
    long) or it's no longer running -- stop working on it, don't report."""


@dataclass
class HttpResponse:
    status: int
    body: bytes


Send = Callable[[str, str, dict[str, str], bytes | None], HttpResponse]
Fetch = Callable[[str], Iterator[bytes]]


def urllib_send(method: str, url: str, headers: dict[str, str], body: bytes | None) -> HttpResponse:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return HttpResponse(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read())


def urllib_fetch(url: str) -> Iterator[bytes]:
    with urllib.request.urlopen(url, timeout=600) as response:
        while chunk := response.read(1024 * 1024):
            yield chunk


class CindraApi:
    def __init__(
        self,
        base_url: str,
        token: str,
        agent_name: str,
        version: str,
        send: Send = urllib_send,
        fetch: Fetch = urllib_fetch,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self.agent_name = agent_name
        self._version = version
        self._send = send
        self._fetch = fetch

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None,
              body: bytes | None = None, content_type: str = "application/json") -> HttpResponse:
        headers = {"Authorization": f"Bearer {self._token}"}
        if payload is not None:
            body = json.dumps(payload).encode()
        if body is not None:
            headers["Content-Type"] = content_type
        response = self._send(method, f"{self._base}{path}", headers, body)
        if response.status == 409:
            raise JobLost(response.body.decode(errors="replace"))
        if response.status >= 400:
            detail = response.body.decode(errors="replace")[:500]
            raise ApiError(f"Cindra API {response.status} on {path}: {detail}")
        return response

    def _hello(self) -> dict[str, str]:
        return {"agent_name": self.agent_name, "version": self._version}

    def heartbeat(self) -> None:
        self._call("POST", "/montage/agent/heartbeat", self._hello())

    def claim(self) -> dict[str, Any] | None:
        response = self._call("POST", "/montage/agent/claim", self._hello())
        return json.loads(response.body) if response.status == 200 else None

    def progress(self, job_id: str, stage: str, progress: int) -> None:
        self._call(
            "POST",
            f"/montage/agent/jobs/{job_id}/progress",
            {"agent_name": self.agent_name, "stage": stage, "progress": progress},
        )

    def fail(self, job_id: str, error: str) -> None:
        self._call(
            "POST",
            f"/montage/agent/jobs/{job_id}/fail",
            {"agent_name": self.agent_name, "error": error[:4000]},
        )

    def upload_result(self, job_id: str, video: Path) -> None:
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="montage.mp4"\r\n'
            "Content-Type: video/mp4\r\n\r\n"
        ).encode() + video.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        name = urllib.parse.quote(self.agent_name)
        self._call(
            "POST",
            f"/montage/agent/jobs/{job_id}/result?agent_name={name}",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )

    def download(self, url: str, destination: Path, max_bytes: int) -> None:
        # No Authorization header: media lives on the public bucket and
        # must never see the agent token.
        written = 0
        with destination.open("wb") as out:
            for chunk in self._fetch(url):
                written += len(chunk)
                if written > max_bytes:
                    raise ApiError(f"Файл больше {max_bytes // (1024 * 1024)}MB: {url}")
                out.write(chunk)
        if written == 0:
            raise ApiError(f"Пустой файл: {url}")
