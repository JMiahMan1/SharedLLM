"""Google Books and Open Library in Accounts & Integrations (services/common/book_metadata.py).

* A person's own Google Books key wins; without one the system default user's
  key (the household default) applies, with no grant.
* Each choice (on/off, order, Open Library's contact email) is the person's own,
  else the household's, else the default (Open Library on, Google Books off).
* The key reaches internal callers through /api/resolve only; the account page
  learns whether one is set and where it comes from, never the key.
* The Test button does one lookup and answers the exact outcome.
Every key and address here is made up.
"""
import json

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity import book_metadata_check
from services.identity.crypto import decrypt, encrypt
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import User

HOUSEHOLD_KEY = "made-up-household-books-key"
OWN_KEY = "made-up-own-books-key"
TYPED_KEY = "made-up-typed-books-key"
SECRETS = (HOUSEHOLD_KEY, OWN_KEY, TYPED_KEY)


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="default", display_name="User 1", is_admin=True, is_system_default=True,
                         google_books_key_enc=encrypt(HOUSEHOLD_KEY),
                         book_metadata=json.dumps({"google_books_enabled": True,
                                                   "open_library_email": "library@example.com"})))
        session.add(User(username="alex", display_name="Alex", google_books_key_enc=encrypt(OWN_KEY),
                         book_metadata=json.dumps({"order": ["open_library", "google_books"]})))
        session.add(User(username="kim", display_name="Kim", family_role="child"))
        session.commit()
        yield session


@pytest.fixture
def catalogue(monkeypatch):
    """The catalogues, replaced: ``answer`` is what the next lookup gets (or raises)."""
    state = {"answer": (200, {"items": [{"volumeInfo": {"title": "The Brothers Karamazov"}}]}), "calls": []}

    def _fetch(url, headers):
        state["calls"].append((url, headers))
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    monkeypatch.setattr(book_metadata_check, "fetch", _fetch)
    return state


@pytest.fixture(name="client")
def client_fixture(session: Session, catalogue):
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


# --- resolution ---------------------------------------------------------------------------


def test_without_their_own_a_person_uses_the_households_key_and_choices(client):
    data = _resolve(client, rag_user="kim")
    google = data["book_metadata"]["sources"]["google_books"]
    assert google["api_key"] == HOUSEHOLD_KEY and google["key_source"] == "household"
    assert google["enabled"] is True and google["enabled_from"] == "household" and google["ready"] is True
    assert data["credential_sources"]["google_books"] == "household"
    assert data["book_metadata"]["sources"]["open_library"]["contact_email"] == "library@example.com"


def test_a_persons_own_key_and_order_win(client):
    data = _resolve(client, rag_user="alex")
    assert data["book_metadata"]["sources"]["google_books"]["api_key"] == OWN_KEY
    assert data["credential_sources"]["google_books"] == "own"
    assert data["book_metadata"]["order"] == ["open_library", "google_books"]


def test_the_household_resolves_the_default_users_settings(client):
    data = _resolve(client, household=True)
    assert data["book_metadata"]["sources"]["google_books"]["api_key"] == HOUSEHOLD_KEY


def test_with_nothing_set_open_library_is_on_and_google_books_is_not_set_up(client, session):
    default = _user(session, "default")
    default.google_books_key_enc = None
    default.book_metadata = None
    session.commit()
    sources = _resolve(client, rag_user="kim")["book_metadata"]["sources"]
    assert sources["open_library"]["ready"] is True
    assert sources["google_books"]["ready"] is False
    assert "not set up" in sources["google_books"]["reason"]


# --- the account page ---------------------------------------------------------------------


def test_the_account_page_says_where_the_key_comes_from_never_the_key(client):
    _act_as("kim")
    resp = client.get("/api/users/me")
    assert resp.status_code == 200
    status = resp.json()["book_metadata_status"]
    assert status["sources"]["google_books"]["key_set"] is True
    assert status["sources"]["google_books"]["key_source"] == "household"
    for secret in SECRETS:
        assert secret not in resp.text


def test_a_person_saves_their_key_and_choices_merged_over_what_is_saved(client, session):
    _act_as("kim")
    resp = client.patch("/api/users/me", json={"google_books_key": f"  {TYPED_KEY} ",
                                               "book_metadata": {"open_library_enabled": False}})
    assert resp.status_code == 200, resp.text
    for secret in SECRETS:
        assert secret not in resp.text
    assert "google_books_key" in resp.json()["saved_credentials"]
    kim = _user(session, "kim")
    assert decrypt(kim.google_books_key_enc) == TYPED_KEY
    resp = client.patch("/api/users/me", json={"book_metadata": {"order": ["open_library", "google_books"]}})
    assert json.loads(_user(session, "kim").book_metadata) == {"open_library_enabled": False,
                                                               "order": ["open_library", "google_books"]}
    data = _resolve(client, rag_user="kim")
    assert data["credential_sources"]["google_books"] == "own"
    assert data["book_metadata"]["sources"]["open_library"]["enabled"] is False


def test_a_bad_setting_is_refused_with_the_reason(client, session):
    _act_as("kim")
    resp = client.patch("/api/users/me", json={"book_metadata": {"order": ["google_books"]}})
    assert resp.status_code == 422 and "order must list each of" in resp.json()["detail"]
    assert _user(session, "kim").book_metadata is None


def test_removing_a_key_falls_back_to_the_households(client, session):
    _act_as("alex")
    resp = client.patch("/api/users/me", json={"clear_fields": ["google_books_key"]})
    assert resp.status_code == 200
    assert _user(session, "alex").google_books_key_enc is None
    assert _resolve(client, rag_user="alex")["credential_sources"]["google_books"] == "household"


def test_an_admin_sets_the_household_key_on_the_default_user(client, session):
    _act_as("default")
    resp = client.patch("/api/users/default", json={"google_books_key": TYPED_KEY,
                                                    "book_metadata": {"google_books_enabled": True}})
    assert resp.status_code == 200, resp.text
    assert decrypt(_user(session, "default").google_books_key_enc) == TYPED_KEY


def test_the_key_is_never_revealed(client):
    resp = client.post("/api/users/alex/reveal", json={"field": "google_books_key"})
    assert resp.status_code == 422, "write-only, like the abuse.ch key"
    assert OWN_KEY not in resp.text


# --- the Test button ----------------------------------------------------------------------


def test_testing_google_books_uses_the_key_being_typed_else_the_saved_one(client, catalogue):
    _act_as("kim")
    resp = client.post("/api/users/me/book-metadata/test", json={"source": "google_books", "api_key": TYPED_KEY})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True and "The Brothers Karamazov" in resp.json()["message"]
    assert f"key={TYPED_KEY}" in catalogue["calls"][-1][0]
    client.post("/api/users/me/book-metadata/test", json={"source": "google_books"})
    assert f"key={HOUSEHOLD_KEY}" in catalogue["calls"][-1][0]
    for secret in SECRETS:
        assert secret not in resp.text


@pytest.mark.parametrize("answer, words", [
    ((429, None), "answered 429: the API key's daily quota is used up"),
    ((403, {"error": {"errors": [{"reason": "accessNotConfigured"}]}}), "HTTP 403, accessNotConfigured"),
    ((400, {"error": {"message": "API key not valid. Please pass a valid API key."}}), "not a valid key"),
    (OSError("no route"), "did not answer"),
])
def test_a_failed_google_test_says_exactly_why(client, catalogue, answer, words):
    catalogue["answer"] = answer
    out = client.post("/api/users/me/book-metadata/test", json={"source": "google_books"}).json()
    assert out["ok"] is False and words in out["message"]


def test_testing_google_books_without_any_key_says_so_and_asks_nothing(client, session, catalogue):
    default = _user(session, "default")
    default.google_books_key_enc = None
    session.commit()
    _act_as("kim")
    out = client.post("/api/users/me/book-metadata/test", json={"source": "google_books"}).json()
    assert out["ok"] is False and "no API key to test" in out["message"]
    assert catalogue["calls"] == []


def test_testing_open_library_sends_the_contact_email(client, catalogue):
    catalogue["answer"] = (200, {"docs": [{"title": "The Brothers Karamazov"}]})
    _act_as("kim")
    out = client.post("/api/users/me/book-metadata/test", json={"source": "open_library"}).json()
    assert out["ok"] is True
    url, headers = catalogue["calls"][-1]
    assert url.startswith("https://openlibrary.org/search.json")
    assert headers["User-Agent"] == "JarvisOS-Library/1.0 (library@example.com)"
    client.post("/api/users/me/book-metadata/test", json={"source": "open_library", "contact_email": "me@example.com"})
    assert catalogue["calls"][-1][1]["User-Agent"] == "JarvisOS-Library/1.0 (me@example.com)"


def test_an_unknown_source_is_refused(client):
    resp = client.post("/api/users/me/book-metadata/test", json={"source": "amazon"})
    assert resp.status_code == 422
