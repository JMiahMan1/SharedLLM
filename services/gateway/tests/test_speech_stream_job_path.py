"""A voice satellite's speech_stream answered through the job queue: sentences arrive before the job ends.

Production (2026-11-19): every chat turn is enqueued as a job; the background worker runs the orchestrator
and the HTTP response only relays the job's chunks. This drives that whole path in one event loop: the
real worker (``RavenWorker._process_inference_job``), the real ``process_full_orchestration`` and
``_single_turn_inference`` with a model that streams slowly, and the chat handler's relay
(``main._speech_stream_gen``), over an in-memory stand-in for the Redis job queue.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

import services.gateway.background_worker as bw
from services.gateway import main, orchestrator
from services.gateway.messaging import JobStatus


class MemoryJobQueue:
    """The InferenceJobQueue calls the worker and the relay make, kept in memory (chunks as JSON strings)."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.chunks: dict[str, list[str]] = {}

    async def enqueue_job(self, user_id, payload, priority=0, tier="tier2"):
        job_id = f"job-{len(self.jobs) + 1}"
        # Through JSON, as Redis stores it: a payload key that does not survive this is lost in production.
        self.jobs[job_id] = json.loads(json.dumps({"job_id": job_id, "user_id": user_id, "payload": payload,
                                                   "status": JobStatus.QUEUED, "result": None, "error": None}))
        return job_id

    async def get_job_status(self, job_id):
        job = self.jobs.get(job_id)
        return json.loads(json.dumps(job)) if job else None

    async def push_chunk(self, job_id, chunk):
        assert isinstance(chunk, str)  # Redis holds strings; the worker JSON-encodes dict chunks
        self.chunks.setdefault(job_id, []).append(chunk)

    async def get_chunks(self, job_id):
        out, self.chunks[job_id] = self.chunks.get(job_id, []), []
        return out

    async def heartbeat_job(self, job_id):
        pass

    async def complete_job(self, job_id, result):
        self.jobs[job_id].update(status=JobStatus.COMPLETED, result=result)

    async def fail_job(self, job_id, error):
        self.jobs[job_id].update(status=JobStatus.FAILED, error=error)


@pytest.fixture(autouse=True)
def _fresh_tier_locks(monkeypatch):
    monkeypatch.setattr(bw, "TIER2_SEMAPHORE", asyncio.Semaphore(3))
    monkeypatch.setattr(bw, "TIER3_LOCK", asyncio.Lock())


def _quiet_orchestration(monkeypatch, model_turns, tool_results=None):
    """The real orchestrator with its network edges replaced: retrieval, history, the prompt, the tools."""
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _n: "Tools: BibleRequest")
    monkeypatch.setattr(bw, "get_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(bw, "update_history", AsyncMock())
    tools = list(tool_results or [])

    async def execute(action, tool_data, query, creds, workspace_id=None):
        return tools.pop(0)

    monkeypatch.setattr(orchestrator, "_execute_single_tool", execute)
    turns = iter(model_turns)

    async def call_ollama(payload, **kwargs):
        pieces = next(turns)
        cb = payload.get("chunk_callback")
        for piece, pause in pieces:
            if cb is not None:
                await cb({"type": "content", "text": piece})
            await pause() if callable(pause) else asyncio.sleep(pause)
        return {"message": {"content": "".join(p for p, _ in pieces)}}

    monkeypatch.setattr(orchestrator, "call_ollama", call_ollama)


async def _run_job_and_relay(monkeypatch, queue, payload, lines=None):
    """Enqueue, let the worker run the job, and relay it as the chat handler does; each line with the job's
    status at the moment it was relayed."""
    monkeypatch.setattr(main, "job_queue", queue)
    job_id = await queue.enqueue_job("household", payload)
    worker = bw.RavenWorker()
    worker.job_queue = queue
    job = {"job_id": job_id, "user_id": "household", "payload": queue.jobs[job_id]["payload"]}
    work = asyncio.create_task(worker._process_inference_job(job))
    lines = [] if lines is None else lines
    async for line in main._speech_stream_gen(job_id, "m", "af_heart"):
        lines.append((time.monotonic(), json.loads(line), queue.jobs[job_id]["status"]))
    await asyncio.wait_for(work, 5)
    return lines


def _payload(query: str, **extra) -> dict:
    return {"query": query, "model": "m", "creds": {"user": "household"}, "system": "S",
            "voice_context": {"satellite": "assist_satellite.office", "area": "Office"},
            "speech_stream": True, **extra}


async def test_an_enqueued_voice_answer_streams_sentences_before_the_job_finishes(monkeypatch):
    released = asyncio.Event()

    async def model_still_writing():
        await released.wait()

    _quiet_orchestration(monkeypatch, [[
        ("Today's verse is from the Psalms. ", 0.02),
        ("The Lord is my shepherd, ", model_still_writing),
        ("I shall not want. It is a reminder of care.", 0.0),
    ]])
    queue = MemoryJobQueue()
    lines: list = []
    relay = asyncio.create_task(_run_job_and_relay(monkeypatch, queue, _payload("What's the verse of the day?"),
                                                   lines))
    for _ in range(300):  # the model is still writing its second sentence
        if any(obj.get("speech") for _t, obj, _s in lines):
            break
        await asyncio.sleep(0.01)
    assert [obj["message"]["content"] for _t, obj, _s in lines if obj.get("speech")] == [
        "Today's verse is from the Psalms."]
    assert queue.jobs["job-1"]["status"] != JobStatus.COMPLETED
    released.set()
    await asyncio.wait_for(relay, 5)
    spoken = [(obj["message"]["content"], status) for _t, obj, status in lines if obj.get("speech")]
    assert spoken[0][0] == "Today's verse is from the Psalms."
    assert spoken[0][1] != JobStatus.COMPLETED  # relayed before the job was done
    done = lines[-1][1]
    assert done["done"] and done["reply"] == ("Today's verse is from the Psalms. The Lord is my shepherd, "
                                              "I shall not want. It is a reminder of care.")
    said = " ".join(s for s, _ in spoken)
    assert (said + " " + done["speech_rest"]).strip() == done["reply"]


async def test_a_one_sentence_answer_is_still_spoken_from_the_stream(monkeypatch):
    """The last sentence of a turn has nothing after it; once the model call ends it is said, not held."""
    _quiet_orchestration(monkeypatch, [[("Today's verse is Psalm 23:1, \"The Lord is my shepherd.\"", 0.0)]])
    lines = await _run_job_and_relay(monkeypatch, MemoryJobQueue(), _payload("What's the verse of the day?"))
    spoken = [obj["message"]["content"] for _t, obj, _s in lines if obj.get("speech")]
    assert spoken == ["Today's verse is Psalm 23:1, \"The Lord is my shepherd.\""]
    assert lines[-1][1]["speech_rest"] == ""


async def test_after_a_tool_the_answer_streams_and_the_tool_call_never_does(monkeypatch):
    _quiet_orchestration(monkeypatch, [
        [('{"tool": "BibleRequest", "action": "votd"}', 0.0)],
        [("Today's verse comes from Lamentations. ", 0.02), ("His mercies are new every morning.", 0.0)],
    ], tool_results=["Verse of the day: Lamentations 3:22-23"])
    lines = await _run_job_and_relay(monkeypatch, MemoryJobQueue(), _payload("What's the verse of the day?"))
    spoken = [obj["message"]["content"] for _t, obj, _s in lines if obj.get("speech")]
    assert spoken == ["Today's verse comes from Lamentations.", "His mercies are new every morning."]
    assert not any("{" in s or "BibleRequest" in s for s in spoken)
    assert lines[-1][1]["speech_rest"] == ""


async def test_a_device_command_through_the_job_queue_streams_nothing(monkeypatch):
    claim = "The pantry lights should be off now. Anything else?"
    _quiet_orchestration(monkeypatch, [[(claim, 0.0)]])
    lines = await _run_job_and_relay(monkeypatch, MemoryJobQueue(), _payload("Turn off the pantry lights."))
    assert not any(obj.get("speech") for _t, obj, _s in lines)
    done = lines[-1][1]
    assert done["reply"] == "I didn't switch anything just now, so I can't say it's off. Please ask me again."
    assert done["speech_rest"] == done["reply"]


async def test_a_tool_written_as_prose_is_never_spoken(monkeypatch):
    _quiet_orchestration(monkeypatch, [[("1. WorkspaceCreateRequest name 'notes' display_name 'Notes'. ", 0.0),
                                         ("2. GitOperationRequest action 'commit'.", 0.0)],
                                        [("I could not do that from here.", 0.0)]],
                         tool_results=["Sorry, I couldn't complete that action: not here"])
    lines = await _run_job_and_relay(monkeypatch, MemoryJobQueue(), _payload("make a notes workspace"))
    spoken = [obj["message"]["content"] for _t, obj, _s in lines if obj.get("speech")]
    assert not any("Request" in s for s in spoken)
