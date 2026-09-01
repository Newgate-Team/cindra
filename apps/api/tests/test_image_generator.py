import json
from unittest.mock import patch

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.image_generator import (
    ImageGenerationFailedError,
    nano_banana_image_generator,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sends_correct_request_shape_and_parses_response() -> None:
    # CIN-142: the image model receives the enhancer's engineered
    # English prompt, not the raw Russian wrapper -- the wrapper is
    # exercised separately as the fallback path below.
    engineered = "A steaming cup of coffee on a sunlit wooden table, soft morning light"
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            captured["url"] = str(request.url)
            captured["headers"] = request.headers
            captured["body"] = request.content
            return httpx.Response(
                200,
                json={
                    "id": "abc123",
                    "status": "completed",
                    "output_image": {
                        "type": "image",
                        "data": "ZmFrZS1pbWFnZQ==",
                        "mime_type": "image/png",
                    },
                },
            )
        # generateContent serves two internal calls: the prompt
        # enhancer (CIN-142, before the image) and the caption
        # (CIN-114, after it) -- told apart by the meta-prompt marker.
        if b"prompt engineer" in request.content:
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": engineered}]}}]}
            )
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "Подпись"}]}}]}
        )

    payload = {"topic": "утренний кофе", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ) as upload:
        result = nano_banana_image_generator(payload, client=_client(handler))

    upload.assert_called_once_with(b"fake-image", "image/png", "png")
    assert result["image_url"] == "https://media.cindra.example/abc.png"
    assert result["prompt"] == engineered
    assert result["text"] == "Подпись"
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert "x-goog-api-key" in captured["headers"]
    body = json.loads(captured["body"])
    assert body["model"] == "gemini-2.5-flash-image"
    assert body["input"] == engineered


def test_enhancer_failure_falls_back_to_baseline_prompt() -> None:
    # CIN-142: the enhancer is best-effort -- when it fails, the image
    # is still generated with _build_image_prompt's wrapper (which the
    # CIN-117/125/132 tests below pin down in detail).
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    payload = {"topic": "утренний кофе", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        result = nano_banana_image_generator(payload, client=_client(handler))
    assert result["image_url"] == "https://media.cindra.example/abc.png"
    assert "утренний кофе" in captured["body"]["input"]
    assert "Фотореалистичное изображение" in captured["body"]["input"]


def test_aspect_ratio_follows_content_kind_and_platform() -> None:
    # CIN-145: story -> vertical, instagram feed post -> square,
    # anything else -> no response_format at all (model default).
    def run(payload: dict) -> dict:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if "interactions" in str(request.url):
                captured["body"] = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                    },
                )
            return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

        with patch(
            "app.content_pipeline.image_generator.upload_bytes",
            return_value="https://media.cindra.example/abc.png",
        ):
            nano_banana_image_generator(payload, client=_client(handler))
        return captured["body"]

    story = run({"topic": "x", "platform": "instagram", "content_kind": "story"})
    assert story["response_format"] == {"type": "image", "aspect_ratio": "9:16"}

    feed_post = run({"topic": "x", "platform": "instagram", "content_kind": "post"})
    assert feed_post["response_format"] == {"type": "image", "aspect_ratio": "1:1"}

    telegram_post = run({"topic": "x", "platform": "telegram", "content_kind": "post"})
    assert "response_format" not in telegram_post


def test_template_directive_survives_enhancer_fallback() -> None:
    # CIN-143: the user picked a template -- its art direction must not
    # silently disappear just because the enhancer call failed.
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    payload = {
        "topic": "новая кофемашина",
        "platform": "instagram",
        "image_template": "product_shot",
    }
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        nano_banana_image_generator(payload, client=_client(handler))
    assert "Template: product shot." in captured["body"]["input"]


def test_429_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})

    with pytest.raises(TransientGenerationError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_5xx_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})

    with pytest.raises(TransientGenerationError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_network_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    with pytest.raises(TransientGenerationError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_image_attachment_becomes_reference_image_in_input_array() -> None:
    reference_bytes = b"fake-reference-image"

    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            body = json.loads(request.content)
            assert isinstance(body["input"], list)
            assert body["input"][0] == {"type": "text", "text": body["input"][0]["text"]}
            assert "утренний кофе" in body["input"][0]["text"]
            assert body["input"][1]["type"] == "image"
            assert body["input"][1]["mime_type"] == "image/jpeg"
            import base64

            assert base64.b64decode(body["input"][1]["data"]) == reference_bytes
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(200, content=reference_bytes)

    payload = {
        "topic": "утренний кофе",
        "platform": "instagram",
        "attachments": [{"url": "https://r2.example/mood.jpg", "attachment_type": "image"}],
    }
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        result = nano_banana_image_generator(payload, client=_client(handler))
    assert result["image_url"] == "https://media.cindra.example/abc.png"


def test_two_image_attachments_both_become_reference_images() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            body = json.loads(request.content)
            assert isinstance(body["input"], list)
            assert len(body["input"]) == 3  # text + 2 reference images
            assert [p["type"] for p in body["input"]] == ["text", "image", "image"]
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(200, content=b"fake-reference-image")

    payload = {
        "topic": "утренний кофе",
        "platform": "instagram",
        "attachments": [
            {"url": "https://r2.example/mood1.jpg", "attachment_type": "image"},
            {"url": "https://r2.example/mood2.jpg", "attachment_type": "image"},
        ],
    }
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        nano_banana_image_generator(payload, client=_client(handler))


def test_document_attachment_adds_context_without_changing_input_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            body = json.loads(request.content)
            assert isinstance(body["input"], str)
            assert "бренд-гайд от клиента" in body["input"]
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(200, content="бренд-гайд от клиента".encode())

    payload = {
        "topic": "утренний кофе",
        "platform": "instagram",
        "attachments": [{"url": "https://r2.example/brief.txt", "attachment_type": "document"}],
    }
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        nano_banana_image_generator(payload, client=_client(handler))


def test_400_is_not_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})

    with pytest.raises(httpx.HTTPStatusError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )


def test_missing_output_image_raises_clear_error_not_keyerror() -> None:
    # Gemini can respond 200 with no output_image at all -- e.g. it
    # declined to generate an image for this request. Blindly indexing
    # body["output_image"] used to crash with a bare, unhelpful
    # KeyError("output_image") that got shown to the end user verbatim.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "abc123", "status": "completed"})

    with pytest.raises(ImageGenerationFailedError) as exc_info:
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )
    assert "output_image" not in str(exc_info.value)


def test_missing_output_image_logs_raw_response_for_diagnosis(caplog: pytest.LogCaptureFixture) -> None:
    # CIN-110: the user-facing error stays generic, but the raw response
    # body is logged so a real failure can be diagnosed after the fact
    # instead of guessed at (see CIN-105's unresolved root cause).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "abc123", "status": "completed", "steps": [{"type": "text"}]}
        )

    with caplog.at_level("WARNING"), pytest.raises(ImageGenerationFailedError):
        nano_banana_image_generator(
            {"topic": "x", "platform": "instagram"}, client=_client(handler)
        )

    # only this module's diagnostic record -- the prompt enhancer
    # (CIN-142) legitimately logs its own fallback warning here too
    records = [r for r in caplog.records if r.name == "app.content_pipeline.image_generator"]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "status=completed" in message
    assert '"steps"' in message


def test_successful_generation_includes_generated_caption() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "Отличная подпись"}]}}]}
        )

    payload = {"topic": "утренний кофе", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        result = nano_banana_image_generator(payload, client=_client(handler))
    assert result["text"] == "Отличная подпись"


def test_caption_failure_does_not_fail_the_image_generation() -> None:
    # CIN-114: a caption is best-effort -- if it fails, the successful
    # (already paid-for) image must still be returned, just without "text".
    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    payload = {"topic": "утренний кофе", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        result = nano_banana_image_generator(payload, client=_client(handler))
    assert result["image_url"] == "https://media.cindra.example/abc.png"
    assert "text" not in result


def test_prompt_does_not_forbid_text_on_the_image() -> None:
    # CIN-117: a real production request ("покажи наш логотип на экране
    # ноутбука") failed because this hard-coded instruction directly
    # contradicted what the user asked for -- the same request succeeds
    # in AI Studio, which has no such instruction. Removed entirely;
    # whether text belongs on the image is now up to topic/brand_guide.
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    payload = {"topic": "логотип на экране ноутбука", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        nano_banana_image_generator(payload, client=_client(handler))
    assert "текста" not in captured["body"]["input"]
    assert "запрещ" not in captured["body"]["input"]


def test_prompt_nudges_toward_short_correctly_spelled_text() -> None:
    # CIN-125: real generated photos came back with actual spelling/
    # grammar errors baked into on-image text for longer phrases (e.g.
    # "хочошо"/"рабюто" instead of "хочу"/"работать") -- nudge toward
    # the length/correctness regime image models handle more reliably,
    # without re-introducing CIN-117's blanket "no text" restriction.
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    payload = {"topic": "мотивационный плакат", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        nano_banana_image_generator(payload, client=_client(handler))
    assert "4-6 слов" in captured["body"]["input"]
    assert "без орфографических и грамматических ошибок" in captured["body"]["input"]


def test_prompt_nudges_toward_clean_credible_composition_with_negative_space() -> None:
    # CIN-132: grounded in the run-social-content skill's
    # create-social-image-posts reference (prompt-contracts.md's
    # exclusion list, visual-formats.md's "natural moment over generic
    # pose"). Negative space matters specifically here since Instagram
    # Stories composite a caption onto the image afterward (CIN-123).
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    payload = {"topic": "осенняя коллекция кофе", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ):
        nano_banana_image_generator(payload, client=_client(handler))
    body = captured["body"]["input"]
    assert "свободного пространства" in body
    assert "искажённых лиц" in body


def test_image_found_in_steps_is_used_when_output_image_is_missing() -> None:
    # CIN-118: a real production response had status=completed and a
    # genuinely generated image, but no top-level output_image at all
    # -- the image only existed nested in steps[].content[]. Every
    # prior "Gemini declined" diagnosis (CIN-105/110/117) was chasing
    # the wrong cause; this is the actual fix.
    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "steps": [
                        {
                            "type": "model_output",
                            "content": [{"type": "text", "text": "Вот ваше изображение!"}],
                        },
                        {
                            "type": "model_output",
                            "content": [
                                {"mime_type": "image/png", "data": "ZmFrZS1pbWFnZQ=="}
                            ],
                        },
                    ],
                },
            )
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    payload = {"topic": "x", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ) as upload:
        result = nano_banana_image_generator(payload, client=_client(handler))
    upload.assert_called_once_with(b"fake-image", "image/png", "png")
    assert result["image_url"] == "https://media.cindra.example/abc.png"


def test_last_image_in_steps_is_used_when_multiple_are_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "interactions" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "steps": [
                        {"type": "model_output", "content": [{"mime_type": "image/png", "data": "Zmlyc3Q="}]},
                        {"type": "model_output", "content": [{"mime_type": "image/png", "data": "bGFzdA=="}]},
                    ],
                },
            )
        return httpx.Response(500, json={"error": {"status": "INTERNAL"}})

    payload = {"topic": "x", "platform": "instagram"}
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/abc.png",
    ) as upload:
        nano_banana_image_generator(payload, client=_client(handler))
    upload.assert_called_once_with(b"last", "image/png", "png")


def test_illustration_kind_uses_illustration_wrapper_and_skips_caption() -> None:
    # CIN-137: studio illustrations are drawn assets -- the
    # photorealistic lead would fight the illustration prompt, and the
    # caption call is wasted money for an asset that never becomes a
    # post.
    captured = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["calls"] += 1
        if "interactions" in str(request.url):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output_image": {"data": "ZmFrZS1pbWFnZQ==", "mime_type": "image/png"},
                },
            )
        return httpx.Response(200, json={"candidates": []})

    payload = {
        "topic": "тающие часы, тёмный фон",
        "image_kind": "illustration",
        "content_kind": "post",
    }
    with patch(
        "app.content_pipeline.image_generator.upload_bytes",
        return_value="https://media.cindra.example/illustration.png",
    ):
        result = nano_banana_image_generator(payload, client=_client(handler))
    prompt = captured["body"]["input"]
    assert prompt.startswith("Иллюстрация:")
    assert "Фотореалистичное" not in prompt
    # only the image call itself -- no caption generateContent call
    assert captured["calls"] == 1
    assert "text" not in result
