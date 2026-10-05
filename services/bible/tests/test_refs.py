"""Reference parsing.

The reader turns taps into references, and Jarvis turns questions into
references, so this parser is the trust boundary for every passage request. A
reference that resolves to the wrong verses is worse than one that refuses.
"""
import pytest

from services.bible.refs import (
    ReferenceError,
    VerseSpan,
    format_reference,
    parse_one,
    parse_reference,
)


def ids(spans):
    return [(s.book, s.chapter_start, s.verse_start, s.verse_end) for s in spans]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("John 3:16", [("John", 3, 16, 16)]),
        ("Jn 3:16", [("John", 3, 16, 16)]),
        ("Jn. 3.16", [("John", 3, 16, 16)]),
        ("1 Cor 13:4-7", [("1Cor", 13, 4, 7)]),
        ("Ps 23:1-3", [("Ps", 23, 1, 3)]),
        ("John 3", [("John", 3, 1, None)]),
        ("John 3:16, 4:22", [("John", 3, 16, 16), ("John", 4, 22, 22)]),
        ("Gen 1:26-28; 3:15", [("Gen", 1, 26, 28), ("Gen", 3, 15, 15)]),
        ("John 3:16-4:2", [("John", 3, 16, 2)]),
        ("John 3:16-4", [("John", 3, 16, None)]),
        ("Acts 2:38,39", [("Acts", 2, 38, 38), ("Acts", 2, 39, 39)]),
        ("Deut 8:2-6, 10, 17-18", [("Deut", 8, 2, 6), ("Deut", 8, 10, 10), ("Deut", 8, 17, 18)]),
        ("John 3–18", [("John", 3, 1, None)]),
        ("Zephaniah 3:17 ESV", [("Zeph", 3, 17, 17)]),
        ("2 Thess 1:1-1:2", [("2Thess", 1, 1, 2)]),
        ("song of songs 2:1", [("Song", 2, 1, 1)]),
        ("John 3, 4", [("John", 3, 1, None), ("John", 4, 1, None)]),
    ],
)
def test_parses_reference_shapes(text, expected):
    assert ids(parse_reference(text)) == expected


def test_bare_book_is_whole_book():
    span = parse_one("John")
    assert span.whole_book is True
    assert span.book == "John"


def test_chapter_span_is_open_ended():
    span = parse_one("Ps 119")
    assert span.verse_start == 1 and span.verse_end is None
    assert span.resolved_chapter_end == 119


def test_bare_number_after_a_verse_inherits_the_chapter():
    assert ids(parse_reference("John 3:16, 17")) == [("John", 3, 16, 16), ("John", 3, 17, 17)]


def test_parse_one_refuses_an_ambiguous_multi_passage_reference():
    with pytest.raises(ReferenceError):
        parse_one("John 3:16, 17")


def test_book_range_is_canonical_order():
    assert ids(parse_reference("Psa-Mal")) == [
        ("Ps", 1, 1, None),
        ("Prov", 1, 1, None),
        ("Eccl", 1, 1, None),
        ("Song", 1, 1, None),
        ("Isa", 1, 1, None),
        ("Jer", 1, 1, None),
        ("Lam", 1, 1, None),
        ("Ezek", 1, 1, None),
        ("Dan", 1, 1, None),
        ("Hos", 1, 1, None),
        ("Joel", 1, 1, None),
        ("Amos", 1, 1, None),
        ("Obad", 1, 1, None),
        ("Jonah", 1, 1, None),
        ("Mic", 1, 1, None),
        ("Nah", 1, 1, None),
        ("Hab", 1, 1, None),
        ("Zeph", 1, 1, None),
        ("Hag", 1, 1, None),
        ("Zech", 1, 1, None),
        ("Mal", 1, 1, None),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "3:16",
        "Foo 1:1",
        "John 4:2-1",
        "1:1:1",
        "John 1-2-3",
        "John 999:1",
        "Ps 151:1",
        "Mal-Mal",
    ],
)
def test_rejects_nonsense_instead_of_guessing(text):
    with pytest.raises(ReferenceError):
        parse_reference(text)


def test_rejects_reversed_book_range():
    with pytest.raises(ReferenceError):
        parse_reference("Mal-Mal")


def test_display_round_trips_through_the_parser():
    for text in ("John 3:16", "Ps 119", "John 3:16-4:2", "Gen 1:26-28; 3:15"):
        spans = parse_reference(text)
        assert format_reference(spans) == format_reference(parse_reference(format_reference(spans)))


def test_span_display_is_human_readable():
    assert parse_one("John 3:16").display() == "John 3:16"
    assert parse_one("Ps 119").display() == "Psalms 119"
    assert parse_one("John 3:16-4:2").display() == "John 3:16-4:2"
    assert parse_one("John").display() == "John"


def test_none_is_not_a_reference():
    with pytest.raises(ReferenceError):
        parse_reference(None)


def test_verse_span_is_hashable_and_frozen():
    span = VerseSpan(book="John", chapter_start=3, verse_start=16, verse_end=16)
    assert {span} == {VerseSpan(book="John", chapter_start=3, verse_start=16, verse_end=16)}