"""The daily reading reminder: only after the chosen time, only if unread, once a day."""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi.testclient import TestClient
from sqlmodel import Session

from services.bible import main as bible_main
from services.bible import reminders
from services.bible.models import ReadingEvent, UserBibleState


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
    [(_user, note)] = _due("08:00")
    assert note["title"] == "Today in John in 21 Days" and note["body"] == "Day 1: John 1"


def test_the_message_falls_back_to_memory_verses(loaded_client: TestClient):
    loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": "07:00"})
    loaded_client.post("/memorize", params={"username": "sam"}, json={"ref": "Genesis 1:2"})
    [(_user, note)] = _due("08:00")
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

    from services.common import http

    monkeypatch.setattr(http, "get_client", _Client)
    ok = await reminders.send("sam", {"title": "t", "body": "b"}, "2026-10-13", "http://telemetry:11438", "s3")
    assert ok
    url, body, headers = sent[0]
    assert url.endswith("/api/telemetry/notify") and headers == {"X-Internal-Secret": "s3"}
    assert body["once"] == "bible-nudge:2026-10-13" and body["user"] == "sam"
    assert body["category"] == "bible" and body["tag"] == "bible:reminder"


def test_a_failing_reminder_pass_is_logged_and_the_loop_continues(monkeypatch, caplog):
    """The loop used to suppress every exception, so a pass failing each time left no trace."""
    import asyncio
    import logging

    import pytest

    class _Stop(Exception):
        pass

    calls = {"pass": 0, "sleep": 0}

    async def failing(*_a):
        calls["pass"] += 1
        raise RuntimeError("telemetry gone")

    async def fake_sleep(_s):
        calls["sleep"] += 1
        if calls["sleep"] > 2:
            raise _Stop

    monkeypatch.setattr(reminders, "remind_once", failing)
    monkeypatch.setattr(reminders.asyncio, "sleep", fake_sleep)
    with caplog.at_level(logging.WARNING), pytest.raises(_Stop):
        asyncio.run(reminders.reminder_loop(lambda: None, "http://telemetry:11438", "s"))
    assert calls["pass"] == 2
    assert sum("RuntimeError: telemetry gone" in r.getMessage() for r in caplog.records) == 2


def test_a_reminder_is_not_sent_hours_late(loaded_client: TestClient):
    """Setting 07:00 in the evening (or a late restart) must not push at once."""
    loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": "07:00"})
    assert [u for u, _ in _due("08:59")] == ["sam"]
    assert _due("09:00") == []
    assert _due("21:00") == []


def test_the_plan_link_is_url_encoded(loaded_client: TestClient):
    loaded_client.put("/state", params={"username": "sam"}, json={"reminder_time": "07:00"})
    loaded_client.post("/plans/john-21/start", params={"username": "sam"})
    [(_, note)] = _due("07:30")
    # The reader opens the whole chapter from ref, scrolled to the verse (the page
    # reads book= only as a study book id, so a chapter link never uses it).
    assert note["data"]["url"] == "/bible?tab=read&ref=John%201%3A1&verse=1#v1"
    lnk = note["data"]["link"]
    assert lnk["route"] == "bible"
    assert lnk["query"] == {"tab": "read", "ref": "John 1:1", "verse": "1"}
    assert lnk["hash"] == "v1"

RENGAGE_NOW = datetime(2026, 2, 10, 10, 0)
RENGAGE_TODAY = "2026-02-10"


class _HouseholdResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload


class _HouseholdSession:
    def __init__(self, payload, fail=False):
        self._payload = payload
        self.fail = fail
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        if self.fail:
            return _HouseholdResponse(500, {})
        return _HouseholdResponse(200, self._payload)


def _seed(engine, states, events):
    with Session(engine) as session:
        for username, fields in states.items():
            session.add(UserBibleState(username=username, **fields))
        for username, day, kind in events:
            session.add(ReadingEvent(username=username, day=day, kind=kind))
        session.commit()


def test_reengage_due_steps():
    today = "2026-02-10"
    assert reminders.reengage_due("2026-02-07", "", today) is True  # step 3 reached
    assert reminders.reengage_due("2026-02-07", today, today) is False  # never twice a day
    assert reminders.reengage_due("2026-02-07", "2026-02-09", today) is True  # nudged before the step date
    assert reminders.reengage_due("2026-02-03", "", today) is True  # step 7
    assert reminders.reengage_due("2026-01-27", "", today) is True  # step 14
    assert reminders.reengage_due("2026-01-11", "", today) is True  # step 30
    assert reminders.reengage_due("2026-01-11", today, today) is False  # already nudged at step 30
    assert reminders.reengage_due("2025-12-12", "", today) is True  # step 60: every 30 after 30
    assert reminders.reengage_due("2026-02-08", "", today) is False  # step 3 not reached yet
    assert reminders.reengage_due(None, "", today) is True  # never read: first check is day 3
    assert reminders.reengage_due(None, today, today) is False


def test_reengage_once_household(engine, monkeypatch):
    states = {
        "reader-a": {"reengage": True, "reminder_time": ""},   # read 10 days ago: due (step 7)
        "reader-b": {"reengage": True, "reminder_time": ""},   # read today: skip
        "reader-c": {"reengage": False, "reminder_time": ""},  # reengage off: skip
        "reader-d": {"reengage": True, "reminder_time": "07:00"},  # reminder set: skip
        "reader-f": {"reengage": True, "reminder_time": ""},   # read yesterday: not due yet
    }
    events = [
        ("reader-a", "2026-01-31", "chapter_complete"),
        ("reader-b", RENGAGE_TODAY, "chapter_complete"),
        ("reader-f", "2026-02-09", "chapter_complete"),
    ]
    _seed(engine, states, events)

    bodies = []

    async def fake_notify(body, telemetry=None, secret=None):
        bodies.append(body)
        return True

    household = _HouseholdSession({"usernames": ["reader-a", "reader-b", "reader-c", "reader-d", "reader-e", "reader-f"]})
    monkeypatch.setattr(reminders.aiohttp, "ClientSession", lambda *a, **k: household)
    monkeypatch.setattr(reminders.notify, "send", fake_notify)

    n = asyncio.run(
        reminders.reengage_once(
            engine, "http://telemetry:11438", "s3", "http://identity:11439", now=RENGAGE_NOW
        )
    )
    assert n == 2
    assert [body["user"] for body in bodies] == ["reader-a", "reader-e"]
    for body in bodies:
        assert body["kind"] == "bible_reengage"
        assert body["tag"] == "bible:reengage"
        assert body["once"] == f"bible-reengage:{RENGAGE_TODAY}"
        assert body["category"] == "bible"
        # Every nudge carries what to show: a title, a warm body and a link back to the reader.
        assert body["title"] == "Bible" and body["body"]
        # The shape telemetry's notify accepts (the same as the daily reminder's).
        assert "link" not in body and body["data"]["url"] == body["data"]["link"]["href"]
    # The reader with no state row got one, stamped today.
    with Session(engine) as session:
        assert session.get(UserBibleState, "reader-e").reengage_last_sent == RENGAGE_TODAY

    # Same hour again: nobody nudged twice.
    bodies.clear()
    n = asyncio.run(
        reminders.reengage_once(
            engine, "http://telemetry:11438", "s3", "http://identity:11439", now=RENGAGE_NOW
        )
    )
    assert n == 0
    assert bodies == []

    # Outside REENGAGE_HOUR: nothing sent, identity never consulted.
    household.calls = 0
    bodies.clear()
    n = asyncio.run(
        reminders.reengage_once(
            engine, "http://telemetry:11438", "s3", "http://identity:11439",
            now=datetime(2026, 2, 10, 9, 0),
        )
    )
    assert n == 0
    assert bodies == []
    assert household.calls == 0

    # Identity unreachable: nothing sent, warning logged.
    bodies.clear()
    broken = _HouseholdSession(None, fail=True)
    monkeypatch.setattr(reminders.aiohttp, "ClientSession", lambda *a, **k: broken)
    n = asyncio.run(
        reminders.reengage_once(
            engine, "http://telemetry:11438", "s3", "http://identity:11439", now=RENGAGE_NOW
        )
    )
    assert n == 0
    assert bodies == []


def test_reminder_loop_runs_reengage_after_remind(monkeypatch):
    calls = []

    async def fake_remind_once(*_a):
        calls.append("remind")

    async def fake_reengage_once(*_a):
        calls.append("reengage")
        return 0

    monkeypatch.setattr(reminders, "remind_once", fake_remind_once)
    monkeypatch.setattr(reminders, "reengage_once", fake_reengage_once)

    class _Stop(Exception):
        pass

    sleeps = {"n": 0}

    async def fake_sleep(_s):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise _Stop

    monkeypatch.setattr(reminders.asyncio, "sleep", fake_sleep)
    import pytest

    with pytest.raises(_Stop):
        asyncio.run(
            reminders.reminder_loop(
                lambda: None, "http://telemetry:11438", "s", "http://identity:11439"
            )
        )
    assert calls == ["remind", "reengage"]
