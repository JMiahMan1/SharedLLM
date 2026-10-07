"""The Calibre tool surface: reads only, refusals visible, truncation honest.

Pins three things that would otherwise rot silently:

* the action ``Literal`` is exactly four reads -- no ``delete``, no write --
  because this codebase has no approval gate anywhere in the tool path and a
  Calibre removal cannot be undone;
* client refusals name the real problem (a blank ``calibre_library_path``, a
  PDF-only book, an over-ceiling fetch) instead of degrading to an empty
  answer, per the fail-fast rule;
* a truncated fetch says so with both counts, so a model can never present
  the first 20,000 characters as the whole book.
"""

from __future__ import annotations

import json
import typing

import pytest
from pydantic import ValidationError

from services.execution import calibre_client
from services.execution.handlers import calibre as calibre_handler
from services.execution.schemas import CalibreRequest, UserContext

WESLEY = {
    "path": "/Books/Text/John Wesley/Sermons John Wesley (11).txt",
    "name": "Sermons John Wesley (11).txt",
    "is_dir": False,
    "indexed": True,
    "metadata": {
        "calibre_id": 11,
        "title": "Sermons",
        "author": "John Wesley",
        "tags": "sermon; wesley",
        "formats": "EPUB",
    },
}
MACDUFF = {
    "path": "/Books/Text/John R. Macduff/Memories of Bethany (617).txt",
    "name": "Memories of Bethany (617).txt",
    "is_dir": False,
    "indexed": False,
    "metadata": {
        "calibre_id": 617,
        "title": "Memories of Bethany",
        "author": "John R. Macduff",
        "tags": "devotional",
        "formats": "EPUB",
    },
}
PDF_ONLY = {
    "path": "/Books/Text/A. Author/PDF Book (5).txt",
    "name": "PDF Book (5).txt",
    "is_dir": False,
    "indexed": False,
    "metadata": {
        "calibre_id": 5,
        "title": "PDF Book",
        "author": "A. Author",
        "tags": "",
        "formats": "PDF",
    },
}
THREE_BOOKS = [WESLEY, MACDUFF, PDF_ONLY]


def _resp(payload, status: int = 200) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"status_code": status, "text": text}


def _identity(value: str = "/Books/Text", status: int = 200) -> dict:
    if status != 200:
        return _resp({"detail": "identity down"}, status=status)
    return _resp({"key": calibre_client.LIBRARY_SETTING, "value": value})


def _list(entries: list[dict]) -> dict:
    return _resp({"status": "SUCCESS", "count": len(entries), "entries": entries})


def _fetch(text: str, status: int = 200) -> dict:
    import base64

    body = {"status": "SUCCESS", "content_b64": base64.b64encode(text.encode("utf-8")).decode()}
    if status != 200:
        body = {"detail": "byte fetch refused"}
    return _resp(body, status=status)


class _FakeHttp:
    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected request to {url}")
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _fresh_caches():
    calibre_client.clear_cache()
    yield
    calibre_client.clear_cache()


def _install(monkeypatch, *responses: dict) -> _FakeHttp:
    fake = _FakeHttp(*responses)
    monkeypatch.setattr(calibre_client.http_client, "request", fake.request)
    return fake


def _client() -> calibre_client.CalibreClient:
    return calibre_client.CalibreClient(
        storage_url="http://storage:8005", internal_secret="s3cret"
    )


def _request(action: str, **kwargs) -> CalibreRequest:
    return CalibreRequest(user_context=UserContext(user="tester"), action=action, **kwargs)


async def test_list_returns_rows_and_total(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    rows, total = await _client().list_books(limit=10)
    assert total == 3
    assert [row["book_id"] for row in rows] == [11, 617, 5]
    assert rows[0]["author"] == "John Wesley"
    assert rows[1]["path"].endswith("Memories of Bethany (617).txt")
    assert rows[0]["indexed"] is True
    assert rows[1]["indexed"] is False


async def test_the_shelf_is_fetched_once_and_then_cached(monkeypatch):
    fake = _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    await _client().list_books()
    await _client().list_books()
    urls = [call["url"] for call in fake.calls]
    assert len(urls) == 2, urls
    assert urls[0].endswith(f"/api/settings/{calibre_client.LIBRARY_SETTING}")
    assert urls[1].endswith("/providers/list")


async def test_a_blank_library_path_is_refused_by_name(monkeypatch):
    _install(monkeypatch, _identity(""))
    with pytest.raises(calibre_client.CalibreLibraryError, match="calibre_library_path"):
        await _client().list_books()


async def test_identity_failure_is_named_not_guessed(monkeypatch):
    _install(monkeypatch, _identity(status=500))
    with pytest.raises(calibre_client.CalibreUnavailable, match="Identity answered 500"):
        await _client().list_books()


async def test_search_matches_words_across_title_author_and_tags(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    rows, total = await _client().search(query="wesley sermons", limit=10)
    assert total == 1
    assert rows[0]["book_id"] == 11


async def test_search_requires_every_word(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    _rows, total = await _client().search(query="wesley bethany", limit=10)
    assert total == 0


async def test_search_ranks_a_title_substring_first(monkeypatch):
    title_hit = dict(WESLEY, metadata=dict(WESLEY["metadata"], title="On Grace Alone"))
    author_hit = dict(
        MACDUFF, metadata=dict(MACDUFF["metadata"], title="Meditations", author="Grace Writer")
    )
    _install(monkeypatch, _identity(), _list([author_hit, title_hit]))
    rows, total = await _client().search(query="grace", limit=10)
    assert total == 2
    assert rows[0]["title"] == "On Grace Alone"
    assert rows[1]["title"] == "Meditations"


async def test_search_needs_at_least_one_word(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    with pytest.raises(calibre_client.CalibreLibraryError, match="at least one word"):
        await _client().search(query="!!!")


async def test_get_book_accepts_an_id_as_int_or_string(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    as_int = await _client().get_book(book_id=617)
    as_str = await _client().get_book(book_id="617")
    assert as_int["title"] == as_str["title"] == "Memories of Bethany"


async def test_get_book_accepts_a_shelf_path(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    book = await _client().get_book(path=MACDUFF["path"])
    assert book["book_id"] == 617


async def test_get_book_without_a_locator_is_refused(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    with pytest.raises(calibre_client.CalibreLibraryError, match="book_id"):
        await _client().get_book()


async def test_get_book_unknown_id_suggests_list_or_search(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS))
    with pytest.raises(calibre_client.CalibreLibraryError, match="No book with id 999"):
        await _client().get_book(book_id=999)


async def test_fetch_text_returns_the_books_prose(monkeypatch):
    prose = "Alpha beta gamma. " * 10
    fake = _install(monkeypatch, _identity(), _list(THREE_BOOKS), _fetch(prose))
    text, detail = await _client().fetch_text(book_id=617)
    assert text == prose
    assert detail["total_chars"] == len(prose)
    assert detail["truncated"] is False
    fetch_call = fake.calls[-1]
    assert fetch_call["url"].endswith("/providers/fetch")
    assert fetch_call["json"]["path"] == MACDUFF["path"]
    assert fetch_call["json"]["max_bytes"] == calibre_client.FETCH_MAX_BYTES


async def test_fetch_text_truncates_with_an_explicit_marker(monkeypatch):
    prose = "Alpha beta gamma. " * 10
    _install(monkeypatch, _identity(), _list(THREE_BOOKS), _fetch(prose))
    text, detail = await _client().fetch_text(book_id=617, max_chars=100)
    assert text.startswith(prose[:100])
    assert "Truncated" in text
    assert f"{len(prose):,} characters" in text
    assert "calibre_files" in text
    assert detail["truncated"] is True
    assert detail["total_chars"] == len(prose)


async def test_a_pdf_only_book_is_refused_with_its_actual_problem(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS), _fetch("", status=502))
    with pytest.raises(calibre_client.CalibreLibraryError, match="no text to fetch"):
        await _client().fetch_text(book_id=5)


async def test_an_oversize_fetch_points_at_the_ceiling(monkeypatch):
    _install(monkeypatch, _identity(), _list(THREE_BOOKS), _fetch("", status=413))
    with pytest.raises(calibre_client.CalibreLibraryError, match="too large for one fetch"):
        await _client().fetch_text(book_id=617)


async def test_a_storage_failure_keeps_its_status(monkeypatch):
    _install(monkeypatch, _identity(), _resp({"detail": "disk on fire"}, status=500))
    with pytest.raises(calibre_client.CalibreUnavailable, match="answered 500.*disk on fire"):
        await _client().list_books()


async def test_the_action_set_is_exactly_the_four_reads():
    annotation = CalibreRequest.model_fields["action"].annotation
    assert set(typing.get_args(annotation)) == {"list", "search", "get_book", "fetch_text"}


async def test_delete_is_not_a_nameable_action():
    with pytest.raises(ValidationError):
        _request("delete")


def _stub(monkeypatch, **methods):
    class Stub:
        pass

    stub = Stub()
    for name, fn in methods.items():
        setattr(stub, name, fn)
    monkeypatch.setattr(calibre_handler, "_client", lambda: stub)
    return stub


async def test_list_reports_the_shelf_size(monkeypatch):
    async def list_books(*, limit):
        return [{"title": "Sermons", "author": "John Wesley", "book_id": 1}], 4000

    _stub(monkeypatch, list_books=list_books)
    result = await calibre_handler.handle_calibre(_request("list"))
    assert result.status == "SUCCESS"
    assert "4,000 books" in result.message
    assert result.detail["total"] == 4000


async def test_list_says_how_many_are_hidden_and_how_to_see_them(monkeypatch):
    async def list_books(*, limit):
        return [{"title": f"T{i}", "author": "A", "book_id": i} for i in range(12)], 4000

    _stub(monkeypatch, list_books=list_books)
    result = await calibre_handler.handle_calibre(_request("list"))
    assert "3,990 more not shown; raise limit" in result.message


async def test_search_without_a_query_is_refused_by_name(monkeypatch):
    async def search(*, query, limit):
        raise AssertionError("search must not run without a query")

    _stub(monkeypatch, search=search)
    result = await calibre_handler.handle_calibre(_request("search"))
    assert result.status == "FAILURE"
    assert "needs a query" in result.message


async def test_search_reports_no_matches_honestly(monkeypatch):
    async def search(*, query, limit):
        return [], 0

    _stub(monkeypatch, search=search)
    result = await calibre_handler.handle_calibre(_request("search", query="zzzz"))
    assert result.status == "SUCCESS"
    assert "No books on the shelf match 'zzzz'" in result.message


async def test_a_client_refusal_becomes_a_visible_failure(monkeypatch):
    async def get_book(*, book_id=None, path=None):
        raise calibre_client.CalibreLibraryError("No book with id 42 on the shelf.")

    _stub(monkeypatch, get_book=get_book)
    result = await calibre_handler.handle_calibre(_request("get_book", book_id=42))
    assert result.status == "FAILURE"
    assert "No book with id 42" in result.message


async def test_an_unexpected_crash_is_reported_not_raised(monkeypatch):
    async def list_books(*, limit):
        raise RuntimeError("kaboom")

    _stub(monkeypatch, list_books=list_books)
    result = await calibre_handler.handle_calibre(_request("list"))
    assert result.status == "FAILURE"
    assert "failed unexpectedly" in result.message
    assert "kaboom" in result.message


async def test_fetch_text_leads_with_the_book_and_returns_the_prose(monkeypatch):
    async def fetch_text(*, book_id=None, path=None, max_chars=20000):
        return (
            "Prose here.",
            {
                "book_id": 617,
                "title": "Memories of Bethany",
                "author": "John R. Macduff",
                "total_chars": 11,
                "truncated": False,
            },
        )

    _stub(monkeypatch, fetch_text=fetch_text)
    result = await calibre_handler.handle_calibre(_request("fetch_text", book_id=617))
    assert result.status == "SUCCESS"
    assert result.message.startswith(
        "Memories of Bethany by John R. Macduff (11 characters):"
    )
    assert "Prose here." in result.message
