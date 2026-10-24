"""Screen-time routes: status from identity's rules + today's use; requests tell people."""
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=ZoneInfo("America/Phoenix"))  # a Wednesday


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []
    sent: list[dict] = []
    answers: dict = {}

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        async def json(self, content_type=None):
            return self._body

        async def text(self):
            return ""

    class _Client:
        async def request(self, method, url, **kw):
            calls.append({"method": method, "url": url, **kw})
            return _Resp(*answers.get((method, url.split("/api", 1)[1]), (200, {})))

        async def get(self, url, **kw):
            calls.append({"method": "GET", "url": url, **kw})
            return _Resp(200, {"days": [{"day": "2026-10-21", "millis": 100 * 60000}]})

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def fake_send(payload, **kw):
        sent.append(payload)
        return True

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main._notify, "send", fake_send)
    monkeypatch.setattr(gateway_main, "_household_now", lambda: NOW)
    return TestClient(app, headers={"Authorization": "Bearer kid-key"}), calls, sent, answers


def test_status_combines_rules_bonus_and_todays_use(wired):
    client, calls, _, answers = wired
    answers[("GET", "/screen-time/kid/rules")] = (200, {"rules": {"daily_limit_min": 120}, "bonus_min": 10, "can_manage": False})

    body = client.get("/api/screen-time/kid/status").json()

    assert (body["limit_min"], body["used_min"], body["bonus_min"], body["remaining_min"]) == (120, 100, 10, 30)
    assert body["locked"] is False
    rules_call = calls[0]
    assert rules_call["headers"]["Authorization"] == "Bearer kid-key"  # identity checks the caller
    assert rules_call["params"] == {"day": "2026-10-21"}


def test_status_is_refused_when_identity_refuses(wired):
    client, _, _, answers = wired
    answers[("GET", "/screen-time/kid/rules")] = (403, {"detail": "Only a parent can see this"})
    assert client.get("/api/screen-time/kid/status").status_code == 403


def test_asking_for_time_tells_the_parents(wired):
    client, calls, sent, answers = wired
    answers[("POST", "/screen-time/requests")] = (
        200,
        {"request": {"username": "kid", "minutes": 30, "note": "one more level"}, "notify": ["dad", "mom"]},
    )

    r = client.post("/api/screen-time/requests", json={"minutes": 30, "note": "one more level"})

    assert r.status_code == 200
    assert calls[0]["json"]["day"] == "2026-10-21"  # the household's day, not the phone's
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert "30 more minutes" in sent[0]["body"] and "one more level" in sent[0]["body"]


def test_a_refused_request_tells_nobody(wired):
    client, _, sent, answers = wired
    answers[("POST", "/screen-time/requests")] = (409, {"detail": "You already asked"})
    assert client.post("/api/screen-time/requests", json={"minutes": 30}).status_code == 409
    assert sent == []
