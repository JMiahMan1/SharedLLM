"""Tests for the per-device activity endpoint.

A device row opens into this. The behaviours pinned here are the ones that
make that safe and useful rather than merely present:

* the allowlist still decides what can appear -- an event that happened is
  kept even when the small scalars beside it cannot be read, because dropping
  the whole row would hide that the device reported at all;
* the window and the limit are the caller's to choose but not their bounds to
  exceed, so a big ``hours`` cannot turn into an unbounded scan;
* a device belongs to its reader: another reader is refused, an admin is not.
"""
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import Device, DeviceEvent, User

REGISTER = "/api/user-panel/devices/register"
TELEMETRY = "/api/user-panel/devices/telemetry"


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_user(session: Session, username: str, *, is_admin: bool = False) -> User:
    user = User(username=username, display_name=username.title(), is_admin=is_admin)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def login(session: Session, username: str) -> TestClient:
    """Authenticate as ``username`` from here on.

    The override is global to the app, so switching caller mid-test is the
    honest way to express "the same route, a different user".
    """
    user = session.exec(select(User).where(User.username == username)).first()
    assert user is not None, f"no such user: {username}"
    app.dependency_overrides[require_api_key] = lambda: user
    return TestClient(app)


def _client(session: Session, caller: str = "michele") -> TestClient:
    main.engine = session.bind
    SQLModel.metadata.create_all(main.engine)
    for name, admin in (("jeremiah", True), ("michele", False), ("kate", False)):
        if session.exec(select(User).where(User.username == name)).first() is None:
            _make_user(session, name, is_admin=admin)
    session.commit()
    app.dependency_overrides[require_internal] = lambda: True
    return login(session, caller)


@pytest.fixture
def client(session: Session):
    yield _client(session, "michele")
    app.dependency_overrides = {}


def _phone_payload(**over):
    body = {
        "device_key": "phone-abc12345",
        "model": "Pixel 7",
        "manufacturer": "Google",
        "os_version": "14",
        "app_version": "1.5.0",
        "app_build": "24",
    }
    body.update(over)
    return body


def _register(client: TestClient, **over) -> str:
    resp = client.post(REGISTER, json=_phone_payload(**over))
    assert resp.status_code == 200, resp.text
    return resp.json()["device_key"]


def _report(client: TestClient, key: str, event: str, **extra):
    return client.post(
        TELEMETRY,
        json={"device_key": key, "events": [{"event": event, "extra": extra}]},
    )


class TestWhatADeviceHasReported:
    def test_activity_is_built_from_the_events_that_arrived(self, client: TestClient):
        key = _register(client)
        _report(client, key, "app_open")
        _report(client, key, "app_open")
        _report(client, key, "battery", pct=72.0, usb=False)
        body = client.get(f"/api/user-panel/devices/{key}/activity").json()
        assert body["counts"] == {"app_open": 2, "battery": 1}
        assert len(body["events"]) == 3

    def test_the_newest_event_is_first(self, client: TestClient, session: Session):
        key = _register(client)
        now = datetime.now()
        for offset, event in ((2, "app_open"), (1, "battery")):
            session.add(
                DeviceEvent(
                    device_key=key,
                    username="michele",
                    event=event,
                    extra="{}",
                    at=(now - timedelta(hours=offset)).isoformat(),
                )
            )
        session.commit()
        body = client.get(f"/api/user-panel/devices/{key}/activity").json()
        assert [e["event"] for e in body["events"]] == ["battery", "app_open"]

    def test_the_small_scalars_come_back_with_the_event(self, client: TestClient):
        key = _register(client)
        _report(client, key, "battery", pct=72.0, usb=False, cell_v=3.9)
        body = client.get(f"/api/user-panel/devices/{key}/activity").json()
        assert body["events"][0]["extra"] == {"pct": 72.0, "usb": False, "cell_v": 3.9}

    def test_an_unreadable_extra_keeps_the_event_and_drops_only_the_extras(
        self, client: TestClient, session: Session
    ):
        key = _register(client)
        session.add(DeviceEvent(device_key=key, username="michele", event="app_open", extra="{not json"))
        session.commit()
        body = client.get(f"/api/user-panel/devices/{key}/activity").json()
        assert body["counts"] == {"app_open": 1}
        assert body["events"][0]["extra"] == {}

    def test_a_device_with_nothing_to_report_says_so_by_being_empty(self, client: TestClient):
        key = _register(client)
        body = client.get(f"/api/user-panel/devices/{key}/activity").json()
        assert body["counts"] == {} and body["events"] == []

    def test_the_device_record_travels_with_its_activity(self, client: TestClient):
        key = _register(client)
        _report(client, key, "app_open")
        body = client.get(f"/api/user-panel/devices/{key}/activity").json()
        assert body["device"]["device_key"] == key
        assert body["device"]["model"] == "Pixel 7"
        assert body["device"]["app_version"] == "1.5.0"
        assert body["last_seen_at"] is not None


class TestTheWindowAndTheLimit:
    def test_an_event_older_than_the_window_is_left_out(self, client: TestClient, session: Session):
        key = _register(client)
        session.add(
            DeviceEvent(
                device_key=key,
                username="michele",
                event="app_open",
                extra="{}",
                at="2026-01-01T00:00:00",
            )
        )
        session.add(
            DeviceEvent(
                device_key=key,
                username="michele",
                event="battery",
                extra="{}",
                at="2099-01-01T00:00:00",
            )
        )
        session.commit()
        body = client.get(f"/api/user-panel/devices/{key}/activity?hours=24").json()
        assert body["counts"] == {"battery": 1}

    def test_the_limit_caps_the_events_but_not_the_counts(self, client: TestClient):
        key = _register(client)
        for _ in range(5):
            _report(client, key, "app_open")
        body = client.get(f"/api/user-panel/devices/{key}/activity?limit=2").json()
        assert len(body["events"]) == 2
        assert body["counts"] == {"app_open": 5}

    @pytest.mark.parametrize("query", ["hours=0", "hours=99999", "limit=0", "limit=999"])
    def test_an_out_of_range_query_is_refused(self, client: TestClient, query: str):
        key = _register(client)
        assert client.get(f"/api/user-panel/devices/{key}/activity?{query}").status_code == 422


class TestWhoseDeviceItIs:
    def test_an_unknown_device_is_a_404(self, client: TestClient):
        resp = client.get("/api/user-panel/devices/never-seen/activity")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "No such device"

    def test_another_readers_device_is_refused(self, client: TestClient, session: Session):
        key = _register(client)
        resp = login(session, "kate").get(f"/api/user-panel/devices/{key}/activity")
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Not your device"

    def test_an_admin_may_read_any_device(self, client: TestClient, session: Session):
        key = _register(client)
        _report(client, key, "app_open")
        body = login(session, "jeremiah").get(f"/api/user-panel/devices/{key}/activity").json()
        assert body["counts"] == {"app_open": 1}

    def test_the_response_carries_no_field_outside_the_allowlist(self, client: TestClient):
        key = _register(client)
        _report(client, key, "app_open")
        body = client.get(f"/api/user-panel/devices/{key}/activity").json()
        allowed = set(main.NO_OPT_IN_EVENTS)
        assert {e["event"] for e in body["events"]} <= allowed
        assert json.loads(json.dumps(body)) == body

    def test_a_revoked_device_still_answers_its_owner(self, client: TestClient, session: Session):
        """Revoking stops reports being accepted, not the history being read:
        the reader is asking what this device did, and it did do it."""
        key = _register(client)
        _report(client, key, "app_open")
        row = session.exec(select(Device).where(Device.device_key == key)).first()
        assert row is not None
        row.revoked = True
        session.add(row)
        session.commit()
        assert client.get(f"/api/user-panel/devices/{key}/activity").status_code == 200
