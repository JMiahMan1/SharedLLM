"""Shared hub devices (voice satellites, speakers, TVs) and who a spoken request is from.

The order is fixed: the speaker the voice identifies, else the person the
satellite is assigned to, else the default user. Every name and device here is
made up; the devices arrive as HomeHub would report them.
"""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity import hub_devices
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import Device, DeviceAssignment, User

SATELLITE = "assist_satellite.test_room_one"
SATELLITE_MAC = "mac:02:00:00:00:00:01"


def _hub(*, entity=SATELLITE, name="Test Room One", hardware=SATELLITE_MAC, kind="voice_satellite", area="Room One"):
    return {"hardware_id": hardware, "hub_device_id": "dev-1", "kind": kind, "name": name, "area": area,
            "entity_ids": [entity, "media_player.test_room_one_speaker"], "manufacturer": "Test", "model": "Sat"}


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    hub_devices.clear_cache()
    with Session(engine) as session:
        session.add(User(username="default", is_admin=True, is_system_default=True))
        session.add(User(username="parent", display_name="Parent", is_admin=True, family_role="father"))
        session.add(User(username="kid", display_name="Kid", family_role="child"))
        session.commit()
        yield session
    hub_devices.clear_cache()


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_internal] = lambda: True
    _act_as("parent")
    client = TestClient(app)
    client.act_as = _act_as
    yield client
    app.dependency_overrides.clear()


def _act_as(username: str) -> None:
    def _require(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _require


def _resolve(client, **body):
    return client.post("/api/resolve", json={"household": True, **body})


# --- discovery -----------------------------------------------------------------------


def test_hub_devices_arrive_shared_and_keyed_by_hardware_not_name(session):
    result = hub_devices.sync(session, [_hub(), _hub(hardware="mac:02:00:00:00:00:02", kind="tv",
                                                     entity="media_player.test_tv", name="Test TV")])
    assert result == {"added": 2, "updated": 0, "reported": 2}
    row = session.exec(select(Device).where(Device.device_key == "hub:" + SATELLITE_MAC)).first()
    assert row.kind == "voice_satellite" and row.owner_username is None and row.hub_area == "Room One"
    assert SATELLITE in hub_devices.entity_list(row)


def test_an_assignment_survives_a_rename_of_the_device_and_its_entity(session):
    hub_devices.sync(session, [_hub()])
    hub_devices.assign(session, "hub:" + SATELLITE_MAC, {"owner_username": "kid", "room": "Kid's room"})
    renamed = "assist_satellite.renamed_room"
    hub_devices.sync(session, [_hub(entity=renamed, name="Renamed")])
    owner = hub_devices.owner_for(session, renamed)
    assert owner["username"] == "kid" and owner["room"] == "Kid's room" and owner["label"] == "Renamed"
    assert hub_devices.owner_for(session, SATELLITE) is None


def test_devices_without_a_stable_id_or_entities_are_ignored(session):
    result = hub_devices.sync(session, [{"kind": "speaker", "entity_ids": ["media_player.x"]},
                                        {"hardware_id": "mac:1", "kind": "speaker", "entity_ids": []},
                                        {"hardware_id": "mac:2", "kind": "fridge", "entity_ids": ["media_player.y"]}])
    assert result["reported"] == 0


def test_an_assignment_change_is_seen_at_once_despite_the_cache(session):
    hub_devices.sync(session, [_hub()])
    assert hub_devices.owner_for(session, SATELLITE)["username"] is None
    hub_devices.assign(session, "hub:" + SATELLITE_MAC, {"owner_username": "kid"})
    assert hub_devices.owner_for(session, SATELLITE)["username"] == "kid"
    hub_devices.assign(session, "hub:" + SATELLITE_MAC, {"owner_username": None})
    assert hub_devices.owner_for(session, SATELLITE)["username"] is None


def test_the_default_user_cannot_be_assigned_shared_means_default(session):
    hub_devices.sync(session, [_hub()])
    with pytest.raises(hub_devices.HubDeviceError, match="shared"):
        hub_devices.assign(session, "hub:" + SATELLITE_MAC, {"owner_username": "default"})


# --- the order: speaker, then device, then default ---------------------------------------


def test_an_identified_speaker_wins_over_the_device_assignment(client, session):
    hub_devices.sync(session, [_hub()])
    hub_devices.assign(session, "hub:" + SATELLITE_MAC, {"owner_username": "kid"})
    body = _resolve(client, voice_id="parent", voice_device=SATELLITE).json()
    assert body["user"] == "parent" and body["identified_by"] is None


def test_not_identified_at_an_assigned_satellite_is_its_person_without_admin(client, session):
    hub_devices.sync(session, [_hub()])
    hub_devices.assign(session, "hub:" + SATELLITE_MAC, {"owner_username": "parent"})
    body = _resolve(client, voice_device=SATELLITE).json()
    assert body["user"] == "parent"
    assert body["identified_by"] == "device"
    assert body["is_admin"] is False  # a device cannot prove who is speaking


@pytest.mark.parametrize("satellite", [SATELLITE, "assist_satellite.never_synced"])
def test_not_identified_at_a_shared_or_unknown_satellite_is_the_default(client, session, satellite):
    hub_devices.sync(session, [_hub()])
    resp = _resolve(client, voice_device=satellite)
    assert resp.status_code == 200
    body = resp.json()
    assert body["identified_by"] is None and body["is_admin"] is False
    assert body["user"] not in ("parent", "kid")


def test_a_control_grant_does_not_make_a_satellite_someones(client, session):
    """DeviceAssignment is who may control an entity (every discovered entity is
    granted to the default user); it must not name a satellite's person."""
    kid = session.exec(select(User).where(User.username == "kid")).first()
    session.add(DeviceAssignment(device_id=SATELLITE, user_id=kid.id))
    session.commit()
    assert client.get("/api/internal/satellite-owner", params={"entity_id": SATELLITE}).json()["owners"] == 0
    hub_devices.sync(session, [_hub()])
    hub_devices.assign(session, "hub:" + SATELLITE_MAC, {"owner_username": "kid"})
    owner = client.get("/api/internal/satellite-owner", params={"entity_id": SATELLITE}).json()
    assert owner["username"] == "kid" and owner["owners"] == 1


# --- the admin API -------------------------------------------------------------------------


def test_the_admin_lists_and_assigns_shared_devices(client, session):
    hub_devices.sync(session, [_hub()])
    listed = client.get("/api/admin/hub-devices").json()["devices"]
    assert [d["assignment"] for d in listed] == ["shared"]
    resp = client.patch(f"/api/admin/hub-devices/hub:{SATELLITE_MAC}", json={"owner_username": "kid", "room": "Den"})
    assert resp.status_code == 200
    assert resp.json()["owner_username"] == "kid" and resp.json()["room"] == "Den"
    assert client.patch(f"/api/admin/hub-devices/hub:{SATELLITE_MAC}", json={"owner_username": "nobody"}).status_code == 422
    assert client.patch("/api/admin/hub-devices/hub:unknown", json={"room": "x"}).status_code == 404


def test_a_non_admin_cannot_assign_shared_devices(client, session):
    hub_devices.sync(session, [_hub()])
    client.act_as("kid")
    assert client.get("/api/admin/hub-devices").status_code == 403
    assert client.patch(f"/api/admin/hub-devices/hub:{SATELLITE_MAC}", json={"owner_username": "kid"}).status_code == 403


def test_hub_devices_stay_out_of_the_companion_device_list(client, session):
    hub_devices.sync(session, [_hub()])
    keys = [d["device_key"] for d in client.get("/api/user-panel/devices").json()]
    assert "hub:" + SATELLITE_MAC not in keys
