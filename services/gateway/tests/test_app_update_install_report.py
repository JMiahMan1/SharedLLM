"""Phones report what happened to an app update, so an update that still asks
for a tap or the phone's lock can be explained from what Android answered."""
import json

import pytest
from fastapi.testclient import TestClient


class _Redis:
    def __init__(self):
        self.items = []

    def rpush(self, key, value):
        self.items.append((key, value))

    def ltrim(self, key, start, end):
        self.items = self.items[start:] if start < 0 else self.items


@pytest.fixture
def reports(monkeypatch):
    from services.gateway import cache, main

    redis = _Redis()

    async def caller(request):
        return "leo"

    monkeypatch.setattr(main, "_require_authenticated", caller)
    monkeypatch.setattr(cache, "get_redis", lambda: redis)
    return TestClient(main.app), redis, main


def test_a_report_keeps_only_known_fields_with_who_and_when(reports):
    client, redis, main = reports
    body = {"event": "status", "status": -1, "message": "x" * 1000, "installer": "com.android.chrome",
            "update_owner": None, "path": "background", "secret": "nope"}
    assert client.post("/api/app-updates/install-report", json=body).status_code == 200
    key, raw = redis.items[-1]
    stored = json.loads(raw)
    assert key == main.INSTALL_REPORTS_KEY
    assert stored["user"] == "leo" and stored["status"] == -1 and stored["installer"] == "com.android.chrome"
    assert len(stored["message"]) == 300 and "secret" not in stored and "at" in stored


def test_a_report_without_an_event_is_refused(reports):
    client, redis, _ = reports
    assert client.post("/api/app-updates/install-report", json={"status": 0}).status_code == 422
    assert redis.items == []
