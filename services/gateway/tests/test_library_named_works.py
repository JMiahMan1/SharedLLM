"""Library pre-fetch: the similarity floor on every path, and a named book that is not there.

Live, 2026-11: asked for notes on "Wild at Heart" by John Eldredge, every answer was
about John R. Macduff's "Memories of Bethany", because the library's nearest
passages went into the prompt under "Answer from these passages". Now a library hit
must clear the floor on its ``similarity`` (never its rank-fusion ``score``), and a
question that names a book or author only gets passages from it, or a note that it
is not in the indexed library.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.gateway import library_names, orchestrator, prompt_budget
from services.gateway.config import RAG_PREFETCH_MIN_SIMILARITY

FLOOR = RAG_PREFETCH_MIN_SIMILARITY


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from services.common.circuit_breaker import reset_breakers

    reset_breakers()
    prompt_budget.reset()
    monkeypatch.setattr(orchestrator, "get_all_settings", AsyncMock(return_value={"rag_svc_url": "http://rag"}))
    yield
    reset_breakers()


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self):
        return self._payload


class _Library:
    """A fake /rag/search: the question's hits, and per-name lookups."""

    def __init__(self, hits, lookups=None):
        self.hits = hits
        self.lookups = lookups or {}
        self.searches: list[dict] = []

    async def post(self, url, json=None, **kwargs):
        if url.endswith("/rag/search"):
            self.searches.append(json)
            if json["collection_name"] != "calibre_files":
                return _Resp({"results": []})
            if len(self.searches) == 1:
                return _Resp({"results": list(self.hits)})
            return _Resp({"results": list(self.lookups.get(json["query"], []))})
        return _Resp({})

    async def get(self, url, **kwargs):
        return _Resp({})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _passage(text, title, author, similarity=0.7, score=0.016, **extra):
    return {"content": text, "score": score, "similarity": similarity,
            "metadata": {"title": title, "author": author, "chapter": "IV", **extra}}


MACDUFF = _passage("Bethany was the home of Martha and Mary.", "Memories of Bethany", "John R. Macduff")
ELDREDGE = _passage("Every man wants a battle to fight.", "Wild at Heart", "John Eldredge")


async def _fetch(session, query):
    with patch("services.gateway.main.shared_http_client", return_value=session):
        return await orchestrator._fetch_rag_context(query, "u", None, include_curriculum=False,
                                                     include_library=True)


# --- the floor ---------------------------------------------------------------------

async def test_a_library_hit_under_the_floor_is_dropped_whatever_its_score():
    low = _passage("An unrelated meditation.", "Memories of Bethany", "John R. Macduff",
                   similarity=FLOOR - 0.1, score=0.9)
    context = await _fetch(_Library([low]), "what does the library say about courage")
    assert "unrelated meditation" not in context


async def test_a_hit_at_or_above_the_floor_is_kept():
    context = await _fetch(_Library([MACDUFF]), "what does the library say about Bethany")
    assert "Martha and Mary" in context
    assert "Memories of Bethany by John R. Macduff" in context


async def test_a_keyword_phrase_match_is_kept_as_rag_kept_it():
    phrase = dict(_passage("Bethany, the home of friends.", "Memories of Bethany", "John R. Macduff",
                           similarity=0.1), phrase_match=True)
    context = await _fetch(_Library([phrase]), "what does the library say about Bethany")
    assert "home of friends" in context


def test_the_floor_compares_similarity_not_the_rank_fusion_score():
    hits = [{"content": "a", "score": 0.016, "similarity": 0.6}, {"content": "b", "score": 0.9, "similarity": 0.2}]
    assert [h["content"] for h in orchestrator._above_floor(hits, 0.53, "calibre_files")] == ["a"]
    assert orchestrator._above_floor(hits, None, "calibre_files") == hits


async def test_the_named_work_lookup_obeys_the_floor_too():
    low = _passage("Wild at Heart, a weak echo.", "Wild at Heart", "John Eldredge", similarity=FLOOR - 0.2)
    session = _Library([MACDUFF], lookups={"Wild at Heart": [low], "John Eldredge": [low]})
    context = await _fetch(session, "Give me notes on Wild at Heart by John Eldredge, chapter 4")
    assert "weak echo" not in context
    assert all(s.get("min_similarity") == FLOOR for s in session.searches)


# --- a named book ---------------------------------------------------------------------

async def test_a_named_book_not_in_the_library_gets_a_note_and_no_other_books_passages():
    session = _Library([MACDUFF])
    context = await _fetch(session, "Give me notes on Wild at Heart by John Eldredge, chapter 4")
    assert "Martha and Mary" not in context
    assert "Macduff" not in context
    assert "not found in the library catalogue search" in context
    assert '"Wild at Heart"' in context
    assert orchestrator.LIBRARY_NOT_INDEXED_HEADER in context
    assert "Answer from these passages" not in context


async def test_a_named_book_in_the_library_gets_its_own_passages_only():
    context = await _fetch(_Library([MACDUFF, ELDREDGE]), "Give me notes on Wild at Heart by John Eldredge, chapter 4")
    assert "battle to fight" in context
    assert "Martha and Mary" not in context
    assert "not found in the library catalogue search" not in context


async def test_a_named_book_outranked_by_others_is_found_by_its_name():
    session = _Library([MACDUFF], lookups={"Wild at Heart": [ELDREDGE]})
    context = await _fetch(session, "Give me notes on Wild at Heart by John Eldredge, chapter 4")
    assert "battle to fight" in context
    assert "Martha and Mary" not in context


async def test_a_misheard_author_still_finds_the_book():
    context = await _fetch(_Library([MACDUFF, ELDREDGE]), "notes on wild at heart by john eldridge chapter 4")
    assert "battle to fight" in context
    assert "Martha and Mary" not in context


async def test_a_question_that_names_no_book_is_answered_from_any_book():
    context = await _fetch(_Library([MACDUFF]), "what does the library say about Bethany and friendship")
    assert "Martha and Mary" in context


# --- the name reader ------------------------------------------------------------------

@pytest.mark.parametrize("question, names", [
    ("Give me notes on Wild at Heart by John Eldredge, chapter 4.", ["Wild at Heart", "John Eldredge"]),
    ("notes on wild at heart by john eldridge chapter 4", ["wild at heart", "john eldridge"]),
    ('summarize chapter 2 of "The Faithful Promiser"', ["The Faithful Promiser"]),
    ("any books by Macduff?", ["Macduff"]),
    ("What does the Holy Spirit do?", []),
    ("What does the commentary in our library say about the prodigal son?", []),
    ("what is a sermon about grace", []),
])
def test_names_are_read_from_the_question_not_from_a_list(question, names):
    assert library_names.named_works(question) == names


@pytest.mark.parametrize("name, target, expected", [
    ("John Eldridge", "John Eldredge", True),
    ("ELDREDGE", "John Eldredge", True),
    ("Wild at Heart!", "Wild at Heart", True),
    ("Macduff", "John R. Macduff", True),
    ("John Eldredge", "John R. Macduff", False),
    ("Wild at Heart", "Memories of Bethany", False),
])
def test_matching_ignores_case_punctuation_and_small_misspellings(name, target, expected):
    assert library_names.name_matches(name, target) is expected


# --- a named book found by its catalogue card ----------------------------------------
#: The library index holds each book's catalogue card, never its text (2026-11-24).

def _card(title, author, similarity=0.7):
    return {"content": f"Book: {title}\nAuthor: {author}\nDescription: A test description.\nContents: One; Two",
            "score": 0.016, "similarity": similarity,
            "metadata": {"title": title, "author": author, "is_metadata": True, "catalogue": True}}


async def test_a_named_book_found_by_its_card_gets_the_card_and_how_to_read_it():
    card = _card("Wild at Heart", "John Eldredge")
    session = _Library([MACDUFF], lookups={"Wild at Heart": [card]})
    context = await _fetch(session, 'Give me three discussion questions on "Wild at Heart"')
    assert "A test description." in context
    assert orchestrator.LIBRARY_CATALOGUE_HEADER in context
    assert '"Wild at Heart by John Eldredge": in the library' in context
    assert "action fetch_text" in context
    assert "not found in the library catalogue search" not in context
    assert "Martha and Mary" not in context


async def test_a_card_among_the_questions_own_hits_counts_as_the_book():
    context = await _fetch(_Library([_card("Wild at Heart", "John Eldredge")]),
                           'Give me three discussion questions on "Wild at Heart"')
    assert orchestrator.LIBRARY_CATALOGUE_HEADER in context


async def test_a_passage_of_the_book_needs_no_read_it_note():
    context = await _fetch(_Library([ELDREDGE, _card("Wild at Heart", "John Eldredge")]),
                           "Give me notes on Wild at Heart by John Eldredge")
    assert "battle to fight" in context
    assert orchestrator.LIBRARY_CATALOGUE_HEADER not in context


async def test_a_named_chapter_being_read_needs_no_read_it_note():
    import asyncio

    read = asyncio.get_running_loop().create_future()
    read.set_result(orchestrator.ChapterPrefetch(True, "chapter text"))
    with patch("services.gateway.main.shared_http_client",
               return_value=_Library([_card("Wild at Heart", "John Eldredge")])):
        context = await orchestrator._fetch_rag_context(
            "Summarize chapter 4 of Wild at Heart by John Eldredge", "u", None,
            include_curriculum=False, include_library=True, chapter_read=read,
        )
    assert orchestrator.LIBRARY_CATALOGUE_HEADER not in context


def test_the_catalogue_note_says_the_card_is_not_the_text():
    note = library_names.catalogue_only_note(["Quiet Harbor Lights by Ann Fakewright"])
    assert "the card is not the book's text" in note
    assert "action chapters" in note and "action fetch_text" in note
