"""Raven missions through the real worker path, and priority preemption.

Every mission failed at start from 1c0670b4c on: the worker called
``register_raven_mission(mission_id)`` while the queue required a
``pause_event`` too. The worker tests used a fake queue and never ran a job
carrying ``_mission_id``, so nothing exercised the call. These tests run
missions through ``RavenWorker`` with the real ``InferenceJobQueue`` (on an
in-memory Redis), so a signature drift between the two fails here.
"""

import asyncio
import fnmatch
import json
import time
from contextlib import asynccontextmanager, suppress

import pytest
import redis.asyncio as redis_asyncio

import services.gateway.background_worker as bw
from services.gateway import agent_loop, messaging
from services.gateway.messaging import (
    _CLAIM_JOB,
    PREEMPTED,
    PRIORITY_CRITICAL,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    InferenceJobQueue,
    infer_priority_from_payload,
    mission_pause_event,
)

# --- In-memory Redis -------------------------------------------------------


class FakePubSub:
    def __init__(self, hub: "FakeRedis"):
        self.hub = hub
        self.channels: set[str] = set()
        self.queue: asyncio.Queue = asyncio.Queue()

    async def subscribe(self, *channels):
        self.channels.update(channels)
        self.hub.subscribers.append(self)

    async def unsubscribe(self, *channels):
        self.channels.difference_update(channels)

    async def get_message(self, ignore_subscribe_messages=True, timeout=None):
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout or 0.01)
        except TimeoutError:
            return None

    async def listen(self):
        while True:
            yield await self.queue.get()

    async def close(self):
        pass

    async def aclose(self):
        pass


class FakeRedis:
    """Just enough of redis.asyncio for the queue, the worker and the loop."""

    def __init__(self):
        self.strings: dict[str, str] = {}
        self.lists: dict[str, list] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []
        self.subscribers: list[FakePubSub] = []

    # strings
    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        if ex:
            self.ttls[key] = ex
        return True

    async def exists(self, key):
        return int(key in self.strings or key in self.lists or key in self.zsets)

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return key in self.strings

    async def delete(self, *keys):
        n = 0
        for key in keys:
            for store in (self.strings, self.lists, self.zsets):
                if store.pop(key, None) is not None:
                    n += 1
        return n

    async def type(self, key):
        if key in self.strings:
            return "string"
        if key in self.lists:
            return "list"
        if key in self.zsets:
            return "zset"
        return "none"

    async def eval(self, script, numkeys, *args):
        if script == _CLAIM_JOB:
            return self._claim(*args)
        # Otherwise only the queue's compare-and-delete script is used.
        assert "redis.call('del'" in script
        key, expected = args[0], args[1]
        if self.strings.get(key) == expected:
            del self.strings[key]
            return 1
        return 0

    def _claim(self, queue, processing, job_key, lease_key, job_id, read, new, ttl, lease, lease_ttl):
        # Python twin of messaging._CLAIM_JOB (test_claim_job_lua runs the real script).
        if job_id not in self.zsets.get(queue, {}):
            return 0
        current = self.strings.get(job_key)
        if current is None:
            if read == "":
                self.zsets[queue].pop(job_id)
                return -1
            return 0
        if current != read:
            return 0
        self.zsets[queue].pop(job_id)
        self.strings[job_key], self.ttls[job_key] = new, int(ttl)
        self.strings[lease_key], self.ttls[lease_key] = str(lease), int(lease_ttl)
        self.lists.setdefault(processing, []).append(job_id)
        return 1

    # lists
    async def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        return list(items[start:] if end == -1 else items[start:end + 1])

    async def lrem(self, key, count, value):
        items = self.lists.get(key, [])
        if value in items:
            items.remove(value)
            return 1
        return 0

    async def ltrim(self, key, start, end):
        return True

    # sorted sets
    async def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zrange(self, key, start, end):
        ordered = [m for m, _ in sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])]
        return ordered[start:] if end == -1 else ordered[start:end + 1]

    async def zrem(self, key, member):
        return int(self.zsets.get(key, {}).pop(member, None) is not None)

    # pub/sub
    async def publish(self, channel, message):
        self.published.append((channel, message))
        for sub in self.subscribers:
            if any(fnmatch.fnmatch(channel, c) for c in sub.channels):
                sub.queue.put_nowait({"type": "message", "channel": channel, "data": message})
        return 1

    def pubsub(self):
        return FakePubSub(self)

    async def close(self):
        pass

    async def aclose(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


# --- Identity and the rest of the world --------------------------------------


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body or {}

    async def json(self):
        return self._body

    async def text(self):
        return json.dumps(self._body)


class FakeHttp:
    def __init__(self):
        self.patches: list[tuple[str, dict]] = []

    async def patch(self, url, json=None, headers=None, **kw):
        self.patches.append((url, json or {}))
        return FakeResponse()

    async def get(self, url, headers=None, **kw):
        return FakeResponse(200, {"workspace_id": None})

    async def post(self, url, json=None, headers=None, **kw):
        return FakeResponse()

    def mission_statuses(self, mission_id):
        return [body.get("status") for url, body in self.patches if url.endswith(f"/missions/{mission_id}")]

    def last_patch(self, mission_id):
        return [body for url, body in self.patches if url.endswith(f"/missions/{mission_id}")][-1]


@pytest.fixture
def world(monkeypatch):
    fake_redis = FakeRedis()
    http = FakeHttp()

    @asynccontextmanager
    async def shared_client():
        yield http

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: fake_redis)
    monkeypatch.setattr(agent_loop, "_redis_cmd", None)
    monkeypatch.setattr(bw, "_shared_http_client", shared_client)
    monkeypatch.setattr(bw, "_mission_deadline", nothing)
    monkeypatch.setattr(bw, "_capture_mission_artifacts", nothing)
    monkeypatch.setattr(bw.notices, "mission_finished", nothing)
    monkeypatch.setattr(bw, "push_mission", nothing)
    monkeypatch.setattr(bw.RavenWorker, "_dispatch_chained_missions", nothing)
    monkeypatch.setattr(agent_loop, "PAUSE_POLL_SECONDS", 0.01)
    # Module-global asyncio primitives; every test has a fresh event loop.
    monkeypatch.setattr(bw, "TIER2_SEMAPHORE", asyncio.Semaphore(3))
    monkeypatch.setattr(bw, "TIER3_LOCK", asyncio.Lock())
    monkeypatch.setattr(messaging, "_raven_missions", {})
    monkeypatch.setattr(messaging, "_preemptors", {})

    queue = InferenceJobQueue("redis://fake")
    queue._redis = fake_redis
    worker = bw.RavenWorker()
    worker.job_queue = queue
    worker.is_running = True
    worker._last_reap = time.time()
    return worker, queue, fake_redis, http


async def _wait_until(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)


async def _run_loop(worker):
    return asyncio.create_task(worker._inference_loop())


async def _stop(worker, supervisor):
    worker.is_running = False
    supervisor.cancel()
    with suppress(asyncio.CancelledError):
        await supervisor


def _mission_payload(mission_id, query="Build a small game"):
    return {"query": query, "model": "m", "creds": {"user": "alice"}, "_mission_id": mission_id}


# --- A mission starts, runs and finishes ----------------------------------------


async def test_a_mission_runs_through_the_real_worker_and_queue(world, monkeypatch):
    worker, queue, _redis, http = world
    seen = {}

    async def orchestration(payload, chunk_callback=None):
        # The worker registered this mission, with its own pause event, before
        # running it: what the TypeError used to stop.
        seen["event"] = mission_pause_event(payload["_mission_id"])
        return "Mission complete. Built game.py and a README; both run cleanly."

    monkeypatch.setattr(bw, "process_full_orchestration", orchestration)
    job_id = await queue.enqueue_job("alice", _mission_payload(41))
    supervisor = await _run_loop(worker)
    try:
        await _wait_until(lambda: "completed" in http.mission_statuses(41))
    finally:
        await _stop(worker, supervisor)

    assert isinstance(seen["event"], asyncio.Event) and not seen["event"].is_set()
    assert http.mission_statuses(41) == ["executing", "completed"]
    assert (await queue.get_job_status(job_id))["status"] == "completed"
    # Unregistered once it finished.
    assert mission_pause_event(41) is None
    assert await queue.get_current_raven_mission() is None


async def test_a_mission_whose_model_failed_is_marked_failed_not_completed(world, monkeypatch):
    worker, queue, _redis, http = world

    async def orchestration(payload, chunk_callback=None):
        return (
            "SYSTEM ERROR: Inference failed after multiple retries. Detail: Ollama stream "
            "HTTP 500: Internal Server Error. Please check the LLM provider status."
        )

    monkeypatch.setattr(bw, "process_full_orchestration", orchestration)
    await queue.enqueue_job("alice", _mission_payload(42))
    supervisor = await _run_loop(worker)
    try:
        await _wait_until(lambda: len(http.mission_statuses(42)) == 2)
    finally:
        await _stop(worker, supervisor)

    final = http.last_patch(42)
    assert final["status"] == "failed"
    assert final["result"].startswith("SYSTEM ERROR")


async def test_a_killed_mission_stops_and_stays_failed(world, monkeypatch):
    worker, queue, fake_redis, http = world
    started = asyncio.Event()

    async def orchestration(payload, chunk_callback=None):
        started.set()
        await asyncio.Event().wait()  # runs until cancelled

    monkeypatch.setattr(bw, "process_full_orchestration", orchestration)
    await queue.enqueue_job("alice", _mission_payload(43))
    supervisor = await _run_loop(worker)
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        # The kill monitor (which never started before) cancels the run.
        await _wait_until(lambda: any(s.channels for s in fake_redis.subscribers))
        await fake_redis.publish("raven:mission:kill:43", "KILL")
        await _wait_until(lambda: len(http.mission_statuses(43)) == 2)
    finally:
        await _stop(worker, supervisor)

    final = http.last_patch(43)
    assert final["status"] == "failed"
    assert final["result"] == "Mission aborted by user."


# --- Preemption --------------------------------------------------------------


async def test_an_urgent_job_pauses_the_running_mission_until_it_is_done(world, monkeypatch):
    worker, queue, fake_redis, http = world
    mission_started = asyncio.Event()
    release_mission = asyncio.Event()
    release_chat = asyncio.Event()
    during_chat = {}

    async def orchestration(payload, chunk_callback=None):
        if payload.get("_mission_id"):
            mission_started.set()
            await release_mission.wait()
            return "Mission complete. Built game.py; it runs cleanly."
        during_chat["event_set"] = mission_pause_event(44).is_set()
        during_chat["flag"] = await fake_redis.get("raven:mission:pause:44")
        await release_chat.wait()
        return "The lights are off."

    monkeypatch.setattr(bw, "process_full_orchestration", orchestration)
    await queue.enqueue_job("alice", _mission_payload(44))
    supervisor = await _run_loop(worker)
    try:
        await asyncio.wait_for(mission_started.wait(), timeout=3)
        chat_id = await queue.enqueue_job(
            "alice", {"query": "turn off the kitchen lights", "creds": {"user": "alice"}, "voice_context": {"room": "x"}}
        )
        assert (await queue.get_job_status(chat_id))["priority"] == PRIORITY_HIGH
        await _wait_until(lambda: "event_set" in during_chat)
        assert during_chat == {"event_set": True, "flag": PREEMPTED}

        release_chat.set()
        await _wait_until(lambda: not mission_pause_event(44).is_set())
        assert await fake_redis.get("raven:mission:pause:44") is None
        assert ("raven:mission:pause:44", "RESUMED") in fake_redis.published
    finally:
        release_chat.set()
        release_mission.set()
        await _wait_until(lambda: "completed" in http.mission_statuses(44))
        await _stop(worker, supervisor)


async def test_a_normal_chat_does_not_pause_the_mission(world):
    _worker, queue, fake_redis, _http = world
    event = asyncio.Event()
    await queue.register_raven_mission(45, event)
    assert await queue.request_preemption("job-1", PRIORITY_NORMAL) is None
    assert not event.is_set()
    assert "raven:mission:pause:45" not in fake_redis.strings


async def test_two_urgent_jobs_resume_the_mission_only_after_both(world):
    _worker, queue, fake_redis, _http = world
    event = asyncio.Event()
    await queue.register_raven_mission(46, event)
    assert await queue.request_preemption("a", PRIORITY_HIGH) == "46"
    assert await queue.request_preemption("b", PRIORITY_CRITICAL) == "46"
    await queue.release_preemption("a")
    assert event.is_set() and fake_redis.strings["raven:mission:pause:46"] == PREEMPTED
    await queue.release_preemption("b")
    assert not event.is_set() and "raven:mission:pause:46" not in fake_redis.strings


async def test_a_users_own_pause_survives_a_preemption(world):
    _worker, queue, fake_redis, _http = world
    event = asyncio.Event()
    await queue.register_raven_mission(47, event)
    await fake_redis.set("raven:mission:pause:47", "PAUSED")
    await queue.request_preemption("a", PRIORITY_HIGH)
    assert fake_redis.strings["raven:mission:pause:47"] == "PAUSED"
    await queue.release_preemption("a")
    assert fake_redis.strings["raven:mission:pause:47"] == "PAUSED"


async def test_a_mission_waiting_for_the_lock_is_not_the_one_preempted(world, monkeypatch):
    worker, queue, _redis, http = world
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def orchestration(payload, chunk_callback=None):
        if payload["_mission_id"] == 48:
            first_started.set()
            await release_first.wait()
        return "Mission complete. Built game.py; it runs cleanly."

    monkeypatch.setattr(bw, "process_full_orchestration", orchestration)
    await queue.enqueue_job("alice", _mission_payload(48))
    supervisor = await _run_loop(worker)
    try:
        await asyncio.wait_for(first_started.wait(), timeout=3)
        await queue.enqueue_job("alice", _mission_payload(49))
        await _wait_until(lambda: len(queue._redis.lists.get(queue.PROCESSING_KEY, [])) == 2)
        await asyncio.sleep(0.05)
        assert await queue.get_current_raven_mission() == "48"
    finally:
        release_first.set()
        await _wait_until(lambda: "completed" in http.mission_statuses(49))
        await _stop(worker, supervisor)


async def test_the_agent_loop_waits_on_the_pause_event(world, monkeypatch):
    _worker, queue, _redis, _http = world
    event = asyncio.Event()
    await queue.register_raven_mission(50, event)

    async def flag_never(key):
        return False

    monkeypatch.setattr(agent_loop, "_redis_flag_active", flag_never)
    event.set()
    events = []

    async def stream(kind, text):
        events.append(text)

    waiter = asyncio.create_task(agent_loop._await_mission_resume(50, stream))
    await asyncio.sleep(0.1)
    assert not waiter.done()
    event.clear()
    assert await asyncio.wait_for(waiter, timeout=2) is False
    assert events[-1] == "Mission resumed."


async def test_a_reused_mission_id_never_starts_paused(world):
    _worker, queue, _redis, _http = world
    old = asyncio.Event()
    await queue.register_raven_mission(51, old)
    await queue.request_preemption("a", PRIORITY_HIGH)
    await queue.unregister_raven_mission(51)
    fresh = asyncio.Event()
    await queue.register_raven_mission(51, fresh)
    assert not fresh.is_set()


# --- The rest of what the preemption commit touched ---------------------------


async def test_a_tier_two_job_cancelled_while_waiting_raises_its_cancellation(world, monkeypatch):
    worker, _queue, _redis, _http = world
    monkeypatch.setattr(bw, "TIER2_SEMAPHORE", asyncio.Semaphore(0))
    job = {"job_id": "j1", "user_id": "alice", "payload": {"query": "hello", "creds": {"user": "alice"}}}
    task = asyncio.create_task(worker._process_inference_job(job))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_heartbeat_keeps_the_job_record_alive(world):
    _worker, queue, fake_redis, _http = world
    job_id = await queue.enqueue_job("alice", _mission_payload(52))
    fake_redis.ttls.pop(f"{queue.JOB_PREFIX}{job_id}")
    await queue.heartbeat_job(job_id)
    assert fake_redis.ttls[f"{queue.JOB_PREFIX}{job_id}"] == queue.DEFAULT_TTL_SECONDS


async def test_jobs_left_in_the_old_fifo_list_are_moved_into_the_priority_queue(world):
    _worker, queue, fake_redis, _http = world
    job = {"job_id": "old-1", "payload": {"query": "hi"}, "created_at": time.time(), "status": "queued"}
    await fake_redis.set(f"{queue.JOB_PREFIX}old-1", json.dumps(job))
    await fake_redis.rpush(queue.QUEUE_KEY, "old-1")
    claimed = await queue.claim_job()
    assert claimed is not None and claimed["job_id"] == "old-1"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("turn off the kitchen lights", PRIORITY_HIGH),
        ("set a timer for ten minutes", PRIORITY_HIGH),
        ("emergency: smoke in the garage", PRIORITY_CRITICAL),
        # Substring matches used to make these urgent.
        ("can you help me write an essay", PRIORITY_NORMAL),
        ("display the block schedule", PRIORITY_NORMAL),
        ("how warm is a farm in winter", PRIORITY_NORMAL),
    ],
)
def test_priority_inference_matches_whole_words(query, expected):
    assert infer_priority_from_payload({"query": query}) == expected


def test_missions_are_low_priority():
    assert infer_priority_from_payload({"query": "turn on the lights", "_mission_id": 3}) == PRIORITY_LOW
