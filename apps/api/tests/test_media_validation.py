from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.social_integrations.errors import PermanentPublishError
from app.social_integrations.media_validation import validate_own_media_url

_R2_BASE = "https://media.cindra.test"


def _with_settings(r2_public_url_base: str):
    return patch(
        "app.social_integrations.media_validation.get_settings",
        return_value=SimpleNamespace(r2_public_url_base=r2_public_url_base),
    )


def test_accepts_own_bucket_url() -> None:
    with _with_settings(_R2_BASE):
        validate_own_media_url(f"{_R2_BASE}/clip.mp4", "TikTok")  # must not raise


def test_rejects_foreign_https_host() -> None:
    # The CIN-156 scenario: an attacker-supplied external URL.
    with _with_settings(_R2_BASE), pytest.raises(PermanentPublishError):
        validate_own_media_url("https://evil.example.com/clip.mp4", "Telegram")


def test_rejects_internal_network_address() -> None:
    # A cloud metadata endpoint / internal service, not just "some
    # other public site" -- the actual SSRF target class this guards.
    with _with_settings(_R2_BASE), pytest.raises(PermanentPublishError):
        validate_own_media_url("https://169.254.169.254/latest/meta-data/", "Telegram")


def test_rejects_plain_http_even_to_the_right_host() -> None:
    with _with_settings(_R2_BASE), pytest.raises(PermanentPublishError):
        validate_own_media_url(f"{_R2_BASE.replace('https', 'http')}/clip.mp4", "TikTok")


def test_rejects_host_that_merely_starts_with_the_allowed_base() -> None:
    # media.cindra.test.evil.example.com must not pass just because the
    # string "https://media.cindra.test" is a prefix of it.
    with _with_settings(_R2_BASE), pytest.raises(PermanentPublishError):
        validate_own_media_url("https://media.cindra.test.evil.example.com/x.mp4", "TikTok")


def test_rejects_everything_when_bucket_is_not_configured() -> None:
    with _with_settings(""), pytest.raises(PermanentPublishError):
        validate_own_media_url(f"{_R2_BASE}/clip.mp4", "TikTok")


def test_error_message_names_the_platform() -> None:
    with _with_settings(_R2_BASE), pytest.raises(PermanentPublishError) as exc_info:
        validate_own_media_url("https://evil.example.com/x.mp4", "Telegram")
    assert "Telegram" in str(exc_info.value)
