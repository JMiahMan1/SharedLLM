"""Account changes leave a trail, and a blank field can no longer wipe a login.

Observed: Michele's Nextcloud and Home Assistant logins were gone and nothing
said who or what removed them. Every form loads secrets blank (they are never
sent to the browser) and the server read blank as "erase", so saving the form
for any reason wiped them -- and nothing recorded it.
"""
import json

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.crypto import decrypt, encrypt
from services.identity.main import app, get_session, require_admin_or_internal, require_api_key, require_internal
from services.identity.models import AuditEvent, User


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="default", display_name="Admin", is_admin=True, is_system_default=True))
        session.add(
            User(
                username="michele",
                display_name="Michele",
                nextcloud_url="https://cloud.example",
                nextcloud_user="michele",
                nextcloud_pass_enc=encrypt("nc-app-password"),
                ha_url="https://ha.example",
                ha_token_enc=encrypt("ha-long-lived-token"),
            )
        )
        session.commit()
        yield session


def _act_as(username: str) -> None:
    def _require(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _require


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    app.dependency_overrides[require_internal] = lambda: True
    app.dependency_overrides[require_admin_or_internal] = lambda: True
    _act_as("default")
    yield TestClient(app)
    app.dependency_overrides = {}


def _michele(session: Session) -> User:
    session.expire_all()
    return session.exec(select(User).where(User.username == "michele")).first()


def _events(session: Session) -> list[AuditEvent]:
    session.expire_all()
    return session.exec(select(AuditEvent).order_by(AuditEvent.id)).all()


# ─── A blank field is not an eraser ───────────────────────────────────────────


def test_saving_the_admin_dialog_with_blank_secrets_keeps_them(client, session):
    """The Admin "Edit user" dialog sends every field, secrets blank."""
    resp = client.patch(
        "/api/users/michele",
        json={"display_name": "Michele S", "nextcloud_url": "https://cloud.example", "nextcloud_user": "michele",
              "nextcloud_pass": "", "ha_url": "https://ha.example", "ha_token": ""},
    )
    assert resp.status_code == 200, resp.text
    m = _michele(session)
    assert decrypt(m.nextcloud_pass_enc) == "nc-app-password"
    assert decrypt(m.ha_token_enc) == "ha-long-lived-token"
    assert m.display_name == "Michele S"


def test_a_blank_url_or_username_does_not_erase_it_either(client, session):
    client.patch("/api/users/michele", json={"nextcloud_url": "", "nextcloud_user": "  "})
    m = _michele(session)
    assert m.nextcloud_url == "https://cloud.example"
    assert m.nextcloud_user == "michele"


def test_erasing_a_login_has_to_be_asked_for_by_name(client, session):
    client.patch("/api/users/michele", json={"clear_fields": ["nextcloud_pass", "nextcloud_user", "nextcloud_url"]})
    m = _michele(session)
    assert m.nextcloud_pass_enc is None and m.nextcloud_user is None and m.nextcloud_url is None
    assert decrypt(m.ha_token_enc) == "ha-long-lived-token"


def test_clear_fields_cannot_touch_identity_or_privilege(client, session):
    client.patch("/api/users/michele", json={"clear_fields": ["username", "password_hash", "is_admin"]})
    assert _michele(session).username == "michele"


def test_the_settings_card_blank_save_keeps_the_secret(client, session):
    _act_as("michele")
    client.patch("/api/users/me", json={"ha_url": "https://ha.example", "ha_token": ""})
    assert decrypt(_michele(session).ha_token_enc) == "ha-long-lived-token"


# ─── The trail ────────────────────────────────────────────────────────────────


def test_an_admin_edit_records_who_changed_which_fields_without_values(client, session):
    client.patch("/api/users/michele", json={"ha_token": "new-token", "display_name": "Mich"})
    event = _events(session)[-1]
    assert (event.actor, event.actor_kind, event.action, event.target) == ("default", "user", "user.update", "michele")
    changes = {c["field"]: c for c in json.loads(event.changes)}
    assert changes["ha_token"] == {"field": "ha_token", "change": "changed", "secret": True}
    assert changes["display_name"]["change"] == "changed"
    raw = event.model_dump_json()
    assert "new-token" not in raw and "Mich" not in raw, "the trail must never hold values"


def test_a_cleared_login_is_recorded_as_cleared(client, session):
    client.patch("/api/users/michele", json={"clear_fields": ["nextcloud_pass"]})
    changes = json.loads(_events(session)[-1].changes)
    assert {"field": "nextcloud_pass", "change": "cleared", "secret": True} in changes


def test_a_no_op_save_records_no_changes(client, session):
    client.patch("/api/users/michele", json={"nextcloud_pass": "", "ha_token": ""})
    assert json.loads(_events(session)[-1].changes) == []


def test_create_password_reset_grant_and_delete_are_all_recorded(client, session):
    client.post("/api/users", json={"username": "casey", "password": "pw", "nextcloud_pass": "secret"})
    client.post("/api/users/casey/password", json={"new_password": "pw2"})
    client.put("/api/users/casey/credential-shares", json={"services": ["nextcloud"]})
    client.delete("/api/users/casey")
    actions = [e.action for e in _events(session) if e.target == "casey"]
    assert actions == ["user.create", "user.password_reset", "credential.share", "user.delete"]
    created = json.loads(next(e for e in _events(session) if e.action == "user.create").changes)
    assert {"field": "nextcloud_pass", "change": "set", "secret": True} in created
    assert all("secret" not in json.dumps(c) or "pw" not in json.dumps(c) for c in created)


def test_admins_read_the_trail_and_users_read_their_own(client, session):
    client.patch("/api/users/michele", json={"display_name": "M"})
    resp = client.get("/api/admin/audit", params={"target": "michele"})
    assert resp.status_code == 200
    assert resp.json()["events"][0]["action"] == "user.update"

    _act_as("michele")
    assert client.get("/api/admin/audit").status_code == 403
    mine = client.get("/api/users/me/audit").json()["events"]
    assert mine and all(e["target"] == "michele" for e in mine)


# ─── Show password ────────────────────────────────────────────────────────────


def test_the_owner_can_reveal_their_own_secret_and_it_is_audited(client, session):
    _act_as("michele")
    resp = client.post("/api/users/michele/reveal", json={"field": "nextcloud_pass"})
    assert resp.json() == {"field": "nextcloud_pass", "value": "nc-app-password"}
    event = _events(session)[-1]
    assert event.action == "credential.reveal" and event.actor == "michele"
    assert "nc-app-password" not in event.model_dump_json()


def test_an_admin_can_reveal_but_another_user_cannot(client, session):
    assert client.post("/api/users/michele/reveal", json={"field": "ha_token"}).json()["value"] == "ha-long-lived-token"
    session.add(User(username="jeremiah"))
    session.commit()
    _act_as("jeremiah")
    assert client.post("/api/users/michele/reveal", json={"field": "ha_token"}).status_code == 403


def test_reveal_only_returns_credential_fields(client, session):
    assert client.post("/api/users/michele/reveal", json={"field": "password_hash"}).status_code == 422


def test_a_user_read_says_which_secrets_are_saved_but_never_their_values(client, session):
    body = client.get("/api/users").json()
    michele = next(u for u in body if u["username"] == "michele")
    assert sorted(michele["saved_credentials"]) == ["ha_token", "nextcloud_pass"]
    assert "nc-app-password" not in json.dumps(body)


def test_any_signed_in_user_can_read_the_household_service_urls_but_no_secrets(client, session, monkeypatch):
    # No system config: the addresses fall back to the default user's.
    for var in ("NEXTCLOUD_URL", "HA_URL", "HOME_ASSISTANT_URL"):
        monkeypatch.delenv(var, raising=False)
    owner = session.exec(select(User).where(User.username == "default")).first()
    owner.nextcloud_url = "https://cloud.example"
    owner.ha_url = "https://ha.example"
    owner.nextcloud_pass_enc = encrypt("admin-secret")
    session.add(owner)
    session.commit()
    _act_as("michele")
    body = client.get("/api/users/service-defaults").json()
    assert body["nextcloud_url"] == "https://cloud.example"
    assert body["ha_url"] == "https://ha.example"
    assert "admin-secret" not in json.dumps(body)
    assert not any(k.endswith(("_pass", "_token", "_enc")) for k in body)


def test_prefill_urls_come_from_the_system_config_first(client, session, monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_URL", "https://cloud.from-config")
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_URL", raising=False)
    owner = session.exec(select(User).where(User.username == "default")).first()
    owner.nextcloud_url = "https://cloud.from-user1"
    owner.ha_url = "https://ha.from-user1"
    session.add(owner)
    session.commit()
    body = client.get("/api/users/service-defaults").json()
    assert body["nextcloud_url"] == "https://cloud.from-config"
    assert body["source"]["nextcloud_url"] == "system_config"
    assert body["ha_url"] == "https://ha.from-user1"
    assert body["source"]["ha_url"] == "default_user"


def test_seeding_a_system_credential_works_and_is_recorded(client, session):
    resp = client.post("/api/admin/seed-credential", json={"field": "audiobookshelf_api_key", "value": "new-key"})
    assert resp.status_code == 200, resp.text
    owner = session.exec(select(User).where(User.username == "default")).first()
    session.refresh(owner)
    assert decrypt(owner.audiobookshelf_api_key_enc) == "new-key"
    event = _events(session)[-1]
    assert event.action == "credential.seed" and "new-key" not in event.model_dump_json()


def test_generating_and_revoking_an_api_key_is_recorded(client, session):
    key_id = client.post("/api/users/me/keys", json={"label": "phone"}).json()["id"]
    assert client.delete(f"/api/users/me/keys/{key_id}").status_code == 200
    assert [e.action for e in _events(session)][-2:] == ["api_key.create", "api_key.revoke"]


def test_seeding_a_url_stores_it_plain(client, session):
    """Seeding encrypted every value, so User 1's Audiobookshelf URL became ciphertext."""
    assert client.post("/api/admin/seed-credential", json={"field": "audiobookshelf_url", "value": "https://abs.example"}).status_code == 200
    owner = session.exec(select(User).where(User.username == "default")).first()
    session.refresh(owner)
    assert owner.audiobookshelf_url == "https://abs.example"
    assert "secret" not in json.loads(_events(session)[-1].changes)[0]
