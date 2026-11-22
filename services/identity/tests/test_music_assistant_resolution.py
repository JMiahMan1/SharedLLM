"""Which Music Assistant server and login a person uses (services/identity/music_assistant.py).

Rules under test:

* A person's own server (their saved URL) is used with their own token, and is never
  swapped for the household's behind their back; an unreachable one is reported.
* Otherwise the household server is wherever HomeHub's Music Assistant integration
  says. No environment variable names it: with no HomeHub entry and no own server the
  error says so.
* On the household server each person uses their own MA account: a saved token, else
  one made for them (once: it is saved). An admin grant only lends the household
  account's login when no account can be made.
* The migration clears saved URLs on a retired host (tokens kept), dry run first,
  names only.

Every address and token is made up.
"""
import threading
import time

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity import music_assistant
from services.identity.crypto import decrypt, encrypt
from services.identity.main import app, get_session, require_admin_or_internal, require_api_key, require_internal
from services.identity.models import User

HOUSEHOLD = "http://music.example.test:8095"
MINE = "http://mine.example.test:8095"
RETIRED = "http://old-ha.example.test:8095"


def household(url=HOUSEHOLD, error=None):
    return lambda: {"url": url, "error": error}


def never_provision(*_a, **_k):
    raise AssertionError("must not make an account here")


# -- the rules (pure) ------------------------------------------------------------------------

def test_an_own_server_is_used_with_the_own_token_and_never_asks_the_hub():
    res = music_assistant.resolve(username="kim", own_url=MINE, own_token="t-kim",
                                  server_fn=lambda: pytest.fail("the hub is not asked"), provision_fn=never_provision)
    assert (res.url, res.token, res.server, res.login, res.error) == (MINE, "t-kim", "own", "own", None)


def test_an_own_server_without_a_token_is_an_error_not_a_switch():
    res = music_assistant.resolve(username="kim", own_url=MINE, own_token=None, server_fn=household(),
                                  provision_fn=never_provision)
    assert res.token is None and res.server == "own"
    assert "has no access token" in res.error and "household Music Assistant" in res.error


def test_a_token_alone_is_a_login_on_the_household_server():
    res = music_assistant.resolve(username="kim", own_url=None, own_token="t-kim", own_user_id="u-kim",
                                  server_fn=household(), provision_fn=never_provision)
    assert (res.url, res.token, res.server, res.login, res.user_id) == (HOUSEHOLD, "t-kim", "household", "own", "u-kim")


def test_a_person_without_a_login_gets_their_own_account():
    made = []

    def provision(username, display_name):
        made.append((username, display_name))
        return {"username": username, "user_id": "u-new", "created": True, "token": "t-new"}

    res = music_assistant.resolve(username="kim", own_url=None, own_token=None, display_name="Kim",
                                  grant=(None, "t-household"), server_fn=household(), provision_fn=provision)
    assert made == [("kim", "Kim")]
    assert (res.token, res.login, res.user_id, res.provisioned) == ("t-new", "provisioned", "u-new", {"kim": True})


def test_without_a_household_server_the_error_names_both_ways_to_set_one_up():
    res = music_assistant.resolve(username="kim", own_url=None, own_token="t-kim",
                                  server_fn=household(None, music_assistant.NO_MA_MESSAGE), provision_fn=never_provision)
    assert res.url is None and res.token is None
    assert res.error == ("No Music Assistant is set up: add the Music Assistant integration in HomeHub "
                         "or your own in Accounts & Integrations.")


def test_a_grant_lends_the_household_login_only_when_no_account_can_be_made():
    def refuse(*_a):
        raise music_assistant.HubUnavailable("HomeHub is not connected to Music Assistant")

    res = music_assistant.resolve(username="kim", own_url=None, own_token=None, grant=(None, "t-household"),
                                  server_fn=household(), provision_fn=refuse)
    assert (res.token, res.login) == ("t-household", "granted")
    res = music_assistant.resolve(username="kim", own_url=None, own_token=None, grant=None,
                                  server_fn=household(), provision_fn=refuse)
    assert res.token is None and "HomeHub is not connected to Music Assistant" in res.error


def test_the_unreachable_message_says_whose_server_and_what_to_do():
    own = music_assistant.Resolution(url=MINE, token="t", server="own", login="own")
    assert "Your own Music Assistant" in music_assistant.unreachable_message(own, "timeout")
    assert "switch to the household Music Assistant" in music_assistant.unreachable_message(own, "timeout")
    hh = music_assistant.Resolution(url=HOUSEHOLD, token="t", server="household", login="own")
    assert music_assistant.unreachable_message(hh, "timeout").startswith("The household Music Assistant")


def test_without_homehub_there_is_no_household_server_and_no_env_fallback(monkeypatch):
    from services import config

    music_assistant.clear_cache()
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setenv("MA_URL", RETIRED)
    monkeypatch.setenv("MUSIC_ASSISTANT_URL", RETIRED)
    try:
        server = music_assistant.household_server(fresh=True)
    finally:
        music_assistant.clear_cache()
    assert server["url"] is None and server["error"] == music_assistant.NO_MA_MESSAGE


def test_the_household_server_comes_from_the_hub(monkeypatch):
    from services import config

    asked = []

    def hub(url, secret, path, body=None):
        asked.append((url, path))
        return {"configured": True, "url": HOUSEHOLD, "error": None}

    music_assistant.clear_cache()
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.example.test:8123")
    monkeypatch.setattr(music_assistant, "_hub_request", hub)
    try:
        assert music_assistant.household_server(fresh=True)["url"] == HOUSEHOLD
        assert music_assistant.household_server()["url"] == HOUSEHOLD  # cached
    finally:
        music_assistant.clear_cache()
    assert asked == [("http://hub.example.test:8123", "/hub/music_assistant/server")]


def test_a_hub_without_the_integration_gives_the_set_up_message(monkeypatch):
    from services import config

    music_assistant.clear_cache()
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.example.test:8123")
    monkeypatch.setattr(music_assistant, "_hub_request",
                        lambda *a, **k: {"configured": False, "url": None, "error": "HomeHub has no Music Assistant"})
    try:
        assert music_assistant.household_server(fresh=True)["error"] == music_assistant.NO_MA_MESSAGE
    finally:
        music_assistant.clear_cache()


# -- through identity's API -------------------------------------------------------------------

@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="default", display_name="Household", is_admin=True, is_system_default=True,
                         mass_token_enc=encrypt("t-household")))
        session.add(User(username="kim", display_name="Kim", mass_url=MINE, mass_token_enc=encrypt("t-kim")))
        session.add(User(username="lee", display_name="Lee", mass_url=RETIRED, mass_token_enc=encrypt("t-lee")))
        session.add(User(username="max", display_name="Max"))
        session.add(User(username="qa-bot", display_name="QA Bot", is_test_account=True))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session, monkeypatch):
    identity_main.engine = session.bind
    identity_main._MA_PROVISION_FAILED.clear()
    app.dependency_overrides[require_internal] = lambda: True
    app.dependency_overrides[require_admin_or_internal] = lambda: True
    made: list[str] = []

    def provision(username, display_name=None):
        made.append(username)
        return {"username": username, "user_id": f"u-{username}", "created": True, "token": f"t-new-{username}"}

    monkeypatch.setattr(music_assistant, "household_server", lambda fresh=False: {"url": HOUSEHOLD, "error": None})
    monkeypatch.setattr(music_assistant, "provision_account", provision)
    client = TestClient(app)
    client.made = made
    yield client
    app.dependency_overrides = {}


def _as(username):
    def _require(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _require


def _resolve(client, username):
    resp = client.post("/api/resolve", json={"rag_user": username})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_resolve_gives_each_person_their_own_account_once(client, session):
    data = _resolve(client, "max")
    assert (data["mass_url"], data["mass_token"], data["mass_server"]) == (HOUSEHOLD, "t-new-max", "household")
    assert data["mass_user_id"] == "u-max" and data["mass_error"] is None
    assert data["credential_sources"]["music_assistant"] == "own"
    max_ = session.exec(select(User).where(User.username == "max")).first()
    session.refresh(max_)
    assert decrypt(max_.mass_token_enc) == "t-new-max" and max_.mass_user_id == "u-max"
    assert _resolve(client, "max")["mass_token"] == "t-new-max"
    assert client.made == ["max"]  # idempotent: made once, then the saved token is used


def test_resolve_keeps_an_own_server_and_the_household_account_is_not_provisioned(client):
    kim = _resolve(client, "kim")
    assert (kim["mass_url"], kim["mass_token"], kim["mass_server"]) == (MINE, "t-kim", "own")
    household = client.post("/api/resolve", json={"household": True})
    assert household.status_code == 200, household.text
    assert household.json()["mass_url"] == HOUSEHOLD and household.json()["mass_token"] == "t-household"
    assert client.made == []


def test_status_reports_an_unreachable_own_server_and_offers_the_household(client, monkeypatch):
    monkeypatch.setattr(music_assistant, "probe", lambda url, token: {"state": "unreachable", "message": "No answer."})
    _as("kim")
    body = client.get("/api/auth/music-assistant/status").json()
    assert body["state"] == "unreachable" and body["server"] == "own" and body["can_use_household"] is True
    assert body["message"].startswith(f"Your own Music Assistant at {MINE} did not answer")
    assert "token" not in {k for k in body if "token" in k and body[k]}  # no token in the answer
    # Choosing the household server is clearing their URL; their account id is forgotten with it.
    assert client.patch("/api/users/me", json={"clear_fields": ["mass_url"]}).status_code == 200
    monkeypatch.setattr(music_assistant, "probe",
                        lambda url, token: {"state": "ok", "ma_user_id": "u-kim", "ma_username": "kim"})
    body = client.get("/api/auth/music-assistant/status").json()
    assert (body["state"], body["server"], body["url"], body["ma_username"]) == ("ok", "household", HOUSEHOLD, "kim")


def test_status_says_how_to_set_one_up_when_there_is_none(client, monkeypatch):
    monkeypatch.setattr(music_assistant, "household_server",
                        lambda fresh=False: {"url": None, "error": music_assistant.NO_MA_MESSAGE})
    _as("max")
    body = client.get("/api/auth/music-assistant/status").json()
    assert body["state"] == "not_set_up" and body["message"] == music_assistant.NO_MA_MESSAGE


def test_the_stale_url_migration_is_a_dry_run_first_and_keeps_tokens(client, session):
    dry = client.get("/api/admin/music-assistant/stale-urls", params={"retired": "old-ha.example.test"}).json()
    assert dry == {"retired_hosts": ["old-ha.example.test"], "users": [{"username": "lee", "host": "old-ha.example.test"}]}
    lee = session.exec(select(User).where(User.username == "lee")).first()
    session.refresh(lee)
    assert lee.mass_url == RETIRED  # the dry run changed nothing
    assert client.post("/api/admin/music-assistant/stale-urls",
                       json={"retired_hosts": ["old-ha.example.test"]}).status_code == 400  # needs confirm
    done = client.post("/api/admin/music-assistant/stale-urls",
                       json={"retired_hosts": ["old-ha.example.test"], "confirm": True}).json()
    assert done["cleared"] == [{"username": "lee", "host": "old-ha.example.test"}]
    session.refresh(lee)
    assert lee.mass_url is None and decrypt(lee.mass_token_enc) == "t-lee"
    assert _resolve(client, "lee")["mass_url"] == HOUSEHOLD
    kim = session.exec(select(User).where(User.username == "kim")).first()
    session.refresh(kim)
    assert kim.mass_url == MINE  # an own server elsewhere is untouched


def test_retired_hosts_default_to_the_redirect_sources(monkeypatch):
    monkeypatch.setenv("HA_URL_REDIRECTS", f"{RETIRED}=http://new.example.test:8095")
    assert identity_main._ma_retired_hosts(None) == ["old-ha.example.test"]
    assert identity_main._ma_retired_hosts(["Other.Example.Test"]) == ["other.example.test"]


def test_bulk_provisioning_lists_then_makes_only_missing_accounts(client):
    plan = client.get("/api/admin/music-assistant/accounts").json()["users"]
    assert plan == [{"username": "kim", "action": "own_server"}, {"username": "lee", "action": "own_server"},
                    {"username": "max", "action": "needs_account"}]
    assert client.made == []
    assert client.post("/api/admin/music-assistant/accounts", json={}).status_code == 400
    done = client.post("/api/admin/music-assistant/accounts", json={"confirm": True}).json()["users"]
    assert [r["result"] for r in done] == ["unchanged", "unchanged", "provisioned"]
    again = client.post("/api/admin/music-assistant/accounts", json={"confirm": True}).json()["users"]
    assert [r["result"] for r in again] == ["unchanged", "unchanged", "unchanged"]
    assert client.made == ["max"]
    assert "t-new-max" not in str(done)  # never a token in the answer


# -- who never gets an account ----------------------------------------------------------------

def _user(session, username):
    return session.exec(select(User).where(User.username == username)).first()


def test_test_accounts_and_the_household_account_are_never_given_one_on_first_use(client, session):
    for username in ("qa-bot", "default"):
        res = identity_main._resolve_music_assistant(session, _user(session, username), set())
        assert res.login != "provisioned"
    qa = _resolve(client, "qa-bot")
    assert qa["mass_token"] is None
    _as("qa-bot")
    assert client.get("/api/auth/music-assistant/status").json()["state"] == "not_set_up"
    assert client.made == []
    assert _user(session, "qa-bot").mass_token_enc is None


def test_test_accounts_and_the_household_account_are_left_out_of_the_admin_accounts_run(client):
    plan = client.get("/api/admin/music-assistant/accounts").json()["users"]
    assert {r["username"] for r in plan} == {"kim", "lee", "max"}
    done = client.post("/api/admin/music-assistant/accounts",
                       json={"confirm": True, "usernames": ["qa-bot", "default"]}).json()["users"]
    assert all(r["result"] == "unchanged" for r in done)
    assert client.made == []


def test_the_admin_command_dry_run_does_not_list_test_accounts(client, capsys):
    from services.identity import music_assistant_admin

    assert music_assistant_admin.main(["accounts"]) == 0
    out = capsys.readouterr().out
    assert "max: needs_account" in out
    assert "qa-bot" not in out and "default:" not in out
    assert client.made == []


# -- one account request per person at a time -------------------------------------------------

def test_concurrent_first_use_makes_one_account_and_both_get_its_token(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'identity.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as setup:
        setup.add(User(username="default", display_name="Household", is_system_default=True))
        setup.add(User(username="robin", display_name="Robin"))
        setup.commit()
    identity_main._MA_PROVISION_FAILED.clear()
    calls: list[str] = []
    calls_lock = threading.Lock()

    def slow_provision(username, display_name=None):
        with calls_lock:
            calls.append(username)
        time.sleep(0.2)  # long enough for the other request to arrive while this one is out
        return {"username": username, "user_id": f"u-{username}", "created": False, "token": f"t-{len(calls)}"}

    monkeypatch.setattr(music_assistant, "household_server", lambda fresh=False: {"url": HOUSEHOLD, "error": None})
    monkeypatch.setattr(music_assistant, "provision_account", slow_provision)
    start = threading.Barrier(3)
    results: list[music_assistant.Resolution] = []

    def first_use():
        with Session(engine) as s:
            user = s.exec(select(User).where(User.username == "robin")).first()
            start.wait()
            results.append(identity_main._resolve_music_assistant(s, user, set()))

    threads = [threading.Thread(target=first_use) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert calls == ["robin"]
    assert sorted(r.token for r in results) == ["t-1", "t-1", "t-1"]
    with Session(engine) as s:
        robin = s.exec(select(User).where(User.username == "robin")).first()
        assert decrypt(robin.mass_token_enc) == "t-1" and robin.mass_user_id == "u-robin"
