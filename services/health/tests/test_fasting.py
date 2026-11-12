"""The fasting window: adults only, started and stopped by the person."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services.health import fasting, fasting_notices, fasting_traditions
from services.health.models import FastingSession

LOCAL_TZ = ZoneInfo("Etc/GMT+7")


@pytest.fixture(autouse=True)
def sent(monkeypatch):
    """Notices the fasting code sends, recorded instead of delivered."""
    out: list[dict] = []

    async def send(payload, **kw):
        out.append(payload)
        return True

    async def verse(ref, username=None):
        return f"[{ref} for {username}]"

    monkeypatch.setattr(fasting_notices.notify, "send", send)
    monkeypatch.setattr(fasting_notices, "verse_text", verse)
    return out


def test_progress_against_a_target():
    start = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
    half = fasting.progress(start, start + timedelta(hours=8), 16)
    assert {k: half[k] for k in ("elapsed_minutes", "target_minutes", "percent", "remaining_minutes", "reached")} == {
        "elapsed_minutes": 480, "target_minutes": 960, "percent": 50, "remaining_minutes": 480, "reached": False,
    }
    assert half["remaining_seconds"] == 8 * 3600
    # Percent is floored: a minute short of the end is not 100.
    assert fasting.progress(start, start + timedelta(hours=16) - timedelta(minutes=1), 16)["percent"] == 99
    done = fasting.progress(start, start + timedelta(hours=17), 16)
    assert done["reached"] is True and done["percent"] == 100 and done["remaining_minutes"] == 0


def test_progress_without_a_target_is_elapsed_time_only():
    start = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
    assert fasting.progress(start, start + timedelta(minutes=90), None)["percent"] is None


def test_start_and_stop_a_fast(client):
    started = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    body = client.post("/fasting/start", json={"started_at": started, "target_hours": 14}).json()
    assert body["target_hours"] == 14
    assert 179 <= body["elapsed_minutes"] <= 181
    assert client.post("/fasting/start", json={}).status_code == 409
    state = client.get("/fasting").json()
    assert state["allowed"] is True and state["active"]["id"] == body["id"]
    stopped = client.post("/fasting/stop", json={}).json()
    assert stopped["ended_at"] is not None
    state = client.get("/fasting").json()
    assert state["active"] is None and len(state["recent"]) == 1
    assert client.delete(f"/fasting/{body['id']}").status_code == 200


def test_a_fast_cannot_start_in_the_future_or_days_ago(client):
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert client.post("/fasting/start", json={"started_at": future}).status_code == 422
    old = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    assert client.post("/fasting/start", json={"started_at": old}).status_code == 422
    assert client.post("/fasting/start", json={"target_hours": 73}).status_code == 422
    assert client.post("/fasting/start", json={"kind": "nonsense"}).status_code == 422


def test_minors_never_get_fasting(client):
    client.put("/profile", json={"kid_mode": True})
    assert client.get("/fasting").json() == {"allowed": False, "active": None, "recent": [], "presets": [12, 14, 16]}
    assert client.post("/fasting/start", json={}).status_code == 403
    teen = {"X-User-Id": "teen"}
    client.put("/profile", json={"birth_year": date.today().year - 16}, headers=teen)
    assert client.post("/fasting/start", json={}, headers=teen).status_code == 403


def test_stopping_with_nothing_running_is_a_404(client):
    assert client.post("/fasting/stop", json={}).status_code == 404


def test_a_shared_fast_shows_its_start_target_and_time_left(client):
    # The client fixture's caller is the fast's owner; "teen" is who it is shared with.
    started = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
    client.post("/fasting/start", json={"started_at": started, "target_hours": 16})
    assert client.put("/shares", json={"viewer": "teen", "scope": "fasting"}).status_code == 200

    people = client.get("/family", headers={"X-User-Id": "teen"}).json()["people"]
    shared = people[0]["fasting"]
    assert people[0]["scopes"] == ["fasting"]
    assert shared["active"]["started_at"] == started
    assert shared["active"]["target_hours"] == 16
    assert 359 <= shared["active"]["remaining_minutes"] <= 361
    assert shared["last"] is None

    client.post("/fasting/stop", json={})
    shared = client.get("/family", headers={"X-User-Id": "teen"}).json()["people"][0]["fasting"]
    assert shared["active"] is None and shared["last"]["ended_at"] is not None


def test_a_fast_is_not_shared_without_the_owner_choosing_to(client):
    client.post("/fasting/start", json={})
    assert client.get("/family", headers={"X-User-Id": "teen"}).json()["people"] == []


def test_a_minor_has_no_fasting_to_share(client):
    client.put("/profile", json={"kid_mode": True})
    assert client.put("/shares", json={"viewer": "parent", "scope": "fasting"}).status_code == 422


def test_a_station_fast_ends_at_the_ninth_hour(client):
    started = datetime(2026, 11, 4, 7, 0, tzinfo=LOCAL_TZ)
    end = fasting_traditions.planned_end("station", started, target_hours=None, days=None, until=None, tz=LOCAL_TZ)
    assert end == datetime(2026, 11, 4, 15, 0, tzinfo=LOCAL_TZ)
    # Begun after 3 PM, it runs to 3 PM the next day.
    late = fasting_traditions.planned_end("station", started.replace(hour=19), target_hours=None, days=None, until=None, tz=LOCAL_TZ)
    assert late == datetime(2026, 11, 5, 15, 0, tzinfo=LOCAL_TZ)
    assert fasting_traditions.planned_end("lent", started, target_hours=None, days=None, until=None, tz=LOCAL_TZ) == started + timedelta(days=40)
    assert fasting_traditions.planned_end("paschal", started, target_hours=None, days=None, until=None, tz=LOCAL_TZ) == started + timedelta(hours=40)


def test_a_fast_of_a_kind_carries_its_name_end_and_season(client):
    body = client.post("/fasting/start", json={"kind": "lent", "intention": "our neighbors"}).json()
    assert body["kind"] == "lent" and body["kind_name"] == "Great Lent (the Forty Days)"
    assert body["days"] == 40 and body["day"] == 1 and body["ends_at"]
    assert body["intention"] == "our neighbors" and body["remaining_seconds"] > 39 * 86400
    client.post("/fasting/stop", json={})
    paschal = client.post("/fasting/start", json={"kind": "paschal"}).json()
    assert paschal["days"] is None and 2399 <= paschal["remaining_minutes"] <= 2400


def test_the_traditions_name_their_sources(client):
    data = client.get("/fasting/traditions").json()
    assert {k["key"] for k in data["kinds"]} >= {"station", "lent", "paschal", "xerophagy", "ember", "baptism"}
    assert all(k["sources"] for k in data["kinds"] if k["key"] != "custom")
    assert "Isaiah 58:3-9" in data["heart"] and data["today"] and data["prayer_hours"][2]["clock"] == "15:00"


def test_those_it_is_shared_with_are_asked_to_pray(client, sent):
    client.put("/shares", json={"viewer": "sam", "scope": "fasting"})
    client.post("/fasting/start", json={"kind": "station", "intention": "a friend's surgery"})
    start = [n for n in sent if n.get("kind") == "fast_shared"]
    assert [n["user"] for n in start] == ["sam"]
    assert "Pray with Alex" in start[0]["title"] and "a friend's surgery" in start[0]["body"]
    client.post("/fasting/stop", json={})
    assert sum(1 for n in sent if n.get("kind") == "fast_shared") == 2


def test_a_private_fast_tells_no_one(client, sent):
    client.put("/shares", json={"viewer": "sam", "scope": "fasting"})
    client.post("/fasting/start", json={"notify_shared": False})
    client.post("/fasting/stop", json={})
    assert not [n for n in sent if n.get("kind") == "fast_shared"]


def _row(**kw) -> FastingSession:
    base = {"id": 7, "user_id": "alex", "started_at": datetime(2026, 11, 4, 6, 0, tzinfo=LOCAL_TZ).isoformat()}
    return FastingSession(**{**base, **kw})


def test_the_hours_of_prayer_are_due_only_inside_the_fast():
    row = _row(ends_at=datetime(2026, 11, 4, 15, 0, tzinfo=LOCAL_TZ).isoformat())
    at_noon = datetime(2026, 11, 4, 12, 10, tzinfo=LOCAL_TZ)
    assert [(what, d.get("clock")) for _, what, d in fasting_notices.due([row], at_noon, LOCAL_TZ)] == [("prayer", "12:00")]
    assert fasting_notices.due([row], datetime(2026, 11, 4, 12, 45, tzinfo=LOCAL_TZ), LOCAL_TZ) == []
    # Reminders off: nothing until the end.
    assert fasting_notices.due([_row(prayer_reminders=False)], at_noon, LOCAL_TZ) == []
    # Before the fast began, the hour is not its own.
    late = _row(started_at=datetime(2026, 11, 4, 12, 5, tzinfo=LOCAL_TZ).isoformat())
    assert fasting_notices.due([late], at_noon, LOCAL_TZ) == []
    done = fasting_notices.due([row], datetime(2026, 11, 4, 15, 3, tzinfo=LOCAL_TZ), LOCAL_TZ)
    assert [what for _, what, _ in done] == ["complete"]



@pytest.mark.asyncio
async def test_an_hour_of_prayer_carries_the_psalm_and_opens_it_in_the_bible(sent, engine, monkeypatch):
    from sqlmodel import Session

    started = datetime(2026, 11, 4, 6, 0, tzinfo=LOCAL_TZ)
    with Session(engine) as session:
        session.add(FastingSession(user_id="alex", started_at=started.isoformat(),
                                   ends_at=datetime(2026, 11, 4, 15, 0, tzinfo=LOCAL_TZ).isoformat()))
        session.commit()
    await fasting_notices.remind_once(lambda: engine, lambda: LOCAL_TZ, now=datetime(2026, 11, 4, 12, 5, tzinfo=LOCAL_TZ))
    prayer = next(n for n in sent if n.get("kind") == "fast_prayer")
    assert prayer["title"] == "Sixth hour: time to pray"
    psalm = prayer["data"]["url"].split("ref=", 1)[1]
    assert prayer["data"]["url"].startswith("/bible?ref=Psalm")
    # The psalm's words are in the message, in the person's translation.
    assert "for alex]" in prayer["body"] and "Lord's Prayer" in prayer["body"]
    assert [a["label"] for a in prayer["actions"]][1] == "Open my fast" and psalm


def test_a_shared_fast_asks_for_prayer_with_a_verse_to_open(client, sent):
    client.put("/shares", json={"viewer": "sam", "scope": "fasting"})
    client.post("/fasting/start", json={"kind": "station"})
    notice = next(n for n in sent if n.get("kind") == "fast_shared")
    assert notice["data"]["url"].startswith("/bible?ref=")
    assert notice["actions"][0]["label"].startswith("Read ")
    assert "for sam]" in notice["body"]


def test_a_running_fasts_start_can_be_corrected_and_its_end_moves_with_it(client):
    started = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    body = client.post("/fasting/start", json={"started_at": started, "target_hours": 36}).json()
    fixed = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    after = client.patch("/fasting/active", json={"started_at": fixed}).json()
    assert 59 <= after["elapsed_minutes"] <= 61
    assert after["started_at"] == datetime.fromisoformat(fixed).isoformat()
    # The fast keeps its length: the planned end moved with the start.
    length = lambda f: datetime.fromisoformat(f["ends_at"]) - datetime.fromisoformat(f["started_at"])  # noqa: E731
    assert length(after) == length(body) == timedelta(hours=36)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert client.patch("/fasting/active", json={"started_at": future}).status_code == 422
    client.post("/fasting/stop", json={})
    assert client.patch("/fasting/active", json={"started_at": fixed}).status_code == 404
