"""A satellite says the gateway's "thinking brain is offline" answer instead of failing."""
from __future__ import annotations

import json

import pytest

from services.homehub import voice_pipeline

OFFLINE = "My thinking brain is offline right now. I can still control the house and tell you the time and the weather, but I can't answer that yet."


def _offline_body() -> dict:
    return {"status": "ERROR", "message": {"role": "assistant", "content": OFFLINE}, "error": OFFLINE,
            "error_code": "local_ai_offline", "error_id": "abcd1234", "offline": "local_ai", "done": True}


def test_the_offline_answer_is_spoken():
    reply = voice_pipeline._reply_from_answer(503, _offline_body())
    assert str(reply) == OFFLINE


def test_any_other_failure_is_still_an_error():
    with pytest.raises(voice_pipeline.PipelineError):
        voice_pipeline._reply_from_answer(500, {"status": "ERROR", "message": "Job failed"})


async def test_an_offline_end_of_a_streamed_answer_is_spoken():
    async def lines():
        yield json.dumps({"done": True, "message": {"role": "assistant", "content": ""}, "reply": OFFLINE,
                          "speech_rest": OFFLINE, "error_code": "local_ai_offline"}).encode()

    said: list[str] = []

    async def on_sentence(text, voice):
        said.append(text)

    reply = await voice_pipeline.read_speech_stream(lines(), on_sentence)
    assert str(reply) == OFFLINE and reply.rest == OFFLINE
