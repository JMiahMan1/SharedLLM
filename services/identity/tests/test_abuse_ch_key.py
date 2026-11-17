"""The abuse.ch Auth-Key in Accounts & Integrations (services/identity/abuse_ch.py).

* A person's own key wins.
* Without one, the system default user's key (the household default) applies,
  with no admin grant.
* The key reaches internal callers through /api/resolve only; the UI learns
  whether it is set and where it comes from, never the key.
* A key is checked with abuse.ch before it is saved; a refused key or an
  abuse.ch that does not answer leaves nothing saved.
"""
import logging

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.common import malwarebazaar
from services.identity import abuse_ch
from services.identity.crypto import decrypt, encrypt
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import GlobalSetting, User

HOUSEHOLD_KEY = "made-up-household-abuse-key"
OWN_KEY = "made-up-own-abuse-key"
NEW_KEY = "made-up-new-abuse-key"
SECRETS = (HOUSEHOLD_KEY, OWN_KEY, NEW_KEY)


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="default", display_name="User 1", is_admin=True, is_system_default=True,
                         malwarebazaar_key_enc=encrypt(HOUSEHOLD_KEY)))
        session.add(User(username="alex", display_name="Alex", malwarebazaar_key_enc=encrypt(OWN_KEY)))
        session.add(User(username="kim", display_name="Kim", family_role="child"))
        session.commit()
        yield session


@pytest.fixture
def abuse(monkeypatch):
    """abuse.ch, replaced: ``answer`` is what the next lookup gets (or raises)."""
    state = {"answer": (200, {"query_status": "hash_not_found"}), "calls": []}

    def _lookup(key, sha):
        state["calls"].append((key, sha))
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    monkeypatch.setattr(abuse_ch, "lookup", _lookup)
    return state


@pytest.fixture(name="client")
def client_fixture(session: Session, abuse):
    identity_main.engine = session.bind
    app.dependency_overrides[require_internal] = lambda: True
    _act_as("default")
    yield TestClient(app)
    app.dependency_overrides = {}


def _act_as(username: str) -> None:
    def _as(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _as


def _user(session: Session, username: str) -> User:
    session.expire_all()
    return session.exec(select(User).where(User.username == username)).first()


def _resolve(client, **body) -> dict:
    resp = client.post("/api/resolve", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


# --- resolution ---------------------------------------------------------------


def test_a_persons_own_key_wins(client):
    data = _resolve(client, rag_user="alex")
    assert data["malwarebazaar_key"] == OWN_KEY
    assert data["credential_sources"]["abuse_ch"] == "own"


def test_without_their_own_a_person_uses_the_household_key_with_no_grant(client):
    data = _resolve(client, rag_user="kim")
    assert data["malwarebazaar_key"] == HOUSEHOLD_KEY
    assert data["credential_sources"]["abuse_ch"] == "household"
    # Nothing was granted, and the granted services stay absent.
    assert data["credential_sources"]["home_assistant"] == "absent"


def test_the_default_user_resolves_its_own_key(client, session):
    data = _resolve(client, rag_user="default")
    assert data["malwarebazaar_key"] == HOUSEHOLD_KEY and data["credential_sources"]["abuse_ch"] == "own"
    _user(session, "default").malwarebazaar_key_enc = None
    session.commit()
    data = _resolve(client, rag_user="default")
    assert data["malwarebazaar_key"] is None and data["credential_sources"]["abuse_ch"] == "absent"


def test_no_household_key_and_none_of_their_own_is_absent(client, session):
    _user(session, "default").malwarebazaar_key_enc = None
    session.commit()
    data = _resolve(client, rag_user="kim")
    assert data["malwarebazaar_key"] is None and data["credential_sources"]["abuse_ch"] == "absent"


def test_the_household_identity_gets_the_household_key(client):
    data = _resolve(client, household=True)
    assert data["malwarebazaar_key"] == HOUSEHOLD_KEY and data["credential_sources"]["abuse_ch"] == "household"


# --- what the UI sees -----------------------------------------------------------


def test_the_account_page_learns_only_whether_and_where_from(client):
    _act_as("kim")
    me = client.get("/api/users/me")
    assert me.json()["abuse_ch_status"] == "household"
    assert "malwarebazaar_key" not in me.json()["saved_credentials"]
    _no_secret(me.text)
    _act_as("alex")
    me = client.get("/api/users/me")
    assert me.json()["abuse_ch_status"] == "own" and "malwarebazaar_key" in me.json()["saved_credentials"]
    _no_secret(me.text)
    _act_as("default")
    assert client.get("/api/users/me").json()["abuse_ch_status"] == "own"


def test_without_a_household_key_a_person_with_none_is_not_set_up(client, session):
    _user(session, "default").malwarebazaar_key_enc = None
    session.commit()
    _act_as("kim")
    assert client.get("/api/users/me").json()["abuse_ch_status"] == "not_set_up"
    _act_as("default")
    assert client.get("/api/users/me").json()["abuse_ch_status"] == "not_set_up"


def test_the_admin_user_list_never_carries_the_key(client):
    app.dependency_overrides[identity_main.require_admin_or_internal] = lambda: True
    listed = client.get("/api/users")
    assert listed.status_code == 200
    _no_secret(listed.text)
    by_name = {u["username"]: u for u in listed.json()}
    assert "malwarebazaar_key" in by_name["default"]["saved_credentials"]


def test_the_key_cannot_be_revealed(client):
    resp = client.post("/api/users/default/reveal", json={"field": "malwarebazaar_key"})
    assert resp.status_code == 422
    _no_secret(resp.text)


# --- saving ---------------------------------------------------------------------


def test_a_key_abuse_ch_accepts_is_saved_and_never_logged(client, session, abuse, caplog):
    caplog.set_level(logging.DEBUG)
    _act_as("kim")
    resp = client.patch("/api/users/me", json={"malwarebazaar_key": f"  {NEW_KEY} "})
    assert resp.status_code == 200, resp.text
    _no_secret(resp.text)
    assert decrypt(_user(session, "kim").malwarebazaar_key_enc) == NEW_KEY
    assert abuse["calls"] == [(NEW_KEY, malwarebazaar.KEY_CHECK_SHA256)]  # only the empty file's hash went out
    assert client.get("/api/users/me").json()["abuse_ch_status"] == "own"
    _no_secret(caplog.text)


@pytest.mark.parametrize("answer", [(401, None), (200, {"query_status": "unknown_auth_key"})])
def test_a_refused_key_is_not_saved(client, session, abuse, caplog, answer):
    abuse["answer"] = answer
    _act_as("kim")
    resp = client.patch("/api/users/me", json={"malwarebazaar_key": NEW_KEY})
    assert resp.status_code == 400
    assert "refused" in resp.json()["detail"] and "auth.abuse.ch" in resp.json()["detail"]
    assert _user(session, "kim").malwarebazaar_key_enc is None
    _no_secret(resp.text + caplog.text)


@pytest.mark.parametrize("answer", [OSError("network down"), (503, None), (200, None)])
def test_an_abuse_ch_that_does_not_answer_saves_nothing(client, session, abuse, caplog, answer):
    abuse["answer"] = answer
    _act_as("alex")
    resp = client.patch("/api/users/me", json={"malwarebazaar_key": NEW_KEY})
    assert resp.status_code == 502 and "did not answer" in resp.json()["detail"]
    assert decrypt(_user(session, "alex").malwarebazaar_key_enc) == OWN_KEY  # the old one is untouched
    _no_secret(resp.text + caplog.text)


def test_other_fields_save_without_asking_abuse_ch(client, abuse):
    _act_as("kim")
    assert client.patch("/api/users/me", json={"display_name": "Kimmy", "malwarebazaar_key": ""}).status_code == 200
    assert abuse["calls"] == []


def test_an_admin_sets_the_household_key_on_the_default_user(client, session, abuse):
    resp = client.patch("/api/users/default", json={"malwarebazaar_key": NEW_KEY})
    assert resp.status_code == 200, resp.text
    _no_secret(resp.text)
    assert _resolve(client, rag_user="kim")["malwarebazaar_key"] == NEW_KEY
    abuse["answer"] = (403, None)
    assert client.patch("/api/users/default", json={"malwarebazaar_key": "made-up-bad"}).status_code == 400
    assert decrypt(_user(session, "default").malwarebazaar_key_enc) == NEW_KEY


def test_a_new_account_with_a_refused_key_is_not_created(client, session, abuse):
    abuse["answer"] = (401, None)
    resp = client.post("/api/users", json={"username": "newbie", "password": "made-up-pass", "malwarebazaar_key": NEW_KEY})
    assert resp.status_code == 400
    assert _user(session, "newbie") is None
    abuse["answer"] = (200, {"query_status": "hash_not_found"})
    resp = client.post("/api/users", json={"username": "newbie", "password": "made-up-pass", "malwarebazaar_key": NEW_KEY})
    assert resp.status_code == 200, resp.text
    _no_secret(resp.text)
    assert decrypt(_user(session, "newbie").malwarebazaar_key_enc) == NEW_KEY


def test_clear_fields_removes_it_and_the_household_key_applies_again(client, session):
    _act_as("alex")
    resp = client.patch("/api/users/me", json={"clear_fields": ["malwarebazaar_key"]})
    assert resp.status_code == 200
    assert _user(session, "alex").malwarebazaar_key_enc is None
    assert client.get("/api/users/me").json()["abuse_ch_status"] == "household"
    assert _resolve(client, rag_user="alex")["malwarebazaar_key"] == HOUSEHOLD_KEY


# --- the moves from the old homes -------------------------------------------------


def test_the_hubs_move_sets_the_household_key_only_when_there_is_none(client, session, caplog):
    caplog.set_level(logging.DEBUG)
    kept = client.post("/api/internal/household-abuse-ch-key", json={"key": NEW_KEY})
    assert kept.json()["status"] == "kept"
    assert decrypt(_user(session, "default").malwarebazaar_key_enc) == HOUSEHOLD_KEY
    _user(session, "default").malwarebazaar_key_enc = None
    session.commit()
    moved = client.post("/api/internal/household-abuse-ch-key", json={"key": NEW_KEY})
    assert moved.json()["status"] == "set"
    assert decrypt(_user(session, "default").malwarebazaar_key_enc) == NEW_KEY
    assert client.post("/api/internal/household-abuse-ch-key", json={"key": " "}).status_code == 422
    _no_secret(kept.text + moved.text + caplog.text)


def test_the_hubs_move_needs_the_internal_secret(session):
    identity_main.engine = session.bind
    app.dependency_overrides = {}
    resp = TestClient(app).post("/api/internal/household-abuse-ch-key", json={"key": NEW_KEY})
    assert resp.status_code == 403


def test_the_old_global_setting_moves_to_the_household_default(session, caplog):
    caplog.set_level(logging.DEBUG)
    _user(session, "default").malwarebazaar_key_enc = None
    session.add(GlobalSetting(key="malwarebazaar_auth_key", value=NEW_KEY, description="d"))
    session.commit()
    identity_main._drop_retired_settings(session)
    assert decrypt(_user(session, "default").malwarebazaar_key_enc) == NEW_KEY
    assert session.exec(select(GlobalSetting).where(GlobalSetting.key == "malwarebazaar_auth_key")).first() is None
    _no_secret(caplog.text)


def test_the_old_global_setting_never_overwrites_a_household_key(session):
    session.add(GlobalSetting(key="malwarebazaar_auth_key", value=NEW_KEY, description="d"))
    session.commit()
    identity_main._drop_retired_settings(session)
    assert decrypt(_user(session, "default").malwarebazaar_key_enc) == HOUSEHOLD_KEY
    assert session.exec(select(GlobalSetting).where(GlobalSetting.key == "malwarebazaar_auth_key")).first() is None


# --- the check itself -------------------------------------------------------------


@pytest.mark.parametrize(("answer", "outcome"), [
    ((200, {"query_status": "hash_not_found"}), None),
    ((200, {"query_status": "ok", "data": []}), None),
    ((200, {"query_status": "user_blacklisted"}), abuse_ch.KeyRefused),
    ((403, None), abuse_ch.KeyRefused),
    ((500, None), abuse_ch.Unreachable),
    ((200, {"query_status": "rate_limited"}), abuse_ch.Unreachable),
    (TimeoutError(), abuse_ch.Unreachable),
])
def test_check_key(abuse, answer, outcome):
    abuse["answer"] = answer
    if outcome is None:
        abuse_ch.check_key(NEW_KEY)
    else:
        with pytest.raises(outcome):
            abuse_ch.check_key(NEW_KEY)
