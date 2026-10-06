"""Verse of the Day.

Two devices render this card and neither may disagree with the other, and the
whole thing has to survive an empty corpus -- so the tests are mostly about
determinism and about refusing to invent a verse.
"""
from datetime import date

import pytest

from services.bible import books as book_table
from services.bible import corpus, votd


def testament(osis: str) -> str:
    return "ot" if book_table.BOOK_BY_OSIS[osis]["order"] <= 39 else "nt"


def test_picks_a_verse(loaded):
    verse = votd.pick(loaded, day=date(2026, 9, 1))
    chapter = corpus.fetch_chapter(loaded, "kjv", verse["osis"], verse["chapter"])
    assert verse["text"] in [row["text"] for row in chapter]
    assert verse["reference"] == f"{verse['book_name']} {verse['chapter']}:{verse['verse']}"
    assert verse["version"] == "kjv"
    assert verse["day"] == "2026-09-01"


def key(verse: dict) -> tuple[str, int, int]:
    return (verse["osis"], verse["chapter"], verse["verse"])


def test_the_same_day_always_gives_the_same_verse(loaded):
    day = date(2026, 9, 1)
    assert key(votd.pick(loaded, day=day)) == key(votd.pick(loaded, day=day))


def test_the_draw_is_stable_across_sessions(loaded, corpus_file):
    """Two readers on two devices must see the same verse without coordinating."""
    first = key(votd.pick(loaded, day=date(2026, 12, 25)))
    fresh = corpus.import_corpus(loaded, code="kjv", name="King James Version", source_path=corpus_file)
    assert fresh["verses"] > 0
    assert key(votd.pick(loaded, day=date(2026, 12, 25))) == first


def test_different_days_can_differ(loaded):
    picks = {
        (v["osis"], v["chapter"], v["verse"])
        for v in (votd.pick(loaded, day=date(2026, 9, d)) for d in range(1, 29))
    }
    assert len(picks) > 1


def test_a_seed_rotates_the_draw_without_changing_the_date(loaded):
    day = date(2026, 9, 1)
    unseeded = key(votd.pick(loaded, day=day))
    seeded = key(votd.pick(loaded, day=day, seed="rotate"))
    assert unseeded != seeded


def test_new_testament_scope_only_returns_new_testament_books(loaded):
    for d in range(1, 29):
        assert testament(votd.pick(loaded, day=date(2026, 9, d), scope="nt")["osis"]) == "nt"


def test_old_testament_scope_excludes_the_new(loaded):
    for d in range(1, 29):
        assert testament(votd.pick(loaded, day=date(2026, 9, d), scope="ot")["osis"]) == "ot"


def test_an_unknown_scope_is_rejected(loaded):
    with pytest.raises(ValueError):
        votd.pick(loaded, scope="apocrypha")


def test_an_empty_corpus_explains_how_to_fix_itself(session):
    with pytest.raises(LookupError) as exc:
        votd.pick(session)
    message = str(exc.value)
    assert "Admin > Bible" in message
    assert "import_corpus" not in message


def test_an_unimported_version_is_refused_not_swapped(loaded):
    with pytest.raises(LookupError) as exc:
        votd.pick(loaded, version="esv")
    message = str(exc.value)
    assert "esv" in message and "kjv" in message


def test_a_known_version_is_honoured(loaded, corpus_file):
    corpus.import_corpus(loaded, code="asv", name="American Standard Version", source_path=corpus_file)
    assert votd.pick(loaded, version="asv")["version"] == "asv"


def test_verse_number_is_inside_the_book(loaded):
    verse = votd.pick(loaded, day=date(2026, 9, 5))
    chapter = corpus.fetch_chapter(loaded, "kjv", verse["osis"], verse["chapter"])
    assert verse["verse"] <= len(chapter)