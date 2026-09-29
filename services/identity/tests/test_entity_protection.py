"""Entity protection: an admin locks an entity so only named users can control it.

Policy under test (services/identity/main.py):

* A protected entity ignores its DeviceAssignment row. Only admins, the system
  default user, and the entity's own permit list may control it.
* Protection is invisible to everyone else: the internal permissions endpoint
  reports the lock so Execution can hide the entity entirely.
* Only an admin may create, change or release a lock.
* A permit list naming a user who does not exist is rejected, never stored.
"""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import EntityProtection, User

ADMIN = "default"
ADMIN_ALT = "root"


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        # Real API keys: POST /api/users/devices authenticates from the header
        # directly (it also accepts the internal secret, which the Gateway's
        # entity auto-discovery uses), so it cannot be covered by a
        # dependency override alone.
        session.add(User(username=ADMIN, is_admin=True, is_system_default=True, api_key="key-default"))
        session.add(User(username=ADMIN_ALT, is_admin=True, api_key="key-root"))
        session.add(User(username="alice", is_admin=False, api_key="key-alice"))
        session.add(User(username="bob", is_admin=False, api_key="key-bob"))
        session.commit()
        yield session


def _act_as(session: Session, client: TestClient, username: str) -> None:
    """Authenticate as ``username`` for the request's own session.

    Two mechanisms, because the endpoints use two: the ``Depends``-based ones
    read the override, while the hand-rolled ``POST /api/users/devices`` reads
    the Authorization header directly.
    """

    def _require_as_user(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(
            select(User).where(User.username == username)
        ).first()

    app.dependency_overrides[require_api_key] = _require_as_user
    client.headers["Authorization"] = f"Bearer key-{username}"


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    SQLModel.metadata.create_all(identity_main.engine)
    app.dependency_overrides[require_internal] = lambda: True
    client = TestClient(app)
    _act_as(session, client, ADMIN)
    yield client
    app.dependency_overrides = {}


def _as(session: Session, client: TestClient, username: str) -> None:
    _act_as(session, client, username)


def _lock(client: TestClient, entity_id, permitted=(), note=None):
    return client.put(
        f"/api/entity-protection/{entity_id}",
        json={"protected": True, "permitted_usernames": list(permitted), "note": note},
    )


def _release(client: TestClient, entity_id):
    return client.put(
        f"/api/entity-protection/{entity_id}",
        json={"protected": False, "permitted_usernames": []},
    )


def _permissions(client: TestClient, username: str) -> dict:
    resp = client.get("/api/internal/user-device-assignments", params={"username": username})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── the admin surface ─────────────────────────────────────────────────────────


def test_locking_an_entity_records_the_permit_list(client: TestClient):
    resp = _lock(client, "climate.hallway", permitted=["alice"], note="Guest frost line")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entity_id"] == "climate.hallway"
    assert body["permitted_usernames"] == ["alice"]
    assert body["note"] == "Guest frost line"
    assert body["granted_by"] == ADMIN
    assert body["granted_at"]


def test_re_locking_replaces_rather_than_appends(client: TestClient):
    _lock(client, "climate.hallway", permitted=["alice", "bob"])
    _lock(client, "climate.hallway", permitted=["bob"])
    body = client.get("/api/entity-protection").json()
    assert len(body) == 1
    assert body[0]["permitted_usernames"] == ["bob"]


def test_releasing_removes_the_row_entirely(client: TestClient, session: Session):
    """No third 'protected but inactive' state: a released entity is just an
    ordinary entity again, with no stale permit list left behind."""
    _lock(client, "climate.hallway", permitted=["alice"])
    assert _release(client, "climate.hallway").status_code == 200
    assert client.get("/api/entity-protection").json() == []
    assert session.exec(select(EntityProtection)).all() == []


def test_releasing_something_that_is_not_locked_is_harmless(client: TestClient):
    assert _release(client, "climate.never_locked").status_code == 200


def test_usernames_are_normalised_so_case_cannot_split_the_list(client: TestClient):
    body = _lock(client, "climate.hallway", permitted=["Alice"]).json()
    assert body["permitted_usernames"] == ["alice"]


def test_a_permit_for_a_user_who_does_not_exist_is_rejected(client: TestClient, session: Session):
    """A typo'd grant must fail loudly rather than silently permit nobody."""
    resp = _lock(client, "climate.hallway", permitted=["alice", "alise"])
    assert resp.status_code == 400
    assert "alise" in resp.json()["detail"]
    # Nothing was written: the whole request is refused, not partially applied.
    assert session.exec(select(EntityProtection)).all() == []


def test_a_malformed_entity_id_is_rejected(client: TestClient):
    resp = _lock(client, "hallway")
    assert resp.status_code == 400
    assert "entity id" in resp.json()["detail"]


def test_locking_rejects_an_empty_entity_id(client: TestClient, session: Session):
    """No id, no route (405), and above all no row written."""
    assert _lock(client, "").status_code in (400, 404, 405)
    assert session.exec(select(EntityProtection)).all() == []


def test_the_locked_entity_is_exactly_the_one_in_the_path(client: TestClient, session: Session):
    """Whatever the path resolves to is the entity that gets locked — no more.

    A ``..`` segment is resolved by URL normalisation before the request is
    even sent, so the server receives ``/api/entity-protection/light.kitchen``
    and can only ever lock what it was handed. That is why the route is a
    single segment: a slash that survives as an encoded ``%2F`` matches no
    route at all instead of being split on.
    """
    assert _lock(client, "climate.hallway/../light.kitchen").status_code == 200
    locked = [row.entity_id for row in session.exec(select(EntityProtection)).all()]
    assert locked == ["light.kitchen"]

    for row in session.exec(select(EntityProtection)).all():
        session.delete(row)
    session.commit()

    resp = _lock(client, "climate.hallway%2F..%2Flight.kitchen")
    assert resp.status_code == 404
    assert session.exec(select(EntityProtection)).all() == []


def test_a_partially_shaped_entity_id_is_rejected(client: TestClient, session: Session):
    """A dot is not enough — the id has to look like ``domain.object_id``."""
    for bad in (".hallway", "climate.", "Hallway Thermostat.x", "climate.Hall-Way"):
        resp = _lock(client, bad)
        assert resp.status_code == 400, bad
    assert session.exec(select(EntityProtection)).all() == []


# ── only admins may manage protection ─────────────────────────────────────────


def test_a_normal_user_cannot_lock_an_entity(client: TestClient, session: Session):
    _as(session, client, "alice")
    resp = _lock(client, "climate.hallway", permitted=["alice"])
    assert resp.status_code == 403
    assert session.exec(select(EntityProtection)).all() == []


def test_a_normal_user_cannot_release_a_lock(client: TestClient, session: Session):
    _lock(client, "climate.hallway")
    _as(session, client, "bob")
    assert _release(client, "climate.hallway").status_code == 403
    assert len(session.exec(select(EntityProtection)).all()) == 1


def test_a_normal_user_cannot_widen_a_permit_list(client: TestClient, session: Session):
    _lock(client, "climate.hallway", permitted=["alice"])
    _as(session, client, "bob")
    assert _lock(client, "climate.hallway", permitted=["bob"]).status_code == 403
    row = session.exec(select(EntityProtection)).first()
    assert row.permitted_usernames == '["alice"]'


def test_a_normal_user_cannot_read_the_protection_list(client: TestClient, session: Session):
    """The permit list is who-can-open-this-house information."""
    _lock(client, "climate.hallway", permitted=["alice"])
    _as(session, client, "bob")
    assert client.get("/api/entity-protection").status_code == 403


def test_any_admin_may_manage_protection(client: TestClient, session: Session):
    _as(session, client, ADMIN_ALT)
    assert _lock(client, "climate.hallway", permitted=["alice"]).status_code == 200


def test_unauthenticated_requests_are_refused(client: TestClient, session: Session):
    app.dependency_overrides.pop(require_api_key, None)
    client.headers.pop("Authorization", None)
    assert _lock(client, "climate.hallway").status_code == 401


def test_a_valid_api_key_without_admin_rights_is_forbidden_not_unauthorised(
    client: TestClient, session: Session
):
    """401 means 'who are you'; 403 means 'no'. The distinction matters to the UI."""
    _as(session, client, "alice")
    assert _lock(client, "climate.hallway").status_code == 403


# ── what the internal endpoint reports ────────────────────────────────────────


def test_a_lock_is_reported_as_a_lock_not_as_a_grant(client: TestClient):
    _lock(client, "climate.hallway", permitted=["alice"])
    data = _permissions(client, "bob")
    assert data["protected_entity_ids"] == ["climate.hallway"]
    assert data["permitted_entity_ids"] == []
    assert "climate.hallway" not in data["device_ids"]


def test_a_permitted_user_is_told_explicitly(client: TestClient):
    _lock(client, "climate.hallway", permitted=["alice"])
    assert _permissions(client, "alice")["permitted_entity_ids"] == ["climate.hallway"]


def test_permits_do_not_transfer_between_users(client: TestClient):
    _lock(client, "climate.hallway", permitted=["alice"])
    assert _permissions(client, "bob")["permitted_entity_ids"] == []


def test_every_admin_and_the_default_user_may_control_a_lock(client: TestClient):
    _lock(client, "climate.hallway")
    for who in (ADMIN, ADMIN_ALT):
        assert _permissions(client, who)["permitted_entity_ids"] == ["climate.hallway"]


def test_an_unlocked_entity_reports_no_protection_at_all(client: TestClient):
    data = _permissions(client, "alice")
    assert data["protected_entity_ids"] == []
    assert data["permitted_entity_ids"] == []


def test_an_unknown_user_gets_empty_everything(client: TestClient):
    data = _permissions(client, "nobody")
    assert data == {
        "device_ids": [],
        "protected_entity_ids": [],
        "permitted_entity_ids": [],
    }


def test_a_corrupt_permit_list_fails_loudly_rather_than_granting(client: TestClient, session: Session):
    """Never swallow bad data into a permissive default."""
    session.add(EntityProtection(entity_id="climate.hallway", permitted_usernames="not json"))
    session.commit()
    resp = client.get("/api/entity-protection")
    assert resp.status_code == 500
    assert "climate.hallway" in resp.json()["detail"]


def test_a_non_list_permit_list_also_fails_loudly(client: TestClient, session: Session):
    session.add(EntityProtection(entity_id="climate.hallway", permitted_usernames='{"a": 1}'))
    session.commit()
    assert client.get("/api/entity-protection").status_code == 500


# ── the assignment hole this feature depends on being closed ─────────────────


def test_a_normal_user_cannot_assign_a_device_to_themselves(client: TestClient, session: Session):
    """Otherwise protection is decorative: self-assign, then control."""
    _as(session, client, "alice")
    resp = client.post(
        "/api/users/devices", json={"username": "alice", "device_id": "climate.hallway"}
    )
    assert resp.status_code == 403


def test_a_normal_user_cannot_reassign_someone_elses_device(client: TestClient, session: Session):
    client.post("/api/users/devices", json={"username": "bob", "device_id": "light.kitchen"})
    _as(session, client, "alice")
    resp = client.post(
        "/api/users/devices", json={"username": "alice", "device_id": "light.kitchen"}
    )
    assert resp.status_code == 403


def test_a_normal_user_cannot_delete_or_revoke_an_assignment(client: TestClient, session: Session):
    client.post("/api/users/devices", json={"username": "bob", "device_id": "light.kitchen"})
    _as(session, client, "alice")
    assert client.delete("/api/devices/light.kitchen").status_code == 403
    assert client.post("/api/devices/light.kitchen/revoke").status_code == 403


def test_an_admin_can_still_assign_and_revoke(client: TestClient, session: Session):
    assert client.post(
        "/api/users/devices", json={"username": "bob", "device_id": "light.kitchen"}
    ).status_code == 200
    assert client.post("/api/devices/light.kitchen/revoke").status_code == 200


def test_a_normal_user_can_still_read_their_own_assignments(client: TestClient, session: Session):
    client.post("/api/users/devices", json={"username": "alice", "device_id": "light.kitchen"})
    _as(session, client, "alice")
    assert client.get("/api/users/devices").status_code == 200
