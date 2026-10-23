"""Recent chat turns for queued inference jobs (G4).

The queued chat path in ``main.py`` does not pass the request transcript and
does not write history itself. The worker therefore attaches the per-user
history to a chat job and stores the completed turn; the orchestrator consumes
that list. Missions stay unaffected; Nextcloud Talk turns keep a transcript of
their own, scoped to the room instead of the user.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import services.gateway.agent_loop as agent_loop
import services.gateway.background_worker as background_worker
import services.gateway.orchestrator as orchestrator

HISTORY = [
    {"role": "user", "content": "how is the greenhouse doing?"},
    {"role": "assistant", "content": "The soil sensor reads 42 percent."},
]

ROOM_HISTORY = [
    {"role": "user", "content": "How did the fort turn out?"},
    {"role": "assistant", "content": "It survived the night."},
]


class FakeJobQueue:
    def __init__(self):
        self.completed = []
        self.failed = []

    async def heartbeat_job(self, job_id):
        pass

    async def complete_job(self, job_id, result):
        self.completed.append((job_id, result))

    async def fail_job(self, job_id, error):
        self.failed.append((job_id, error))


def _job(**payload_overrides):
    payload = {
        "query": "and the tomatoes?",
        "model": "some-model",
        "system": "SYSTEM",
        "creds": {"user": "alice"},
    }
    payload.update(payload_overrides)
    return {"job_id": "job-1", "user_id": "alice", "payload": payload}


@pytest.fixture(autouse=True)
def _fresh_tier_locks(monkeypatch):
    monkeypatch.setattr(background_worker, "TIER2_SEMAPHORE", asyncio.Semaphore(3))
    monkeypatch.setattr(background_worker, "TIER3_LOCK", asyncio.Lock())


async def test_a_queued_chat_job_reads_history_and_stores_the_turn(monkeypatch):
    monkeypatch.setattr(background_worker, "get_history", AsyncMock(return_value=HISTORY))
    monkeypatch.setattr(background_worker, "update_history", AsyncMock())

    seen = {}

    async def fake_orchestration(payload, chunk_callback=None):
        seen["history"] = payload.get("history")
        return "They are ripe."

    monkeypatch.setattr(background_worker, "process_full_orchestration", fake_orchestration)
    queue = FakeJobQueue()
    worker = background_worker.RavenWorker()
    worker.job_queue = queue

    await worker._process_inference_job(_job())

    assert seen["history"] == HISTORY
    background_worker.get_history.assert_awaited_once_with("alice")
    assert queue.completed == [("job-1", "They are ripe.")]
    assert [tuple(c.args) for c in background_worker.update_history.await_args_list] == [
        ("alice", "user", "and the tomatoes?"),
        ("alice", "assistant", "They are ripe."),
    ]


async def test_a_payload_history_list_wins_over_the_store(monkeypatch):
    supplied = [{"role": "user", "content": "from the request"}]
    monkeypatch.setattr(background_worker, "get_history", AsyncMock(return_value=HISTORY))
    monkeypatch.setattr(background_worker, "update_history", AsyncMock())

    seen = {}

    async def fake_orchestration(payload, chunk_callback=None):
        seen["history"] = payload.get("history")
        return "ok"

    monkeypatch.setattr(background_worker, "process_full_orchestration", fake_orchestration)
    worker = background_worker.RavenWorker()
    worker.job_queue = FakeJobQueue()

    await worker._process_inference_job(_job(history=supplied))

    assert seen["history"] == supplied
    background_worker.get_history.assert_not_awaited()


async def test_an_autonomous_job_does_not_read_chat_history(monkeypatch):
    """Raven missions run on their own task text, not the user's transcript."""
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("mission read the chat history")

    monkeypatch.setattr(orchestrator, "get_history", fail_if_called)
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    seen = {}

    async def fake_agent_loop(*args, **kwargs):
        seen["history"] = args[3]
        return "MISSION ANSWER"

    monkeypatch.setattr(agent_loop, "AgentLoop", fake_agent_loop)

    answer = await orchestrator.process_full_orchestration(
        {
            "query": "fix the failing parser",
            "model": "some-model",
            "system": "SYSTEM",
            "creds": {"user": "alice"},
            "_mission_id": 7,
        }
    )

    assert answer == "MISSION ANSWER"
    assert seen["history"] == []


async def test_the_orchestrator_falls_back_to_the_history_store(monkeypatch):
    """A direct caller that did not attach history still gets recent turns."""
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=HISTORY))
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    seen = {}

    async def fake_single_turn(*args, **kwargs):
        seen["history"] = args[4]
        return "SINGLE ANSWER"

    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_single_turn)

    answer = await orchestrator.process_full_orchestration(
        {
            "query": "what about the basil?",
            "model": "some-model",
            "system": "SYSTEM",
            "creds": {"user": "alice"},
        }
    )

    assert answer == "SINGLE ANSWER"
    assert seen["history"] == HISTORY
    orchestrator.get_history.assert_awaited_once_with("alice")


async def test_the_orchestrator_uses_history_attached_to_the_job(monkeypatch):
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    seen = {}

    async def fake_single_turn(*args, **kwargs):
        seen["history"] = args[4]
        return "SINGLE ANSWER"

    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_single_turn)

    await orchestrator.process_full_orchestration(
        {
            "query": "and the basil?",
            "model": "some-model",
            "system": "SYSTEM",
            "creds": {"user": "alice"},
            "history": HISTORY,
        }
    )

    assert seen["history"] == HISTORY


async def test_a_talk_room_job_reads_and_stores_the_room_transcript(monkeypatch):
    """@Jarvis in a room keeps the room's own memory, not the user's."""
    monkeypatch.setattr(background_worker, "get_history", AsyncMock(return_value=HISTORY))
    monkeypatch.setattr(background_worker, "update_history", AsyncMock())
    monkeypatch.setattr(background_worker, "get_room_history", AsyncMock(return_value=ROOM_HISTORY))
    monkeypatch.setattr(background_worker, "update_room_history", AsyncMock())
    monkeypatch.setattr(background_worker.RavenWorker, "_trigger_talk_callback", AsyncMock())
    seen = {}

    async def fake_orchestration(payload, chunk_callback=None):
        seen["history"] = payload.get("history")
        return "They are ripe."

    monkeypatch.setattr(background_worker, "process_full_orchestration", fake_orchestration)
    queue = FakeJobQueue()
    worker = background_worker.RavenWorker()
    worker.job_queue = queue

    await worker._process_inference_job(_job(_talk_token="room-1"))

    assert seen["history"] == ROOM_HISTORY
    background_worker.get_room_history.assert_awaited_once_with("room-1")
    background_worker.get_history.assert_not_awaited()
    background_worker.update_history.assert_not_awaited()
    assert queue.completed == [("job-1", "They are ripe.")]
    assert [tuple(c.args) for c in background_worker.update_room_history.await_args_list] == [
        ("room-1", "user", "and the tomatoes?"),
        ("room-1", "assistant", "They are ripe."),
    ]


async def test_the_orchestrator_uses_the_room_transcript_when_attached(monkeypatch):
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    seen = {}

    async def fake_single_turn(*args, **kwargs):
        seen["history"] = args[4]
        return "SINGLE ANSWER"

    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_single_turn)

    await orchestrator.process_full_orchestration(
        {
            "query": "what about the basil?",
            "model": "some-model",
            "system": "SYSTEM",
            "creds": {"user": "alice"},
            "_talk_token": "room-1",
            "history": ROOM_HISTORY,
        }
    )

    assert seen["history"] == ROOM_HISTORY


async def test_a_talk_job_without_attached_history_stays_empty(monkeypatch):
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=HISTORY))
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    seen = {}

    async def fake_single_turn(*args, **kwargs):
        seen["history"] = args[4]
        return "SINGLE ANSWER"

    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_single_turn)

    await orchestrator.process_full_orchestration(
        {
            "query": "and the basil?",
            "model": "some-model",
            "system": "SYSTEM",
            "creds": {"user": "alice"},
            "_talk_token": "room-1",
        }
    )

    assert seen["history"] == []
    orchestrator.get_history.assert_not_awaited()
