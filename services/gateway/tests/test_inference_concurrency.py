"""Tier 2 inference can run while Tier 3 is active (G5).

The worker used to claim a job and await it in the same loop, so
``TIER2_SEMAPHORE`` never allowed a second job: one Raven mission blocked every
chat request behind it. These tests pin the dispatcher behaviour: claims stay
sequential and safe, each job runs in its own task, Tier 3 is still exclusive,
and Tier 2 fills its three slots alongside a running mission.
"""

import asyncio
import time
from contextlib import suppress
from unittest.mock import AsyncMock

import pytest

import services.gateway.background_worker as bw


class FakeJobQueue:
    def __init__(self, jobs):
        self._jobs = list(jobs)
        self.claimed = []
        self.completed = []
        self.failed = []

    async def reclaim_expired_jobs(self):
        return 0

    async def claim_job(self):
        if not self._jobs:
            return None
        job = self._jobs.pop(0)
        self.claimed.append(job["job_id"])
        return job

    async def heartbeat_job(self, job_id):
        pass

    async def push_chunk(self, job_id, chunk):
        pass

    async def complete_job(self, job_id, result):
        self.completed.append((job_id, result))

    async def fail_job(self, job_id, error):
        self.failed.append((job_id, error))


class RecordingOrchestration:
    def __init__(self):
        self.releases: dict[str, asyncio.Event] = {}
        self.started_events: dict[str, asyncio.Event] = {}
        self.started: list[str] = []
        self.active = 0
        self.max_active = 0

    def event_pair(self, query: str) -> tuple[asyncio.Event, asyncio.Event]:
        release = self.releases.setdefault(query, asyncio.Event())
        started = self.started_events.setdefault(query, asyncio.Event())
        return release, started

    async def __call__(self, payload, chunk_callback=None):
        query = payload["query"]
        release, started = self.event_pair(query)
        self.started.append(query)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        started.set()
        try:
            await release.wait()
        finally:
            self.active -= 1
        if payload.get("_fail"):
            raise RuntimeError("inference failed")
        return f"answer:{query}"


def _job(job_id: str, query: str, user: str = "alice", **extra) -> dict:
    payload = {"query": query, "model": "some-model", "creds": {"user": user}}
    payload.update(extra)
    return {"job_id": job_id, "user_id": user, "payload": payload}


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)


async def _start(monkeypatch, queue, orchestration):
    monkeypatch.setattr(bw, "process_full_orchestration", orchestration)
    monkeypatch.setattr(bw, "get_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(bw, "update_history", AsyncMock())
    worker = bw.RavenWorker()
    worker.job_queue = queue
    worker.is_running = True
    worker._last_reap = time.time()
    supervisor = asyncio.create_task(worker._inference_loop())
    return worker, supervisor


async def _stop(worker, supervisor) -> None:
    worker.is_running = False
    supervisor.cancel()
    with suppress(asyncio.CancelledError):
        await supervisor


@pytest.fixture(autouse=True)
def _fresh_tier_locks(monkeypatch):
    # The locks are module globals, and pytest-asyncio gives every test a fresh
    # event loop. A semaphore that has waited on an older loop would fail.
    monkeypatch.setattr(bw, "TIER2_SEMAPHORE", asyncio.Semaphore(3))
    monkeypatch.setattr(bw, "TIER3_LOCK", asyncio.Lock())


async def test_chat_runs_while_a_raven_job_is_active(monkeypatch):
    orchestration = RecordingOrchestration()
    raven_release, raven_started = orchestration.event_pair("raven, audit the workshop")
    chat_release, chat_started = orchestration.event_pair("turn on the porch light")
    queue = FakeJobQueue([
        _job("raven-1", "raven, audit the workshop"),
        _job("chat-1", "turn on the porch light"),
    ])
    worker, supervisor = await _start(monkeypatch, queue, orchestration)
    try:
        await asyncio.wait_for(raven_started.wait(), timeout=2)
        await asyncio.wait_for(chat_started.wait(), timeout=2)
        assert queue.claimed == ["raven-1", "chat-1"]
        assert queue.completed == []

        chat_release.set()
        await _wait_until(lambda: len(queue.completed) == 1)
        assert queue.completed[0][0] == "chat-1"
        assert not raven_release.is_set()

        raven_release.set()
        await _wait_until(lambda: len(queue.completed) == 2)
    finally:
        raven_release.set()
        chat_release.set()
        await _stop(worker, supervisor)


async def test_three_tier_two_jobs_run_alongside_a_raven_job(monkeypatch):
    orchestration = RecordingOrchestration()
    raven_release, raven_started = orchestration.event_pair("raven, audit the workshop")
    chats = [f"chat {i}" for i in range(3)]
    chat_started = []
    for query in chats:
        _, started = orchestration.event_pair(query)
        chat_started.append(started)
    queue = FakeJobQueue(
        [_job("raven-1", "raven, audit the workshop")]
        + [_job(f"chat-{i}", query) for i, query in enumerate(chats)]
    )
    worker, supervisor = await _start(monkeypatch, queue, orchestration)
    try:
        await asyncio.wait_for(raven_started.wait(), timeout=2)
        for started in chat_started:
            await asyncio.wait_for(started.wait(), timeout=2)

        assert orchestration.max_active == 4

        raven_release.set()
        for release in orchestration.releases.values():
            release.set()
        await _wait_until(lambda: len(queue.completed) == 4)
    finally:
        raven_release.set()
        for release in orchestration.releases.values():
            release.set()
        await _stop(worker, supervisor)


async def test_tier_two_fills_three_slots_and_holds_the_fourth(monkeypatch):
    orchestration = RecordingOrchestration()
    jobs = []
    for i in range(4):
        query = f"chat {i}"
        orchestration.event_pair(query)
        jobs.append(_job(f"chat-{i}", query))
    queue = FakeJobQueue(jobs)
    worker, supervisor = await _start(monkeypatch, queue, orchestration)
    try:
        await _wait_until(lambda: len(orchestration.started) >= 3)
        await asyncio.sleep(0.1)

        assert len(orchestration.started) == 3
        assert "chat 3" not in orchestration.started
        assert orchestration.max_active == 3

        for i in range(4):
            orchestration.releases[f"chat {i}"].set()
        await _wait_until(lambda: len(queue.completed) == 4)
    finally:
        for event in orchestration.releases.values():
            event.set()
        await _stop(worker, supervisor)


async def test_raven_jobs_stay_serialized(monkeypatch):
    orchestration = RecordingOrchestration()
    first_release, first_started = orchestration.event_pair("raven, audit one")
    second_release, second_started = orchestration.event_pair("raven, audit two")
    queue = FakeJobQueue([
        _job("raven-1", "raven, audit one"),
        _job("raven-2", "raven, audit two"),
    ])
    worker, supervisor = await _start(monkeypatch, queue, orchestration)
    try:
        await asyncio.wait_for(first_started.wait(), timeout=2)
        await asyncio.sleep(0.1)
        assert orchestration.started == ["raven, audit one"]
        assert not second_started.is_set()

        first_release.set()
        await asyncio.wait_for(second_started.wait(), timeout=2)
        assert orchestration.max_active == 1

        second_release.set()
        await _wait_until(lambda: len(queue.completed) == 2)
    finally:
        first_release.set()
        second_release.set()
        await _stop(worker, supervisor)


async def test_a_failed_job_does_not_stop_the_loop(monkeypatch):
    orchestration = RecordingOrchestration()
    orchestration.event_pair("bad chat")[0].set()
    orchestration.event_pair("good chat")[0].set()
    queue = FakeJobQueue([
        _job("bad-job", "bad chat", _fail=True),
        _job("good-job", "good chat"),
    ])
    worker, supervisor = await _start(monkeypatch, queue, orchestration)
    try:
        await _wait_until(lambda: queue.failed and queue.completed)
        assert [job_id for job_id, _ in queue.failed] == ["bad-job"]
        assert [job_id for job_id, _ in queue.completed] == ["good-job"]
    finally:
        await _stop(worker, supervisor)


async def test_cancelling_the_supervisor_cancels_in_flight_jobs(monkeypatch):
    orchestration = RecordingOrchestration()
    release, started = orchestration.event_pair("raven, long task")
    queue = FakeJobQueue([_job("raven-1", "raven, long task")])
    worker, supervisor = await _start(monkeypatch, queue, orchestration)
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        in_flight = list(worker._inference_jobs)
        assert len(in_flight) == 1

        await _stop(worker, supervisor)

        assert in_flight[0].cancelled()
        assert worker._inference_jobs == set()
    finally:
        release.set()
