"""The gateway's Talk monitor answers @Jarvis questions typed in other Talk apps.

A question asked through the Family app is answered by execution's send (and
claimed in Redis); the monitor must not answer it a second time.
"""
import json
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from services.config import JARVIS_TALK_CLAIM_KEY
from services.gateway import background_worker as bw


class _Resp:
    status = 200

    def __init__(self, body):
        self._body = body

    async def text(self):
        return json.dumps(self._body)


class _Client:
    def __init__(self, messages):
        self.messages = messages
        self.game_posts = []

    async def post(self, url, json=None, **kwargs):
        if json["action"] == "list":
            return _Resp({"detail": {"conversations": [{"token": "room1"}]}})
        if json["action"] == "game":
            self.game_posts.append(json)
            return _Resp({"status": "SUCCESS"})
        return _Resp({"detail": {"messages": self.messages}})


class _Redis:
    def __init__(self, claimed=()):
        self.store = {JARVIS_TALK_CLAIM_KEY.format(token="room1", message_id=m): "1" for m in claimed}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value

    async def exists(self, key):
        return int(key in self.store)


@pytest.fixture
def worker(monkeypatch):
    def run(messages):
        client_instance = _Client(messages)

        @asynccontextmanager
        async def client():
            yield client_instance

        monkeypatch.setattr(bw, "_shared_http_client", client)
        w = types.SimpleNamespace(
            _get_system_creds=AsyncMock(return_value={"user": "admin", "nextcloud_user": "admin"}),
            _get_model_from_settings=AsyncMock(return_value="m"),
            job_queue=types.SimpleNamespace(enqueue_job=AsyncMock()),
        )
        w.client = client_instance
        return w

    return run


@pytest.mark.asyncio
async def test_monitor_answers_a_question_from_another_talk_app(worker):
    w = worker([{"id": 7, "actor_id": "michele", "message": "@Jarvis, what's for dinner?"}])
    r = _Redis()
    # A primed room: only messages after the last one seen count.
    r.store["jarvis:talk:last_msg:room1"] = "6"
    await bw.RavenWorker._check_talk_once(w, r)
    w.job_queue.enqueue_job.assert_awaited_once()
    assert w.job_queue.enqueue_job.await_args.kwargs["payload"]["query"] == "what's for dinner?"
    assert w.client.game_posts == []


@pytest.mark.asyncio
async def test_monitor_skips_a_question_the_app_already_claimed(worker):
    w = worker([{"id": 7, "actor_id": "michele", "message": "@Jarvis what's for dinner?"}])
    r = _Redis(claimed=[7])
    r.store["jarvis:talk:last_msg:room1"] = "6"
    await bw.RavenWorker._check_talk_once(w, r)
    w.job_queue.enqueue_job.assert_not_awaited()
    assert r.store["jarvis:talk:last_msg:room1"] == "7"


async def test_monitor_carries_the_room_context_into_the_job(worker):
    messages = [
        {"id": 5, "actor_id": "kiddo", "actor_display_name": "Kiddo", "message": "the fort survived the night!"},
        {
            "id": 6,
            "actor_id": "mom",
            "actor_display_name": "Mom",
            "message": 'It survived the night.\n\n```jarvis-envelope\n{"kind": "assistant", "title": "Jarvis"}\n```jarvis-envelope',
        },
        {"id": 7, "actor_id": "michele", "actor_display_name": "Michele", "message": "@Jarvis, what's for dinner?"},
    ]
    w = worker(messages)
    r = _Redis()
    r.store["jarvis:talk:last_msg:room1"] = "4"
    await bw.RavenWorker._check_talk_once(w, r)
    w.job_queue.enqueue_job.assert_awaited_once()
    history = w.job_queue.enqueue_job.await_args.kwargs["payload"]["history"]
    assert history == [
        {"role": "user", "content": "Kiddo: the fort survived the night!"},
        {"role": "assistant", "content": "It survived the night."},
    ]
