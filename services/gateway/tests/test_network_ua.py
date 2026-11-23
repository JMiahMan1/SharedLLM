"""Gateway User-Agent capture for device identification."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.gateway import network_ua


class FakeRedis:
    def __init__(self):
        self.hashes: dict = {}
        self.writes = 0

    def hset(self, key, field, value):
        self.writes += 1
        self.hashes.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hlen(self, key):
        return len(self.hashes.get(key, {}))

    def hdel(self, key, *fields):
        for f in fields:
            self.hashes.get(key, {}).pop(f, None)


def _request(ip, ua="Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)"):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers={"user-agent": ua})


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    network_ua.reset()
    monkeypatch.setattr(network_ua, "client_ip", lambda req: req.client.host)
    yield
    network_ua.reset()


def test_only_private_client_addresses_recorded():
    r = FakeRedis()
    network_ua.record_user_agent(_request("203.0.113.7"), redis_client=r)
    assert r.hgetall(network_ua.UA_KEY) == {}
    network_ua.record_user_agent(_request("192.168.10.5"), redis_client=r)
    assert "192.168.10.5" in r.hgetall(network_ua.UA_KEY)


def test_missing_user_agent_is_ignored():
    req = SimpleNamespace(client=SimpleNamespace(host="192.168.10.9"), headers={})
    assert network_ua.due(req) is None


def test_ua_not_logged(caplog):
    r = FakeRedis()
    with caplog.at_level("DEBUG"):
        network_ua.record_user_agent(_request("192.168.10.6", "SECRET-USER-AGENT-STRING"), redis_client=r)
    assert "SECRET-USER-AGENT-STRING" not in caplog.text


def test_same_ua_is_throttled_then_refreshed():
    req = _request("192.168.10.7", "UA-ONE")
    assert network_ua.due(req, now=1000.0)
    assert network_ua.due(req, now=1100.0) is None
    assert network_ua.due(req, now=1000.0 + network_ua.THROTTLE_SECONDS + 1)


def test_changed_ua_writes_at_once():
    r = FakeRedis()
    network_ua.record_user_agent(_request("192.168.10.8", "UA-ONE"), redis_client=r)
    network_ua.record_user_agent(_request("192.168.10.8", "UA-TWO"), redis_client=r)
    assert r.writes == 2
    assert json.loads(r.hgetall(network_ua.UA_KEY)["192.168.10.8"])["ua"] == "UA-TWO"


def test_redis_hash_is_capped_to_the_newest(monkeypatch):
    monkeypatch.setattr(network_ua, "MAX_UA_ENTRIES", 2)
    r = FakeRedis()
    for i, ip in enumerate(["192.168.10.1", "192.168.10.2", "192.168.10.3"]):
        r.hset(network_ua.UA_KEY, ip, json.dumps({"ua": "x", "at": i}))
    network_ua.write("192.168.10.4", "y", 99.0, redis_client=r)
    assert set(r.hgetall(network_ua.UA_KEY)) == {"192.168.10.3", "192.168.10.4"}


def test_touch_hint_is_kept_with_the_user_agent():
    req = SimpleNamespace(
        client=SimpleNamespace(host="192.168.10.12"),
        headers={"user-agent": "Mozilla/5.0 (Macintosh)", "x-device-hint": "touch"},
    )
    assert network_ua.due(req)[1] == "Mozilla/5.0 (Macintosh) [touch]"


def test_unknown_hint_values_are_dropped():
    req = SimpleNamespace(
        client=SimpleNamespace(host="192.168.10.13"),
        headers={"user-agent": "Mozilla/5.0 (Macintosh)", "x-device-hint": "anything else"},
    )
    assert network_ua.due(req)[1] == "Mozilla/5.0 (Macintosh)"
