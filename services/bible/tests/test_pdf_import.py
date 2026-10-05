import sys

import pytest

pytest.importorskip("pymupdf", reason="PDF import needs PyMuPDF")

from services.bible.books import BOOKS  # noqa: E402
from services.bible.pdf_import import (  # noqa: E402
    PdfImportError,
    _CHAPTER_WORD,
    _PAGE_NUMBER,
    _match_book_heading,
    _normalise,
    extract,
    extract_lines,
    parse,
    to_source,
)

PAGE_WIDTH = 432
PAGE_BOTTOM = 560
MARGIN = 54
BODY_SIZE = 11
CHARS_PER_LINE = int((PAGE_WIDTH - 2 * MARGIN) / (BODY_SIZE * 0.5))


def _wrap(text):
    words, line, out = text.split(), "", []
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) > CHARS_PER_LINE and line:
            out.append(line)
            line = word
        else:
            line = candidate
    if line:
        out.append(line)
    return out


def _build_pdf(path, script):
    """Render a page-per-block script into a real PDF, so the extractor is
    exercised against genuine PyMuPDF output rather than a hand-written dict."""
    import pymupdf

    document = pymupdf.open()
    page = None
    y = 60

    def new_page():
        nonlocal page, y
        page = document.new_page(width=PAGE_WIDTH, height=648)
        y = 60

    for kind, payload, *rest in script:
        if kind == "page":
            new_page()
            continue
        size = rest[0] if rest else BODY_SIZE
        chunks = _wrap(payload) if size == BODY_SIZE else [payload]
        for chunk in chunks:
            if y > PAGE_BOTTOM:
                new_page()
            page.insert_text((MARGIN, y), chunk, fontsize=size, fontname="helv")
            y += size * 1.45
    document.save(path)
    document.close()
    return path


def _verse(number, text):
    return f"{number} {text}"


def _plain(verses):
    """The same chapter list with the leading verse numbers dropped, which is
    what the extractor stores."""
    return [[v.split(" ", 1)[1] for v in chapter] for chapter in verses]


GENESIS_CHAPTERS = [
    [
        _verse(1, "In the beginning God created the heaven and the earth."),
        _verse(2, "And the earth was without form, and void."),
    ],
    [
        _verse(1, "Thus the evening and the morning were the second day."),
        _verse(2, "And God said, Let the earth bring forth grass."),
    ],
    [
        _verse(1, "And God saw that it was good, and there was evening."),
        _verse(2, "And the LORD God planted a garden eastward in Eden."),
    ],
]

JOHN_CHAPTERS = [
    [
        _verse(1, "In the beginning was the Word, and the Word was with God."),
        _verse(2, "He was in the beginning with God."),
    ],
    [
        _verse(1, "The wedding took place in Cana of Galilee."),
        _verse(2, "There were six stone water jars for purification."),
    ],
]


def genesis_script():
    script = [("page", None), ("line", "THE HOLY BIBLE", 8), ("line", "Page 1", 8),
              ("line", "Genesis", 16), ("line", "Chapter 1", 13)]
    script += [("line", verse) for verse in GENESIS_CHAPTERS[0]]
    script += [("line", "Chapter 2", 13)]
    script += [("line", verse) for verse in GENESIS_CHAPTERS[1]]
    script += [("page", None), ("line", "Genesis 2", 8), ("line", "3", 8),
               ("line", "Chapter 3", 13)]
    script += [("line", verse) for verse in GENESIS_CHAPTERS[2]]
    script += [("page", None), ("line", "John 1", 16)]
    script += [("line", verse) for verse in JOHN_CHAPTERS[0]]
    script += [("page", None), ("line", "John", 16), ("line", "Chapter 2", 13)]
    script += [("line", verse) for verse in JOHN_CHAPTERS[1]]
    return script


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    directory = tmp_path_factory.mktemp("pdf")
    return _build_pdf(directory / "sample_bible.pdf", genesis_script())


@pytest.fixture(scope="module")
def extracted(sample_pdf):
    return extract(sample_pdf)


def test_extracts_every_verse_text(extracted):
    assert extracted["books"]["Gen"] == _plain(GENESIS_CHAPTERS)
    assert extracted["books"]["John"] == _plain(JOHN_CHAPTERS)


def test_running_heads_and_page_numbers_are_dropped(extracted):
    body = " ".join(verse for c in extracted["books"]["Gen"] for verse in c)
    assert "THE HOLY BIBLE" not in body
    assert "Page 1" not in body


def test_single_column_page_is_not_mistaken_for_two(extracted):
    assert extracted["columns"] == 1


def test_short_pdf_is_reported_as_incomplete(extracted):
    genesis = next(b for b in extracted["reports"] if b["osis"] == "Gen")
    assert genesis["chapters"] == 3
    assert genesis["expected_chapters"] == 50
    assert genesis["ok"] is False
    assert "found 3 chapters, the Bible has 50" in genesis["problems"]
    assert extracted["complete"] is False


def test_incomplete_book_is_rejected_in_strict_mode(sample_pdf):
    with pytest.raises(PdfImportError, match="Genesis: 3 chapters extracted"):
        extract(sample_pdf, strict=True)


def test_to_source_refuses_a_partial_bible(extracted):
    with pytest.raises(PdfImportError, match="Exodus is missing"):
        to_source(extracted)


def test_missing_file_names_itself(tmp_path):
    with pytest.raises(PdfImportError, match="PDF not found"):
        extract(tmp_path / "absent.pdf")


def test_empty_stream_reports_no_book_headings():
    parsed = parse(["just some prose", "and more prose"])
    assert parsed["books"] == {}


def test_two_column_page_is_detected(tmp_path):
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    for index in range(24):
        x = 54 if index % 2 == 0 else 320
        page.insert_text((x, 60 + (index // 2) * 16), f"{index + 1} And verse {index + 1}.", fontsize=11)
    document.save(tmp_path / "two_col.pdf")
    document.close()

    from services.bible.pdf_import import body_size, column_count

    lines, _ = extract_lines(tmp_path / "two_col.pdf")
    assert column_count(lines, body_size(lines)) == 2


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Genesis", ("Gen", None)),
        ("genesis", ("Gen", None)),
        ("Gen", ("Gen", None)),
        ("1 Corinthians", ("1Cor", None)),
        ("John 1", ("John", 1)),
        ("Psalms 119", ("Ps", 119)),
        ("Song of Songs", ("Song", None)),
        ("1pe. 1.3", None),
        ("Deuteronomy", ("Deut", None)),
    ],
)
def test_book_heading_matching(line, expected):
    assert _match_book_heading(line) == expected


def test_normalise_strips_punctuation():
    assert _normalise("1 Cor. 15:58") == "1cor1558"


@pytest.mark.parametrize("line", ["Page 12", "- 12 -", "• 12"])
def test_page_number_pattern_matches_furniture(line):
    assert _PAGE_NUMBER.match(line)


@pytest.mark.parametrize("line", ["Chapter 3", "chapter 12", "CHAPTER 7"])
def test_chapter_marker_matches(line):
    assert _CHAPTER_WORD.match(line)


def test_bare_verse_is_not_a_chapter_marker():
    assert _CHAPTER_WORD.match("3 In the beginning was the Word") is None


def test_a_stray_number_in_body_text_is_not_a_chapter():
    """A cross-reference or footnote marker must not open a chapter.

    Only the verse we are expecting counts, so a number out of sequence is
    treated as continuation text rather than silently starting chapter 35.
    """
    parsed = parse(
        [
            "Ruth",
            "Chapter 1",
            "1 In the days when the judges ruled.",
            "See also 2 Samuel 7:12 for the later reference.",
            "2 And a certain man of Bethlehem",
            "came into the country of Moab.",
        ]
    )
    assert parsed["books"]["Ruth"] == [
        [
            "In the days when the judges ruled. "
            "See also 2 Samuel 7:12 for the later reference.",
            "And a certain man of Bethlehem came into the country of Moab.",
        ]
    ]
    assert len(parsed["books"]["Ruth"]) == 1


def test_repeated_book_heading_mid_book_does_not_open_a_chapter():
    parsed = parse(
        [
            "John",
            "Chapter 1",
            "1 In the beginning was the Word.",
            "2 He was in the beginning with God.",
            "John",
            "Chapter 2",
            "1 The wedding took place in Cana.",
        ]
    )
    assert parsed["books"]["John"] == [
        ["In the beginning was the Word.", "He was in the beginning with God."],
        ["The wedding took place in Cana."],
    ]


def test_chapter_marker_beyond_the_bible_is_reported():
    parsed = parse(
        [
            "Obadiah",
            "Chapter 1",
            "1 Thus saith the LORD.",
            "Chapter 9",
            "2 And it shall be in that day.",
        ]
    )
    report = parsed["reports"]["Obad"]
    assert report.chapters == 1
    assert any(
        "claims chapter 9 but we are already in chapter 1" in problem
        for problem in report.problems
    )


def test_continuation_lines_join_the_verse_in_progress():
    parsed = parse(
        [
            "Psalms",
            "Chapter 1",
            "1 The LORD is my shepherd;",
            "I shall not want.",
            "Chapter 2",
            "2 He maketh me to lie down",
            "in green pastures.",
        ]
    )
    assert parsed["books"]["Ps"] == [
        ["The LORD is my shepherd; I shall not want."],
        ["He maketh me to lie down in green pastures."],
    ]
    assert any("opens at verse 2" in problem for problem in parsed["reports"]["Ps"].problems)



def test_to_source_needs_every_canonical_book(extracted):
    filled = {
        book["osis"]: [[f"{i} placeholder {i}" for i in range(1, 4)] for _ in range(book["chapters"])]
        for book in BOOKS
    }
    payload = to_source({"books": filled})
    assert len(payload) == len(BOOKS)
    assert payload[0]["abbrev"] == "Gen"
    assert len(payload[0]["chapters"]) == 50
    assert payload[-1]["abbrev"] == "Rev"


def test_report_flag_on_import_cli_refuses_partial_books(sample_pdf, capsys):
    from services.bible import import_pdf

    assert import_pdf.main(["--source", str(sample_pdf), "--report"]) == 0
    out = capsys.readouterr().out
    assert "Genesis" in out
    assert "FAIL" in out
    assert "Not imported" in out


def test_import_cli_writes_nothing_without_import_flag(sample_pdf, capsys):
    from services.bible import import_pdf

    assert import_pdf.main(["--source", str(sample_pdf)]) == 2
    assert "Nothing was written" in capsys.readouterr().err


def test_import_cli_refuses_incomplete_book(sample_pdf, tmp_path, capsys):
    from services.bible import import_pdf

    staged = tmp_path / "out.json"
    code = import_pdf.main(
        ["--source", str(sample_pdf), "--import", "--stage", str(staged),
         "--database-url", f"sqlite:///{tmp_path / 'bible.db'}"]
    )
    assert code == 1
    assert "did not extract a full chapter count" in capsys.readouterr().err
    assert not staged.exists()


def test_import_cli_without_a_database_url_says_so(sample_pdf, monkeypatch, capsys, tmp_path):
    from services.bible import import_pdf

    monkeypatch.setattr("services.config.BIBLE_DATABASE_URL", "")
    staged = tmp_path / "out.json"
    assert import_pdf.main(
        ["--source", str(sample_pdf), "--import", "--stage", str(staged)]
    ) == 1
    assert "BIBLE_DATABASE_URL is not set" in capsys.readouterr().err


@pytest.fixture(scope="module")
def complete_pdf(tmp_path_factory):
    """Every canonical book at its real chapter count, so the import can pass."""
    script = [("page", None)]
    for book in BOOKS:
        script.append(("line", book["name"], 16))
        for chapter in range(1, book["chapters"] + 1):
            script.append(("line", f"Chapter {chapter}", 13))
            script.append(("line", _verse(1, f"First verse of {book['name']} {chapter}.")))
            script.append(("line", _verse(2, f"Second verse of {book['name']} {chapter}.")))
    directory = tmp_path_factory.mktemp("pdf-complete")
    return _build_pdf(directory / "complete_bible.pdf", script)


def test_a_complete_pdf_installs_and_the_reader_can_fetch_from_it(complete_pdf, tmp_path, capsys):
    from sqlmodel import SQLModel, Session, create_engine

    from services.bible import import_pdf
    from services.bible.corpus import fetch_passage, list_versions
    from services.bible.refs import parse_one

    database = tmp_path / "bible.db"
    database_url = f"sqlite:///{database}"
    create_engine(database_url)
    assert import_pdf.main(
        ["--source", str(complete_pdf), "--import", "--code", "nkjv",
         "--database-url", database_url]
    ) == 0

    engine = create_engine(database_url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        assert [v["code"] for v in list_versions(session)] == ["nkjv"]
        verses = fetch_passage(session, "nkjv", [parse_one("John 3:1")])
    assert verses[0]["osis"] == "John"
    assert verses[0]["text"].startswith("First verse of John 3")


def test_pdf_import_takes_the_licence_class_from_the_manifest(complete_pdf, tmp_path, capsys):
    from services.bible import import_pdf

    database = tmp_path / "bible.db"
    database_url = f"sqlite:///{database}"
    assert import_pdf.main(
        ["--source", str(complete_pdf), "--import", "--code", "esv",
         "--database-url", database_url]
    ) == 0
    payload = capsys.readouterr().out
    assert '"license_class": "licensed"' in payload
    assert '"rights_holder": "Crossway"' in payload


def test_manifest_only_covers_translations_we_declare():
    from services.bible.corpus import load_manifest

    codes = {entry["code"] for entry in load_manifest()}
    assert {"kjv", "asv", "web"} <= codes
    assert {"nkjv", "nlt", "esv", "niv"} <= codes


def test_licensed_manifest_entries_carry_no_download():
    from services.bible.corpus import load_manifest

    for entry in load_manifest():
        if entry["license_class"] == "licensed":
            assert entry["source_url"] == ""
            assert entry["sha256"] == ""
            assert entry["rights_holder"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))