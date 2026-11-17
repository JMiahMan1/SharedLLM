"""Deleting an account removes what it left under its username."""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.main import app, get_session, require_api_key
from services.identity.models import Device, DeviceEvent, ScreenRules, User, UserActivitySharing, UserWidget


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1, username="alex", display_name="Alex", is_admin=True))
        session.add(User(id=2, username="kid1", display_name="Kid"))
        session.add(User(id=3, username="kid10", display_name="Other kid"))
        session.commit()
        for who in ("kid1", "kid10"):
            session.add(UserWidget(username=who, widget_key="notes"))
            session.add(UserActivitySharing(username=who))
            session.add(ScreenRules(username=who))
            session.add(Device(device_key=f"phone-{who}", owner_username=who))
            session.add(DeviceEvent(device_key=f"phone-{who}", event="heartbeat", username=who))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session, monkeypatch):
    identity_main.engine = session.bind
    deleted_keys: list[str] = []

    class _Redis:
        def smembers(self, _key):
            return set()

        def delete(self, *keys):
            deleted_keys.extend(keys)

        def scan_iter(self, match, count=None):
            return iter([match.replace("*", "pixel")])

        def close(self):
            pass

    import redis

    monkeypatch.setattr(redis, "from_url", lambda *a, **k: _Redis())

    def _admin(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == "alex")).first()

    app.dependency_overrides[require_api_key] = _admin
    client = TestClient(app)
    client.deleted_keys = deleted_keys
    yield client
    app.dependency_overrides = {}


def test_deleting_an_account_removes_its_rows_and_only_its_rows(client, session):
    assert client.delete("/api/users/kid1").status_code == 200
    for model, column in (
        (UserWidget, UserWidget.username),
        (UserActivitySharing, UserActivitySharing.username),
        (ScreenRules, ScreenRules.username),
        (DeviceEvent, DeviceEvent.username),
        (Device, Device.owner_username),
    ):
        assert session.exec(select(model).where(column == "kid1")).all() == []
        # A username that merely starts the same keeps everything.
        assert len(session.exec(select(model).where(column == "kid10")).all()) == 1


def test_deleting_an_account_removes_its_phone_and_telemetry_keys(client):
    assert client.delete("/api/users/kid1").status_code == 200
    keys = client.deleted_keys
    for key in ("tel:activity:kid1", "tel:concerns:kid1", "tel:apps:devices:kid1", "phone_beacon:kid1",
                "tel:apps:inv:kid1:pixel", "tel:concern-image:kid1:pixel"):
        assert key in keys
    assert not any("kid10" in k for k in keys)
