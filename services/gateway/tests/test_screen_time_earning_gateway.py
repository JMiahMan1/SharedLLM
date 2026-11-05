"""Earned time in the live status: the week's steps, exercise and approved
chores, carried-over minutes, the "chores first" lock, and a parent's answer."""
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

TZ = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 10, 21, 16, 0, tzinfo=TZ)  # a Wednesday; the week began Monday the 19th
RULES = {"daily_limit_min": 60, "chores_first": True,
         "earn": {"steps_every": 1000, "steps_min": 5, "exercise_every": 15, "exercise_min": 10, "chore_min": 10}}


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []
    sent: list[dict] = []
    world = {
        "rules": {"rules": RULES, "bonus_min": 0, "can_manage": False},
        "usage": [
            {"day": "2026-10-19", "millis": 50 * 60000},   # Monday: 10 under the limit
            {"day": "2026-10-21", "millis": 60 * 60000},   # today: at the limit
        ],
        "steps": {"daily_steps": {"2026-10-19": 2000, "2026-10-21": 3000}, "today": 3000},
        "workouts": {"workouts": [
            {"start_time": datetime(2026, 10, 20, 17, tzinfo=TZ).timestamp(), "duration_seconds": 30 * 60, "status": "completed"},
            {"start_time": datetime(2026, 10, 21, 15, tzinfo=TZ).timestamp(), "duration_seconds": 40 * 60, "status": "in_progress"},
        ]},
        "chores": {"status": "SUCCESS", "chores": [
            {"id": "50", "title": "Trash", "completed": True},
            {"id": "51", "title": "Dishes", "completed": False},
        ]},
        "sync": {
            "checks": [
                {"id": 1, "day": "2026-10-19", "chore_id": "9", "title": "Bed", "status": "approved"},
                {"id": 2, "day": "2026-10-21", "chore_id": "50", "title": "Trash", "status": "pending"},
            ],
            "new": [{"id": 2, "day": "2026-10-21", "chore_id": "50", "title": "Trash", "status": "pending"}],
            "parents": ["dad", "mom"],
            "bonus_by_day": {},
        },
        "decide": (200, {"check": {"id": 2, "username": "kid", "chore_id": "51", "title": "Dishes", "status": "declined"},
                         "notify": ["kid"], "untick": True}),
    }

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        async def json(self, content_type=None):
            return self._body

        async def text(self):
            return ""

    def answer(method, url):
        if "/screen-time/kid/rules" in url:
            return 200, world["rules"]
        if url.endswith("/steps"):
            return (200, world["steps"]) if world["steps"] is not None else (503, {})
        if url.endswith("/workouts"):
            return 200, world["workouts"]
        if url.endswith("/skylight/chores"):
            return 200, world["chores"]
        if "/chore-checks/kid/sync" in url:
            return 200, world["sync"]
        if "/screen-time/chore-checks/" in url:
            return world["decide"]
        if url.endswith("/uncomplete"):
            return 200, {"status": "SUCCESS"}
        return 200, {}

    class _Client:
        async def request(self, method, url, **kw):
            calls.append({"method": method, "url": url, **kw})
            return _Resp(*answer(method, url))

        async def get(self, url, **kw):
            calls.append({"method": "GET", "url": url, **kw})
            return _Resp(200, {"days": world["usage"]})

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
    yield TestClient(app, headers={"Authorization": "Bearer kid-key"}), calls, sent, world
    gateway_main._forget_chores()


def test_status_earns_carries_over_and_locks_for_chores(wired):
    client, calls, sent, _ = wired
    body = client.get("/api/screen-time/kid/status").json()
    # Monday earned 10 (steps) + 10 (approved chore), used none of it: 20 carried.
    # Tuesday's 30 minutes of exercise earned 20, and Tuesday used nothing.
    assert body["banked_min"] == 40
    # Today: 3000 steps = 15; the pending chore earns nothing yet; the
    # workout still under way earns nothing.
    assert body["earned_min"] == 15
    assert body["remaining_min"] == 60 + 40 + 15 - 60
    assert (body["locked"], body["reason"], body["chores_left"]) == (True, "chores", 1)
    assert body["chores_waiting"] == [{"id": 2, "title": "Trash", "day": "2026-10-21"}]
    sync = next(c for c in calls if "/sync" in c["url"])
    assert sync["json"] == {"day": "2026-10-21", "week_start": "2026-10-19", "done": [{"id": "50", "title": "Trash"}]}
    # Both parents hear about the newly ticked-off chore, with the answer on the notice.
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert "Trash" in sent[0]["body"]
    assert [a["label"] for a in sent[0]["actions"]] == ["Done right", "Redo it"]
    assert sent[0]["actions"][0]["params"] == {"id": 2, "approve": True}


def test_skylight_is_asked_at_most_once_in_a_while(wired):
    client, calls, _, _ = wired
    client.get("/api/screen-time/kid/status")
    client.get("/api/screen-time/kid/status")
    assert sum(1 for c in calls if c["url"].endswith("/skylight/chores")) == 1


def test_a_chart_that_cannot_be_read_neither_locks_nor_changes_checks(wired):
    client, calls, _, world = wired
    world["chores"] = {"status": "FAILURE", "message": "Skylight not configured"}
    world["sync"] = {**world["sync"], "new": []}
    body = client.get("/api/screen-time/kid/status").json()
    assert body["locked"] is False and body["chores_left"] is None
    assert next(c for c in calls if "/sync" in c["url"])["json"]["done"] is None


def test_steps_that_cannot_be_read_earn_nothing_but_the_rest_still_counts(wired):
    client, _, _, world = wired
    world["steps"] = None
    body = client.get("/api/screen-time/kid/status").json()
    assert body["earned_min"] == 0 and body["banked_min"] == 30  # Monday's chore + Tuesday's exercise


def test_rules_without_earning_never_ask_for_steps_or_chores(wired):
    client, calls, _, world = wired
    world["rules"] = {"rules": {"daily_limit_min": 60}, "bonus_min": 0}
    body = client.get("/api/screen-time/kid/status").json()
    assert body["earned_min"] == 0 and "chores_waiting" not in body
    assert not any(c["url"].endswith(("/steps", "/workouts", "/skylight/chores")) for c in calls)


def test_a_redo_unticks_the_chore_on_skylight_and_tells_the_child(wired):
    client, calls, sent, _ = wired
    r = client.post("/api/screen-time/chore-checks/2", json={"approve": False})
    assert r.status_code == 200 and r.json()["unticked"] is True
    untick = next(c for c in calls if c["url"].endswith("/uncomplete"))
    assert "/skylight/chores/51/uncomplete" in untick["url"] and untick["params"] == {"user": "kid"}
    assert [n["user"] for n in sent] == ["kid"] and "redo" in sent[0]["body"]


def test_an_approval_does_not_untick(wired):
    client, calls, sent, world = wired
    world["decide"] = (200, {"check": {"id": 2, "username": "kid", "chore_id": "50", "title": "Trash", "status": "approved"},
                             "notify": ["kid"], "untick": False})
    assert client.post("/api/screen-time/chore-checks/2", json={"approve": True}).status_code == 200
    assert not any(c["url"].endswith("/uncomplete") for c in calls)
    assert sent[0]["title"] == "Chore approved"


def test_a_refused_answer_is_passed_on(wired):
    client, calls, sent, world = wired
    world["decide"] = (403, {"detail": "Only a parent can answer"})
    assert client.post("/api/screen-time/chore-checks/2", json={"approve": True}).status_code == 403
    assert sent == [] and not any(c["url"].endswith("/uncomplete") for c in calls)
