"""Request outcomes per person (services/gateway/voice_outcomes.py): counted in
memory on the request's path, written to Redis in batches, kept a bounded time,
and never holding a question, an answer or a tool's arguments."""
import asyncio
import json
import time
from datetime import date

import fakeredis
import pytest

from services.gateway import voice_outcomes as vo


@pytest.fixture
def store(monkeypatch):
    fake = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(vo, "_client", fake)
    vo._pending.clear()
    monkeypatch.setattr(vo, "_ensure_flusher", lambda: None)
    yield fake
    vo._pending.clear()


def _run(coro):
    return asyncio.run(coro)


def test_finishing_only_counts_in_memory(store):
    rec = vo.begin("voice")
    vo.set_user("Pat")
    vo.finish(rec)
    assert sum(vo._pending.values()) > 0
    assert _run(store.keys("*")) == []  # nothing written on the request's path


def test_a_flush_writes_one_hash_per_person_and_day_with_a_bounded_life(store):
    rec = vo.begin("voice")
    vo.set_user("pat")
    vo.note_tool("light_control", True)
    vo.note_tool("set_oven", False)
    vo.finish(rec)
    assert _run(vo.flush()) > 0
    key = f"outcomes:pat:{date.today().isoformat()}"
    row = _run(store.hgetall(key))
    assert row["n"] == "1"
    assert row["o:tool_failure"] == "1"
    assert row["t:set_oven:fail"] == "1"
    assert row["t:light_control:calls"] == "1"
    ttl = _run(store.ttl(key))
    assert 0 < ttl <= vo.RETENTION_DAYS * 86400


def test_no_question_or_answer_text_is_ever_stored(store):
    rec = vo.begin("chat")
    vo.set_user("pat")
    vo.note_tool("web search: what is my secret", False)
    vo.finish(rec)
    _run(vo.flush())
    stored = json.dumps(_run(store.hgetall(f"outcomes:pat:{date.today().isoformat()}")))
    assert "secret" in stored  # the tool name is normalised but kept...
    assert " " not in "".join(k for k in json.loads(stored))  # ...as a token, never free text
    assert "what is my" not in stored


def test_an_unknown_person_or_a_refused_request_is_not_counted(store):
    rec = vo.begin("chat")
    vo.finish(rec)  # never named: an anonymous refusal
    assert not vo._pending

    class Resp:
        status_code = 401
    rec = vo.begin("chat")
    vo.set_user("pat")
    vo.finish_response(rec, Resp())
    assert not vo._pending


def test_a_server_error_and_a_gateway_timeout_are_failures(store):
    class Resp:
        def __init__(self, code):
            self.status_code = code
    for code, outcome in ((500, "error"), (504, "timeout")):
        rec = vo.begin("chat")
        vo.set_user("pat")
        vo.finish_response(rec, Resp(code))
        assert vo._pending[("pat", date.today().isoformat(), f"o:{outcome}")] == 1


def test_a_stream_is_counted_when_it_ends(store):
    class Stream:
        status_code = 200

        def __init__(self):
            async def gen():
                yield b"a"
                yield b"b"
            self.body_iterator = gen()

    rec = vo.begin("voice")
    vo.set_user("pat")
    resp = vo.finish_response(rec, Stream())
    assert not vo._pending

    async def drain():
        return [c async for c in resp.body_iterator]
    assert _run(drain()) == [b"a", b"b"]
    assert vo._pending[("pat", date.today().isoformat(), "o:ok")] == 1


def test_a_deferred_request_is_finished_by_its_job_with_the_wait_counted(store):
    rec = vo.begin("voice")
    vo.set_user("pat")
    carried = vo.defer()
    assert vo.finish_response(rec, object()) is not None
    assert not vo._pending  # the handler leaves it to the job
    now = time.time()
    job = {"user_id": "pat", "payload": {"_outcome": carried}, "created_at": now - 10, "started_at": now - 4, "completed_at": now}
    vo.finish_job(job, failed=False)
    day = date.today().isoformat()
    assert vo._pending[("pat", day, "o:ok")] == 1
    assert vo._pending[("pat", day, "q")] == 1  # waited six seconds before a worker took it


def test_a_failed_job_says_why(store):
    for detail, error, outcome in (
        ({"offline": "llm"}, "The AI is offline", "offline"),
        ({"code": "timeout"}, "It took too long", "timeout"),
        ({}, "Something broke", "error"),
    ):
        vo._pending.clear()
        job = {"user_id": "pat", "payload": {"_outcome": {"source": "chat", "t0": time.time(), "tools": []}},
               "created_at": time.time(), "started_at": time.time(), "completed_at": time.time()}
        vo.finish_job(job, failed=True, error=error, detail=detail)
        assert vo._pending[("pat", date.today().isoformat(), f"o:{outcome}")] == 1


def test_a_job_with_no_record_is_not_a_request_to_jarvis(store):
    vo.finish_job({"user_id": "raven_admin", "payload": {"_mission_id": 3}}, failed=True, error="x")
    assert not vo._pending


def test_the_worker_resumes_the_record_so_tool_calls_count(store):
    rec = vo.begin("chat")
    vo.set_user("pat")
    carried = vo.defer()
    vo._current.set(None)
    resumed = vo.resume({"_outcome": carried}, "pat")
    vo.note_tool("calendar_read", False)
    assert resumed is vo.current()
    vo.finish_job({"user_id": "pat", "payload": {"_outcome": carried}, "completed_at": time.time()}, failed=False)
    assert vo._pending[("pat", date.today().isoformat(), "t:calendar_read:fail")] == 1
    assert rec.done is False  # the handler's own copy was never counted twice


def test_tool_failure_markers():
    assert vo.tool_failed("Sorry, I couldn't complete that action: no oven")
    assert not vo.tool_failed("")
    assert not vo.tool_failed("Turned on the kitchen lights.")


def test_the_summary_adds_up_rates_tools_and_slow_hours(store, monkeypatch):
    monkeypatch.setattr(vo, "SLOW_MS", 1000)
    for ms, tools in ((300, [("light", True)]), (2500, [("set_oven", False)]), (5000, [])):
        rec = vo.Record(source="voice", user="pat", t0=time.time() - ms / 1000, tools=list(tools))
        vo.finish(rec)
    out = _run(vo.summary("pat", days=7))
    assert out["total"] == 3
    assert out["outcomes"] == {"ok": 2, "tool_failure": 1}
    assert out["tools"] == [{"tool": "set_oven", "failures": 1, "calls": 1}]
    assert out["latency"]["p50_ms"] in (4000, 8000)
    assert out["slow_hours"][0]["slow"] == 2
    assert out["per_day"][-1]["failed"] == 1


def test_nothing_recorded_reads_as_none(store):
    assert _run(vo.summary("nobody")) is None


def test_a_redis_failure_keeps_the_counts_for_the_next_flush(store, monkeypatch):
    class Broken:
        def pipeline(self, **kw):
            raise ConnectionError("down")
    monkeypatch.setattr(vo, "_client", Broken())
    rec = vo.begin("chat")
    vo.set_user("pat")
    vo.finish(rec)
    before = dict(vo._pending)
    assert _run(vo.flush()) == 0
    assert dict(vo._pending) == before


# --------------------------------------------------------------------------- the hooks


@pytest.fixture
def queue():
    from services.gateway.messaging import InferenceJobQueue

    q = InferenceJobQueue("redis://fake")
    q._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    return q


@pytest.mark.asyncio
async def test_a_queued_request_is_counted_when_its_job_completes(store, queue):
    rec = vo.begin("voice")
    vo.set_user("pat")
    job_id = await queue.enqueue_job("pat", {"query": "hello", "_outcome": vo.defer()})
    await queue.claim_job()
    await queue.complete_job(job_id, "Hi")
    assert vo._pending[("pat", date.today().isoformat(), "o:ok")] == 1
    assert vo._pending[("pat", date.today().isoformat(), "s:voice")] == 1
    assert rec.deferred


@pytest.mark.asyncio
async def test_a_failed_job_is_counted_and_a_mission_is_not(store, queue):
    rec = vo.begin("chat")
    vo.set_user("pat")
    job_id = await queue.enqueue_job("pat", {"query": "hello", "_outcome": vo.defer()})
    await queue.claim_job()
    await queue.fail_job(job_id, "It took too long", {"code": "llm_timeout"})
    assert vo._pending[("pat", date.today().isoformat(), "o:timeout")] == 1
    vo._pending.clear()
    vo._current.set(None)
    mission = await queue.enqueue_job("raven_admin", {"_mission_id": 4})
    await queue.claim_job()
    await queue.fail_job(mission, "broke")
    assert not vo._pending
    assert rec.user == "pat"


def test_the_chat_handler_does_not_count_a_refused_request(store, monkeypatch):
    from fastapi.testclient import TestClient

    from services.gateway import main as gateway_main

    monkeypatch.setattr(gateway_main.client_origin, "request_is_inside", lambda request: False)
    client = TestClient(gateway_main.app)
    resp = client.post("/api/chat", json={"query": "hello"})
    assert resp.status_code == 401
    assert not vo._pending


def test_a_tool_run_by_the_single_turn_path_is_counted(store, monkeypatch):
    from services.gateway import orchestrator

    async def fake_call(tool, payload, user, fetch):
        return "Sorry, I couldn't complete that action: the oven is not connected"

    monkeypatch.setattr(orchestrator.tool_cache, "call", fake_call)
    rec = vo.begin("voice")
    vo.set_user("pat")

    class Creds:
        user = "pat"

    asyncio.run(orchestrator._execute_single_tool("SetOven", {"payload": {"temp": 400}}, "preheat", Creds()))
    assert rec.tools and rec.tools[0][1] is False
    vo.finish(rec)
    assert vo._pending[("pat", date.today().isoformat(), "o:tool_failure")] == 1
    assert not any("400" in k[2] or "preheat" in k[2] for k in vo._pending)
