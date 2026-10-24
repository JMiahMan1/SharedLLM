"""Game and activity suggestions: derived from the member's own week, nothing raw returned."""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


@pytest.fixture
def client(monkeypatch):
    async def _resolve(_request):
        return {"user": "kid", "user_id": 7, "is_admin": False}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _resolve)
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def _telemetry(monkeypatch, usage=None, events=None):
    calls: list[str] = []

    class _Resp:
        status = 200

        def __init__(self, body):
            self._body = body

        async def json(self, content_type=None):
            return self._body

    class _Client:
        async def get(self, url, **kw):
            calls.append(url)
            if "/usage/" in url:
                return _Resp({"status": "SUCCESS", "days": usage or []})
            return _Resp({"status": "SUCCESS", "events": events or []})

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    return calls


def test_game_suggestion_comes_from_the_members_own_play(client, monkeypatch):
    calls = _telemetry(monkeypatch, usage=[{"day": "2026-10-23", "apps": [{"package": "com.epicgames.fortnite", "millis": 3600000}]}])

    r = client.get("/api/recommendations/game")

    assert r.status_code == 200
    rec = r.json()["recommendation"]
    assert rec["game"] == "Rocket League" and rec["matched_on"] == "Fortnite"
    assert all(url.endswith("/kid") for url in calls)  # only the caller's own data
    assert "apps" not in json.dumps(r.json())  # nothing raw comes back


def test_activity_suggestion_from_a_late_week(client, monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/Phoenix")
    late = 1792821600000  # 2026-10-23 23:00 in Phoenix
    _telemetry(monkeypatch, usage=[{"day": "a", "last_use": late}, {"day": "b", "last_use": late - 86400000}])

    rec = client.get("/api/recommendations/activity").json()["recommendation"]

    assert rec["theme"] == "late nights" and "last_use" not in rec


def test_nothing_to_suggest_is_an_empty_answer(client, monkeypatch):
    _telemetry(monkeypatch)
    assert client.get("/api/recommendations/game").json()["recommendation"] is None
    assert client.get("/api/recommendations/activity").json()["recommendation"] is None


def test_a_lane_the_parents_blocked_returns_nothing(client, monkeypatch):
    seen: list[str] = []

    class _Resp:
        status = 200

        def __init__(self, body):
            self._body = body

        async def json(self, content_type=None):
            return self._body

    class _Client:
        async def get(self, url, **kw):
            seen.append(url)
            if "recommendation-lanes" in url:
                return _Resp({"blocked": ["game"]})
            return _Resp({"days": [{"day": "d", "apps": [{"package": "com.epicgames.fortnite", "millis": 3600000}]}], "events": []})

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)

    body = client.get("/api/recommendations/game").json()
    assert body["recommendation"] is None and body["blocked"] is True
    assert not any("/usage/" in url for url in seen)  # a blocked lane reads nothing
    assert client.get("/api/recommendations/activity").json().get("blocked") is None
