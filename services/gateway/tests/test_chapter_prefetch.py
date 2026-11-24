"""A question that names a chapter of a book gets that chapter read before the model runs.

Live, 2026-11: asked by voice to "read chapter 4 of the book <Title> by <Author> from our
library and give me a summary", the book was not in the search index yet (a full reindex
was running), the prompt said it was "not in the indexed library", and the model answered
that it could not read the chapter and summarized from memory, never calling a tool. Yet
CalibreRequest fetch_text with query and chapter read that chapter from the PDF. Now the
gateway reads it itself, puts it with the question (never in the system message), and
skips the not-indexed note for that book; a failure is reported plainly.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.gateway import library_names, orchestrator, prompt_budget

QUESTION = (
    "Please read chapter 4 of the book Quiet Harbor Lights by Ann Fakewright from our library "
    "and give me a summary."
)
CHAPTER_TEXT = "The lighthouse keeper counted the ships. " * 20


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from services.common.circuit_breaker import reset_breakers

    reset_breakers()
    prompt_budget.reset()
    monkeypatch.setattr(orchestrator, "get_all_settings", AsyncMock(
        return_value={"rag_svc_url": "http://rag", "execution_svc_url": "http://exec"}))
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=[]))
    yield
    reset_breakers()


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


def _chapter_ok(text: str = CHAPTER_TEXT) -> dict:
    return {
        "status": "SUCCESS", "service": "calibre",
        "message": f"Quiet Harbor Lights by Ann Fakewright, chapter 4: The Keeper:\n\n{text}",
        "detail": {"book_id": 31, "title": "Quiet Harbor Lights", "author": "Ann Fakewright",
                   "chapter": {"index": 5, "number": 4, "title": "The Keeper", "part": 1, "parts": 1}},
    }


class _Services:
    """A fake RAG that has not indexed the book, and a fake execution /execute/calibre."""

    def __init__(self, calibre: dict | None = None, delay: float = 0.0):
        self.calibre = calibre if calibre is not None else _chapter_ok()
        self.delay = delay
        self.calibre_calls: list[dict] = []
        self.headers: list[dict] = []

    async def post(self, url, json=None, headers=None, **kwargs):
        if url.endswith("/rag/search"):
            return _Resp({"results": []})
        if url == "http://exec/execute/calibre":
            self.calibre_calls.append(json)
            self.headers.append(headers or {})
            if self.delay:
                await asyncio.sleep(self.delay)
            return _Resp(self.calibre)
        return _Resp({})

    async def get(self, url, **kwargs):
        return _Resp({})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def _ask(monkeypatch, services: _Services, query: str = QUESTION) -> dict:
    """Run one chat turn; return what reached the model call."""
    seen: dict = {}

    async def fake_inference(query, model, system_prompt, rag_context, history, creds, *args, **kwargs):
        seen.update(kwargs, rag_context=rag_context, system_prompt=system_prompt)
        return "ok"

    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    with patch("services.gateway.main.shared_http_client", return_value=services):
        await orchestrator.process_full_orchestration({
            "query": query, "creds": {"user": "reader"}, "model": "m", "system": "FIXED SYSTEM",
        })
    return seen


# --- reading the chapter number ---------------------------------------------------------

@pytest.mark.parametrize("question, chapter, title, author", [
    (QUESTION, 4, "Quiet Harbor Lights", "Ann Fakewright"),
    ("summarize chapter four of quiet harbor lights by ann fakewright", 4, "quiet harbor lights", "ann fakewright"),
    ('read the fourth chapter of "Quiet Harbor Lights"', 4, "Quiet Harbor Lights", ""),
    ("What does chapter IV of The Pilgrim Lantern by Tom Notreal say?", 4, "The Pilgrim Lantern", "Tom Notreal"),
    ("the 3rd chapter of Morning Orchard by Bea Madeup please", 3, "Morning Orchard", "Bea Madeup"),
    ("chapter twenty-one of Rivers Below by Ann Fakewright", 21, "Rivers Below", "Ann Fakewright"),
    ("read chapter xii of Seven Small Lamps by Tom Notreal", 12, "Seven Small Lamps", "Tom Notreal"),
])
def test_a_named_chapter_is_read_in_digits_words_ordinals_and_roman(question, chapter, title, author):
    ref = library_names.chapter_named(question)
    assert ref is not None
    assert (ref.chapter, ref.title, ref.author) == (chapter, title, author)
    assert ref.book_query == f"{title} {author}".strip()


@pytest.mark.parametrize("token, number", [
    ("4", 4), ("four", 4), ("fourth", 4), ("4th", 4), ("IV", 4), ("iv", 4), ("twenty-one", 21),
    ("XII", 12), ("mix", None), ("did", None), ("0", None), ("999", None),
])
def test_chapter_numbers(token, number):
    assert library_names.chapter_number(token) == number


@pytest.mark.parametrize("question", [
    "What does the library say about lighthouses?",
    "Give me notes on Quiet Harbor Lights by Ann Fakewright",
    "summarize chapter 3 of John",
    "read chapter 3 of the book of Romans",
    "what did chapter i say about it",
    "John chapter 3 verse 16",
])
def test_no_chapter_ref_without_a_chapter_of_a_book(question):
    assert library_names.chapter_named(question) is None


# --- the note for a book the index does not hold yet ------------------------------------

def test_the_not_indexed_note_never_says_the_book_cannot_be_read():
    note = library_names.not_indexed_note(["Quiet Harbor Lights", "Ann Fakewright"])
    assert "not found in the library catalogue search" in note
    assert "CalibreRequest" in note and "fetch_text" in note and "chapter" in note
    assert "not in the indexed library" not in note
    assert "not in the library" not in note


# --- the prefetch -----------------------------------------------------------------------

async def test_a_named_chapter_is_read_as_the_asker_and_put_with_the_question(monkeypatch):
    services = _Services()
    seen = await _ask(monkeypatch, services)
    assert services.calibre_calls == [{
        "action": "fetch_text", "query": "Quiet Harbor Lights Ann Fakewright", "chapter": "4",
        "user_context": services.calibre_calls[0]["user_context"],
    }]
    assert services.calibre_calls[0]["user_context"]["user"] == "reader"
    assert services.headers[0].get("X-Internal-Secret") == orchestrator.INTERNAL_SECRET
    chapter = seen["book_chapter"]
    assert chapter.startswith(
        "[BOOK CHAPTER, READ FROM YOUR LIBRARY: Quiet Harbor Lights by Ann Fakewright, chapter 4: The Keeper]")
    assert CHAPTER_TEXT.strip() in chapter
    # The system message is the caller's, untouched; the chapter is not in it.
    assert seen["system_prompt"] == "FIXED SYSTEM"
    # The book was not in the index, but its chapter was read: no not-indexed note.
    assert orchestrator.LIBRARY_NOT_INDEXED_HEADER not in seen["rag_context"]
    assert "catalogue search" not in seen["rag_context"]


async def test_a_long_chapter_is_cut_with_a_note(monkeypatch):
    monkeypatch.setattr(orchestrator, "CHAPTER_PREFETCH_MAX_CHARS", 100)
    seen = await _ask(monkeypatch, _Services())
    assert CHAPTER_TEXT[:90] in seen["book_chapter"]
    assert CHAPTER_TEXT not in seen["book_chapter"]
    assert f"the first 100 of {len(CHAPTER_TEXT):,} characters" in seen["book_chapter"]


async def test_a_chapter_that_cannot_be_read_is_reported_plainly(monkeypatch):
    failure = {"status": "FAILURE", "service": "calibre",
               "message": "Quiet Harbor Lights: no chapter 4 (the book has 3 chapters)."}
    seen = await _ask(monkeypatch, _Services(calibre=failure))
    chapter = seen["book_chapter"]
    assert "[BOOK CHAPTER NOT READ: chapter 4 of Quiet Harbor Lights Ann Fakewright]" in chapter
    assert "no chapter 4 (the book has 3 chapters)" in chapter
    assert "READ FROM YOUR LIBRARY" not in chapter
    # Nothing was read, so the index note stays (in its new wording).
    assert "not found in the library catalogue search" in seen["rag_context"]


async def test_a_slow_library_is_given_up_on(monkeypatch):
    monkeypatch.setattr(orchestrator, "CHAPTER_PREFETCH_TIMEOUT_S", 0.05)
    seen = await _ask(monkeypatch, _Services(delay=1.0))
    assert "BOOK CHAPTER NOT READ" in seen["book_chapter"]
    assert "no answer within" in seen["book_chapter"]


async def test_no_prefetch_when_no_chapter_is_named(monkeypatch):
    services = _Services()
    seen = await _ask(monkeypatch, services, "Give me notes on Quiet Harbor Lights by Ann Fakewright")
    assert services.calibre_calls == []
    assert "book_chapter" not in seen


# --- where the chapter travels ----------------------------------------------------------

async def test_the_chapter_rides_with_the_question_and_the_system_message_is_unchanged(monkeypatch):
    calls: list[list[dict]] = []

    async def _call_ollama(payload, **kwargs):
        calls.append(payload["messages"])
        return {"message": {"content": "A summary."}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "## Available Tools\n")
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "KNOWLEDGE MAP")
    block = "[BOOK CHAPTER, READ FROM YOUR LIBRARY: Quiet Harbor Lights by Ann Fakewright, chapter 4]\nText."
    creds = orchestrator.ResolvedCredentials(user="reader")
    for chapter in ("", block):
        await orchestrator._single_turn_inference(
            query=QUESTION, model="m", system_prompt="FIXED SYSTEM", rag_context="", history=[],
            creds=creds, book_chapter=chapter,
        )
    assert calls[0][0] == calls[1][0]
    assert block not in calls[1][0]["content"]
    assert block in calls[1][-1]["content"]
    assert calls[1][-1]["content"].index(block) < calls[1][-1]["content"].index("[Question]")


def test_the_prefetch_waits_long_enough_for_a_first_read():
    # A first read downloads and opens the book; the voice satellite holds meanwhile.
    assert orchestrator.CHAPTER_PREFETCH_TIMEOUT_S == 60.0


async def test_a_closest_book_read_for_a_misheard_name_is_named(monkeypatch):
    read = _chapter_ok()
    read["detail"]["asked_for"] = "Quiet Harper Lights by Anne Fakewrite"
    read["message"] = ("Read Quiet Harbor Lights by Ann Fakewright (asked for 'Quiet Harper Lights by Anne "
                       f"Fakewrite'), chapter 4: The Keeper:\n\n{CHAPTER_TEXT}")
    seen = await _ask(monkeypatch, _Services(calibre=read))
    chapter = seen["book_chapter"]
    assert chapter.startswith("[BOOK CHAPTER, READ FROM YOUR LIBRARY: Quiet Harbor Lights by Ann Fakewright")
    assert "named 'Quiet Harper Lights by Anne Fakewrite'" in chapter and "Say at the start which book" in chapter
    assert CHAPTER_TEXT.strip() in chapter
