""""Read ..." on the fast path: scripture, a chapter of a library book, a book, and keep reading.

Titles, authors and the library are fakes; the Bible reference parser is the real one.
"""
from __future__ import annotations

import fakeredis
import pytest

from services.gateway import fast_read


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    from services.gateway import cache

    monkeypatch.setattr(cache, "_redis", fakeredis.FakeRedis(decode_responses=True))


@pytest.mark.parametrize("query", [
    "Read my messages", "read me my email", "Read the news", "read this article", "Read it",
    "read my notifications", "read today's schedule", "read the recipe",
])
def test_other_kinds_of_reading_are_not_books(query):
    assert fast_read.reads_something_else(query)
    assert fast_read.parse(query) is None


@pytest.mark.parametrize(("query", "ref"), [
    ("Read John 3:16", "John 3:16"),
    ("read psalm 23", "psalm 23"),
    ("Read Psalm twenty three.", "Psalm 23"),
    ("read me first Corinthians thirteen", "1 Corinthians 13"),
    ("Jarvis, read John chapter 3 verse 16 please", "John 3:16"),
    ("read Romans 8 verses 28 through 30", "Romans 8:28-30"),
    ("Read Psalm one hundred nineteen", "Psalm 119"),
])
def test_scripture_with_a_chapter_is_read_from_the_bible(query, ref):
    ask = fast_read.parse(query)
    assert ask is not None and ask.kind == "scripture"
    assert ask.scripture.text == ref


def test_a_bare_book_name_is_a_book_not_scripture():
    ask = fast_read.parse("Read Job")
    assert (ask.kind, ask.title) == ("book", "Job")


def test_a_chapter_of_a_named_book():
    ask = fast_read.parse("Read chapter 1 of Moon Garden by Ann Writer")
    assert (ask.kind, ask.chapter, ask.title, ask.author) == ("chapter", 1, "Moon Garden", "Ann Writer")
    ask = fast_read.parse("read me the third chapter of Moon Garden")
    assert (ask.kind, ask.chapter, ask.title) == ("chapter", 3, "Moon Garden")


@pytest.mark.parametrize("query", ["continue", "keep going", "Continue please",
                                   "Read the following code and tell me what the second function returns when it is "
                                   "called with an empty list and no other arguments at all", "read this:\nsome text"])
def test_chat_continuations_and_long_prompts_are_not_readings(query):
    assert fast_read.parse(query) is None


def test_a_book_by_title_and_author():
    ask = fast_read.parse("Read Moon Garden by A. Writer")
    assert (ask.kind, ask.title, ask.author) == ("book", "Moon Garden", "A. Writer")
    assert fast_read.parse("Read me a story") is None


@pytest.mark.parametrize(("query", "next_chapter"), [
    ("keep reading", False), ("Continue reading", False), ("read the next part", False),
    ("read on", False), ("read the next chapter", True),
])
def test_continue_phrases(query, next_chapter):
    ask = fast_read.parse(query)
    assert (ask.kind, ask.next_chapter) == ("continue", next_chapter)


class FakeExecution:
    """Execution's /execute/bible and /execute/calibre, answering from fake books."""

    def __init__(self, parts: int = 2, chapters: int = 3):
        self.calls: list[tuple[str, dict]] = []
        self.parts = parts
        self.chapters = chapters

    async def __call__(self, path: str, payload: dict) -> dict:
        self.calls.append((path, payload))
        if path == "/execute/bible":
            return {"status": "SUCCESS",
                    "message": f"{payload['ref']} (TEST), 2 verses:\n\nBook 1:1 First words.\nBook 1:2 Second words.",
                    "detail": {"reference": payload["ref"], "version": "test", "count": 2}}
        if payload["action"] == "search":
            return {"status": "SUCCESS", "detail": {"books": [{"book_id": 42, "title": "Moon Garden",
                                                               "authors": ["A. Writer"]}]}}
        chapter, part = int(payload["chapter"]), int(payload.get("part") or 1)
        if chapter > self.chapters:
            return {"status": "FAILURE", "message": f"There is no chapter {chapter}."}
        return {"status": "SUCCESS",
                "message": f"Moon Garden by A. Writer, chapter {chapter}:\n\nText of {chapter}.{part}.",
                "detail": {"book_id": 42, "title": "Moon Garden", "author": "A. Writer",
                           "chapter": {"number": chapter, "title": f"Chapter {chapter}", "part": part,
                                       "parts": self.parts}}}


@pytest.mark.asyncio
async def test_scripture_is_said_without_each_verses_reference():
    post = FakeExecution()
    said = await fast_read.handle(fast_read.parse("Read John 3:16"), post, {"user": "u"}, "u")
    assert said == "John 3:16, TEST. First words. Second words."
    assert post.calls[0] == ("/execute/bible", {"action": "read", "ref": "John 3:16", "user_context": {"user": "u"}})


@pytest.mark.asyncio
async def test_a_chapter_is_read_in_voice_sized_parts_and_keep_reading_goes_on():
    post = FakeExecution(parts=2)
    said = await fast_read.handle(fast_read.parse("Read chapter 1 of Moon Garden"), post, {"user": "u"}, "u")
    assert said.startswith("Moon Garden by A. Writer, chapter 1. Text of 1.1.")
    assert said.endswith("That's part 1 of 2 of this chapter. Say 'keep reading' for more.")
    assert post.calls[-1][1]["max_chars"] == fast_read.VOICE_PART_CHARS
    said = await fast_read.handle(fast_read.parse("keep reading"), post, {"user": "u"}, "u")
    assert "Text of 1.2." in said and said.startswith("chapter 1, continued.")
    assert post.calls[-1][1]["book_id"] == 42
    said = await fast_read.handle(fast_read.parse("keep reading"), post, {"user": "u"}, "u")
    assert "Text of 2.1." in said
    said = await fast_read.handle(fast_read.parse("read the next chapter"), post, {"user": "u"}, "u")
    assert "Text of 3.1." in said


@pytest.mark.asyncio
async def test_keep_reading_after_the_last_chapter_says_so():
    post = FakeExecution(parts=1, chapters=1)
    await fast_read.handle(fast_read.parse("Read chapter 1 of Moon Garden"), post, {"user": "u"}, "u")
    said = await fast_read.handle(fast_read.parse("keep reading"), post, {"user": "u"}, "u")
    assert said == "That was the last chapter of Moon Garden."


@pytest.mark.asyncio
async def test_keep_reading_with_nothing_read_falls_through():
    assert await fast_read.handle(fast_read.parse("keep reading"), FakeExecution(), {"user": "u"}, "nobody") is None


@pytest.mark.asyncio
async def test_keep_reading_scripture_reads_the_next_chapter():
    post = FakeExecution()
    await fast_read.handle(fast_read.parse("Read Psalm 23"), post, {"user": "u"}, "u")
    await fast_read.handle(fast_read.parse("keep reading"), post, {"user": "u"}, "u")
    assert post.calls[-1][1]["ref"] == "Ps 24"


@pytest.mark.asyncio
async def test_a_book_in_the_library_is_read_from_chapter_one_and_one_not_there_falls_through():
    post = FakeExecution()
    said = await fast_read.handle(fast_read.parse("Read Moon Garden"), post, {"user": "u"}, "u")
    assert "Text of 1.1." in said
    assert post.calls[-1][1]["book_id"] == 42
    assert await fast_read.handle(fast_read.parse("Read Harbor Lights"), post, {"user": "u"}, "u") is None
