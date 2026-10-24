"""Family relationships and the minor sharing rule.

Policy:

* Father is the admin flag, not a role that silently promotes an account.
* Mother gets the household permissions (devices and family links) without
  becoming an account administrator.
* A child or teen is private by default but their family-circle activity
  sharing cannot be switched off. An adult child can opt out.
* A mother link can only name an account marked as a mother.
"""
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from services.identity import roles
from services.identity.models import User
from services.identity.tests.test_entity_protection import (  # noqa: F401 (fixtures)
    _as,
    client_fixture,
    session_fixture,
)


def _set_role(session: Session, username: str, role: str):
    user = session.exec(select(User).where(User.username == username)).one()
    user.family_role = role
    session.add(user)
    session.commit()
    # The relationship is kept as a real role row, so reconcile it the same
    # way the API does when a family role is set.
    if roles.sync_family_role(session, user):
        session.commit()
    return user


def test_family_roles_are_the_roles_the_admin_panel_shows(client: TestClient, session: Session):
    """The household roles are ordinary rows in the Roles table."""
    _set_role(session, "alice", "mother")
    _as(session, client, "default")

    catalog = client.get("/api/users/roles").json()
    mother = next(r for r in catalog["roles"] if r["name"] == "Mother")
    assert "family.manage" in mother["permissions"]
    assert "alice" in mother["members"]

    resp = client.patch("/api/users/bob/family", json={"family_role": "teen"})
    assert resp.status_code == 200, resp.text
    catalog = client.get("/api/users/roles").json()
    teen = next(r for r in catalog["roles"] if r["name"] == "Teen")
    child = next(r for r in catalog["roles"] if r["name"] == "Child")
    assert "bob" in teen["members"]
    assert "bob" not in child["members"]


def test_mother_gets_household_permissions_without_admin(client: TestClient, session: Session):
    _set_role(session, "alice", "mother")
    _as(session, client, "alice")

    me = client.get("/api/users/me").json()
    assert me["is_admin"] is False
    assert me["family_role"] == "mother"
    assert "family.manage" in me["permissions"]
    assert "devices.assign" in me["permissions"]
    assert "devices.lock" in me["permissions"]


def test_mother_can_set_a_teen_and_link_herself(client: TestClient, session: Session):
    _set_role(session, "alice", "mother")
    _as(session, client, "alice")

    resp = client.patch(
        "/api/users/bob/family",
        json={"family_role": "teen", "mother_username": "alice"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["family_role"] == "teen"
    assert resp.json()["mother_username"] == "alice"
    assert resp.json()["is_minor"] is True
    assert resp.json()["sharing_locked"] is True

    roster = client.get("/api/users/family")
    assert roster.status_code == 200
    assert {p["username"] for p in roster.json()} == {"default", "root", "alice", "bob"}


def test_teen_cannot_turn_off_family_circle_sharing(client: TestClient, session: Session):
    _set_role(session, "alice", "mother")
    _as(session, client, "alice")
    assert client.patch("/api/users/bob/family", json={"family_role": "teen"}).status_code == 200

    _as(session, client, "bob")
    current = client.get("/api/users/me/activity-sharing").json()
    assert current["enabled"] is True
    assert current["audience"] == "circle"
    assert current["sharing_locked"] is True

    resp = client.put("/api/users/me/activity-sharing", json={"enabled": False})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "A child or teen's family sharing cannot be turned off"


def test_adult_child_can_turn_sharing_off(client: TestClient, session: Session):
    _set_role(session, "bob", "adult_child")
    _as(session, client, "bob")

    current = client.get("/api/users/me/activity-sharing").json()
    assert current["sharing_locked"] is False
    assert current["enabled"] is False

    assert client.put("/api/users/me/activity-sharing", json={"enabled": True}).status_code == 200
    assert client.put("/api/users/me/activity-sharing", json={"enabled": False}).status_code == 200


def test_mother_cannot_edit_a_parent_or_make_one(client: TestClient, session: Session):
    _set_role(session, "alice", "mother")
    _set_role(session, "bob", "father")
    _as(session, client, "alice")

    assert client.patch("/api/users/default/family", json={"family_role": "child"}).status_code == 403
    assert client.patch("/api/users/bob/family", json={"family_role": "child"}).status_code == 403
    assert client.patch("/api/users/bob/family", json={"family_role": "mother"}).status_code == 403


def test_a_mother_link_must_name_a_mother(client: TestClient, session: Session):
    _set_role(session, "bob", "other")
    _as(session, client, "default")

    resp = client.patch(
        "/api/users/bob/family",
        json={"family_role": "child", "mother_username": "alice"},
    )
    assert resp.status_code == 422
    assert "not marked as a mother" in resp.json()["detail"]


def test_father_must_be_an_admin(client: TestClient, session: Session):
    _as(session, client, "default")
    resp = client.patch("/api/users/bob/family", json={"family_role": "father"})
    assert resp.status_code == 422
    assert "admin" in resp.json()["detail"]


def test_family_endpoints_require_a_parent_or_manager(client: TestClient, session: Session):
    _as(session, client, "bob")
    assert client.get("/api/users/family").status_code == 403
    assert client.patch("/api/users/alice/family", json={"family_role": "child"}).status_code == 403
