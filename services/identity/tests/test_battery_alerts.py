"""Low phone battery: the family hears once per discharge, again only after
the phone has been charged."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.common import notify
from services.identity import battery_alerts
from services.identity import main as identity_main
from services.identity.models import User

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


def test_charging_arms_it_again_for_the_next_discharge(family):
    report, sent = family
    report(14)
    assert len(sent) == 2
    report(13, charging=True)
    report(12)
    assert len(sent) == 4, "charging reset it, so the next low reading announces again"
    report(15)  # rising: plugged in
    report(14)
    assert len(sent) == 6


def test_a_phone_low_but_charging_is_not_news(family):
    report, sent = family
    report(10, charging=True)
    assert sent == []


@pytest.mark.parametrize("previous,reading,charging,expected", [
    (None, 50, None, (False, False)),
    (None, 14, None, (True, True)),
    ({"battery": 14, "battery_low_sent": True}, 13, None, (False, True)),
    ({"battery": 14, "battery_low_sent": True}, 15, None, (False, False)),
    ({"battery": 14, "battery_low_sent": True}, None, None, (False, True)),
])
def test_step(previous, reading, charging, expected):
    assert battery_alerts.step(previous, reading, charging) == expected
