"""The phone's charging state and next alarm ride the location upload to geo.

homehub's battery_state, charger_type, is_charging and next_alarm sensors read
them from geo, so identity must pass them on, and an older app that sends none
of them must forward exactly what it did before.
"""
import asyncio

import pytest

from services.identity import main as identity_main


class _Resp:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, sent):
        self.sent = sent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None, timeout=None):
        self.sent.append((url, json))
        return _Resp()


@pytest.fixture
def geo(monkeypatch):
    from services import config
    from services.common import http

    sent: list = []
    monkeypatch.setattr(config, "GEO_SVC_URL", "http://geo.invalid:8000")
    monkeypatch.setattr(http, "get_client_insecure", lambda: _Client(sent))
    return sent


def _forward(**fields):
    update = identity_main.LocationUpdate(latitude=1.0, longitude=2.0, accuracy=8.0, battery=55, **fields)
    asyncio.run(identity_main._forward_location_to_geo("sam", update))


def test_status_fields_reach_geo(geo):
    _forward(charging=True, battery_state="charging", charger_type="wireless",
             next_alarm_ms=1_900_000_000_000, next_alarm_package="com.example.clock")
    url, payload = geo[0]
    assert url.endswith("/people/sam/record")
    assert payload["battery"] == 55
    assert payload["charging"] is True
    assert payload["battery_state"] == "charging"
    assert payload["charger_type"] == "wireless"
    assert payload["next_alarm_ms"] == 1_900_000_000_000
    assert payload["next_alarm_package"] == "com.example.clock"


def test_no_alarm_is_forwarded_as_zero_not_dropped(geo):
    _forward(next_alarm_ms=0)
    assert geo[0][1]["next_alarm_ms"] == 0


def test_an_older_app_forwards_no_status_fields(geo):
    _forward()
    payload = geo[0][1]
    for field in ("charging", "battery_state", "charger_type", "next_alarm_ms", "next_alarm_package"):
        assert field not in payload


def test_the_family_cards_get_a_charging_flag(monkeypatch):
    import json

    from fastapi.testclient import TestClient
    from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

    from services.identity.models import GlobalSetting, User

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(identity_main, "engine", engine)
    with Session(engine) as session:
        session.add(User(username="sam", display_name="Sam"))
        session.commit()

    async def nowhere(*a, **kw):
        return None

    monkeypatch.setattr(identity_main, "_forward_location_to_ha", nowhere)
    monkeypatch.setattr(identity_main, "_forward_location_to_geo", nowhere)
    client = TestClient(identity_main.app)

    def stored(t, **fields):
        body = {"latitude": 1.0, "longitude": 2.0, "accuracy": 8, "battery": 90, "timestamp": t, **fields}
        resp = client.post("/api/users/sam/location", headers={"X-Internal-Secret": "test-secret"}, json=body)
        assert resp.status_code == 200
        with Session(engine) as session:
            row = session.exec(select(GlobalSetting).where(GlobalSetting.key == "user_location:sam")).first()
            return json.loads(row.value)

    assert stored(1_800_000_000.0, battery_state="full", charger_type="ac")["charging"] is True
    assert stored(1_800_000_010.0, battery_state="discharging", charging=True)["charging"] is False
    assert stored(1_800_000_020.0, charging=True)["charging"] is True  # an older build's flag
    assert stored(1_800_000_030.0)["charging"] is None  # not reported: not guessed
