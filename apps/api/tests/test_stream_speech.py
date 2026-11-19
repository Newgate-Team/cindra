import base64
import json
import struct

import httpx
import pytest

from app.content_pipeline.errors import TransientGenerationError
from app.content_pipeline.stream_speech import SpeechError, synthesize

_PCM = struct.pack("<4h", 0, 1000, -1000, 0)


def _wav(pcm: bytes) -> bytes:
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16
    )
    return header + b"data" + struct.pack("<I", len(pcm)) + pcm


def _gemini(audio: bytes | None, status: int = 200, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "nope"}})
        content = [{"type": "audio", "data": base64.b64encode(audio).decode()}] if audio else []
        return httpx.Response(200, json={"steps": [
            {"type": "user_input"},
            {"type": "model_output", "content": content},
        ]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_request_matches_the_interactions_api_and_keeps_the_key_out_of_the_url() -> None:
    seen: list[httpx.Request] = []
    pcm = synthesize("Привет, чат!", voice="Puck", emotion="happy", client=_gemini(_PCM, seen=seen))
    assert pcm == _PCM
    request = seen[0]
    assert request.url.path == "/v1beta/interactions"
    assert "key" not in request.url.params
    assert "x-goog-api-key" in request.headers
    body = json.loads(request.content)
    text = body["input"][0]["content"][0]
    assert text["text"] == "Привет, чат!"  # spoken verbatim: no stage directions inside
    assert text["annotations"][0] == {"type": "speech_metadata", "style": "cheerful and warm"}
    assert body["response_format"] == {"type": "audio", "mime_type": "audio/l16",
                                       "sample_rate": 24000}
    assert body["generation_config"]["speech_config"] == [{"voice": "Puck"}]


def test_a_wav_answer_is_unwrapped_to_pcm() -> None:
    assert synthesize("x", voice="Kore", client=_gemini(_wav(_PCM))) == _PCM


def test_odd_byte_counts_are_trimmed_to_whole_samples() -> None:
    assert synthesize("x", voice="Kore", client=_gemini(_PCM + b"\x01")) == _PCM


def test_failures() -> None:
    with pytest.raises(SpeechError):
        synthesize("x", voice="Kore", client=_gemini(None))
    with pytest.raises(SpeechError):
        synthesize("x", voice="Kore", client=_gemini(_PCM, status=400))
    with pytest.raises(TransientGenerationError):
        synthesize("x", voice="Kore", client=_gemini(_PCM, status=429))
