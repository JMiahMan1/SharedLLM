"""Several devices signed in as one person: only the lead says where they are.

In production a tablet left at home reported Jeremiah's location every ten
minutes (exactly at home, battery stuck at 72%) while his phone was 17 km
away; each tablet fix brought him "home" and the next phone fix had him
"leave" again, five times in one hour.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity import location_devices as ld
from services.identity import main as identity_main
from services.identity.models import GlobalSetting, User

HOME = (33.1667, -111.5646)
WORK = (33.2851, -111.6846)   # 17 km away
T0 = 1_800_000_000.0


def test_one_device_always_counts():
    devices = {}
    assert ld.accept(devices, "phone", "Pixel 10", *HOME, 10, T0, T0)
    assert ld.accept(devices, "phone", "Pixel 10", *WORK, 10, T0 + 60, T0 + 60)


def test_a_tablet_parked_at_home_does_not_bring_someone_home():
    devices = {}
    ld.accept(devices, "tablet", "Tab", *HOME, 100, T0, T0)
    ld.accept(devices, "phone", "Pixel", *HOME, 10, T0 + 10, T0 + 10)
    # The phone drives to work: it moved, so it leads.
    assert ld.accept(devices, "phone", "Pixel", *WORK, 10, T0 + 1800, T0 + 1800)
    # The tablet's ten-minute report from home is noted, not counted.
    assert not ld.accept(devices, "tablet", "Tab", *HOME, 100, T0 + 2400, T0 + 2400)
    assert ld.accept(devices, "phone", "Pixel", *WORK, 15, T0 + 2460, T0 + 2460)
    assert devices["tablet"]["t"] == T0 + 2400   # still kept, for the record


def test_a_phone_that_dies_on_a_trip_does_not_hand_over_to_the_tablet_at_once():
    devices = {}
    ld.accept(devices, "tablet", "Tab", *HOME, 100, T0, T0)
    ld.accept(devices, "phone", "Pixel", *HOME, 10, T0 + 10, T0 + 10)
    ld.accept(devices, "phone", "Pixel", *WORK, 10, T0 + 1800, T0 + 1800)
    # Phone silent for an hour: the tablet at home still does not count.
    assert not ld.accept(devices, "tablet", "Tab", *HOME, 100, T0 + 5400, T0 + 5400)
    # Silent for longer than LEAD_STALE_S: the most recent voice takes over.
    later = T0 + 1800 + ld.LEAD_STALE_S + 60
    assert ld.accept(devices, "tablet", "Tab", *HOME, 100, later, later)


def test_a_new_phone_takes_over_once_it_moves():
    devices = {}
    ld.accept(devices, "old", "Old", *HOME, 10, T0, T0)
    ld.accept(devices, "old", "Old", *WORK, 10, T0 + 600, T0 + 600)      # old phone moved
    assert not ld.accept(devices, "new", "New", *WORK, 10, T0 + 700, T0 + 700)
    assert ld.accept(devices, "new", "New", *HOME, 10, T0 + 2400, T0 + 2400)   # new phone moved


def test_jitter_within_a_fixs_uncertainty_is_not_moving():
    devices = {}
    ld.accept(devices, "tablet", "Tab", *HOME, 100, T0, T0)
    # 350 m off with 90 m uncertainty: within MOVE_M + accuracy, not a move.
    ld.note_fix(devices, "tablet", "Tab", HOME[0] + 0.00315, HOME[1], 90, T0 + 600)
    assert devices["tablet"]["moved_at"] == 0.0
    # Vaguer than MAX_MOVE_ACCURACY_M says nothing about moving at all.
    ld.note_fix(devices, "tablet", "Tab", *WORK, 500, T0 + 1200)
    assert devices["tablet"]["moved_at"] == 0.0


def test_device_ids_are_bounded_and_old_builds_share_one_key():
    assert ld.device_key(None) == ld.UNKNOWN_DEVICE
    assert ld.device_key("") == ld.UNKNOWN_DEVICE
    assert ld.device_key("abc-123_x") == "abc-123_x"
    assert ld.device_key("a/../b;drop") == "abdrop"
    assert len(ld.device_key("x" * 500)) == 64


def test_devices_unheard_for_a_month_are_forgotten():
    devices = {}
    ld.accept(devices, "gone", "Old", *HOME, 10, T0, T0)
    later = T0 + ld.FORGET_S + 60
    ld.accept(devices, "phone", "Pixel", *HOME, 10, later, later)
    assert "gone" not in devices


@pytest.fixture
def upload(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(identity_main, "engine", engine)
    with Session(engine) as session:
        session.add(User(username="default", is_system_default=True, is_admin=True))
        session.add(User(username="jeremiah", display_name="Jeremiah"))
        session.commit()
    forwarded = []

    async def to_geo(user_id, location):
        forwarded.append(location.device_id)

    async def nowhere(*a, **kw):
        return None

    monkeypatch.setattr(identity_main, "_forward_location_to_ha", nowhere)
    monkeypatch.setattr(identity_main, "_forward_location_to_geo", to_geo)
    client = TestClient(identity_main.app)

    def send(device, lat, lon, t, acc=10, name=None):
        body = {"latitude": lat, "longitude": lon, "accuracy": acc, "timestamp": t,
                "device_id": device, "device_name": name or device}
        resp = client.post("/api/users/jeremiah/location", headers={"X-Internal-Secret": "test-secret"}, json=body)
        assert resp.status_code == 200
        return resp.json()

    def position():
        with Session(engine) as session:
            row = session.exec(select(GlobalSetting).where(GlobalSetting.key == "user_location:jeremiah")).first()
            return json.loads(row.value)

    return send, position, forwarded


def test_the_tablet_at_home_does_not_move_the_person(upload):
    import time
    send, position, forwarded = upload
    now = time.time()
    send("tablet", *HOME, now - 3000, acc=100, name="Tab A9")
    send("phone", *HOME, now - 2990, name="Pixel 10")
    send("phone", *WORK, now - 1200, name="Pixel 10")
    reply = send("tablet", *HOME, now - 600, acc=100, name="Tab A9")
    assert reply["counted"] is False
    where = position()
    assert (round(where["latitude"], 4), round(where["longitude"], 4)) == WORK
    assert where["device"] == "Pixel 10"
    assert forwarded.count("tablet") == 1          # only before the phone moved
