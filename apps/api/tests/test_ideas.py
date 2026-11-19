import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.content_pipeline import ideas as ideas_module
from app.content_pipeline.video_studio import VideoStudioFailedError


def _gemini_reply(payload) -> httpx.Response:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


GOOD = [
    {"title": "Как мы выбираем зерно", "hook": "Мы отказались от 9 из 10 поставщиков.", "format": "video"},
    {"title": "Три ошибки в завтраке", "hook": "Кофе на голодный желудок?", "format": "post"},
]


def test_prompt_carries_brand_guide_focus_and_recent_topics() -> None:
    prompt = ideas_module.build_ideas_prompt("Короткие фразы", ["старая тема"], "осенняя коллекция")
    assert "Короткие фразы" in prompt
    assert "осенняя коллекция" in prompt
    assert "старая тема" in prompt and "не повторяй" in prompt


def test_generate_ideas_parses_the_models_json() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _gemini_reply(GOOD)

    result = ideas_module.generate_ideas(None, [], client=_client(handler))
    assert [i["format"] for i in result] == ["video", "post"]
    # Asked for structured output, so the reply is JSON rather than prose.
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_unknown_format_falls_back_to_post_and_blank_titles_are_dropped() -> None:
    raw = json.dumps(
        [{"title": "ok", "hook": "h", "format": "podcast"}, {"title": "  ", "hook": "x", "format": "post"}]
    )
    assert ideas_module.parse_ideas(raw) == [{"title": "ok", "hook": "h", "format": "post"}]


@pytest.mark.parametrize("raw", ["not json", '{"a": 1}', "[]", '[{"hook": "no title"}]'])
def test_unusable_replies_are_reported_not_passed_on(raw: str) -> None:
    with pytest.raises(VideoStudioFailedError):
        ideas_module.parse_ideas(raw)


def _auth(client: TestClient) -> dict[str, str]:
    creds = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=creds)
    token = client.post("/auth/login", json=creds).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_endpoint_needs_a_gemini_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gemini_api_key", "")
    assert client.post("/content/ideas", json={}, headers=_auth(client)).status_code == 503


def test_endpoint_requires_auth(client: TestClient) -> None:
    assert client.post("/content/ideas", json={}).status_code == 401


def test_endpoint_returns_ideas_using_the_saved_brand_guide(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    headers = _auth(client)
    client.patch("/auth/me", json={"role": "solo", "brand_guide": "Тёплый тон"}, headers=headers)
    captured: dict = {}

    def fake(brand_guide, recent_topics, focus=None, client=None):
        captured.update(brand_guide=brand_guide, focus=focus)
        return GOOD

    from app.routers import content

    monkeypatch.setattr(content, "generate_ideas", fake)
    response = client.post("/content/ideas", json={"focus": "запуск"}, headers=headers)
    assert response.status_code == 200
    assert [i["title"] for i in response.json()["ideas"]] == [g["title"] for g in GOOD]
    assert captured == {"brand_guide": "Тёплый тон", "focus": "запуск"}


def test_model_failures_map_to_gateway_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.content_pipeline.errors import TransientGenerationError
    from app.routers import content

    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    headers = _auth(client)

    def overloaded(*args, **kwargs):
        raise TransientGenerationError("busy")

    def garbage(*args, **kwargs):
        raise VideoStudioFailedError("bad json")

    monkeypatch.setattr(content, "generate_ideas", overloaded)
    assert client.post("/content/ideas", json={}, headers=headers).status_code == 503
    monkeypatch.setattr(content, "generate_ideas", garbage)
    assert client.post("/content/ideas", json={}, headers=headers).status_code == 502
