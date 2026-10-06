"""Tests for the User Panel device registry endpoints.

The behaviours pinned here are the ones that make the registry safe rather than
merely functional:

* a phone is a phone -- ``kind`` is not client-controlled, and an existing
  assistant/light cannot be silently reclassified by whoever holds its key;
* the caller's address is read from the request, never accepted from the body,
  so nobody can write an arbitrary IP into someone else's device row;
* assignment is admin-side, because a device able to claim an owner would let
  anyone reassign the family's assistant;
* telemetry fails closed on the ``NO_OPT_IN_EVENTS`` allowlist, so content
  cannot be smuggled in under a plausible-sounding name.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import CapabilityInventory, Device, DeviceEvent, User

REGISTER = "/api/user-panel/devices/register"


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
    """Authenticate as `username` from here on.

    The override is global to the app, so switching caller mid-test is the
    honest way to express "the same route, a different user" -- which is
    exactly what the admin-only rules need to be checked against.
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


class TestSelfRegistration:
    def test_a_phone_registers_itself_on_login(self, client: TestClient, session: Session):
        resp = client.post(REGISTER, json=_phone_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "phone"
        assert body["owner_username"] == "michele"
        assert body["registered_by"] == "self"
        assert body["model"] == "Pixel 7"
        assert body["app_build"] == "24"

    def test_logging_in_twice_does_not_create_a_second_device(self, client: TestClient, session: Session):
        client.post(REGISTER, json=_phone_payload())
        client.post(REGISTER, json=_phone_payload(app_build="25"))
        rows = session.exec(select(Device)).all()
        assert len(rows) == 1
        # ...and it is updated rather than left stale.
        assert rows[0].app_build == "25"

    def test_the_address_comes_from_the_request_not_the_body(self, client: TestClient, session: Session):
        """A client-supplied IP would let a caller plant an address on someone
        else's device row, which the panel then shows as fact."""
        resp = client.post(
            REGISTER,
            json=_phone_payload(last_ip_address="1.2.3.4"),
            headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
        )
        assert resp.status_code == 200
        # 1.2.3.4 was never a field on the schema, and the header wins.
        assert resp.json()["last_ip_address"] == "203.0.113.9"

    def test_kind_is_not_client_controlled(self, client: TestClient):
        """A phone that claimed kind=light would move itself out of the
        advanced panel, so the field is not accepted from the client."""
        resp = client.post(REGISTER, json=_phone_payload(kind="light"))
        assert resp.status_code == 200
        assert resp.json()["kind"] == "phone"

    def test_a_short_key_is_rejected(self, client: TestClient):
        assert client.post(REGISTER, json={"device_key": "abc"}).status_code == 422

    def test_last_seen_is_recorded(self, client: TestClient):
        assert client.post(REGISTER, json=_phone_payload()).json()["last_seen_at"]


class TestDeviceReclassification:
    def test_a_phone_cannot_take_over_an_admin_registered_assistant(self, client: TestClient, session: Session):
        assert login(session, "jeremiah").post(
            "/api/user-panel/devices",
            json={"device_key": "assistant-1", "kind": "assistant", "label": "Kitchen"},
        ).status_code == 200

        resp = client.post(REGISTER, json=_phone_payload(device_key="assistant-1"))
        assert resp.status_code == 409
        assert "another kind" in resp.json()["detail"]

    def test_the_assistant_is_left_intact_afterwards(self, client: TestClient, session: Session):
        login(session, "jeremiah").post("/api/user-panel/devices", json={"device_key": "assistant-1", "kind": "assistant"})
        client.post(REGISTER, json=_phone_payload(device_key="assistant-1"))

        device = session.exec(select(Device)).first()
        assert device.kind == "assistant"
        assert device.registered_by == "admin"
        assert device.owner_username is None


class TestVisibility:
    def test_a_normal_user_sees_only_their_own(self, client: TestClient, session: Session):
        client.post(REGISTER, json=_phone_payload())
        keys = {d["device_key"] for d in client.get("/api/user-panel/devices").json()}
        assert keys == {"phone-abc12345"}

        login(session, "kate").post(REGISTER, json=_phone_payload(device_key="phone-kate99"))
        assert len(client.get("/api/user-panel/devices").json()) == 1

    def test_an_admin_sees_every_device(self, session: Session):
        _client(session)
        login(session, "michele").post(REGISTER, json=_phone_payload())
        login(session, "kate").post(REGISTER, json=_phone_payload(device_key="phone-kate99"))

        keys = {d["device_key"] for d in login(session, "jeremiah").get("/api/user-panel/devices").json()}
        assert keys == {"phone-abc12345", "phone-kate99"}


class TestAdminRegistration:
    def test_phones_may_not_be_created_by_an_admin(self, client: TestClient, session: Session):
        """A phone registers itself; an admin creating one would bypass the
        idempotency and let a stale duplicate exist."""
        resp = login(session, "jeremiah").post("/api/user-panel/devices", json={"device_key": "p1", "kind": "phone"})
        assert resp.status_code == 422
        assert "themselves" in resp.json()["detail"]

    def test_an_unknown_kind_is_rejected(self, session: Session):
        _client(session)
        resp = login(session, "jeremiah").post("/api/user-panel/devices", json={"device_key": "x1", "kind": "toaster"})
        assert resp.status_code == 422

    def test_a_normal_user_may_not_register_a_light(self, client: TestClient):
        assert client.post("/api/user-panel/devices", json={"device_key": "l1", "kind": "light"}).status_code == 403

    def test_a_light_needs_no_account_of_its_own(self, session: Session):
        _client(session)
        resp = login(session, "jeremiah").post(
            "/api/user-panel/devices",
            json={"device_key": "light-1", "kind": "light", "label": "Porch", "capabilities": {"dimmable": True}},
        )
        assert resp.status_code == 200
        assert resp.json()["owner_username"] is None
        assert resp.json()["capabilities"] == {"dimmable": True}

    def test_a_duplicate_key_is_refused(self, session: Session):
        _client(session)
        login(session, "jeremiah").post("/api/user-panel/devices", json={"device_key": "light-1", "kind": "light"})
        assert login(session, "jeremiah").post("/api/user-panel/devices", json={"device_key": "light-1", "kind": "light"}).status_code == 409

    def test_assigning_to_a_user_that_does_not_exist_is_refused(self, session: Session):
        _client(session)
        resp = login(session, "jeremiah").post(
            "/api/user-panel/devices",
            json={"device_key": "light-2", "kind": "light", "owner_username": "nobody"},
        )
        assert resp.status_code == 404


class TestAssignment:
    def test_assignment_is_admin_only(self, client: TestClient, session: Session):
        login(session, "jeremiah").post("/api/user-panel/devices", json={"device_key": "light-1", "kind": "light"})
        login(session, "michele")
        resp = client.patch("/api/user-panel/devices/light-1", json={"owner_username": "michele"})
        assert resp.status_code == 403

    def test_an_admin_can_assign(self, session: Session):
        _client(session)
        login(session, "jeremiah").post("/api/user-panel/devices", json={"device_key": "light-1", "kind": "light"})
        resp = login(session, "jeremiah").patch("/api/user-panel/devices/light-1", json={"owner_username": "michele"})
        assert resp.status_code == 200
        assert resp.json()["owner_username"] == "michele"

    def test_an_admin_can_unassign(self, session: Session):
        _client(session)
        admin = login(session, "jeremiah")
        admin.post("/api/user-panel/devices", json={"device_key": "light-1", "kind": "light", "owner_username": "jeremiah"})
        resp = admin.patch("/api/user-panel/devices/light-1", json={"owner_username": ""})
        assert resp.status_code == 200
        assert resp.json()["owner_username"] is None

    def test_an_unknown_device_is_404(self, session: Session):
        _client(session)
        assert login(session, "jeremiah").patch("/api/user-panel/devices/ghost", json={"owner_username": "michele"}).status_code == 404


class TestTelemetryIngest:
    def _registered(self, client: TestClient) -> str:
        client.post(REGISTER, json=_phone_payload())
        return "phone-abc12345"

    def test_a_usage_event_is_recorded(self, client: TestClient, session: Session):
        key = self._registered(client)
        resp = client.post(
            "/api/user-panel/devices/telemetry",
            json={"device_key": key, "events": [{"event": "app_open", "extra": {"from": "widget"}}]},
        )
        assert resp.status_code == 200
        assert resp.json() == {"written": 1, "rejected": []}
        stored = session.exec(select(DeviceEvent)).all()
        assert len(stored) == 1
        assert json.loads(stored[0].extra) == {"from": "widget"}
        assert stored[0].username == "michele"

    @pytest.mark.parametrize(
        "event", ["transcript", "message_sent", "location_update", "steps", "health_sync"]
    )
    def test_content_events_are_rejected(self, client: TestClient, session: Session, event: str):
        key = self._registered(client)
        resp = client.post("/api/user-panel/devices/telemetry", json={"device_key": key, "events": [{"event": event}]})
        assert resp.json() == {"written": 0, "rejected": [event]}
        assert session.exec(select(DeviceEvent)).all() == []

    def test_a_mix_reports_only_the_rejected_ones(self, client: TestClient):
        key = self._registered(client)
        resp = client.post(
            "/api/user-panel/devices/telemetry",
            json={
                "device_key": key,
                "events": [{"event": "app_open"}, {"event": "transcript"}, {"event": "feature_use"}],
            },
        )
        assert resp.json() == {"written": 2, "rejected": ["transcript"]}

    def test_an_oversized_extra_is_refused(self, client: TestClient):
        key = self._registered(client)
        resp = client.post(
            "/api/user-panel/devices/telemetry",
            json={"device_key": key, "events": [{"event": "app_open", "extra": {"blob": "x" * 3000}}]},
        )
        assert resp.json()["written"] == 0
        assert "too large" in resp.json()["rejected"][0]

    def test_an_unregistered_device_is_refused(self, client: TestClient):
        resp = client.post(
            "/api/user-panel/devices/telemetry", json={"device_key": "never-seen", "events": [{"event": "app_open"}]}
        )
        assert resp.status_code == 404

    def test_you_cannot_write_to_someone_elses_device(self, session: Session):
        _client(session)
        login(session, "michele").post(REGISTER, json=_phone_payload())
        kate = login(session, "kate")
        resp = kate.post(
            "/api/user-panel/devices/telemetry",
            json={"device_key": "phone-abc12345", "events": [{"event": "app_open"}]},
        )
        assert resp.status_code == 403

    def test_the_event_count_is_capped(self, client: TestClient, session: Session):
        key = self._registered(client)
        client.post(
            "/api/user-panel/devices/telemetry",
            json={"device_key": key, "events": [{"event": "app_open"}] * 500},
        )
        assert len(session.exec(select(DeviceEvent)).all()) == 200


class TestCapabilities:
    def test_inventory_is_appended_not_overwritten(self, client: TestClient, session: Session):
        """A gap only means something against what the device could do *then*,
        so a later capability report must not erase the earlier one."""
        client.post(REGISTER, json=_phone_payload())
        for caps in ({"climate": False}, {"climate": True}):
            resp = client.post(
                "/api/user-panel/devices/{}/capabilities".format("phone-abc12345"),
                json={"capabilities": caps, "esphome_version": "2026.1"},
            )
            assert resp.status_code == 200

        rows = session.exec(select(CapabilityInventory)).all()
        assert len(rows) == 2
        assert json.loads(rows[0].capabilities) == {"climate": False}
        assert json.loads(rows[1].capabilities) == {"climate": True}

    def test_the_current_capabilities_land_on_the_device_row(self, client: TestClient):
        client.post(REGISTER, json=_phone_payload())
        client.post(
            "/api/user-panel/devices/phone-abc12345/capabilities", json={"capabilities": {"climate": True}}
        )
        device = client.get("/api/user-panel/devices").json()[0]
        assert device["capabilities"] == {"climate": True}

    def test_capabilities_must_be_an_object(self, client: TestClient):
        client.post(REGISTER, json=_phone_payload())
        resp = client.post("/api/user-panel/devices/phone-abc12345/capabilities", json={"capabilities": "lots"})
        assert resp.status_code == 422


CLAIM = "/api/internal/devices/claim"


def _claim(**over):
    body = {"device_key": "esphome:744dbd2c9728", "kind": "watch", "label": "Jarvis Watch",
            "owner_username": "michele", "verified": True, "esphome_version": "2026.9.0",
            "hardware": "esp32s3", "ip_address": "192.168.2.105"}
    body.update(over)
    return body


class TestCompanionClaim:
    """A user adds a companion device through Jarvis; execution calls claim."""

    def test_pairing_links_the_device_to_the_user_who_added_it(self, client: TestClient, session: Session):
        body = client.post(CLAIM, json=_claim()).json()
        assert body["kind"] == "watch"
        assert body["owner_username"] == "michele"
        assert body["registered_by"] == "paired"
        assert body["last_ip_address"] == "192.168.2.105"
        # ...and it shows up in that user's own device list.
        mine = client.get("/api/user-panel/devices").json()
        assert [d["device_key"] for d in mine] == ["esphome:744dbd2c9728"]

    def test_a_screenless_device_is_adopted_not_paired(self, client: TestClient):
        body = client.post(CLAIM, json=_claim(device_key="esphome:aa", kind="assistant", verified=False)).json()
        assert body["registered_by"] == "adopted"

    def test_someone_elses_device_moves_only_with_the_code(self, client: TestClient):
        client.post(CLAIM, json=_claim(owner_username="kate"))
        refused = client.post(CLAIM, json=_claim(owner_username="michele", verified=False))
        assert refused.status_code == 409
        assert "kate" in refused.json()["detail"]
        moved = client.post(CLAIM, json=_claim(owner_username="michele", verified=True))
        assert moved.status_code == 200
        assert moved.json()["owner_username"] == "michele"

    def test_a_phone_cannot_be_claimed(self, client: TestClient):
        assert client.post(REGISTER, json=_phone_payload()).status_code == 200
        resp = client.post(CLAIM, json=_claim(device_key="phone-abc12345"))
        assert resp.status_code == 409

    def test_kind_phone_is_not_claimable(self, client: TestClient):
        assert client.post(CLAIM, json=_claim(kind="phone")).status_code == 422

    def test_unknown_owner_is_an_error(self, client: TestClient):
        assert client.post(CLAIM, json=_claim(owner_username="nobody")).status_code == 404

    def test_claim_requires_the_internal_secret(self, session: Session):
        client = _client(session, "michele")
        app.dependency_overrides.pop(require_internal, None)
        try:
            resp = client.post(CLAIM, json=_claim())
            assert resp.status_code in (401, 403)
        finally:
            app.dependency_overrides = {}
