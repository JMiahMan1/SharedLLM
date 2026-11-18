"""The cleanup pass syncs each home hub a person can read into RAG ``home_devices`` on its own.

A person with their own Home Assistant login gets that hub's devices and, while
HomeHub is on, HomeHub's too (trimmed to what they may view). Each sync names
its hub, so RAG never lets one hub's sync remove the other's rows. Every name
and address here is made up.
"""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from services.gateway import background_worker as bw


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def text(self):
        return json.dumps(self._body)

    async def json(self, *a, **k):
        return self._body


class _Client:
    """Answers Identity, execution and RAG the way the cleanup pass calls them."""

    def __init__(self, users, creds, hubs):
        self.users, self.creds, self.hubs = users, creds, hubs
        self.syncs: list[dict] = []

    async def get(self, url, params=None, headers=None, **_k):
        if url.endswith("/api/users"):
            return _Resp(200, self.users)
        if url.endswith("/discovery/entities"):
            source = "home_assistant" if (params or {}).get("ha_url") else "homehub"
            if source not in self.hubs:
                return _Resp(503, {"detail": "no hub"})
            return _Resp(200, {"entities": self.hubs[source], "source": source})
        raise AssertionError(url)

    async def post(self, url, json=None, headers=None, **_k):
        if url.endswith("/api/resolve"):
            return _Resp(200, self.creds[json["rag_user"]])
        if url.endswith("/rag/sync/devices"):
            self.syncs.append(json)
            return _Resp(200, {"status": "SUCCESS", "orphaned_entity_ids": []})
        raise AssertionError(url)


class _Redis:
    def delete(self, *_k):
        pass


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr("services.gateway.ha_state_cache.get_redis", lambda: _Redis())
    monkeypatch.setattr("services.gateway.ha_state_cache.cache_all_states", lambda _e: 0)
    return bw.RavenWorker.__new__(bw.RavenWorker)


def _run(worker, monkeypatch, client):
    @asynccontextmanager
    async def _shared():
        yield client

    monkeypatch.setattr(bw, "_shared_http_client", _shared)
    asyncio.run(worker._run_cleanup())


LAMP = {"entity_id": "light.den", "state": "on", "attributes": {}}
FAN = {"entity_id": "fan.loft", "state": "off", "attributes": {}}


def test_both_hubs_are_synced_apart_for_a_person_with_their_own_login(worker, monkeypatch):
    monkeypatch.setattr("services.config.HOMEHUB_URL", "http://hub.example.test:8123")
    monkeypatch.setattr("services.config.HA_URL", "http://ha.example.test:8123")
    client = _Client(
        users=[{"username": "sam"}],
        creds={"sam": {"user": "sam", "is_admin": True,
                       "ha_url": "http://ha.example.test:8123", "ha_token": "fake-token"}},
        hubs={"homehub": [LAMP], "home_assistant": [FAN]},
    )
    _run(worker, monkeypatch, client)

    by_source = {s["source"]: [e["entity_id"] for e in s["entities"]] for s in client.syncs}
    assert by_source == {"home_assistant": ["fan.loft"], "homehub": ["light.den"]}
    assert {s["user_id"] for s in client.syncs} == {"sam"}


def test_homehub_alone_is_synced_with_the_hubs_login(worker, monkeypatch):
    monkeypatch.setattr("services.config.HOMEHUB_URL", "http://hub.example.test:8123")
    monkeypatch.setattr("services.config.HA_URL", "")
    client = _Client(
        users=[{"username": "sam"}],
        creds={"sam": {"user": "sam", "is_admin": True, "ha_url": "http://ha.example.test", "ha_token": "t"}},
        hubs={"homehub": [LAMP]},
    )
    _run(worker, monkeypatch, client)

    assert [(s["source"], s["user_id"]) for s in client.syncs] == [("homehub", "sam")]


def test_nothing_visible_sends_no_sync(worker, monkeypatch):
    monkeypatch.setattr("services.config.HOMEHUB_URL", "http://hub.example.test:8123")
    monkeypatch.setattr("services.config.HA_URL", "")
    client = _Client(users=[{"username": "sam"}], creds={"sam": {"user": "sam", "is_admin": True}},
                     hubs={"homehub": []})
    _run(worker, monkeypatch, client)
    assert client.syncs == []
