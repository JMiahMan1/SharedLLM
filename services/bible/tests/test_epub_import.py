# services/bible/tests/test_epub_import.py
"""Extractor tests, built on real epub files made here rather than fixtures on disk."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from services.bible import epub_import, importer
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
    numbered: bool = False,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Write a minimal but structurally valid epub.

    ``documents`` replaces generated documents by filename, so a test can
    hand-write the markup for the layout cases it cares about. Book numbers in
    the anchors are always the real canonical positions, because that is what
    the extractor trusts. ``numbered`` emits the anchor-less layout instead --
    a chapter heading paragraph and the verse number inside the prose -- which
    is how a typeset commercial EPUB reaches the reader, and ``extra_files``
    writes additional members such as a stylesheet.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    generated: dict[str, str] = {}
    for position, entry in enumerate(BOOKS, start=1):
        if book_filter is not None and entry["osis"] not in book_filter:
            continue
        name = f"OEBPS/book{position:02d}.html"
        if documents is not None and name in documents:
            continue
        body: list[str] = []
        if not numbered:
            body.append(f'<div class="chap" id="bk{position:02d}"><p class="ct">{entry["name"]}</p></div>')
        for chapter in range(1, entry["chapters"] + 1):
            if numbered:
                body.append(
                    f'<p class="hd" id="hd{position:02d}{chapter:03d}">'
                    f'<a href="book{position:02d}.html">{entry["name"]}</a> '
                    f'<a href="book{position:02d}.html">{chapter}</a></p>'
                )
            else:
                body.append(f'<div class="chap" id="ch{position:02d}{chapter:03d}"><p class="cn">{chapter}</p></div>')
            for verse in range(1, per_chapter + 1):
                if (position, chapter) in skip_verses:
                    continue
                if numbered:
                    body.append(
                        f'<p class="v"><span>{verse}</span>{entry["name"]} {chapter}:{verse}</p>'
                    )
                else:
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
        for name, markup_text in all_docs.items():
            archive.writestr(name, markup_text)
        for name, text in (extra_files or {}).items():
            archive.writestr(name, text)
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


def test_a_complete_epub_installs_as_a_translation(complete_epub: Path, session: Session) -> None:
    """The whole install path, not just the extractor.

    The importer used to stage ``to_corpus_source``'s tuple output rather than
    ``to_source``'s list-of-dicts, so the staged JSON began with the
    empty-string name and every real translation died inside ``parse_source``
    with ``'str' object has no attribute 'get'`` -- a 500 the admin page could
    only show as a generic failure. ``to_source``'s shape was tested; this
    pins the wiring from extraction to an installed corpus.
    """
    report = importer.run(
        session,
        importer.ImportPlan(code="kjv", kind="epub", source_path=str(complete_epub)),
    )
    assert report.status == "succeeded", f"{report.message} :: {report.log}"
    assert report.verse_count == sum(entry["chapters"] for entry in BOOKS)


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

def test_numbered_paragraphs_extract_when_anchors_are_absent(tmp_path: Path) -> None:
    """A typeset EPUB numbers its verses in the prose instead of anchoring them.

    The NLT file an admin uploaded carried no ``v`` anchors at all, so the
    anchor reader found nothing and the import died. Chapter headings,
    in-text verse numbers, section titles and editorial boxes all have to be
    told apart from scripture for that file to install.
    """
    path = build_epub(
        tmp_path / "numbered.epub",
        book_filter={"Gen"},
        extra_files={"OEBPS/style.css": ".hd {font-weight: bold;}"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                '<p class="hd"><a href="book01.html">Genesis</a> <a href="book01.html">1</a></p>'
                '<p class="v"><span>1</span>In the beginning<a epub:type="noteref" href="n.xhtml">*</a> God created</p>'
                '<div class="box"><p>listen to the Word</p></div>'
                '<p class="hd" id="a1">The creation numbered</p>'
                '<p class="v"><span>2</span>And the earth was without form</p>'
                '<p class="v"><span>3</span>And God said</p>'
                "<p>Let there be light</p>"
                '<p class="hd"><a href="book01.html">Genesis</a> <a href="book01.html">2</a></p>'
                '<p class="v"><span>1</span>Thus the heavens were finished</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert result["mode"] == "verse"
    assert verse_text(result, "Gen", 1, 1) == "In the beginning God created"
    assert verse_text(result, "Gen", 1, 2) == "And the earth was without form"
    assert verse_text(result, "Gen", 1, 3) == "And God said Let there be light"
    assert verse_text(result, "Gen", 2, 1) == "Thus the heavens were finished"
    joined = " ".join(result["flat"].values())
    assert "listen to the Word" not in joined
    assert "The creation numbered" not in joined
    assert any("not kept as verse text" in warning for warning in result["warnings"])


def test_a_numbered_translation_still_installs_as_a_whole(tmp_path: Path) -> None:
    path = build_epub(tmp_path / "numbered-full.epub", numbered=True)
    result = extract(str(path))
    assert [r.name for r in result["reports"] if not r.ok] == []
    assert verse_text(result, "Gen", 1, 1) == "Genesis 1:1"
    assert verse_text(result, "Rev", 22, 1) == "Revelation 22:1"
    payload = to_source(result)
    assert len(payload) == 66
    assert result["verse_count"] == sum(entry["chapters"] for entry in BOOKS)
    assert not any("not kept as verse text" in warning for warning in result["warnings"])


def test_a_verse_number_out_of_sequence_is_declined_not_guessed(tmp_path: Path) -> None:
    """Sequential numbering is the trust this mode rests on.

    A number far ahead of the sequence is either damaged markup or a fixture
    for a later verse; either way guessing its text onto the wrong verse would
    install a confident lie, so the paragraph is declined and the report says
    so. A number only one or two ahead is left out of sequence by the
    translation itself and is taken at face value (that case has its own test).
    """
    path = build_epub(
        tmp_path / "gapped.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                '<p class="hd"><a href="book01.html">Genesis</a> <a href="book01.html">1</a></p>'
                '<p class="v"><span>1</span>First verse</p>'
                '<p class="v"><span>15</span>Fifteenth verse arrived early</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 1) == "First verse"
    assert (1, 1, 15) not in result["flat"]
    assert any("out of sequence" in warning for warning in result["warnings"])


def test_without_css_a_numbered_heading_is_still_dropped_by_its_id(tmp_path: Path) -> None:
    """A stylesheet-less archive still has to keep titles out of the verses."""
    path = build_epub(
        tmp_path / "numbered-nostyles.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                '<p class="hd"><a href="book01.html">Genesis</a> <a href="book01.html">1</a></p>'
                '<p class="v"><span>1</span>In the beginning</p>'
                '<p id="a77">A section title</p>'
                '<p class="v"><span>2</span>God created</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 1) == "In the beginning"
    assert verse_text(result, "Gen", 1, 2) == "God created"
    assert "A section title" not in " ".join(result["flat"].values())


def test_a_ranged_paragraph_files_every_number_it_names(tmp_path: Path) -> None:
    """Typeset prose numbers several verses as "3-4" over one paragraph.

    NLT Numbers 2 sets pairs of verses this way; splitting by single numbers
    would leave 3 and 4 empty and glue their text onto verse 2.
    """
    path = build_epub(
        tmp_path / "ranged.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                '<p class="hd"><a href="book01.html">Genesis</a> <a href="book01.html">1</a></p>'
                '<p class="v"><span>1</span>First verse</p>'
                '<p class="v"><span>2</span>Second verse</p>'
                '<p class="v"><span>3-4</span>Shared prose for the paired verses</p>'
                '<p class="v"><span>5</span>Fifth verse</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 3) == "Shared prose for the paired verses"
    assert verse_text(result, "Gen", 1, 4) == "Shared prose for the paired verses"
    assert verse_text(result, "Gen", 1, 5) == "Fifth verse"
    assert (1, 1, 2) not in result["flat"].values()


def test_a_translation_that_skips_a_number_keeps_the_verse_that_follows(tmp_path: Path) -> None:
    """The NLT has no John 5:4, so the next paragraph reads 5 where 4 is due.

    Declining that jump would cascade and abandon the rest of the chapter;
    the skipped number is reported instead.
    """
    path = build_epub(
        tmp_path / "skipped.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                '<p class="hd"><a href="book01.html">Genesis</a> <a href="book01.html">1</a></p>'
                '<p class="v"><span>1</span>First verse</p>'
                '<p class="v"><span>3</span>Third verse, the second is gone</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 3) == "Third verse, the second is gone"
    assert (1, 1, 2) not in result["flat"]
    assert any("absent from this translation" in warning for warning in result["warnings"])


def test_a_heading_that_carries_the_anchor_defers_to_the_verse(tmp_path: Path) -> None:
    """A section title that *carries* the verse id is apparatus, not scripture.

    The ESV study-Bible export parks the anchor on ``<p class="heading">`` and
    sets the verse in the next paragraph, whose own class is not a verse
    class. Storing the heading as the verse text made every section-leading
    verse a title instead of a quotation.
    """
    path = build_epub(
        tmp_path / "heading-anchor.epub",
        book_filter={"John"},
        documents={
            "OEBPS/book43.html": (
                "<html><body>"
                "<h2>John</h2>"
                f'<p id="{anchor(43, 3, 16)}" class="heading">For God So Loved the World</p>'
                '<p class="no-indent">'
                '<span class="book-name"><a href="main.html">JOHN</a></span>'
                '<span class="chapter-num"> 3 </span>'
                '<span class="verse-num">16</span>'
                'For God so loved the world'
                '<span class="crossref"><small> </small><a id="cr1" href="xrefs.html#c1">a</a></span>'
                ", that he gave his only Son"
                '<span class="footnote"><a href="notes.html#f1">[8]</a></span>'
                "</p>"
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    text = verse_text(result, "John", 3, 16)
    assert text == "For God so loved the world, that he gave his only Son"
    assert "For God So Loved the World" not in result["flat"].values()
    assert "JOHN" not in text
    assert "John 3" not in text


def test_marker_spans_and_bare_marker_links_are_dropped(tmp_path: Path) -> None:
    """Verse numbers, apparatus letters and index terms never enter the text.

    The NIV and NKJV exports put the verse digit in a ``ver`` span and the
    footnote/cross-reference letters on bare ``enref``/``fnref`` links, with
    study index terms glued to the end of the verse -- all measured from real
    files, all told apart by class name rather than by translation.
    """
    path = build_epub(
        tmp_path / "markers.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                '<p class="pf">'
                f'<span class="ver" id="{anchor(1, 1, 1)}"><a class="calibre3" href="com.html">1</a></span>'
                "In the beginning"
                '<a id="rx1" class="enref" href="part0027.html#x01001001a">a</a>'
                " God created"
                '<a class="enref" href="part0027.html#x01001001b">b</a>'
                " the heavens and the earth."
                '<span class="idx"><a class="xref" href="idx.html#s1">God the Creator</a></span>'
                "</p>"
                '<p><span class="ver-b" id="' + anchor(1, 1, 2) + '">2</span>The earth was '
                'without form<a class="fnref" href="notes.html#f01001002">a</a>.</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 1) == "In the beginning God created the heavens and the earth."
    assert verse_text(result, "Gen", 1, 2) == "The earth was without form."


def test_a_heading_tag_ends_the_verse_instead_of_gluing_its_text(tmp_path: Path) -> None:
    """h1-h6 are pericope titles. An open verse must end at one, not absorb it."""
    path = build_epub(
        tmp_path / "htags.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                "<h2>Genesis</h2>"
                f'<p class="pf" id="{anchor(1, 1, 1)}">In the beginning<h2>The Beginning</h2>God created</p>'
                f'<p class="pf" id="{anchor(1, 1, 2)}">The earth was without form</p>'
                "<h3>Light Created</h3>"
                f'<p class="pf" id="{anchor(1, 1, 3)}">Then God said</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 1) == "In the beginning"
    assert verse_text(result, "Gen", 1, 2) == "The earth was without form"
    assert verse_text(result, "Gen", 1, 3) == "Then God said"
    joined = " ".join(result["flat"].values())
    assert "The Beginning" not in joined
    assert "Light Created" not in joined


def test_a_verse_own_number_is_only_stripped_when_it_matches_the_position(tmp_path: Path) -> None:
    """Some exports glue the number to the first word ("1In the beginning").

    The strip only fires on the verse's own canonical digits followed by a
    letter or quote, so "20 men" (verse 2) and "1,600" are never mangled.
    """
    path = build_epub(
        tmp_path / "own-number.epub",
        book_filter={"Gen"},
        documents={
            "OEBPS/book01.html": (
                "<html><body>"
                f'<p class="pf"><span class="ver" id="{anchor(1, 1, 1)}">1</span>1In the beginning God created</p>'
                f'<p class="pf"><span class="ver" id="{anchor(1, 1, 2)}">2</span>20 men and 1,600 camels</p>'
                f'<p class="pf"><span class="ver" id="{anchor(1, 1, 16)}">16</span>16\u201cFor God so loved</p>'
                "</body></html>"
            )
        },
    )
    result = extract(str(path))
    assert verse_text(result, "Gen", 1, 1) == "In the beginning God created"
    assert verse_text(result, "Gen", 1, 2) == "20 men and 1,600 camels"
    assert verse_text(result, "Gen", 1, 16) == "\u201cFor God so loved"
