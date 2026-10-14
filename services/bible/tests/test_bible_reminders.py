"""The daily reading reminder: only after the chosen time, only if unread, once a day."""
from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient
from sqlmodel import Session

from services.bible import main as bible_main
from services.bible import reminders


def _now(hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


def _due(hhmm: str):
    with Session(bible_main.app.state.engine) as session:
        return reminders.due_now(session, _now(hhmm))


def test_the_reminder_time_is_a_validated_preference(loaded_client: TestClient):
    body = loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": "19:00"}).json()
    assert body["preferences"]["reminder_time"] == "19:00"
    assert loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": "25:00"}).status_code == 422
    off = loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": ""}).json()
    assert off["preferences"]["reminder_time"] == ""


def test_due_only_after_the_time_and_only_if_unread(loaded_client: TestClient):
    loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": "19:00"})
    assert _due("18:59") == []
    assert [u for u, _ in _due("19:05")] == ["sam"]
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "app_open"})
    assert [u for u, _ in _due("19:05")] == ["sam"], "opening the app is not reading"
    loaded_client.post("/events", params={"username": "sam"}, json={"kind": "chapter_complete", "ref": "John 3"})
    assert _due("19:05") == []


def test_the_message_names_todays_plan_reading(loaded_client: TestClient):
    loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": "07:00"})
    loaded_client.post("/plans/john-21/start", params={"username": "sam"})
    [(user, note)] = _due("08:00")
    assert note["title"] == "Today in John in 21 Days" and note["body"] == "Day 1: John 1"


def test_the_message_falls_back_to_memory_verses(loaded_client: TestClient):
    loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": "07:00"})
    loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:2"})
    [(user, note)] = _due("08:00")
    assert note["body"] == "1 verse ready for review." and note["data"]["url"] == "/bible?tab=memorize"


async def test_sending_uses_a_once_key_per_day(monkeypatch):
    sent = []

    class _Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Client:
        def post(self, url, json=None, headers=None, timeout=None):
            sent.append((url, json, headers))
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(reminders.aiohttp, "ClientSession", lambda *a, **kw: _Client())
    ok = await reminders.send("sam", {"title": "t", "body": "b"}, "2026-10-13", "http://telemetry:11438", "s3")
    assert ok
    url, body, headers = sent[0]
    assert url.endswith("/api/telemetry/notify") and headers == {"X-Internal-Secret": "s3"}
    assert body["once"] == "bible-nudge:2026-10-13" and body["user"] == "sam"
