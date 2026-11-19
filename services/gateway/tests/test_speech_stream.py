"""A voice satellite's reply streamed a sentence at a time: only what is safe to say before the answer ends."""
from __future__ import annotations

import json

import pytest

from services.gateway import orchestrator
from services.gateway.speech_stream import SpeechGate, speech_rest


class Sink:
    def __init__(self):
        self.chunks: list[dict] = []

    async def __call__(self, chunk):
        self.chunks.append(chunk)

    def said(self) -> list[str]:
        return [c["text"] for c in self.chunks if c.get("type") == "speech"]


async def _feed(gate: SpeechGate, text: str, size: int = 7) -> None:
    for i in range(0, len(text), size):
        await gate.feed({"type": "content", "text": text[i:i + size]})


async def test_whole_sentences_come_out_as_they_are_written():
    sink = Sink()
    gate = SpeechGate(sink, "what's the weather tomorrow")
    await gate.begin_turn()
    await _feed(gate, "Tomorrow will be sunny and warm. Expect a high of 75 degrees in the after")
    assert sink.said() == ["Tomorrow will be sunny and warm."]
    await _feed(gate, "noon. Light winds.")
    # "Light winds." is shorter than MIN_CHARS and has no end yet: it waits for the rest of the answer.
    assert sink.said() == ["Tomorrow will be sunny and warm.", "Expect a high of 75 degrees in the afternoon."]


async def test_short_sentences_and_abbreviations_are_joined_not_split():
    sink = Sink()
    gate = SpeechGate(sink, "who is on call")
    await gate.begin_turn()
    await _feed(gate, "Okay. Dr. Example is on call tonight. ")
    assert sink.said() == ["Okay. Dr. Example is on call tonight."]


async def test_a_tool_call_is_never_spoken_and_mutes_the_rest_of_its_turn():
    sink = Sink()
    gate = SpeechGate(sink, "what's on my calendar")
    await gate.begin_turn()
    await _feed(gate, 'Let me look that up for you. {"tool": "CalendarRequest", "action": "list"} Done. More words here.')
    assert sink.said() == ["Let me look that up for you."]
    assert not any("{" in s or "tool" in s.lower() for s in sink.said())
    await gate.begin_turn()  # the turn after the tool ran
    await _feed(gate, "You have two meetings tomorrow morning. ")
    assert sink.chunks[-2] == {"type": "speech_turn"}
    assert sink.said()[-1] == "You have two meetings tomorrow morning."


async def test_reasoning_is_never_spoken():
    sink = Sink()
    gate = SpeechGate(sink, "tell me a fact")
    await gate.begin_turn()
    await _feed(gate, "<think>The user wants a fact. Pick one.</think>Honey never spoils in a sealed jar. ")
    assert sink.said() == ["Honey never spoils in a sealed jar."]
    await gate.feed({"type": "thinking", "text": "more private reasoning here."})
    assert sink.said() == ["Honey never spoils in a sealed jar."]


async def test_a_device_command_is_not_streamed_so_device_claims_can_still_correct_it():
    sink = Sink()
    gate = SpeechGate(sink, "Turn off the pantry lights.")
    await gate.begin_turn()
    await _feed(gate, "The pantry lights should be off now. Anything else I can do for you? ")
    await gate.begin_turn()
    assert sink.chunks == []


@pytest.mark.parametrize("reply, spoken, rest", [
    ("Sunny and warm today. High of 75.", ["Sunny and warm today."], "High of 75."),
    ("Sunny and warm today.", ["Sunny and warm today."], ""),
    ("**Sunny** and warm today. High of 75.", ["Sunny and warm today."], "High of 75."),
    ("Something else entirely.", ["Sunny and warm today."], "Something else entirely."),
    ("Nothing was said yet.", [], "Nothing was said yet."),
])
def test_the_rest_of_the_answer_is_what_was_not_said(reply, spoken, rest):
    assert speech_rest(reply, spoken) == rest


MADE_UP = "The pantry lights should be off now. Would you like me to check them again?"


async def test_the_orchestrator_streams_safe_sentences_and_returns_the_whole_answer(monkeypatch):
    async def fake_call(payload, **kwargs):
        cb = payload["chunk_callback"]
        answer = "It will be sunny tomorrow with a light breeze. Highs near seventy."
        for i in range(0, len(answer), 9) if cb is not None else ():
            await cb({"type": "content", "text": answer[i:i + 9]})
        return {"message": {"content": answer}}

    monkeypatch.setattr(orchestrator, "call_ollama", fake_call)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _n: "Tools: WeatherRequest")
    sink = Sink()
    answer = await orchestrator._single_turn_inference(
        query="what's the weather tomorrow", model="m", system_prompt="S", rag_context="", history=[],
        creds=orchestrator.ResolvedCredentials(user="u"), chunk_callback=sink, speech_stream=True)
    assert answer == "It will be sunny tomorrow with a light breeze. Highs near seventy."
    assert sink.said() == ["It will be sunny tomorrow with a light breeze."]
    # Without speech_stream the old behaviour stands: the whole answer as one content chunk at the end.
    plain = Sink()
    await orchestrator._single_turn_inference(
        query="what's the weather tomorrow", model="m", system_prompt="S", rag_context="", history=[],
        creds=orchestrator.ResolvedCredentials(user="u"), chunk_callback=plain)
    assert plain.chunks == [{"type": "content", "text": answer}]


async def test_device_claims_still_correct_a_streamed_voice_answer(monkeypatch):
    async def fake_call(payload, **kwargs):
        cb = payload["chunk_callback"]
        if cb is not None:
            await cb({"type": "content", "text": MADE_UP})
        return {"message": {"content": MADE_UP}}

    monkeypatch.setattr(orchestrator, "call_ollama", fake_call)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _n: "Tools: LightControlRequest")
    sink = Sink()
    answer = await orchestrator._single_turn_inference(
        query="Turn off the pantry lights.", model="m", system_prompt="S", rag_context="", history=[],
        creds=orchestrator.ResolvedCredentials(user="u"), chunk_callback=sink, speech_stream=True)
    assert answer == "I didn't switch anything just now, so I can't say it's off. Please ask me again."
    assert sink.said() == []  # the made-up claim was never streamed


class FakeQueue:
    def __init__(self, chunks, status, result=None, error=None):
        self.chunks, self.status, self.result, self.error = list(chunks), status, result, error

    async def get_job_status(self, job_id):
        return {"status": self.status, "result": self.result, "error": self.error}

    async def get_chunks(self, job_id):
        out, self.chunks = self.chunks, []
        return out


async def _lines(gen) -> list[dict]:
    return [json.loads(line) async for line in gen]


async def test_the_chat_handler_sends_sentences_then_the_rest(monkeypatch):
    from services.gateway import main
    from services.gateway.messaging import JobStatus

    chunks = [json.dumps({"type": "speech", "text": "Turned to the side first."}), json.dumps({"type": "speech_turn"}),
              json.dumps({"type": "speech", "text": "It is sunny and warm today."}),
              json.dumps({"type": "content", "text": "raw words never pass"}), "plain text never passes"]
    monkeypatch.setattr(main, "job_queue", FakeQueue(chunks, JobStatus.COMPLETED,
                                                     result="It is sunny and warm today. Highs near seventy."))
    lines = await _lines(main._speech_stream_gen("job", "m", "af_heart"))
    assert [ln["message"]["content"] for ln in lines if ln.get("speech")] == [
        "Turned to the side first.", "It is sunny and warm today."]
    done = lines[-1]
    assert done["done"] is True and done["reply"] == "It is sunny and warm today. Highs near seventy."
    assert done["speech_rest"] == "Highs near seventy." and done["voice"] == "af_heart"
    assert len(lines) == 3


async def test_a_failed_job_ends_the_speech_stream_with_its_error(monkeypatch):
    from services.gateway import main
    from services.gateway.messaging import JobStatus

    monkeypatch.setattr(main, "job_queue", FakeQueue([], JobStatus.FAILED, error="model server down"))
    lines = await _lines(main._speech_stream_gen("job", "m", None))
    assert lines == [{**lines[0], "done": True, "error": "model server down"}]
    assert "voice" not in lines[0]


def _chat_mocks(main, monkeypatch, chunks, result):
    from unittest.mock import AsyncMock, MagicMock

    from services.gateway import sentence_triggers
    from services.gateway.messaging import JobStatus

    monkeypatch.setattr(main, "resolve_identity", AsyncMock(return_value={
        "user": "household", "ha_url": "http://hub.test", "ha_token": "tok"}))
    for name, value in (("fetch_ha_entities", []), ("update_history", None), ("get_history", []),
                        ("contextualize_query", "what's the weather tomorrow"), ("decompose_command_query", []),
                        ("get_assistant_model", "qwen3:8b"), ("fetch_global_setting", "0.85"),
                        ("_recognised_speaker", None), ("_voice_for_reply", "af_heart"),
                        ("_voice_teaching_reply", None)):
        monkeypatch.setattr(main, name, AsyncMock(return_value=value))
    monkeypatch.setattr(main, "get_llm_settings", AsyncMock(return_value={
        "active_llm_provider": "ollama", "assistant_model": "qwen3:8b"}))
    monkeypatch.setattr(main, "select_system_instruction_for_query", lambda q, m: "# System instruction mock")
    monkeypatch.setattr(sentence_triggers, "ask_hub", AsyncMock(return_value=None))
    queue = FakeQueue(chunks, JobStatus.COMPLETED, result=result)
    queue.enqueue_job = AsyncMock(return_value="job-1")
    queue.get_queue_position = AsyncMock(return_value=0)
    monkeypatch.setattr(main, "job_queue", queue)

    class _Resp:
        status = 200

        async def json(self):
            return {"results": []}

    http = MagicMock()
    http.post = AsyncMock(return_value=_Resp())
    monkeypatch.setattr(main, "get_http_client", lambda: http)
    return queue


async def test_a_satellite_asking_for_speech_gets_sentences_over_http(client, monkeypatch):
    from services.gateway import main

    queue = _chat_mocks(main, monkeypatch, [json.dumps({"type": "speech", "text": "Tomorrow will be sunny and warm."})],
                        "Tomorrow will be sunny and warm. Highs near seventy.")
    body = {"query": "what's the weather tomorrow", "stream": True, "speech_stream": True,
            "voice_context": {"satellite": "assist_satellite.kitchen", "area": "Kitchen"}}
    resp = client.post("/api/chat", json=body, headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    assert queue.enqueue_job.await_args.args[1]["speech_stream"] is True
    lines = [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]
    assert lines[0]["speech"] is True and lines[0]["message"]["content"] == "Tomorrow will be sunny and warm."
    assert lines[-1]["speech_rest"] == "Highs near seventy." and lines[-1]["voice"] == "af_heart"


async def test_speech_streaming_needs_an_internal_voice_context(client, monkeypatch):
    from services.gateway import main

    queue = _chat_mocks(main, monkeypatch, [], "Sunny.")
    body = {"query": "what's the weather tomorrow", "stream": True, "speech_stream": True,
            "voice_context": {"satellite": "assist_satellite.kitchen"}}
    client.post("/api/chat", json=body)  # no internal secret: the voice context is dropped
    assert queue.enqueue_job.await_args.args[1]["speech_stream"] is False
