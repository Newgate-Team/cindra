import json

import httpx

from app.content_pipeline.prompt_enhancer import enhance_image_prompt


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sends_meta_prompt_with_topic_and_brand_guide() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "  A detailed English prompt  "}]}}
                ]
            },
        )

    payload = {
        "topic": "запуск кофейного бренда",
        "brand_guide": "тёплые тона, минимализм",
    }
    result = enhance_image_prompt(
        payload, attachment_texts=["контекст из брифа"], client=_client(handler)
    )

    assert result == "A detailed English prompt"
    assert ":generateContent" in captured["url"]
    sent = captured["body"]["contents"][0]["parts"][0]["text"]
    assert "prompt engineer" in sent
    assert "запуск кофейного бренда" in sent
    assert "тёплые тона, минимализм" in sent
    assert "контекст из брифа" in sent


def test_meta_prompt_keeps_production_lessons() -> None:
    # When the enhancer is active the image model never sees
    # _build_image_prompt's wrapper -- so CIN-117 (no blanket text
    # prohibition), CIN-125 (short, correctly spelled overlay text) and
    # CIN-132 (natural moment, negative space, artifact exclusions)
    # must live in the meta-prompt instead. Pin the load-bearing rules.
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )

    enhance_image_prompt({"topic": "плакат"}, client=_client(handler))
    sent = captured["body"]["contents"][0]["parts"][0]["text"]
    assert "at most 6 words" in sent
    assert "verbatim in its original language" in sent
    assert "distorted faces" in sent
    assert "negative space" in sent
    assert "stock pose" in sent
    # CIN-117's lesson: text on the image must stay allowed when asked for
    assert "Do not add any other text" in sent
    assert "no text on the image" not in sent.lower()


def test_selected_template_directive_is_included() -> None:
    # CIN-143: the template's art direction rides along into the
    # enhancer input; no template -- no "Selected template" line.
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )

    enhance_image_prompt(
        {"topic": "новая кофемашина", "image_template": "product_shot"},
        client=_client(handler),
    )
    sent = captured["body"]["contents"][0]["parts"][0]["text"]
    assert "Selected template to follow: Template: product shot." in sent

    enhance_image_prompt({"topic": "новая кофемашина"}, client=_client(handler))
    sent = captured["body"]["contents"][0]["parts"][0]["text"]
    assert "Selected template" not in sent


def test_http_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    assert enhance_image_prompt({"topic": "x"}, client=_client(handler)) is None


def test_network_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    assert enhance_image_prompt({"topic": "x"}, client=_client(handler)) is None


def test_malformed_response_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    assert enhance_image_prompt({"topic": "x"}, client=_client(handler)) is None


def test_empty_text_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "   "}]}}]}
        )

    assert enhance_image_prompt({"topic": "x"}, client=_client(handler)) is None
