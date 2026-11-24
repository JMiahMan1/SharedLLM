"""One call from "chapter 4 of Wild Heart by John Eldridge" to that chapter's text.

A voice or chat turn has three model turns: read the chapter, save the notes,
answer. So ``fetch_text`` takes the spoken title/author as ``query`` (tolerant
of the misspellings speech recognition makes) and a ``chapter``, finds the
book, and returns the chapter; a tie names both books and a miss names the
closest ones instead of guessing.
"""

from __future__ import annotations

import json

import pytest

from services.execution import calibre_client
from services.execution.calibre_client import CalibreAmbiguous, CalibreLibraryError, match_books, pick_book
from services.execution.handlers import calibre as calibre_handler
from services.execution.schemas import CalibreRequest, UserContext


def _entry(book_id: int, title: str, author: str, formats: str = "EPUB") -> dict:
    return {
        "path": f"/Books/Text/{author}/{title} ({book_id}).txt",
        "name": f"{title} ({book_id}).txt",
        "is_dir": False,
        "indexed": True,
        "metadata": {"calibre_id": book_id, "title": title, "author": author, "tags": "", "formats": formats},
    }


SHELF = [
    _entry(7, "Wild at Heart", "John Eldredge"),
    _entry(8, "Waking the Dead", "John Eldredge"),
    _entry(9, "Wild Hearts Can't Be Broken", "Some Other"),
    _entry(10, "The Sacred Romance", "Brent Curtis"),
    _entry(11, "Memories of Bethany", "John R. Macduff"),
]
ROWS = [calibre_client._book_row(e) for e in SHELF]


def _resp(payload, status: int = 200) -> dict:
    return {"status_code": status, "text": payload if isinstance(payload, str) else json.dumps(payload)}


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
def _fresh(monkeypatch):
    calibre_client.clear_cache()
    monkeypatch.setattr(
        calibre_handler, "_client",
        lambda: calibre_client.CalibreClient(storage_url="http://storage:8005", internal_secret="s3cret"),
    )
    yield
    calibre_client.clear_cache()


def _install(monkeypatch, *responses: dict) -> _FakeHttp:
    fake = _FakeHttp(*responses)
    monkeypatch.setattr(calibre_client.http_client, "request", fake.request)
    return fake


def _identity() -> dict:
    return _resp({"key": calibre_client.LIBRARY_SETTING, "value": "/Books/Text"})


def _shelf(entries=SHELF) -> dict:
    return _resp({"status": "SUCCESS", "count": len(entries), "entries": entries})


CHAPTER_LIST = [
    {"index": 0, "number": None, "title": "Copyright", "label": "Copyright", "front_matter": True, "chars": 80},
    {"index": 1, "number": None, "title": "Introduction", "label": "Introduction", "front_matter": True, "chars": 900},
    *[
        {"index": i + 1, "number": i, "title": t, "label": t, "front_matter": False, "chars": 20000}
        for i, t in enumerate(["Wild at Heart", "The Wild One", "The Question", "The Wound", "The Battle"], 1)
    ],
    {"index": 7, "number": None, "title": "Notes", "label": "Notes", "front_matter": True, "chars": 500},
]


def _chapters(chapter=None) -> dict:
    return _resp({"book": {"id": 7}, "format": "EPUB", "source": "epub-nav", "chapters": CHAPTER_LIST, "chapter": chapter})


def _request(action: str, **kwargs) -> CalibreRequest:
    return CalibreRequest(user_context=UserContext(user="tester"), action=action, **kwargs)


# --- Finding the book from what was said ---


@pytest.mark.parametrize(
    "query",
    ["Wild at Heart by John Eldredge", "wild heart eldridge", "Wild Heart", "WILD AT HEART, John Eldridge", "wild at heart"],
)
def test_speech_misspellings_still_find_the_book(query):
    assert pick_book(ROWS, query)["book_id"] == 7


def test_an_author_with_several_books_is_a_question_not_a_guess():
    with pytest.raises(CalibreAmbiguous) as caught:
        pick_book(ROWS, "John Eldridge")
    assert {row["book_id"] for row in caught.value.candidates} == {7, 8}
    assert "Ask which one" in str(caught.value)


def test_two_books_of_the_same_title_are_both_named():
    rows = [*ROWS, calibre_client._book_row(_entry(12, "Wild at Heart", "Barry Gifford"))]
    with pytest.raises(CalibreAmbiguous) as caught:
        pick_book(rows, "Wild at Heart")
    assert {row["book_id"] for row in caught.value.candidates} == {7, 12}
    assert pick_book(rows, "Wild at Heart Gifford")["book_id"] == 12


def test_two_copies_of_one_book_are_one_match():
    rows = [*ROWS, calibre_client._book_row(_entry(13, "Wild at Heart", "John Eldredge"))]
    matches, _closest = match_books(rows, "wild at heart")
    assert [row["book_id"] for row in matches] == [7]


def test_no_match_says_so_and_names_the_closest_two():
    with pytest.raises(CalibreLibraryError) as caught:
        pick_book(ROWS, "Wild Things by Maurice Sendak")
    message = str(caught.value)
    assert not isinstance(caught.value, CalibreAmbiguous)
    assert message.startswith("The library has no book matching")
    assert "closest titles" in message and "Wild at Heart" in message and message.count(" (id ") == 2


# --- One call: book and chapter ---


async def test_fetch_text_with_query_and_chapter_is_one_tool_call(monkeypatch):
    chapter = {**CHAPTER_LIST[5], "part": 1, "parts": 3, "text": "Every man carries a wound."}
    fake = _install(monkeypatch, _identity(), _shelf(), _chapters(chapter))
    result = await calibre_handler.handle_calibre(
        _request("fetch_text", query="Wild Heart by John Eldridge", chapter="four")
    )
    assert result.status == "SUCCESS", result.message
    assert result.message.startswith("Wild at Heart by John Eldredge, chapter 4: The Wound (part 1 of 3;")
    assert "part=2" in result.message and "Every man carries a wound." in result.message
    post = fake.calls[-1]
    assert post["url"] == "http://storage:8005/calibre/books/7/chapters"
    assert post["json"]["chapter"] == "four" and post["json"]["part"] == 1
    assert post["json"]["max_chars"] == calibre_handler._chapter_max_chars() == 60000
    assert result.detail["chapter"]["number"] == 4 and result.detail["chapter"]["parts"] == 3


async def test_the_last_part_says_it_is_the_last(monkeypatch):
    chapter = {**CHAPTER_LIST[5], "part": 3, "parts": 3, "text": "The end of it."}
    _install(monkeypatch, _identity(), _shelf(), _chapters(chapter))
    result = await calibre_handler.handle_calibre(_request("fetch_text", book_id=7, chapter=4, part=3))
    assert "(part 3 of 3; this is the last part)" in result.message


async def test_a_short_chapter_has_no_part_note(monkeypatch):
    chapter = {**CHAPTER_LIST[5], "part": 1, "parts": 1, "text": "Whole."}
    _install(monkeypatch, _identity(), _shelf(), _chapters(chapter))
    result = await calibre_handler.handle_calibre(_request("fetch_text", book_id=7, chapter="IV"))
    assert result.message == "Wild at Heart by John Eldredge, chapter 4: The Wound:\n\nWhole."


async def test_an_ambiguous_query_returns_the_candidates(monkeypatch):
    _install(monkeypatch, _identity(), _shelf())
    result = await calibre_handler.handle_calibre(_request("fetch_text", query="Eldredge", chapter=4))
    assert result.status == "FAILURE"
    assert result.detail["needs_choice"] is True
    assert {c["book_id"] for c in result.detail["candidates"]} == {7, 8}


async def test_a_missing_chapter_is_the_storage_services_words(monkeypatch):
    _install(monkeypatch, _identity(), _shelf(),
             _resp({"detail": "Wild at Heart: This book has no chapter 9; it has chapters 1 to 5."}, status=404))
    result = await calibre_handler.handle_calibre(_request("fetch_text", query="wild at heart", chapter=9))
    assert result.status == "FAILURE"
    assert result.message == "Wild at Heart: This book has no chapter 9; it has chapters 1 to 5."


async def test_fetch_text_with_only_a_query_reads_the_whole_book(monkeypatch):
    import base64

    fake = _install(
        monkeypatch, _identity(), _shelf(),
        _resp({"status": "SUCCESS", "content_b64": base64.b64encode(b"All of it.").decode()}),
    )
    result = await calibre_handler.handle_calibre(_request("fetch_text", query="memories of bethany"))
    assert result.status == "SUCCESS" and "All of it." in result.message
    assert fake.calls[-1]["json"]["path"].endswith("(11).txt")


async def test_chapters_lists_numbers_and_leaves_front_matter_unnumbered(monkeypatch):
    _install(monkeypatch, _identity(), _shelf(), _chapters())
    result = await calibre_handler.handle_calibre(_request("chapters", query="wild at heart eldredge"))
    assert result.status == "SUCCESS"
    message = result.message
    assert message.startswith("Wild at Heart by John Eldredge (id 7) has 5 chapters, from the book's table of contents.")
    assert "Before chapter 1 (not numbered): Copyright, Introduction." in message
    assert "4. The Wound (~20,000 chars)" in message
    assert "Also (not numbered): Notes." in message
    assert "chapter=<number or title>" in message


async def test_a_book_must_be_named():
    result = await calibre_handler.handle_calibre(_request("chapters"))
    assert result.status == "FAILURE" and "book_id" in result.message and "query" in result.message


def test_chapter_accepts_a_number_or_words():
    assert _request("fetch_text", book_id=7, chapter=4).chapter == 4
    assert _request("fetch_text", book_id=7, chapter="four").chapter == "four"
    with pytest.raises(ValueError):
        _request("fetch_text", book_id=7, chapter=4, part=0)
