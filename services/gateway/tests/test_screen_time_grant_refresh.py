"""Time a parent gives without being asked reaches the child's phone at once.

Production, 2026-11-21: a child's tablet locked at bedtime (21:20), a parent
gave 15 bonus minutes at 21:23 and approved chores at 21:27, and the tablet
stayed locked until the child asked and the parent answered at 21:30. The
tablet had been asleep with no connection; once woken, its guard kept the old
"bedtime" status until its next minute, and the bonus notice that would have
made it re-read had been sent while it was away.

The phone re-reads its status the moment a notice tagged ``screen-time:``
arrives (PushNotice.isScreenTime) or a ``screen_time_refresh`` signal does, and
on waking (GuardService.watchWakes). These tests pin the server's side: every
way a parent gives time sends one of those, and the status the phone then
reads already counts the time.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.common.screen_time import REFRESH_KIND
from services.gateway import main as gateway_main
from services.gateway.main import app

TZ = ZoneInfo("Etc/GMT+7")
NOW = datetime(2026, 11, 21, 21, 25, tzinfo=TZ)  # a Saturday, five minutes into bedtime
BEDTIME = {"bedtime_start": "21:20", "bedtime_end": "06:00", "earn": {"chore_min": 5, "max_min": 40}}


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []
    sent: list[dict] = []
    world: dict = {
        "rules": {"username": "kid", "rules": dict(BEDTIME), "bonus_min": 0, "granted_until": None},
        "approved_today": 0,
        "answers": {},
    }

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        async def json(self, content_type=None):
            return self._body

        async def text(self):
            return ""

    def answer(method, url, kw):
        for suffix, reply in world["answers"].items():
            if url.endswith(suffix) and method != "GET":
                return reply
        if "/screen-time/kid/rules" in url and method == "GET":
            return 200, world["rules"]
        if url.endswith("/skylight/chores"):
            return 200, {"status": "SUCCESS", "chores": []}
        if "/chore-checks/kid/sync" in url:
            checks = [{"id": i, "day": NOW.date().isoformat(), "chore_id": str(i), "title": f"Chore {i}",
                       "status": "approved"} for i in range(world["approved_today"])]
            return 200, {"checks": checks, "new": [], "parents": [], "bonus_by_day": {}}
        return 200, {}

    class _Client:
        async def request(self, method, url, **kw):
            calls.append({"method": method, "url": url, **kw})
            return _Resp(*answer(method, url, kw))

        async def get(self, url, **kw):
            calls.append({"method": "GET", "url": url, **kw})
            return _Resp(200, {"days": [{"day": NOW.date().isoformat(), "millis": 30 * 60000}]})

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def fake_send(payload, **kw):
        sent.append(payload)
        return True

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main._notify, "send", fake_send)
    monkeypatch.setattr(gateway_main, "_household_now", lambda: NOW)
    gateway_main._forget_chores()
    yield TestClient(app, headers={"Authorization": "Bearer parent-key"}), calls, sent, world
    gateway_main._forget_chores()


def _status(client) -> dict:
    return client.get("/api/screen-time/kid/status").json()


def _refreshes_the_phone(notice: dict) -> bool:
    """What the phone keys on (PushNotice.isScreenTime / isScreenTimeRefresh)."""
    return notice["category"] == "family_safety" and (
        str(notice.get("tag") or "").startswith("screen-time:") or notice.get("kind") == REFRESH_KIND)


def test_a_bonus_at_bedtime_unlocks_the_very_next_status(wired):
    client, _, sent, world = wired
    assert (_status(client)["locked"], _status(client)["reason"]) == (True, "bedtime")

    world["answers"]["/screen-time/kid/bonus"] = (200, {"request": {"granted_minutes": 15}, "notify": ["kid"]})
    assert client.post("/api/screen-time/kid/bonus", json={"minutes": 15}).status_code == 200
    # The child's phone is told, with a notice it re-reads its status on.
    assert [n["user"] for n in sent] == ["kid"] and _refreshes_the_phone(sent[0])

    # Identity now counts the minutes; the status the phone reads has no cache in between.
    world["rules"] = {**world["rules"], "bonus_min": 15,
                      "granted_until": (NOW + timedelta(minutes=15)).isoformat()}
    body = _status(client)
    assert body["locked"] is False and body["reason"] is None
    assert body["next_lock_reason"] == "bedtime"
    assert body["next_lock_at"] == (NOW + timedelta(minutes=15)).isoformat()


def test_a_granted_request_refreshes_the_phone(wired):
    client, _, sent, world = wired
    world["answers"]["/screen-time/requests/22"] = (
        200, {"request": {"status": "granted", "granted_minutes": 15}, "notify": ["kid"]})
    assert client.post("/api/screen-time/requests/22", json={"grant": True}).status_code == 200
    assert [n["user"] for n in sent] == ["kid"] and _refreshes_the_phone(sent[0])


def test_an_approved_chore_refreshes_the_phone_and_its_minutes_count_at_once(wired):
    client, _, sent, world = wired
    world["rules"] = {**world["rules"], "rules": {"daily_limit_min": 60, "earn": {"chore_min": 5}}}
    assert _status(client)["remaining_min"] == 30

    world["answers"]["/screen-time/chore-checks/7"] = (200, {
        "check": {"id": 7, "username": "kid", "chore_id": "7", "title": "Bed", "status": "approved"},
        "notify": ["kid"], "untick": False, "approved_today": ["Bed"]})
    assert client.post("/api/screen-time/chore-checks/7", json={"approve": True}).status_code == 200
    assert [n["user"] for n in sent] == ["kid"] and _refreshes_the_phone(sent[0])

    world["approved_today"] = 1
    body = _status(client)
    assert body["earned_min"] == 5 and body["remaining_min"] == 35


def test_a_rule_change_sends_the_phone_a_data_only_refresh(wired):
    client, _, sent, world = wired
    world["answers"]["/screen-time/kid/rules"] = (200, {"username": "kid", "rules": {"bedtime_start": "22:00"}})
    assert client.put("/api/screen-time/kid/rules", json={"bedtime_start": "22:00"}).status_code == 200
    assert len(sent) == 1
    signal = sent[0]
    assert signal["user"] == "kid" and signal["kind"] == REFRESH_KIND and signal["signal"] is True
    # Quiet, one at a time, and still a screen-time notice for an app too old to know the kind.
    assert signal["priority"] == 2 and signal["replace"] is True
    assert signal["tag"].startswith("screen-time:") and _refreshes_the_phone(signal)
    assert "paused" not in signal["title"].lower()  # never read as a parent's pause


def test_a_refused_rule_change_signals_nothing(wired):
    client, _, sent, world = wired
    world["answers"]["/screen-time/kid/rules"] = (403, {"detail": "Only a parent can set a minor's screen time"})
    assert client.put("/api/screen-time/kid/rules", json={"bedtime_start": "22:00"}).status_code == 403
    assert sent == []
