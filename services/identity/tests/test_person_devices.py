"""Who "announce to <name>" means, and the hub devices assigned to them. Every name is made up."""
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity import hub_devices
from services.identity.main import app, get_session, require_internal
from services.identity.models import Device, User


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(username="default", is_system_default=True))
    session.add(User(username="kid", display_name="Kid Example"))
    session.add(User(username="kidtwo", display_name="Other Person"))
    session.add(Device(device_key="hub:mac:1", hardware_id="mac:1", kind="voice_satellite", label="Kid Satellite",
                       owner_username="kid", entity_ids='["assist_satellite.k", "media_player.k"]', registered_by="hub"))
    session.add(Device(device_key="hub:mac:2", hardware_id="mac:2", kind="tv", label="Old TV", owner_username="kid",
                       entity_ids='["media_player.old"]', registered_by="hub", archived_at="2026-01-01"))
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_internal] = lambda: True
    return TestClient(app), session


def test_a_first_name_finds_the_person_and_their_live_devices():
    client, session = _client()
    try:
        body = client.get("/api/internal/person-devices", params={"person": "kid"}).json()
        assert body["username"] == "kid"
        assert [d["label"] for d in body["devices"]] == ["Kid Satellite"]
        assert hub_devices.find_person(session, "Kid's")
        assert hub_devices.find_person(session, "default") is None
        assert client.get("/api/internal/person-devices", params={"person": "nobody"}).status_code == 404
    finally:
        app.dependency_overrides.clear()
