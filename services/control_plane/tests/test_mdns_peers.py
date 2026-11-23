"""control_plane reading HomeHub's mDNS identity table."""
from __future__ import annotations

import logging

import requests

from services.control_plane import mdns_peers


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self.body


def test_off_without_a_hub(monkeypatch):
    monkeypatch.setattr(mdns_peers.config, "HOMEHUB_URL", "")
    monkeypatch.setattr(mdns_peers.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert mdns_peers.fetch() == {}


def test_reads_the_table_with_the_internal_secret(monkeypatch):
    seen = {}

    def get(url, headers, timeout):
        seen.update(url=url, auth=headers["Authorization"])
        return _Resp({"devices": {"192.168.10.4": {"txt": {"model": "iPad13,4"}}, "bad": "x"}})

    monkeypatch.setattr(mdns_peers.config, "HOMEHUB_URL", "http://hub.test:8123")
    monkeypatch.setattr(mdns_peers.config, "INTERNAL_SECRET", "test-secret")
    monkeypatch.setattr(mdns_peers.requests, "get", get)
    assert mdns_peers.fetch() == {"192.168.10.4": {"txt": {"model": "iPad13,4"}}}
    assert seen == {"url": "http://hub.test:8123/api/network/mdns", "auth": "Bearer test-secret"}


def test_an_unreachable_hub_is_logged_once(monkeypatch, caplog):
    def down(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(mdns_peers.config, "HOMEHUB_URL", "http://hub.test:8123")
    monkeypatch.setattr(mdns_peers.requests, "get", down)
    monkeypatch.setattr(mdns_peers, "_failing", False)
    with caplog.at_level(logging.WARNING):
        assert mdns_peers.fetch() == {}
        assert mdns_peers.fetch() == {}
    assert sum("unavailable" in r.message for r in caplog.records) == 1
    assert "test-secret" not in caplog.text
