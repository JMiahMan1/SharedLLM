"""The book catalogues are integrations, not an environment variable.

Google Books and Open Library are set up per person in Settings > Accounts &
Integrations (identity resolves them, the household's on the default user
filling in what a person has not set). Storage asks identity for the requesting
person's settings, asks only the catalogues that are on and ready, in the order
set, and says which were asked, which were skipped and why, and which answered
with an error (a refused key, a used-up quota). Every key here is made up.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from fastapi.testclient import TestClient

from services.common import book_metadata
from services.storage import book_lookup, book_sources, library_api
from services.storage.main import app
from services.storage.tests.test_book_enrichment import _add, _library
from services.storage.tests.test_calibre_writer import _Dav, _library_bytes

client = TestClient(app)
H = {"X-Internal-Secret": "test-secret"}
KEY = "made-up-books-key"


def _resolved(own=None, household=None, key=KEY, key_source="own"):
    return book_metadata.resolve(own, household, key=key, key_source=key_source)


@pytest.fixture
def identity(monkeypatch):
    """identity's /api/resolve, replaced. ``state["answer"]`` is every person's book_metadata (or an exception)."""
    state: dict = {"answer": _resolved({"google_books_enabled": True}), "asked": []}

    async def resolve(body):
        state["asked"].append(body)
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return {"user": body.get("rag_user") or "household", "book_metadata": state["answer"]}

    book_sources.forget()
    book_lookup.LAST_RESULT.clear()
    monkeypatch.setattr(book_sources, "identity_resolve", resolve)
    yield state
    book_sources.forget()
    book_lookup.LAST_RESULT.clear()


@pytest.fixture
def catalogues(monkeypatch):
    """Both catalogues, replaced: each answers ``state[name]`` (a dict, or a CatalogueError to raise)."""
    state: dict = {"order": [], "keys": [], "google_books": {}, "open_library": {}}

    def fake(name):
        async def fetch(session, title, author, isbn, **kw):
            state["order"].append(name)
            if "api_key" in kw:
                state["keys"].append(kw["api_key"])
            answer = state[name]
            if isinstance(answer, Exception):
                raise answer
            out = book_lookup._blank()
            out.update(answer)
            return out
        return fetch

    monkeypatch.setattr(book_lookup, "google_books", fake("google_books"))
    monkeypatch.setattr(book_lookup, "open_library", fake("open_library"))

    async def no_cover(url):
        return None

    monkeypatch.setattr(book_lookup, "fetch_cover", no_cover)
    return state


def _lookup(settings, title="Harbor Lights", authors=("Ann Example",)):
    return asyncio.run(book_lookup.lookup(book_lookup._blank(), "harbor.epub", [], catalogues=settings,
                                          known={"title": title, "authors": list(authors)}))


HARBOR = {"title": "Harbor Lights", "authors": ["Ann Example"], "comments": "A keeper's year."}


# --- the settings ------------------------------------------------------------------------


def test_a_persons_choice_wins_then_the_households_then_the_default():
    own = {"open_library_enabled": False}
    household = {"google_books_enabled": True, "open_library_enabled": True, "order": ["open_library", "google_books"],
                 "open_library_email": "library@example.com"}
    out = book_metadata.resolve(own, household, key=KEY, key_source="household")
    assert out["sources"]["open_library"]["enabled"] is False and out["sources"]["open_library"]["enabled_from"] == "own"
    assert out["sources"]["google_books"]["enabled"] is True and out["sources"]["google_books"]["enabled_from"] == "household"
    assert out["order"] == ["open_library", "google_books"] and out["order_from"] == "household"
    assert out["sources"]["open_library"]["contact_email"] == "library@example.com"
    assert out["sources"]["google_books"]["key_source"] == "household"
    nobody = book_metadata.resolve(None, None, key=None, key_source="absent")
    assert nobody["sources"]["open_library"]["enabled"] is True, "Open Library needs nothing set up"
    assert nobody["sources"]["google_books"]["enabled"] is False, "Google Books is off until it has a key"
    assert nobody["sources"]["google_books"]["reason"] == (
        "Google Books: not set up (add an API key in Settings > Accounts & Integrations)")


def test_turned_on_without_a_key_says_so_and_the_key_never_reaches_a_browser():
    out = book_metadata.resolve({"google_books_enabled": True}, None, key=None, key_source="absent")
    google = out["sources"]["google_books"]
    assert google["ready"] is False
    assert google["reason"] == "Google Books: turned on but no API key is set in Settings > Accounts & Integrations"
    public = book_metadata.public(_resolved({"google_books_enabled": True}))
    assert KEY not in repr(public) and public["sources"]["google_books"]["key_set"] is True


@pytest.mark.parametrize("update, error", [
    ({"order": ["google_books"]}, "order must list each of"),
    ({"google_books_enabled": "yes"}, "must be true, false or null"),
    ({"open_library_email": "not an email"}, "must be an email address"),
    ({"surprise": 1}, "does not have surprise"),
])
def test_a_settings_update_that_cannot_be_saved_says_why(update, error):
    with pytest.raises(book_metadata.SettingsError, match=error):
        book_metadata.clean_update(update)


def test_an_update_merges_over_what_is_saved_and_null_follows_the_household():
    saved = book_metadata.clean_update({"google_books_enabled": True, "open_library_email": "a@example.com"})
    merged = book_metadata.clean_update({"open_library_enabled": False}, saved)
    assert book_metadata.parse(merged) == {"google_books_enabled": True, "open_library_enabled": False,
                                           "open_library_email": "a@example.com"}
    assert book_metadata.clean_update({"google_books_enabled": None, "open_library_enabled": None,
                                       "open_library_email": ""}, merged) is None


@pytest.mark.parametrize("status, body, words", [
    (429, None, "answered 429: the API key's daily quota is used up"),
    (403, {"error": {"errors": [{"reason": "accessNotConfigured"}]}}, "refused the API key (HTTP 403, accessNotConfigured)"),
    (400, {"error": {"message": "API key not valid. Please pass a valid API key."}}, "not a valid key"),
    (503, None, "Google Books answered HTTP 503."),
])
def test_google_errors_are_said_exactly(status, body, words):
    assert words in book_metadata.google_error(status, body)


# --- the lookup --------------------------------------------------------------------------


def test_only_the_catalogues_that_are_on_are_asked_in_the_order_set(catalogues):
    catalogues["open_library"] = HARBOR
    catalogues["google_books"] = HARBOR
    out = _lookup(_resolved({"google_books_enabled": True, "order": ["open_library", "google_books"]}))
    assert catalogues["order"] == ["open_library", "google_books"]
    assert catalogues["keys"] == [KEY], "the person's key is the one used"
    assert out["catalogues"]["used"] == ["Open Library", "Google Books"]

    catalogues["order"].clear()
    out = _lookup(_resolved({"open_library_enabled": False, "google_books_enabled": True}))
    assert catalogues["order"] == ["google_books"]
    assert out["catalogues"]["skipped"] == [
        {"source": "Open Library", "reason": "Open Library: turned off in Settings > Accounts & Integrations"}]


def test_google_books_on_without_a_key_is_skipped_and_never_asked_keyless(catalogues):
    catalogues["open_library"] = HARBOR
    out = _lookup(_resolved({"google_books_enabled": True}, key=None, key_source="absent"))
    assert catalogues["order"] == ["open_library"]
    assert out["catalogues"]["skipped"][0]["reason"] == (
        "Google Books: turned on but no API key is set in Settings > Accounts & Integrations")
    line = book_lookup.catalogue_line(out["catalogues"])
    assert line == ("Asked Open Library. Skipped Google Books: turned on but no API key is set in "
                    "Settings > Accounts & Integrations.")


def test_google_books_itself_refuses_to_ask_without_a_key():
    with pytest.raises(book_lookup.CatalogueError, match="no API key is set"):
        asyncio.run(book_lookup.google_books(None, "Harbor Lights", "", ""))


def test_a_catalogue_error_is_reported_and_the_next_catalogue_still_answers(catalogues, identity):
    catalogues["google_books"] = book_lookup.CatalogueError(book_metadata.google_error(429))
    catalogues["open_library"] = HARBOR
    out = _lookup(_resolved({"google_books_enabled": True}))
    assert out["identified"] is True and out["catalogues"]["used"] == ["Open Library"]
    assert out["catalogues"]["errors"] == [{"source": "Google Books", "error": book_metadata.google_error(429)}]
    message = book_lookup.report("Harbor Lights", True, {"comments": "Open Library"}, [], out["catalogues"])
    assert "Google Books answered 429" in message and "Asked Google Books and Open Library." in message

    # The integration's status shows the last answer of each.
    status = client.post("/calibre/catalogues", headers=H, json={"user": "kim"}).json()
    assert status["sources"]["google_books"]["last"]["ok"] is False
    assert "429" in status["sources"]["google_books"]["last"]["error"]
    assert status["sources"]["open_library"]["last"]["ok"] is True
    assert KEY not in repr(status)


def test_a_real_google_429_and_403_become_catalogue_errors():
    class Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        async def json(self, content_type=None):
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        def __init__(self, status, body=None):
            self.status, self.body, self.urls = status, body, []

        def get(self, url, timeout=None):
            self.urls.append(url)
            return Resp(self.status, self.body)

    for status, words in ((429, "daily quota is used up"), (403, "HTTP 403")):
        session = Session(status, {"error": {"errors": [{"reason": "forbidden"}]}} if status == 403 else None)
        with pytest.raises(book_lookup.CatalogueError, match=words):
            asyncio.run(book_lookup.google_books(session, "Harbor Lights", "", "", api_key=KEY))
        assert f"key={KEY}" in session.urls[0]


def test_when_identity_cannot_be_asked_no_catalogue_is_asked_and_it_says_why(catalogues, identity):
    identity["answer"] = RuntimeError("down")
    settings = asyncio.run(book_sources.settings_for("kim"))
    out = _lookup(settings)
    assert catalogues["order"] == []
    assert {s["reason"] for s in out["catalogues"]["skipped"]} == {book_sources.UNREADABLE}


def test_settings_are_asked_for_the_requesting_person_and_cached(identity):
    asyncio.run(book_sources.settings_for("Kim"))
    asyncio.run(book_sources.settings_for("kim"))
    asyncio.run(book_sources.settings_for(""))
    assert identity["asked"] == [{"rag_user": "kim"}, {"household": True}]


def test_open_librarys_user_agent_names_the_contact_email():
    assert book_metadata.user_agent("library@example.com") == "JarvisOS-Library/1.0 (library@example.com)"
    assert book_metadata.user_agent("") == "JarvisOS-Library/1.0"


def test_nothing_reads_the_environment_for_a_key():
    source = inspect.getsource(book_lookup) + inspect.getsource(book_sources)
    assert "GOOGLE_BOOKS_API_KEY" not in source and "os.getenv" not in source


# --- the routes --------------------------------------------------------------------------


def test_adding_a_book_uses_the_adders_integrations_and_says_what_was_skipped(monkeypatch, catalogues, identity):
    identity["answer"] = _resolved(None, None, key=None, key_source="absent")
    catalogues["open_library"] = HARBOR
    _library(monkeypatch, _Dav(_library_bytes([("Old Sea Tale", "Someone", ["Fiction"])])))
    out = _add({"user": "kim"}).json()
    assert identity["asked"] == [{"rag_user": "kim"}]
    assert out["catalogues"]["used"] == ["Open Library"]
    assert out["catalogues"]["skipped"] == [{"source": "Google Books", "reason":
                                             "Google Books: not set up (add an API key in Settings > Accounts & Integrations)"}]
    assert "Skipped Google Books: not set up (add an API key in Settings > Accounts & Integrations)." in out["message"]


def test_the_enrich_reply_names_the_catalogues_used_and_skipped(monkeypatch, catalogues, identity):
    identity["answer"] = _resolved({"google_books_enabled": True}, key=None, key_source="absent")
    catalogues["open_library"] = {"title": "Old Sea Tale", "authors": ["Someone"], "comments": "Waves."}
    _library(monkeypatch, _Dav(_library_bytes([("Old Sea Tale", "Someone", [])])))

    async def catalog(path):
        return {1: {"id": 1, "title": "Old Sea Tale", "authors": ["Someone"], "tags": [], "text_path": ""}}

    monkeypatch.setattr(library_api, "_catalog", catalog)
    out = client.post("/calibre/books/enrich", headers=H,
                      json={"library_path": "/Books/Text", "user": "kim", "limit": 5}).json()
    assert out["catalogues"]["used"] == ["Open Library"]
    assert out["catalogues"]["skipped"][0]["source"] == "Google Books"
    assert "turned on but no API key" in out["catalogues"]["message"]
    assert out["proposals"][0]["catalogues"]["asked"] == ["Open Library"]
