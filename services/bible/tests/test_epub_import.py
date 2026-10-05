# services/bible/tests/test_epub_import.py
"""Extractor tests, built on real epub files made here rather than fixtures on disk."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from services.bible import epub_import
from services.bible.books import BOOKS
from services.bible.corpus import fetch_passage, list_versions
from services.bible.epub_import import (
    EpubImportError,
    extract,
    report_lines,
    to_source,
)
from services.bible.import_epub import main as epub_main
from services.bible.refs import parse_reference
from services.bible.study import available_kinds, notes_for_verse

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
  </metadata>
  <manifest>{items}</manifest>
  <spine>{spine}</spine>
</package>
"""


def path_documents(path: Path) -> dict[str, str]:
    """Read a generated epub back so one test can extend another's markup."""
    with zipfile.ZipFile(path) as archive:
        return {
            name: archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".html")
        }


def anchor(book: int, chapter: int, verse: int) -> str:
    return "v{:02d}{:03d}{:04d}".format(book, chapter, verse)


def commentary_id(book: int, chapter: int, verse: int) -> str:
    return "com{:02d}{:03d}{:03d}".format(book, chapter, verse)


def build_epub(
    path: Path,
    *,
    title: str = "A Study Bible",
    documents: dict[str, str] | None = None,
    per_chapter: int = 1,
    book_filter=None,
    declare_spine: bool = True,
    append_to: str | None = None,
    markup: str = "",
    skip_verses: frozenset[tuple[int, int]] = frozenset(),
) -> Path:
    """Write a minimal but structurally valid epub.

    ``documents`` replaces generated documents by filename, so a test can
    hand-write the markup for the layout cases it cares about. Book numbers in
    the anchors are always the real canonical positions, because that is what
    the extractor trusts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    generated: dict[str, str] = {}
    for position, entry in enumerate(BOOKS, start=1):
        if book_filter is not None and entry["osis"] not in book_filter:
            continue
        name = f"OEBPS/book{position:02d}.html"
        if documents is not None and name in documents:
            continue
        body = [f'<div class="chap" id="bk{position:02d}"><p class="ct">{entry["name"]}</p></div>']
        for chapter in range(1, entry["chapters"] + 1):
            body.append(f'<div class="chap" id="ch{position:02d}{chapter:03d}"><p class="cn">{chapter}</p></div>')
            for verse in range(1, per_chapter + 1):
                if (position, chapter) in skip_verses:
                    continue
                body.append(
                    f'<p class="sl1" id="{anchor(position, chapter, verse)}">'
                    f'{entry["name"]} {chapter}:{verse}</p>'
                )
        generated[name] = "<html><body>" + "".join(body) + "</body></html>"
        if name == append_to:
            generated[name] = generated[name].replace("</body>", markup + "</body>")

    all_docs = {**generated, **(documents or {})}
    ordered = sorted(all_docs)
    items = "".join(
        f'<item id="d{i}" href="{name.split("OEBPS/")[-1]}" media-type="application/xhtml+xml"/>'
        for i, name in enumerate(ordered)
    )
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(ordered))) if declare_spine else ""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", OPF.format(title=title, items=items, spine=spine))
        for name, markup in all_docs.items():
            archive.writestr(name, markup)
    return path


@pytest.fixture
def complete_epub(tmp_path: Path) -> Path:
    return build_epub(tmp_path / "complete.epub")


def verse_text(result: dict, osis: str, chapter: int, verse: int) -> str:
    position = next(i for i, e in enumerate(BOOKS, start=1) if e["osis"] == osis)
    return result["flat"][(position, chapter, verse)]


def test_a_complete_epub_reports_every_book_ok(complete_epub: Path) -> None:
    result = extract(str(complete_epub))
    failing = [r.name for r in result["reports"] if not r.ok]
    assert failing == []
    assert result["title"] == "A Study Bible"
    assert result["verse_count"] == sum(e["chapters"] for e in BOOKS)


def test_verse_text_is_the_anchored_paragraph(complete_epub: Path) -> None:
    result = extract(str(complete_epub))
    assert verse_text(result, "Gen", 1, 1) == "Genesis 1:1"
    assert verse_text(result, "Rev", 22, 1) == "Revelation 22:1"


def test_a_classless_paragraph_continues_the_verse(tmp_path: Path) -> None:
    """Poetic books put the tail of a verse in a bare paragraph."""
    path = build_epub(
        tmp_path / "poetic.epub",
        book_filter={"Eccl"},
        documents={
            "OEBPS/book21.html": (
                "<html><body>"
                '<div class="chap" id="ch21003"><p class="cn">3</p></div>'
                f'<p class="pf" id="{anchor(21, 3, 1)}">To everything there is a season,</p>'
                "<p>and a time to every purpose under heaven:</p>"
                f'<p class="pf" id="{anchor(21, 3, 2)}">He has made everything beautiful</p>'
                "<p>in its time.</p>"
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Eccl", 3, 1) == "To everything there is a season, and a time to every purpose under heaven:"
    assert verse_text(result, "Eccl", 3, 2) == "He has made everything beautiful in its time."


def test_a_known_continuation_class_merges(tmp_path: Path) -> None:
    path = build_epub(
        tmp_path / "psalm.epub",
        book_filter={"Ps"},
        documents={
            "OEBPS/book19.html": (
                "<html><body>"
                f'<p class="sl1" id="{anchor(19, 23, 1)}">The LORD is my shepherd;</p>'
                '<p class="sl1">I shall not want.</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Ps", 23, 1) == "The LORD is my shepherd; I shall not want."


def test_sidebars_do_not_leak_into_the_verse(tmp_path: Path) -> None:
    path = build_epub(
        tmp_path / "noisy.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                f'<p class="sl1" id="{anchor(1, 1, 1)}">In the beginning</p>'
                '<p class="sbh">A sidebar heading</p>'
                "<p>Sidebar prose that is not scripture.</p>"
                '<p class="com">Commentary about the verse.</p>'
                f'<p class="sl1" id="{anchor(1, 1, 2)}">God created</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 1) == "In the beginning"
    assert verse_text(result, "Gen", 1, 2) == "God created"
    assert result["study"].skipped["sbh"] >= 1
    assert result["study"].skipped["com"] >= 1


def test_superscripts_are_dropped_from_the_verse(tmp_path: Path) -> None:
    path = build_epub(
        tmp_path / "sups.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                f'<p class="sl1" id="{anchor(1, 1, 1)}">In the beginning<sup>1</sup>'
                '<a href="notes.html#fn10001" id="fn10001r"><sup>a</sup></a>'
                " God created</p>"
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 1) == "In the beginning God created"


def test_a_self_closing_sup_does_not_swallow_the_rest_of_the_document(tmp_path: Path) -> None:
    """`<sup/>` used as a spacer must not leave the parser inside a sup forever."""
    path = build_epub(
        tmp_path / "selfclosing.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                f'<p class="sl1" id="{anchor(1, 1, 1)}">first<sup/></p>'
                f'<p class="sl1" id="{anchor(1, 1, 2)}">second</p>'
                f'<p class="sl1" id="{anchor(1, 1, 3)}">third</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert result["verse_count"] == 3
    assert verse_text(result, "Gen", 1, 3) == "third"


def _study_epub(tmp_path: Path, name: str, verse_markup: str, notes_markup: str) -> Path:
    return build_epub(
        tmp_path / name,
        book_filter={"John"},
        documents={
            "OEBPS/book43.html": (
                "<html><body>"
                '<div class="chap" id="ch43003"><p class="cn">3</p></div>'
                + verse_markup
                + "</body></html>"
            ),
            "OEBPS/notes.html": "<html><body>" + notes_markup + "</body></html>",
        },
    )


def test_commentary_is_joined_to_the_verse_that_cites_it(tmp_path: Path) -> None:
    path = _study_epub(
        tmp_path,
        "study.epub",
        f'<p class="sl1" id="{anchor(43, 3, 16)}">For God so loved '
        f'<a href="notes.html#{commentary_id(43, 3, 16)}"><sup>a</sup></a> the world.</p>',
        f'<p class="com" id="{commentary_id(43, 3, 16)}">'
        "3:16 For God so loved &mdash; the Greek is aorist.</p>",
    )
    result = extract(str(path))
    assert verse_text(result, "John", 3, 16) == "For God so loved the world."
    assert result["study"].commentary == 1
    note = result["notes"][0]
    assert note.as_dict()["verse"] == 16
    assert "aorist" in note.body


def test_extended_commentary_is_found_through_its_html_comment(tmp_path: Path) -> None:
    path = _study_epub(
        tmp_path,
        "comx.epub",
        f'<p class="sl1" id="{anchor(43, 3, 16)}">For God so loved '
        f'<a href="notes.html#{commentary_id(43, 3, 16)}"><sup>a</sup></a> the world.</p>',
        f'<p class="comx"><!-- id=&#34;{commentary_id(43, 3, 16)}&#34; -->'
        "Extended note.</p>",
    )
    result = extract(str(path))
    assert result["study"].commentary == 1
    assert "Extended note." in result["notes"][0].body


def test_footnotes_are_joined_and_sorted_by_kind(tmp_path: Path) -> None:
    path = _study_epub(
        tmp_path,
        "footnotes.epub",
        f'<p class="sl1" id="{anchor(43, 3, 16)}">For God so loved '
        f'<a href="notes.html#fn12345"><sup>1</sup></a>the world.</p>',
        '<p class="fn" id="fn12345">Rom. 5:8</p>',
    )
    result = extract(str(path))
    assert result["study"].footnotes == 1
    note = result["notes"][0]
    assert note.kind == "footnote"
    assert note.body == "Rom. 5:8"


def test_a_citation_with_no_block_is_counted_not_hidden(tmp_path: Path) -> None:
    path = _study_epub(
        tmp_path,
        "missing.epub",
        f'<p class="sl1" id="{anchor(43, 3, 16)}">For God so loved '
        f'<a href="notes.html#{commentary_id(43, 3, 16)}"><sup>a</sup></a> the world.</p>',
        "<p class='com'>no id here</p>",
    )
    result = extract(str(path))
    assert result["study"].commentary == 0
    assert result["study"].missing_commentary == 1
    assert "cited commentary blocks" in " ".join(report_lines(result))


def test_a_footnote_no_verse_cites_is_reported_as_an_orphan(tmp_path: Path) -> None:
    path = _study_epub(
        tmp_path,
        "orphan.epub",
        f'<p class="sl1" id="{anchor(43, 3, 16)}">For God so loved the world.</p>',
        '<p class="fn" id="fn99999">Nobody cites me.</p>',
    )
    result = extract(str(path))
    assert result["study"].orphan_footnotes == 1
    assert "not cited by any verse" in " ".join(report_lines(result))


def test_report_lines_names_the_classes_it_declined(tmp_path: Path) -> None:
    path = build_epub(
        tmp_path / "declined.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                f'<p class="sl1" id="{anchor(1, 1, 1)}">In the beginning</p>'
                '<p class="fn">a footnote</p><p class="glo">gloss</p>'
                "</body></html>"
            )
        },
    )
    lines = " ".join(report_lines(extract(str(path))))
    assert "paragraph classes not kept as verse text" in lines
    assert "fn=" in lines and "glo=" in lines


def test_the_spine_decides_reading_order(tmp_path: Path) -> None:
    path = build_epub(
        tmp_path / "spine.epub",
        book_filter={"Gen", "Rev"},
        declare_spine=True,
    )
    result = extract(str(path))
    assert result["reports"][0].osis == "Gen"


def test_a_book_without_spine_entries_still_extracts(tmp_path: Path) -> None:
    path = build_epub(tmp_path / "nospine.epub", book_filter={"Gen"}, declare_spine=False)
    result = extract(str(path))
    assert result["verse_count"] == 50


def test_an_unreadable_epub_says_so(tmp_path: Path) -> None:
    broken = tmp_path / "broken.epub"
    broken.write_bytes(b"this is not a zip file")
    with pytest.raises(EpubImportError, match="not a readable epub"):
        extract(str(broken))


def test_to_source_refuses_a_partial_bible(tmp_path: Path) -> None:
    path = build_epub(tmp_path / "short.epub", book_filter={"Gen"})
    with pytest.raises(EpubImportError, match="Exodus|Exodus"):
        to_source(extract(str(path)))


def test_to_source_uses_canonical_book_names(complete_epub: Path) -> None:
    payload = to_source(extract(str(complete_epub)))
    assert payload[0]["name"] == "Genesis"
    assert payload[-1]["name"] == "Revelation"
    assert len(payload) == 66


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'corpus.db'}"


def test_the_cli_reports_without_a_database_url(tmp_path: Path, capsys) -> None:
    path = build_epub(tmp_path / "complete.epub")
    assert epub_main(["--source", str(path), "--report"]) == 0
    out = capsys.readouterr().out
    assert "A Study Bible" in out
    assert "ok   Genesis" in out


def test_the_cli_refuses_to_write_without_import(tmp_path: Path, capsys) -> None:
    path = build_epub(tmp_path / "complete.epub")
    assert epub_main(["--source", str(path)]) == 2
    assert "Add --import to install it" in capsys.readouterr().err


def test_the_cli_refuses_a_partial_bible(tmp_path: Path, capsys, db_url: str) -> None:
    path = build_epub(tmp_path / "short.epub", book_filter={"Gen"})
    code = epub_main(["--source", str(path), "--code", "gen", "--import", "--database-url", db_url])
    assert code == 1
    assert "did not extract a full chapter count" in capsys.readouterr().err


def test_the_cli_names_the_database_url_it_needs(tmp_path: Path, capsys, monkeypatch) -> None:
    import services.config as cfg

    monkeypatch.setattr(cfg, "BIBLE_DATABASE_URL", None)
    path = build_epub(tmp_path / "complete.epub")
    code = epub_main(["--source", str(path), "--code", "kjv", "--import"])
    assert code == 1
    assert "BIBLE_DATABASE_URL is not set" in capsys.readouterr().err


def test_the_cli_imports_text_and_notes(tmp_path: Path, db_url: str, capsys) -> None:
    path = _study_epub(
        tmp_path,
        "install.epub",
        f'<p class="sl1" id="{anchor(43, 3, 16)}">For God so loved '
        f'<a href="notes.html#{commentary_id(43, 3, 16)}"><sup>a</sup></a> the world.</p>',
        f'<p class="com" id="{commentary_id(43, 3, 16)}">Commentary body.</p>',
    )
    complete = build_epub(tmp_path / "install-full.epub")
    assert epub_main(
        [
            "--source", str(complete),
            "--code", "kjv",
            "--name", "King James Version",
            "--import",
            "--database-url", db_url,
        ]
    ) == 0
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        assert {row["code"] for row in list_versions(session)} == {"kjv"}
        verses = fetch_passage(session, "kjv", parse_reference("Gen 1:1"))
        assert verses[0]["text"] == "Genesis 1:1"
        assert available_kinds(session, "kjv") == {}
    payload = capsys.readouterr().out
    assert '"license_class": "public_domain"' in payload


def test_the_cli_keeps_the_study_material_out_of_the_text(tmp_path: Path, db_url: str, capsys) -> None:
    """A study Bible's commentary must never end up inside a verse."""
    study_verses = (
        f'<p class="sl1" id="{anchor(1, 1, 1)}">For God so loved '
        f'<a href="notes.html#{commentary_id(1, 1, 1)}"><sup>a</sup></a> '
        f'<a href="notes.html#fn70001"><sup>b</sup></a> the world.'
        '<p class="com">Commentary prose that must not appear in scripture.</p>'
        f'<p class="sl1" id="{anchor(1, 1, 2)}">For God did not send '
        f'<a href="notes.html#{commentary_id(1, 1, 2)}"><sup>b</sup></a> his Son.'
    )
    path = build_epub(
        tmp_path / "nkjv.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": "<html><body>" + study_verses + "</body></html>",
            "OEBPS/notes.html": (
                "<html><body>"
                f'<p class="com" id="{commentary_id(1, 1, 1)}">1:1 God so loved the world.</p>'
                f'<p class="com" id="{commentary_id(1, 1, 2)}">1:2 A note about the Son.</p>'
                '<p class="fn" id="fn70001">Rom. 5:8</p>'
                "</body></html>"
            ),
        },
    )
    assert epub_main(
        [
            "--source", str(path),
            "--code", "nkjv",
            "--import",
            "--import-notes",
            "--database-url", db_url,
        ]
    ) == 1
    assert "did not extract a full chapter count" in capsys.readouterr().err

    full = build_epub(
        tmp_path / "nkjv-full.epub",
        documents={
            "OEBPS/notes.html": path_documents(path)["OEBPS/notes.html"],
        },
        append_to="OEBPS/book01.html",
        markup=study_verses,
        skip_verses=frozenset({(1, 1)}),
    )
    assert epub_main(
        [
            "--source", str(full),
            "--code", "nkjv",
            "--import",
            "--import-notes",
            "--database-url", db_url,
        ]
    ) == 0
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        assert available_kinds(session, "nkjv") == {"commentary": 2, "footnote": 1}
        assert len(notes_for_verse(session, "nkjv", "Gen", 1, 1)) == 2
        verses = fetch_passage(session, "nkjv", parse_reference("Gen 1:1-2"))
        assert [v["text"] for v in verses] == [
            "For God so loved the world.",
            "For God did not send his Son.",
        ]
        joined = " ".join(n.body for n in notes_for_verse(session, "nkjv", "Gen", 1, 1))
        assert "God so loved the world" in joined
        assert "Commentary prose that must not appear in scripture" not in joined
    assert '"commentary": 2' in capsys.readouterr().out


def test_the_study_table_is_separate_from_the_verse_table(complete_epub: Path, db_url: str, capsys) -> None:
    assert epub_main(
        [
            "--source", str(complete_epub),
            "--code", "kjv",
            "--import",
            "--import-notes",
            "--database-url", db_url,
        ]
    ) == 0
    engine = create_engine(db_url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        verses = fetch_passage(session, "kjv", parse_reference("Gen 1:1"))[0]["text"]
        assert verses == "Genesis 1:1"
        assert available_kinds(session, "kjv") == {}
        assert notes_for_verse(session, "kjv", "Gen", 1, 1) == []


def test_the_manifest_decides_the_licence(complete_epub: Path, db_url: str, capsys) -> None:
    assert epub_main(
        [
            "--source", str(complete_epub),
            "--code", "nkjv",
            "--import",
            "--database-url", db_url,
        ]
    ) == 0
    out = capsys.readouterr().out
    assert '"rights_holder": "Thomas Nelson"' in out
    assert '"license_class": "licensed"' in out


def test_an_unknown_code_is_never_assumed_public_domain(complete_epub: Path, db_url: str, capsys) -> None:
    assert epub_main(
        [
            "--source", str(complete_epub),
            "--code", "mystery",
            "--import",
            "--database-url", db_url,
        ]
    ) == 0
    out = capsys.readouterr().out
    assert '"license_class": "licensed"' in out
    assert "study notes were not requested" in out