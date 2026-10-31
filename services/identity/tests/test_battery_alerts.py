"""Low phone battery: the family hears once per discharge, again only after
the phone has been charged, and at most once a day per phone owner."""
import asyncio
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.common import notify
from services.identity import battery_alerts
from services.identity import main as identity_main
from services.identity.models import User, UserActivitySharing

HEADERS = {"X-Internal-Secret": "test-secret"}


@pytest.fixture
def family(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(identity_main, "engine", engine)
    with Session(engine) as session:
        session.add(User(username="default", is_system_default=True, is_admin=True))
        session.add(User(username="leo", display_name="Leo"))
        session.add(User(username="michele", display_name="Michele"))
        session.add(User(username="jeremiah", display_name="Jeremiah"))
        # Leo shares with the family circle (everyone in the live family does).
        session.add(UserActivitySharing(username="leo", data=json.dumps({"enabled": True, "audience": "circle"})))
        session.commit()
    sent = []

    async def send(payload, **kw):
        sent.append(payload)
        return True

    async def nowhere(*a, **kw):
        return None

    monkeypatch.setattr(notify, "send", send)
    monkeypatch.setattr(identity_main, "_forward_location_to_ha", nowhere)
    monkeypatch.setattr(identity_main, "_forward_location_to_geo", nowhere)
    client = TestClient(identity_main.app)

    def report(battery, charging=None):
        body = {"latitude": 33.1, "longitude": -111.5, "battery": battery}
        if charging is not None:
            body["charging"] = charging
        assert client.post("/api/users/leo/location", headers=HEADERS, json=body).status_code == 200

    return report, sent


def test_dropping_below_15_tells_the_rest_of_the_family_once(family):
    report, sent = family
    for level in (40, 16, 14, 12, 9):
        report(level)
    assert sorted(n["user"] for n in sent) == ["jeremiah", "michele"]  # not Leo, not the system account
    notice = sent[0]
    assert notice["category"] == "family_safety" and notice["priority"] == 4
    assert notice["title"] == "Leo's phone is at 14%"
    assert notice["tag"] == "battery:leo" and notice["data"]["url"] == "/wander?person=leo"
    assert notice["once"] == f"battery:leo:{date.today().isoformat()}"


def test_charging_arms_it_again_for_the_next_discharge(family):
    report, sent = family
    report(14)
    assert len(sent) == 2
    report(13, charging=True)
    report(12)
    assert len(sent) == 4, "charging reset it, so the next low reading announces again"
    report(20)  # charged back to LOW_BATTERY_PCT + 5
    report(14)
    assert len(sent) == 6
    # Telemetry drops a repeated ``once`` key: one notice per phone owner a day.
    assert {n["once"] for n in sent} == {f"battery:leo:{date.today().isoformat()}"}


def test_a_reading_bouncing_around_the_threshold_announces_once(family):
    report, sent = family
    for level in (14, 15, 13, 16, 14, 19, 13):
        report(level)
    assert len(sent) == 2


def test_two_phones_on_one_account_do_not_take_turns(family):
    """A low phone and a full one reporting in turn: the full one re-arms the
    alert each time, but every notice carries the day's ``once`` key, so
    telemetry delivers only the first to each person."""
    report, sent = family
    for level in (13, 80, 13, 80, 12):
        report(level)
    assert len({n["once"] for n in sent}) == 1


def test_the_once_key_is_per_day():
    sent = []

    async def capture(users, make, **kw):
        sent.extend(make(u) for u in users)
        return len(sent)

    async def run():
        from services.common import notify as n
        real = n.send_many
        n.send_many = capture
        try:
            await battery_alerts.announce("leo", "Leo", 12, ["leo", "kate"], today=date(2026, 10, 15))
            await battery_alerts.announce("leo", "Leo", 12, ["leo", "kate"], today=date(2026, 10, 16))
        finally:
            n.send_many = real

    asyncio.run(run())
    assert [n["once"] for n in sent] == ["battery:leo:2026-10-15", "battery:leo:2026-10-16"]


def test_a_slow_telemetry_does_not_hold_up_the_upload(family, monkeypatch):
    """The announcement runs after the response: an upload never fails on it."""
    async def broken(*a, **kw):
        raise RuntimeError("telemetry down")

    monkeypatch.setattr(battery_alerts, "announce", broken)
    client = TestClient(identity_main.app, raise_server_exceptions=False)
    resp = client.post("/api/users/leo/location", headers=HEADERS,
                       json={"latitude": 33.1, "longitude": -111.5, "battery": 9})
    assert resp.status_code == 200 and resp.json()["status"] == "SUCCESS"


def test_a_phone_low_but_charging_is_not_news(family):
    report, sent = family
    report(10, charging=True)
    assert sent == []


@pytest.mark.parametrize("previous,reading,charging,expected", [
    (None, 50, None, (False, False)),
    (None, 14, None, (True, True)),
    ({"battery": 14, "battery_low_sent": True}, 13, None, (False, True)),
    ({"battery": 14, "battery_low_sent": True}, 15, None, (False, True)),
    ({"battery": 14, "battery_low_sent": True}, 19, None, (False, True)),
    ({"battery": 14, "battery_low_sent": True}, 20, None, (False, False)),
    ({"battery": 14, "battery_low_sent": True}, 13, True, (False, False)),

    ({"battery": 14, "battery_low_sent": True}, None, None, (False, True)),
])
def test_step(previous, reading, charging, expected):
    assert battery_alerts.step(previous, reading, charging) == expected


def test_only_people_who_may_see_the_phone_hear_about_it(family, monkeypatch):
    """Someone who shares their location with no one: the family is not told
    their phone is dying, any more than it is shown where it is."""
    report, sent = family
    with Session(identity_main.engine) as session:
        row = session.exec(select(UserActivitySharing).where(UserActivitySharing.username == "leo")).one()
        row.data = json.dumps({"enabled": False, "audience": "circle"})
        session.add(row)
        session.commit()
    report(14)
    assert sent == []


def test_a_late_fix_from_the_phones_backlog_does_not_move_the_current_position(family, monkeypatch):
    """The phone sends fixes it kept while offline oldest first; one older than
    the fix held is history (geo), not where the person is now."""
    report, sent = family
    forwarded = []

    async def to_geo(user_id, location):
        forwarded.append(location.timestamp)

    monkeypatch.setattr(identity_main, "_forward_location_to_geo", to_geo)
    client = TestClient(identity_main.app)
    new = {"latitude": 33.5, "longitude": -111.9, "battery": 80, "timestamp": 2_000_000.0}
    old = {"latitude": 33.1, "longitude": -111.5, "battery": 9, "timestamp": 1_000_000.0}
    assert client.post("/api/users/leo/location", headers=HEADERS, json=new).status_code == 200
    resp = client.post("/api/users/leo/location", headers=HEADERS, json=old)
    assert resp.json()["message"] == "Older fix added to the history"
    held = client.get("/api/users/leo/location", headers=HEADERS).json()
    assert (held["latitude"], held["battery"]) == (33.5, 80)
    assert forwarded == [2_000_000.0, 1_000_000.0]  # both still reach the history
    assert sent == []  # the old 9% reading is not a low-battery alert
