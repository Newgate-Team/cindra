import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import ratelimit
from app.config import get_settings


class FakeRedis:
    """Just enough of redis.Redis for the fixed-window counter."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def pipeline(self):
        outer = self

        class Pipe:
            def __init__(self) -> None:
                self.key = ""

            def incr(self, key: str) -> None:
                self.key = key

            def expire(self, key: str, seconds: int) -> None:
                pass

            def execute(self) -> list[int]:
                outer.counts[self.key] = outer.counts.get(self.key, 0) + 1
                return [outer.counts[self.key]]

        return Pipe()


@pytest.fixture
def limits_on(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit, "_client", lambda: fake)
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    return fake


def _request(headers: list[tuple[bytes, bytes]] | None = None, host: str = "10.0.0.9") -> Request:
    return Request(
        {"type": "http", "headers": headers or [], "client": (host, 1234), "method": "GET", "path": "/"}
    )


def test_limit_trips_after_the_allowed_calls(limits_on: FakeRedis) -> None:
    for _ in range(3):
        ratelimit.hit("t", "a", limit=3, window_seconds=60)
    with pytest.raises(HTTPException) as caught:
        ratelimit.hit("t", "a", limit=3, window_seconds=60)
    assert caught.value.status_code == 429
    assert int(caught.value.headers["Retry-After"]) <= 60


def test_keys_and_scopes_are_counted_separately(limits_on: FakeRedis) -> None:
    for _ in range(3):
        ratelimit.hit("t", "a", limit=3, window_seconds=60)
    ratelimit.hit("t", "b", limit=3, window_seconds=60)  # another caller
    ratelimit.hit("other", "a", limit=3, window_seconds=60)  # another endpoint


def test_redis_outage_lets_requests_through(
    monkeypatch: pytest.MonkeyPatch, limits_on: FakeRedis
) -> None:
    import redis

    def broken():
        raise redis.ConnectionError("down")

    monkeypatch.setattr(ratelimit, "_client", broken)
    ratelimit.hit("t", "a", limit=1, window_seconds=60)
    ratelimit.hit("t", "a", limit=1, window_seconds=60)  # would trip with Redis up


def test_disabled_limiter_never_counts(monkeypatch: pytest.MonkeyPatch, limits_on: FakeRedis) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    for _ in range(50):
        ratelimit.hit("t", "a", limit=1, window_seconds=60)
    assert limits_on.counts == {}


def test_client_ip_ignores_forwarded_header_by_default() -> None:
    request = _request([(b"x-forwarded-for", b"1.2.3.4")])
    assert ratelimit.client_ip(request) == "10.0.0.9"


def test_client_ip_takes_the_entry_our_proxy_appended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    # The left entry is whatever the client claimed; only the last one is ours.
    request = _request([(b"x-forwarded-for", b"6.6.6.6, 203.0.113.7")])
    assert ratelimit.client_ip(request) == "203.0.113.7"


def test_client_ip_falls_back_when_the_header_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    assert ratelimit.client_ip(_request()) == "10.0.0.9"


def test_register_is_throttled_per_address(client: TestClient, limits_on: FakeRedis) -> None:
    statuses = [
        client.post(
            "/auth/register", json={"email": f"user{i}@cindra.dev", "password": "correct-horse-1"}
        ).status_code
        for i in range(11)
    ]
    assert statuses[:10] == [201] * 10
    assert statuses[10] == 429


def test_password_reset_request_is_throttled_per_mailbox(
    client: TestClient, limits_on: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.example.com")
    codes = [
        client.post("/auth/password-reset/request", json={"email": "someone@cindra.dev"}).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
