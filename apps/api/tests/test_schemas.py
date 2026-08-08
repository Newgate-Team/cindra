import pytest
from pydantic import ValidationError

from app.schemas import TelegramStartVerificationRequest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@mychannel", "@mychannel"),
        ("mychannel", "@mychannel"),
        ("https://t.me/mychannel", "@mychannel"),
        ("http://t.me/mychannel", "@mychannel"),
        ("t.me/mychannel", "@mychannel"),
        ("https://t.me/mychannel/", "@mychannel"),
        ("-1001234567890", "-1001234567890"),
        ("123456", "123456"),
    ],
)
def test_normalize_chat_id(raw: str, expected: str) -> None:
    assert TelegramStartVerificationRequest(chat_id=raw).chat_id == expected


@pytest.mark.parametrize(
    "raw",
    ["https://t.me/+AbCdEfGhIj", "t.me/joinchat/AbCdEfGhIj"],
)
def test_normalize_chat_id_rejects_private_invite_links(raw: str) -> None:
    with pytest.raises(ValidationError, match="Приватные инвайт-ссылки"):
        TelegramStartVerificationRequest(chat_id=raw)
