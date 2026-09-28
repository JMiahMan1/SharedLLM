"""Per-user service credentials: own creds by default, shared creds only by grant.

Policy under test (services/identity/main.py):

* A user's own HA / Music Assistant / Audiobookshelf / Nextcloud credentials are
  always used when they are configured.
* The system default user (``is_system_default``, or id 1, or username
  "default" — i.e. "User 1" even after a rename) owns the shared credentials.
* Any other user only borrows those shared credentials for a service an admin
  explicitly granted to them. Without a grant the service is simply absent, and
  the consumer must fail loudly rather than fall back to the admin's account.
"""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.crypto import encrypt
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import User

SHARED = "default"


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            User(
                username=SHARED,
                display_name="User 1",
                is_admin=True,
                is_system_default=True,
                ha_url="http://ha.shared",
                ha_token_enc=encrypt("shared-ha-token"),
                mass_url="http://ma.shared:8095",
                mass_token_enc=encrypt("shared-ma-token"),
                audiobookshelf_url="http://abs.shared",
                audiobookshelf_user="shared-abs-user",
                audiobookshelf_api_key_enc=encrypt("shared-abs-key"),
                nextcloud_url="http://nc.shared",
                nextcloud_user="shared-nc-user",
                nextcloud_pass_enc=encrypt("shared-nc-pass"),
                skylight_url="http://skylight.shared",
                skylight_email="shared@skylight",
                skylight_pass_enc=encrypt("shared-skylight-pass"),
            )
        )
        # Bob configured his own HA + Audiobookshelf, but no Music Assistant.
        session.add(
            User(
                username="bob",
                is_admin=False,
                ha_url="http://ha.bob",
                ha_token_enc=encrypt("bob-ha-token"),
                audiobookshelf_url="http://abs.bob",
                audiobookshelf_user="bob-abs-user",
                audiobookshelf_api_key_enc=encrypt("bob-abs-key"),
            )
        )
        # Casey configured nothing at all.
        session.add(User(username="casey", is_admin=False))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    SQLModel.metadata.create_all(identity_main.engine)
    app.dependency_overrides[require_internal] = lambda: True
    _act_as(session, SHARED)
    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


def _user(session: Session, username: str) -> User:
    return session.exec(select(User).where(User.username == username)).first()


def _act_as(username_from_fixture: Session, username: str) -> None:
    """Authenticate as ``username`` using the request-scoped session.

    ``require_api_key`` and the endpoint handler share one ``get_session``
    dependency per request, so the override must take the request's session too
    -- returning an object bound to the test's own session makes the handler's
    ``session.add(user)`` fail.
    """

    def _require_as_user(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _require_as_user


def _resolve(client: TestClient, username: str) -> dict:
    resp = client.post("/api/resolve", json={"rag_user": username})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ─── Resolution policy ────────────────────────────────────────────────────────


def test_own_credentials_are_used(client: TestClient, session: Session):
    data = _resolve(client, "bob")
    assert data["ha_url"] == "http://ha.bob"
    assert data["ha_token"] == "bob-ha-token"
    assert data["audiobookshelf_url"] == "http://abs.bob"
    assert data["audiobookshelf_api_key"] == "bob-abs-key"
    assert data["credential_sources"]["home_assistant"] == "own"
    assert data["credential_sources"]["audiobookshelf"] == "own"
    assert data.get("shared_credential_owner") is None


def test_unconfigured_service_is_absent_not_borrowed(client: TestClient):
    """Bob has no Music Assistant credentials and no grant: the shared token
    must not leak in (the pre-fix behavior silently fell back to user 1)."""
    data = _resolve(client, "bob")
    assert data["mass_url"] is None
    assert data["mass_token"] is None
    assert data["credential_sources"]["music_assistant"] == "absent"
    assert data.get("shared_credential_owner") is None


def test_user_with_nothing_configured_gets_nothing(client: TestClient):
    data = _resolve(client, "casey")
    for key in (
        "ha_url",
        "ha_token",
        "mass_url",
        "mass_token",
        "audiobookshelf_url",
        "audiobookshelf_api_key",
        "nextcloud_url",
        "nextcloud_pass",
    ):
        assert data[key] is None, f"{key} must not be borrowed: {data}"
    assert (
        {k: v for k, v in data["credential_sources"].items() if k != "skylight"}
    ) == {
        "home_assistant": "absent",
        "music_assistant": "absent",
        "audiobookshelf": "absent",
        "nextcloud": "absent",
    }, data["credential_sources"]
    # Skylight is one shared system account, so it is the documented exception.
    assert data["credential_sources"]["skylight"] == "shared"


def test_granted_user_borrows_only_the_granted_service(client: TestClient, session: Session):
    _act_as(session, SHARED)
    resp = client.put(
        "/api/users/bob/credential-shares", json={"services": ["music_assistant"]}
    )
    assert resp.status_code == 200, resp.text

    data = _resolve(client, "bob")
    # Granted: the shared Music Assistant credentials.
    assert data["mass_url"] == "http://ma.shared:8095"
    assert data["mass_token"] == "shared-ma-token"
    assert data["credential_sources"]["music_assistant"] == "granted"
    assert data["shared_credential_owner"] == SHARED
    # Not granted: still absent, even though the shared account has credentials.
    assert data["nextcloud_url"] is None
    assert data["credential_sources"]["nextcloud"] == "absent"
    # And the user's own credentials still win over the shared ones.
    assert data["ha_url"] == "http://ha.bob"
    assert data["credential_sources"]["home_assistant"] == "own"


def test_system_default_user_owns_the_shared_credentials(client: TestClient):
    data = _resolve(client, SHARED)
    assert data["mass_token"] == "shared-ma-token"
    assert data["audiobookshelf_api_key"] == "shared-abs-key"
    assert data["credential_sources"]["music_assistant"] == "own"
    assert data["credential_sources"]["audiobookshelf"] == "own"
    assert data.get("shared_credential_owner") is None


def test_renamed_default_user_is_still_the_shared_owner(client: TestClient, session: Session):
    """"User 1" may be renamed: is_system_default, not the username, decides."""
    shared = _user(session, SHARED)
    shared.username = "jeremiah"
    shared.display_name = "Jeremiah"
    session.add(shared)
    session.commit()

    _act_as(session, "jeremiah")
    assert client.put(
        "/api/users/bob/credential-shares", json={"services": ["music_assistant"]}
    ).status_code == 200

    data = _resolve(client, "bob")
    assert data["mass_token"] == "shared-ma-token"
    assert data["shared_credential_owner"] == "jeremiah"


def test_grant_to_a_user_with_no_shared_credentials_fails_loudly(
    client: TestClient, session: Session
):
    """A grant with nothing to borrow must not silently invent credentials."""
    shared = _user(session, SHARED)
    shared.mass_url = None
    shared.mass_token_enc = None
    session.add(shared)
    session.commit()
    _act_as(session, SHARED)
    client.put("/api/users/casey/credential-shares", json={"services": ["music_assistant"]})

    data = _resolve(client, "casey")
    assert data["mass_url"] is None
    assert data["mass_token"] is None
    assert data["credential_sources"]["music_assistant"] == "absent"
    assert data.get("shared_credential_owner") is None


def test_skylight_prefers_the_users_own_account(client: TestClient, session: Session):
    casey = _user(session, "casey")
    casey.skylight_url = "http://skylight.casey"
    casey.skylight_email = "casey@skylight"
    casey.skylight_pass_enc = encrypt("casey-skylight-pass")
    session.add(casey)
    session.commit()

    data = _resolve(client, "casey")
    assert data["skylight_email"] == "casey@skylight"
    assert data["skylight_pass"] == "casey-skylight-pass"
    assert data["credential_sources"]["skylight"] == "own"

    # Somebody without their own Skylight account uses the shared system one.
    other = _resolve(client, "bob")
    assert other["skylight_email"] == "shared@skylight"
    assert other["credential_sources"]["skylight"] == "shared"


# ─── Grant endpoints ──────────────────────────────────────────────────────────


def test_admin_can_opt_in_for_itself(client: TestClient, session: Session):
    _act_as(session, SHARED)
    resp = client.put(
        "/api/users/default/credential-shares", json={"services": ["audiobookshelf"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["services"] == ["audiobookshelf"]
    assert body["granted_by"] == SHARED
    assert body["shared_owner"] == SHARED


def test_admin_can_grant_another_user(client: TestClient, session: Session):
    _act_as(session, SHARED)
    resp = client.put(
        "/api/users/casey/credential-shares",
        json={"services": ["music_assistant", "audiobookshelf"], "note": "family plan"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["services"] == ["audiobookshelf", "music_assistant"]

    read = client.get("/api/users/casey/credential-shares")
    assert read.status_code == 200
    assert read.json()["granted_by"] == SHARED
    assert read.json()["note"] == "family plan"


def test_non_admin_cannot_grant_anything(client: TestClient, session: Session):
    _act_as(session, "bob")
    for target in ("bob", "casey"):
        resp = client.put(f"/api/users/{target}/credential-shares", json={"services": ["music_assistant"]})
        assert resp.status_code == 403, f"{target}: {resp.text}"
    assert _resolve(client, "casey")["mass_url"] is None


def test_grant_list_is_validated(client: TestClient, session: Session):
    _act_as(session, SHARED)
    resp = client.put(
        "/api/users/casey/credential-shares", json={"services": ["music_assistant", "dropbox"]}
    )
    assert resp.status_code == 400
    assert "dropbox" in resp.text
    assert client.get("/api/users/casey/credential-shares").json()["services"] == []


def test_empty_list_revokes_the_grant(client: TestClient, session: Session):
    _act_as(session, SHARED)
    client.put("/api/users/casey/credential-shares", json={"services": ["music_assistant"]})
    assert _resolve(client, "casey")["mass_url"] == "http://ma.shared:8095"

    resp = client.put("/api/users/casey/credential-shares", json={"services": []})
    assert resp.status_code == 200
    assert resp.json()["services"] == []
    data = _resolve(client, "casey")
    assert data["mass_url"] is None
    assert data["credential_sources"]["music_assistant"] == "absent"


def test_unknown_user_is_404(client: TestClient, session: Session):
    _act_as(session, SHARED)
    # A known user works, so the 404 below is about the user, not a missing route.
    assert client.get("/api/users/bob/credential-shares").status_code == 200
    assert client.get("/api/users/nobody/credential-shares").status_code == 404
    assert client.put("/api/users/nobody/credential-shares", json={"services": []}).status_code == 404


def test_user_can_read_own_shares_but_not_another_users(client: TestClient, session: Session):
    _act_as(session, SHARED)
    client.put("/api/users/bob/credential-shares", json={"services": ["nextcloud"]})

    _act_as(session, "bob")
    own = client.get("/api/users/bob/credential-shares")
    assert own.status_code == 200
    assert own.json()["services"] == ["nextcloud"]
    assert client.get("/api/users/default/credential-shares").status_code == 403


def test_deleting_a_user_drops_their_grant(client: TestClient, session: Session):
    _act_as(session, SHARED)
    client.put("/api/users/casey/credential-shares", json={"services": ["music_assistant"]})
    assert client.delete("/api/users/casey").status_code == 200

    # Re-create the user: the stale grant must not come back with them.
    session.add(User(username="casey", is_admin=False))
    session.commit()
    assert _resolve(client, "casey")["mass_url"] is None


# ─── Self-service credential writes ───────────────────────────────────────────


def test_self_service_can_set_the_audiobookshelf_api_key(client: TestClient, session: Session):
    """PATCH /api/users/me used to ignore audiobookshelf_api_key, so a user
    could never set their own ABS key (only an admin could)."""
    _act_as(session, "casey")
    resp = client.patch(
        "/api/users/me",
        json={"audiobookshelf_url": "http://abs.casey", "audiobookshelf_api_key": "casey-abs-key"},
    )
    assert resp.status_code == 200, resp.text

    data = _resolve(client, "casey")
    assert data["audiobookshelf_url"] == "http://abs.casey"
    assert data["audiobookshelf_api_key"] == "casey-abs-key"
    assert _user(session, "casey").audiobookshelf_api_key_enc != "casey-abs-key"
