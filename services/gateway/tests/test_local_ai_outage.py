"""The local AI (the GPU box) being off: fail fast, say it plainly, keep work, recover.

Measured before this: a question that needed the model took 37 s and came back
HTTP 200 "SUCCESS" with "Cannot connect to host <box>:11434 ... [Connect call
failed ('<ip>', 11434)]" as the answer. Host names here are fake.
"""
from __future__ import annotations

import asyncio
import errno
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import fakeredis
import pytest

from services.common.backend_health import BackendUnavailable
from services.gateway import admin_alerts, local_ai, orchestrator
from services.gateway import background_worker as bw
from services.gateway import main as gateway_main
from services.gateway.messaging import InferenceJobQueue, JobStatus
from services.gateway.tests.test_chat_storage_routing import _make_session, client, get_test_settings

FAKE_BOX = "http://gpu-box.test:11434"
LEAKS = ("gpu-box", "11434", "Cannot connect", "Connect call failed", "10.9.9.9")


def _connector_error() -> aiohttp.ClientConnectorError:
    key = aiohttp.client_reqrep.ConnectionKey("gpu-box.test", 11434, False, True, None, None, None)
    return aiohttp.ClientConnectorError(key, OSError(errno.EHOSTUNREACH, "Connect call failed ('10.9.9.9', 11434)"))


def _no_leak(text: str) -> None:
    for leak in LEAKS:
        assert leak not in text, f"{leak!r} reached the person: {text}"


@pytest.fixture
def box_down():
    local_ai.LLM.set_url(FAKE_BOX)
    local_ai.LLM.mark_down("ClientConnectorError: Cannot connect to host gpu-box.test:11434")
    yield local_ai.LLM
    local_ai.take_waiting()


# ------------------------------------------------------------ the provider


class _RefusingClient:
    """Every request fails to connect; counts how many were tried."""

    def __init__(self):
        self.calls = 0

    def _fail(self, *a, **kw):
        self.calls += 1
        raise _connector_error()

    get = post = _fail


async def test_the_provider_opens_the_breaker_and_then_fails_fast(monkeypatch):
    from services.gateway.llm_providers import OllamaProvider

    fake = _RefusingClient()

    class _Ctx:
        async def __aenter__(self):
            return fake

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Ctx())
    provider = OllamaProvider(base_url=FAKE_BOX, timeout=600)
    assert provider.timeout.sock_connect == local_ai.SETTINGS.connect_timeout

    with pytest.raises(BackendUnavailable):
        await provider.generate("assistant", [{"role": "user", "content": "hi"}])
    assert local_ai.LLM.is_down()
    first = fake.calls
    assert first == 1  # the slot check could not connect: no "proceeding anyway"

    started = time.monotonic()
    with pytest.raises(BackendUnavailable):
        await provider.generate("assistant", [{"role": "user", "content": "hi again"}])
    assert time.monotonic() - started < 0.5
    assert fake.calls == first  # nothing was tried while it is known to be down


async def test_a_single_turn_against_an_offline_box_is_not_retried(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    async def offline(payload, use_chat=True):
        calls["n"] += 1
        raise local_ai.LLM.unavailable()

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(orchestrator, "call_ollama", offline)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda x: "guide")
    monkeypatch.setattr(orchestrator.asyncio, "sleep", no_sleep)

    with pytest.raises(orchestrator.LocalAIOffline) as raised:
        await orchestrator._single_turn_inference(
            query="what is a covenant?", model="assistant", system_prompt="sys", rag_context="", history=[],
            creds=gateway_main.ResolvedCredentials(user="alex"),
        )
    assert calls["n"] == 1 and slept == []
    assert str(raised.value) == local_ai.CHAT_OFFLINE


async def test_a_transient_error_is_still_retried(monkeypatch):
    calls = {"n": 0}

    async def flaky(payload, use_chat=True):
        calls["n"] += 1
        if calls["n"] == 1:
            raise aiohttp.SocketTimeoutError("Timeout on reading data from socket")
        return {"message": {"content": "Here you go."}}

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(orchestrator, "call_ollama", flaky)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda x: "guide")
    monkeypatch.setattr(orchestrator.asyncio, "sleep", no_sleep)
    answer = await orchestrator._single_turn_inference(
        query="hello", model="assistant", system_prompt="sys", rag_context="", history=[],
        creds=gateway_main.ResolvedCredentials(user="alex"),
    )
    assert answer == "Here you go." and calls["n"] == 2
    assert not local_ai.LLM.is_down()


async def test_the_vram_check_is_skipped_while_down(box_down, monkeypatch):
    from services.gateway import agent_loop

    def must_not_call(*a, **kw):
        raise AssertionError("the VRAM check called the box while it is known to be down")

    monkeypatch.setattr(agent_loop, "shared_http_client", must_not_call)
    params = await agent_loop.get_vram_safe_params("assistant", {"llm_local_url": FAKE_BOX,
                                                                  "llm_local_max_ctx": "8192"})
    assert params["num_ctx"] == 8192


# ------------------------------------------------------------ the chat handler


@pytest.fixture
def queued(monkeypatch):
    payloads: list[dict] = []
    queue = MagicMock()

    async def enqueue_job(user_id, job_payload):
        payloads.append(job_payload)
        return "job-1"

    queue.enqueue_job = enqueue_job
    monkeypatch.setattr(gateway_main, "job_queue", queue)
    monkeypatch.setattr(gateway_main, "select_system_instruction_for_query", lambda q, m: "# System")
    monkeypatch.setattr(gateway_main.engine, "is_fast_path", lambda *a: False)
    monkeypatch.setattr(gateway_main.engine, "classify", lambda q: ("general", 0.1))
    return payloads


def _ask(monkeypatch, path="/api/chat", **body):
    sess = _make_session(get_test_settings(), iter([]))
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    with patch("aiohttp.ClientSession", return_value=sess), \
            patch("services.gateway.main.shared_http_client", return_value=sess):
        started = time.monotonic()
        resp = client.post(path, json={"query": "explain the parable of the sower", "stream": False, **body},
                           headers={"Authorization": "Bearer test-token"})
        return resp, time.monotonic() - started


def test_chat_answers_at_once_with_a_plain_offline_message(box_down, monkeypatch, queued):
    resp, took = _ask(monkeypatch)
    assert took < 2.0
    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == "ERROR"
    assert data["message"]["content"] == local_ai.CHAT_OFFLINE  # what a chat bubble shows
    assert data["error_code"] == "local_ai_offline" and data["error_id"] == box_down.error_id
    assert resp.headers.get("Retry-After")
    _no_leak(resp.text)
    assert queued == []  # nothing queued to wait out a retry ladder


def test_openai_clients_get_the_same_sentence_in_their_shape(box_down, monkeypatch, queued):
    resp, _took = _ask(monkeypatch, path="/v1/chat/completions",
                       messages=[{"role": "user", "content": "explain the parable of the sower"}])
    assert resp.status_code == 503
    data = resp.json()
    assert data["error"]["message"] == local_ai.CHAT_OFFLINE
    assert data["error"]["type"] == "local_ai_offline"
    _no_leak(resp.text)


def test_the_typed_asker_is_told_when_it_is_back(box_down, monkeypatch, queued):
    _ask(monkeypatch)
    waiting = local_ai.take_waiting()
    assert waiting  # whoever the test token resolves to
    notice = local_ai.back_notice(waiting[0], "abc12345")
    assert notice["title"] == local_ai.BACK_TITLE and notice["category"] == "jobs"


def test_when_up_the_question_is_queued_as_before(monkeypatch, queued):
    queue = gateway_main.job_queue
    queue.get_job_status = AsyncMock(return_value={"status": "completed", "result": "A sower went out."})
    queue.get_chunks = AsyncMock(return_value=[])
    resp, _took = _ask(monkeypatch)
    assert resp.status_code == 200 and queued


def test_a_failed_job_returns_its_status_and_plain_words(monkeypatch, queued):
    queue = gateway_main.job_queue
    queue.get_job_status = AsyncMock(return_value={
        "status": JobStatus.FAILED, "error": local_ai.CHAT_OFFLINE,
        "error_detail": {"code": "local_ai_offline", "error_id": "abcd1234", "http_status": 503,
                         "offline": "local_ai"},
    })
    resp, _took = _ask(monkeypatch)
    assert resp.status_code == 503
    assert resp.json()["message"]["content"] == local_ai.CHAT_OFFLINE
    assert resp.json()["error_id"] == "abcd1234"


def test_the_status_endpoint_feeds_the_banner(box_down, monkeypatch):
    monkeypatch.setattr(gateway_main, "_require_identity_from_request",
                        AsyncMock(return_value={"user": "alex", "is_admin": False}))
    data = asyncio.run(gateway_main.local_ai_status(object()))
    assert data["local_ai"]["offline"] is True
    assert data["local_ai"]["message"] == local_ai.CHAT_OFFLINE
    assert "weather" in data["local_ai"]["still_working"]
    _no_leak(json.dumps(data))


# ------------------------------------------------------------ the worker


def test_describe_failure_never_shows_exception_text():
    offline = local_ai.LLM.unavailable()
    message, detail = bw.describe_failure(orchestrator.LocalAIOffline(offline), {})
    assert message == local_ai.CHAT_OFFLINE and detail["http_status"] == 503
    voice, _ = bw.describe_failure(offline, {"voice_context": {"device": "kitchen"}})
    assert voice == local_ai.VOICE_OFFLINE
    busy, detail = bw.describe_failure(orchestrator.InferenceUnavailable("Ollama HTTP 503: queue_timeout"), {})
    assert busy == local_ai.MODEL_TROUBLE and detail["error_id"]
    other, detail = bw.describe_failure(RuntimeError("Cannot connect to host rag.test:8004"), {})
    assert "rag.test" not in other and detail["http_status"] == 500


@pytest.fixture
async def real_queue():
    queue = InferenceJobQueue("redis://unused")
    queue._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield queue
    await queue._redis.aclose()


async def test_a_parked_job_goes_back_in_line_without_losing_an_attempt(real_queue):
    job_id = await real_queue.enqueue_job("alex", {"query": "build it", "_mission_id": 7})
    claimed = await real_queue.claim_job()
    assert claimed["job_id"] == job_id and claimed["attempts"] == 1

    assert await real_queue.requeue_job(job_id, note=local_ai.MISSION_WAITING)
    job = await real_queue.get_job_status(job_id)
    assert job["status"] == JobStatus.QUEUED and job["attempts"] == 0
    assert job["waiting"] == local_ai.MISSION_WAITING
    assert (await real_queue.peek_job())["job_id"] == job_id
    assert await real_queue._redis.llen(real_queue.PROCESSING_KEY) == 0

    await real_queue._redis.expire(f"{real_queue.JOB_PREFIX}{job_id}", 5)
    assert await real_queue.touch_queued_jobs() == 1
    assert await real_queue._redis.ttl(f"{real_queue.JOB_PREFIX}{job_id}") > 3000


def _worker(queue) -> bw.RavenWorker:
    worker = bw.RavenWorker.__new__(bw.RavenWorker)
    worker.job_queue = queue
    worker._llm_paused = False
    worker._last_touch = 0.0
    return worker


async def test_a_mission_cut_off_by_the_outage_waits_instead_of_failing(real_queue, monkeypatch):
    job_id = await real_queue.enqueue_job("alex", {"query": "raven build the site", "_mission_id": 7, "model": "coder"})
    job = await real_queue.claim_job()
    patches: list[dict] = []

    class _Client:
        async def patch(self, url, json=None, headers=None):
            patches.append(json)
            return MagicMock(status=200)

    class _Ctx:
        async def __aenter__(self):
            return _Client()

        async def __aexit__(self, *exc):
            return False

    async def offline(payload, chunk_callback=None):
        raise local_ai.LLM.unavailable()

    finished = AsyncMock()
    monkeypatch.setattr(bw, "_shared_http_client", lambda: _Ctx())
    monkeypatch.setattr(bw, "process_full_orchestration", offline)
    monkeypatch.setattr(bw.notices, "mission_finished", finished)
    monkeypatch.setattr(bw, "_clear_kill_flag", AsyncMock())
    monkeypatch.setattr(bw, "_mission_deadline", AsyncMock(return_value=None))
    worker = _worker(real_queue)
    worker._preemptible_mission = lambda mid: _null_ctx()
    worker._job_heartbeat = lambda jid: asyncio.sleep(3600)

    await worker._process_inference_job(job)

    stored = await real_queue.get_job_status(job_id)
    assert stored["status"] == JobStatus.QUEUED  # not failed, and still queued: no work lost
    assert {"status": "queued", "result": local_ai.MISSION_WAITING} in patches
    assert not any(p.get("status") == "failed" for p in patches)
    finished.assert_not_awaited()


class _null_ctx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


async def test_the_worker_holds_missions_while_down_and_resumes_after(real_queue, box_down, monkeypatch):
    monkeypatch.setattr(orchestrator, "get_all_settings", AsyncMock(return_value={"active_llm_provider": "ollama"}))
    monkeypatch.setattr(bw.asyncio, "sleep", AsyncMock())
    await real_queue.enqueue_job("alex", {"query": "raven tidy the repo", "_mission_id": 9})
    worker = _worker(real_queue)

    assert await worker._local_ai_paused() is True
    assert await real_queue.queued_count() == 1  # left in line, not claimed

    sent = AsyncMock(return_value=True)
    monkeypatch.setattr("services.common.notify.send", sent)
    local_ai.remember_waiting("alex")
    box_down.mark_up("probe answered")
    assert await worker._local_ai_paused() is False
    sent.assert_awaited_once()
    assert sent.await_args.args[0]["user"] == "alex"


async def test_a_question_at_the_head_is_still_claimed_while_down(real_queue, box_down, monkeypatch):
    monkeypatch.setattr(orchestrator, "get_all_settings", AsyncMock(return_value={"active_llm_provider": "ollama"}))
    await real_queue.enqueue_job("alex", {"query": "what is grace?", "voice_context": {"device": "den"}})
    assert await _worker(real_queue)._local_ai_paused() is False  # claimed, then told "offline" at once


async def test_a_cloud_provider_is_not_held_back(real_queue, box_down, monkeypatch):
    monkeypatch.setattr(orchestrator, "get_all_settings", AsyncMock(return_value={"active_llm_provider": "openrouter"}))
    await real_queue.enqueue_job("alex", {"query": "raven tidy the repo", "_mission_id": 9})
    assert await _worker(real_queue)._local_ai_paused() is False


# ------------------------------------------------------------ admin alert


def test_admins_hear_once_when_it_goes_down_and_once_when_back():
    state: dict = {}
    down = {"state": "down", "since": time.time() - 120, "error_id": "abcd1234"}
    first = admin_alerts.local_ai_transitions(state, down)
    assert [n["title"] for n in first] == ["Local AI offline"]
    assert "abcd1234" in first[0]["body"]
    assert admin_alerts.local_ai_transitions(state, down) == []  # not repeated
    back = admin_alerts.local_ai_transitions(state, {"state": "up"})
    assert [n["title"] for n in back] == ["Local AI back"]
    assert admin_alerts.local_ai_transitions(state, {"state": "up"}) == []
    assert first[0]["tag"] == back[0]["tag"] == "admin:local-ai"


# ------------------------------------------------------------ arcade


def test_the_arcade_poll_fails_fast_and_does_not_log_each_time(monkeypatch, caplog):
    monkeypatch.setattr(gateway_main, "ALPACA_ARCADE_URL", "http://gpu-box.test:5001")
    monkeypatch.setattr(gateway_main, "_require_identity_from_request",
                        AsyncMock(return_value={"user": "alex", "is_admin": False}))
    local_ai.ARCADE.mark_down("refused")

    class _Must:
        def get(self, *a, **kw):
            raise AssertionError("called the arcade while it is known to be down")

    class _Ctx:
        async def __aenter__(self):
            return _Must()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Ctx())
    caplog.set_level("WARNING")
    caplog.clear()  # the one "unreachable" line from marking it down above
    for _ in range(5):
        out = asyncio.run(gateway_main.proxy_arcade_live(object()))
        assert out == {"success": False, "arcade_available": False, "rooms": []}
    assert not [r for r in caplog.records if "arcade" in r.getMessage()]
    assert gateway_main._arcade_error(local_ai.ARCADE.unavailable()) == local_ai.ARCADE_OFFLINE


# ------------------------------------------------------------ Guardian photo check


def test_a_photo_check_that_cannot_run_passes_retry_after_to_the_phone(monkeypatch):
    """The gateway answers the phone at once with telemetry's 503 and its Retry-After,
    so the picture stays queued on the phone and is not sent again until then."""
    class _Resp:
        status = 503

        def __init__(self):
            self.headers = {"Retry-After": "300"}

        async def json(self, content_type=None):
            return {"detail": "The photo check is not available right now; keep the picture and try again later.",
                    "scanned": False, "retry_after_s": 300, "offline": "local_model"}

    class _Client:
        async def post(self, url, json=None, headers=None):
            assert url.endswith("/api/telemetry/media/scan")
            return _Resp()

    class _Ctx:
        async def __aenter__(self):
            return _Client()

        async def __aexit__(self, *exc):
            return False

    class _Req:
        async def json(self):
            return {"media_type": "image", "file_name": "p.jpg", "image_b64": "eA=="}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", AsyncMock(return_value={"user": "kid"}))
    monkeypatch.setattr(gateway_main, "borrow_http_client", lambda: _Ctx())
    resp = asyncio.run(gateway_main.guardian_media_scan(_Req()))
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "300"
    assert json.loads(resp.body)["retry_after_s"] == 300
