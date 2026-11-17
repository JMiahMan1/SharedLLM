"""The verse search: index, query language, ranking and filters.

The corpus here is tiny and invented (with a few public-domain KJV lines) so
each assertion can name exactly which verses must and must not come back.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from services.bible import corpus
from services.bible import search as verse_search
from services.bible.books import BOOKS
from services.bible.models import ConcordanceLine, VerseMark

#: (osis, chapter, verse) -> text; everything else is "Placeholder."
KJV = {
    ("Gen", 1, 1): "In the beginning God created the heaven and the earth.",
    ("Gen", 2, 1): "Thus the heavens and the earth were finished.",
    ("Exod", 1, 1): "These are the names of the children of Israel.",
    ("Ps", 23, 1): "The LORD is my shepherd; I shall not want.",
    ("Jer", 2, 1): "They have forsaken me the fountain of living waters.",
    ("John", 3, 1): "For God so loved the world, that he gave his only begotten Son.",
    ("John", 4, 1): "He would have given thee living water.",
    ("John", 5, 1): "The Father loveth the Son, and sheweth him all things.",
    ("Rom", 1, 1): "For by grace are ye saved through faith; and that not of works.",
    ("Rom", 2, 1): "Faith without works is dead, and the law is holy.",
    ("Gal", 1, 1): "Grace be to you and peace from God the Father.",
    ("Jas", 1, 1): "Show me thy faith without thy works, and I will show thee my faith by my works.",
    ("Rev", 1, 1): "I will give unto him that is athirst of the fountain of the water of life.",
}
#: A second translation with different words in the same places.
WEB = {**KJV, ("John", 3, 1): "For God so loved the world, that he gave his one and only Son.",
       ("John", 5, 1): "The Father has affection for the Son."}


def _source(texts: dict) -> list[dict]:
    out = []
    for entry in BOOKS:
        chapters = []
        for c in range(1, entry["chapters"] + 1):
            chapters.append([texts.get((entry["osis"], c, 1), "Placeholder.")])
        out.append({"abbrev": entry["osis"], "name": entry["name"], "chapters": chapters})
    return out


def _install(session: Session, tmp_path: Path, code: str, texts: dict) -> None:
    path = tmp_path / f"{code}.json"
    path.write_text(json.dumps(_source(texts)), encoding="utf-8")
    corpus.import_corpus(session, code=code, name=code.upper(), source_path=path)


@pytest.fixture
def bible(loaded_client: TestClient, reader_db: Session, tmp_path: Path) -> TestClient:
    _install(reader_db, tmp_path, "kjv", KJV)
    _install(reader_db, tmp_path, "web", WEB)
    return loaded_client


def _search(client: TestClient, q: str, **params) -> dict:
    resp = client.get("/search", params={"q": q, "version": "kjv", **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _refs(body: dict) -> list[str]:
    return [r["reference"] for r in body["results"]]


def test_a_word_finds_its_other_forms(bible):
    refs = _refs(_search(bible, "love"))
    assert "John 3:1" in refs  # loved
    assert "John 5:1" in refs  # loveth


def test_a_quoted_phrase_means_those_words_as_written(bible):
    assert _refs(_search(bible, '"living water"')) == ["John 4:1"]
    loose = _refs(_search(bible, "living water"))
    assert {"John 4:1", "Jer 2:1".replace("Jer", "Jeremiah")} <= set(loose)


def test_exact_match_mode_treats_the_words_as_one_phrase(bible):
    assert _refs(_search(bible, "living water", match="exact")) == ["John 4:1"]


def test_or_any_and_exclusions(bible):
    assert set(_refs(_search(bible, "shepherd OR beginning"))) == {"Psalms 23:1", "Genesis 1:1"}
    assert set(_refs(_search(bible, "shepherd beginning", match="any"))) == {"Psalms 23:1", "Genesis 1:1"}
    with_law = set(_refs(_search(bible, "faith works")))
    without = set(_refs(_search(bible, "faith works -law")))
    assert "Romans 2:1" in with_law and "Romans 2:1" not in without


def test_prefix_and_near(bible):
    assert "Genesis 2:1" in _refs(_search(bible, "heav*"))
    near = _refs(_search(bible, "grace NEAR/3 saved"))
    assert near == ["Romans 1:1"]
    assert _refs(_search(bible, "grace NEAR/1 works")) == []


def test_scopes_books_ranges_and_sections(bible):
    assert _refs(_search(bible, "faith in:Jas")) == ["James 1:1"]
    assert set(_refs(_search(bible, "grace in:Rom-Gal"))) == {"Romans 1:1", "Galatians 1:1"}
    assert set(_refs(_search(bible, "grace in:pauline"))) == {"Romans 1:1", "Galatians 1:1"}
    assert _refs(_search(bible, "earth in:nt")) == []
    assert set(_refs(_search(bible, "fountain in:ot"))) == {"Jeremiah 2:1"}


def test_an_unknown_scope_or_translation_is_a_clear_400(bible):
    resp = bible.get("/search", params={"q": "grace in:Hezekiah"})
    assert resp.status_code == 400 and "Hezekiah" in resp.json()["detail"]
    resp = bible.get("/search", params={"q": "grace ver:xyz"})
    assert resp.status_code == 400 and "installed" in resp.json()["detail"]


def test_only_excluded_words_is_a_400_not_the_whole_bible(bible):
    resp = bible.get("/search", params={"q": "-law"})
    assert resp.status_code == 400


def test_bible_order_is_canonical_not_alphabetical(bible):
    body = _search(bible, "earth OR names OR shepherd", sort="canon")
    assert _refs(body) == ["Genesis 1:1", "Genesis 2:1", "Exodus 1:1", "Psalms 23:1"]


def test_results_say_where_the_words_matched(bible):
    hit = _search(bible, "shepherd")["results"][0]
    start, end = hit["highlights"][0]
    assert hit["text"][start:end] == "shepherd"
    assert "\x02" not in hit["text"]


def test_counts_by_book_and_paging(bible):
    body = _search(bible, "faith", limit=1)
    assert body["total"] == 3 and body["count"] == 1
    assert {b["osis"]: b["count"] for b in body["by_book"]} == {"Rom": 2, "Jas": 1}
    second = _search(bible, "faith", limit=1, offset=1)
    assert second["results"][0]["reference"] != body["results"][0]["reference"]


def test_every_translation_at_once_lists_each_verse_once(bible):
    body = _search(bible, "begotten OR affection ver:all")
    found = {r["reference"]: r["versions"] for r in body["results"]}
    assert found == {"John 3:1": ["kjv"], "John 5:1": ["web"]}
    both = _search(bible, "shepherd ver:all")["results"]
    assert len(both) == 1 and both[0]["versions"] == ["kjv", "web"]


def test_a_reimport_updates_the_index(bible, reader_db, tmp_path):
    _install(reader_db, tmp_path, "kjv", {**KJV, ("Ps", 23, 1): "The LORD is my keeper."})
    assert _refs(_search(bible, "shepherd")) == []
    assert _refs(_search(bible, "keeper")) == ["Psalms 23:1"]


def test_strongs_numbers_search_the_original_word(bible, reader_db):
    reader_db.add(ConcordanceLine(strongs="G26", osis="John", chapter=3, verse=1, word="loved"))
    reader_db.add(ConcordanceLine(strongs="G26", osis="Rom", chapter=1, verse=1, word="grace"))
    reader_db.commit()
    body = _search(bible, "G26")
    assert _refs(body) == ["John 3:1", "Romans 1:1"]
    first = body["results"][0]
    assert [first["text"][s:e] for s, e in first["highlights"]] == ["loved"]
    assert _refs(_search(bible, "G26 faith")) == ["Romans 1:1"]
    assert _refs(_search(bible, "strongs:G26 in:Rom")) == ["Romans 1:1"]


def test_has_searches_only_the_callers_own_marks(bible, reader_db):
    reader_db.add(VerseMark(username="sam", osis="Rom", chapter=1, verse_start=1, kind="highlight"))
    reader_db.add(VerseMark(username="alex", osis="Jas", chapter=1, verse_start=1, kind="highlight"))
    reader_db.commit()
    assert _refs(_search(bible, "faith has:highlight", username="sam")) == ["Romans 1:1"]
    assert _refs(_search(bible, "has:highlight", username="alex")) == ["James 1:1"]
    assert bible.get("/search", params={"q": "has:highlight"}).status_code == 400


@pytest.mark.parametrize("q", ['"unclosed', "faith (", "NEAR", "OR faith", "a:b", "faith OR", '""" in:John', "G26x"])
def test_odd_input_never_breaks_the_query(bible, q):
    resp = bible.get("/search", params={"q": q})
    assert resp.status_code in (200, 400), resp.text


def test_a_database_without_the_index_still_answers(bible, monkeypatch):
    monkeypatch.setattr(verse_search, "ensure", lambda session: False)
    body = _search(bible, "shepherd")
    assert body["index"] == "like" and _refs(body) == ["Psalms 23:1"]


def test_the_index_is_rebuilt_when_it_does_not_match_the_verses(engine, session, corpus_file):
    corpus.import_corpus(session, code="kjv", name="KJV", source_path=corpus_file)
    stale = verse_search.prepare(engine)
    assert set(stale) == {verse_search.STEMMED, verse_search.EXACT}
    verse_search.rebuild(engine, stale)
    assert verse_search.prepare(engine) == []


@pytest.mark.parametrize(
    ("typed", "older"),
    [("love", "loveth"), ("believes", "believeth"), ("give", "givest"), ("say", "saith"), ("have", "hath"),
     ("cries", "crieth"), ("walking", "walketh")],
)
def test_modern_words_find_the_older_verb_forms(typed, older):
    assert older in verse_search.archaic_forms(typed)


def test_parser_reads_the_whole_language():
    parsed = verse_search.parse('grace "living water" faith OR works -law rejoic* in:John ver:all has:note H2617')
    described = parsed.describe()
    assert described["all"] == ["grace", '"living water"', "faith or works", "rejoic*"]
    assert described["without"] == ["law"] and described["scopes"] == ["John"]
    assert described["versions"] == "all" and described["strongs"] == ["H2617"] and described["has"] == ["note"]


def test_word_searches_are_driven_by_the_index_not_a_walk_of_the_translation(bible, engine):
    """With one translation, SQLite preferred bibleverse's key and probed the
    full-text index once per verse: 1.5 s instead of 1 ms on the live Bible."""
    from sqlalchemy import event

    seen: list[tuple[str, object]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "MATCH" in statement and statement.lstrip().startswith(("WITH", "SELECT")):
            seen.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        _search(bible, "faith works in:Jas")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert seen
    with engine.connect() as conn:
        for statement, parameters in seen:
            plan = [row[3] for row in conn.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)]
            fts = next(i for i, step in enumerate(plan) if "verse_fts VIRTUAL TABLE" in step)
            walks = [i for i, step in enumerate(plan) if "sqlite_autoindex_bibleverse" in step]
            assert not walks or fts < walks[0], plan
