"""A tiny stand-in for the two Gemini calls a live stream makes, so the
whole pipeline (chat -> reply -> voice -> avatar -> RTMP) can run
locally without a Gemini key:

- POST /v1beta/models/{model}:generateContent -> a structured reply
  ({"say", "emotion", "reaction"}) that quotes the viewer it answers;
- POST /v1beta/interactions -> speech for that line: real speech via
  macOS `say` + ffmpeg when available (voice Milena), else a tone.

Run: uvicorn devtools.gemini_stub:app --port 8768 and set
GEMINI_API_BASE_URL=http://localhost:8768 for the API. It is a stub,
not a simulator: replies are canned, there's no schema checking.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from itertools import count
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request

app = FastAPI(title="Gemini stub")
_turn = count(1)
_EMOTIONS = ["happy", "laughing", "surprised", "thinking", "neutral"]
_REACTIONS = ["👋", "🔥", "😂", "🤔", "👍"]


def _speech(text: str) -> bytes:
    if shutil.which("say") and shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory() as tmp:
            aiff, pcm = Path(tmp) / "a.aiff", Path(tmp) / "a.pcm"
            subprocess.run(["say", "-v", "Milena", "-o", str(aiff), text], check=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ac", "1",
                            "-ar", "24000", "-f", "s16le", str(pcm)], check=True)
            return pcm.read_bytes()
    seconds = max(1.0, len(text) / 15)
    t = np.arange(int(24000 * seconds)) / 24000
    return (np.sin(2 * np.pi * 220 * t) * 6000).astype(np.int16).tobytes()


@app.post("/v1beta/models/{model_action}")
async def generate(model_action: str, request: Request) -> dict:
    body = await request.json()
    prompt = body["contents"][0]["parts"][0]["text"]
    data = json.loads(prompt.split("\n", 2)[2])
    n = next(_turn)
    if "message" in data:
        who, what = data["message"]["author"], data["message"]["text"]
        say = f"{who}, спасибо за вопрос! Ты спросил: {what[:80]}. Отвечаю коротко: всё отлично!"
    elif "event" in data:
        say = f"Добро пожаловать, {data['event']['author']} и все, кто пришёл с рейдом!"
    else:
        say = f"Чат, пока тихо, поговорим про {data.get('topic', 'маркетинг')}. Что думаете?"
    reply = {"say": say, "emotion": _EMOTIONS[n % 5], "reaction": _REACTIONS[n % 5]}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(reply, ensure_ascii=False)}]},
                            "finishReason": "STOP"}]}


@app.post("/v1beta/interactions")
async def interactions(request: Request) -> dict:
    body = await request.json()
    text = body["input"][0]["content"][0]["text"]
    audio = base64.b64encode(_speech(text)).decode()
    return {"steps": [{"type": "model_output", "content": [{"type": "audio", "data": audio}]}]}
