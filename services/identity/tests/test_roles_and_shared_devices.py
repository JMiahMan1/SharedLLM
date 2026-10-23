"""Shared devices and roles.

* A device can be assigned to several users; adding one never takes it from
  another, and removing one leaves the rest.
* Roles bundle permissions an admin gives to users. Built-in roles are seeded;
  they can be edited but not deleted. Only admins manage roles.
* A role's permissions are enforced: devices.use_all reaches every unlocked
  entity, devices.use_locked every locked one, devices.assign lets a non-admin
  share devices, devices.lock lets them lock.
"""
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from services.identity import roles
from services.identity.main import INTERNAL_SECRET, _assignments_for
from services.identity.models import Role
from services.identity.tests.test_entity_protection import (  # noqa: F401 (fixtures)
    _as,
    _lock,
    _permissions,
    client_fixture,
    session_fixture,
)


def _share(client: TestClient, device_id: str, username: str):
    return client.post("/api/users/devices", json={"device_id": device_id, "username": username})


def _role(session: Session, builtin: str) -> Role:
    roles.seed_default_roles(session)
    return session.exec(select(Role).where(Role.builtin == builtin)).one()


def _give(client: TestClient, session: Session, username: str, builtin: str) -> None:
    _as(session, client, "default")
    resp = client.put(f"/api/users/{username}/roles", json={"role_ids": [_role(session, builtin).id]})
    assert resp.status_code == 200, resp.text


# shared devices


def test_a_device_can_be_shared_by_several_users(client: TestClient, session: Session):
    assert _share(client, "media_player.kitchen", "alice").status_code == 200
    assert _share(client, "media_player.kitchen", "bob").status_code == 200
    assert _share(client, "media_player.kitchen", "bob").status_code == 200  # idempotent

    assert sorted(a.user.username for a in _assignments_for(session, "media_player.kitchen")) == ["alice", "bob"]
    assert "media_player.kitchen" in _permissions(client, "alice")["device_ids"]
    assert "media_player.kitchen" in _permissions(client, "bob")["device_ids"]


def test_removing_one_user_leaves_the_others(client: TestClient, session: Session):
    _share(client, "light.porch", "alice")
    _share(client, "light.porch", "bob")

    assert client.delete("/api/devices/light.porch", params={"username": "alice"}).status_code == 200

    assert [a.user.username for a in _assignments_for(session, "light.porch")] == ["bob"]


def test_rediscovery_does_not_undo_a_revoke(client: TestClient, session: Session):
    _share(client, "light.porch", "alice")
    client.post("/api/devices/light.porch/revoke", params={"username": "alice"})

    del client.headers["Authorization"]
    resp = client.post(
        "/api/users/devices",
        json={"device_id": "light.porch", "username": "alice"},
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked"] is True


# roles


def test_default_roles_are_seeded_once(session: Session):
    roles.seed_default_roles(session)
    roles.seed_default_roles(session)
    names = sorted(r.name for r in session.exec(select(Role)).all())
    assert names == [
        "Adult child",
        "Child",
        "Family member",
        "Father (admin)",
        "Guest",
        "Home manager",
        "Mother",
        "Teen",
    ]


def test_only_admins_manage_roles(client: TestClient, session: Session):
    _as(session, client, "alice")
    assert client.get("/api/users/roles").status_code == 403
    assert client.post("/api/users/roles", json={"name": "Kid"}).status_code == 403
    assert client.put("/api/users/alice/roles", json={"role_ids": []}).status_code == 403


def test_an_admin_creates_edits_and_deletes_a_role(client: TestClient, session: Session):
    created = client.post("/api/users/roles", json={"name": "Lights", "permissions": ["devices.use_all"]})
    assert created.status_code == 200, created.text
    role_id = created.json()["id"]

    edited = client.patch(f"/api/users/roles/{role_id}", json={"name": "Lights", "permissions": ["devices.assign", "devices.use_all"]})
    # Stored in catalog order whatever order they were sent in.
    assert edited.json()["permissions"] == ["devices.use_all", "devices.assign"]

    assert client.post("/api/users/roles", json={"name": "Bad", "permissions": ["root.everything"]}).status_code == 422
    assert client.post("/api/users/roles", json={"name": "Lights"}).status_code == 409

    assert client.delete(f"/api/users/roles/{role_id}").status_code == 200
    assert client.delete(f"/api/users/roles/{_role(session, 'guest').id}").status_code == 409


def test_family_member_controls_every_unlocked_device(client: TestClient, session: Session):
    _lock(client, "lock.front_door")
    assert _permissions(client, "alice")["all_unprotected"] is False

    _give(client, session, "alice", "family_member")

    perms = _permissions(client, "alice")
    assert perms["all_unprotected"] is True
    assert "lock.front_door" not in perms["permitted_entity_ids"]


def test_home_manager_runs_the_house_without_being_admin(client: TestClient, session: Session):
    _lock(client, "lock.front_door")
    _give(client, session, "alice", "home_manager")

    perms = _permissions(client, "alice")
    assert perms["all_unprotected"] is True
    assert "lock.front_door" in perms["permitted_entity_ids"]

    _as(session, client, "alice")
    me = client.get("/api/users/me").json()
    assert me["is_admin"] is False
    assert "devices.assign" in me["permissions"]
    assert _share(client, "media_player.kitchen", "bob").status_code == 200
    assert _lock(client, "climate.hallway").status_code == 200
    # Still not an admin: no accounts, no roles.
    assert client.get("/api/users/roles").status_code == 403
    assert client.patch("/api/users/bob", json={"is_admin": True}).status_code == 403


def test_without_a_role_a_user_cannot_share_or_lock(client: TestClient, session: Session):
    _as(session, client, "bob")
    assert _share(client, "media_player.kitchen", "bob").status_code == 403
    assert _lock(client, "climate.hallway").status_code == 403
