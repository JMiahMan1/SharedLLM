"""The study-book normaliser, format by format, on generated books.

Each case is a layout problem real imports had: running heads and page numbers
in the prose, words split at line ends, paragraphs cut at page breaks, columns
read across, footnotes spliced into sentences, headings lost, CSS noise,
ligatures and mis-decoded quotes. The books are made up in ``study_fixtures``.
"""
from __future__ import annotations

import json
import sys
import types

import pytest
from sqlmodel import Session, SQLModel, create_engine

from services.bible import book_import, study_blocks, study_format, study_html
from services.bible.study_blocks import Block, Extracted, find_references, join_lines, text_fingerprint
from services.bible.tests import study_fixtures as fx


def _all_blocks(document):
    return [block for section in document.sections for block in section.blocks]


def _text(document) -> str:
    return "\n".join(study_blocks.section_text(section) for section in document.sections)


# --- PDF ---------------------------------------------------------------------


def test_a_pdf_loses_its_running_heads_and_page_numbers(tmp_path):
    document = study_format.read(fx.pdf_book(tmp_path / "guide.pdf"))
    text = _text(document)
    assert "A Made-up Study Guide" not in text
    assert document.report["running_lines_removed"] >= 12
    # No page number survives as a paragraph of its own.
    assert not any(block.text.strip().isdigit() for block in _all_blocks(document))


def test_a_pdf_rejoins_split_words_and_wrapped_lines(tmp_path):
    document = study_format.read(fx.pdf_book(tmp_path / "guide.pdf"))
    text = _text(document)
    assert "redemption is split" in text
    assert "covenant and runs on" in text
    assert "redemp-" not in text and "\nfew lines" not in text
    assert document.report["hyphens_joined"] >= 10


def test_a_paragraph_that_runs_over_a_page_break_stays_one_paragraph(tmp_path):
    document = study_format.read(fx.pdf_book(tmp_path / "guide.pdf"))
    joined = [b for b in _all_blocks(document) if "foot of the page and Page 2 opens" in b.text]
    assert len(joined) == 1


def test_a_pdf_is_cut_into_lessons_at_its_headings(tmp_path):
    document = study_format.read(fx.pdf_book(tmp_path / "guide.pdf"))
    assert [s.title for s in document.sections] == [
        "Lesson 1: Walking in the Light",
        "Lesson 2: Walking in the Light",
    ]
    assert [s.id for s in document.sections] == [
        "s-lesson-1-walking-in-the-light",
        "s-lesson-2-walking-in-the-light",
    ]
    first = document.sections[0].blocks[0]
    assert first.type == "heading" and first.level == 1


def test_footnotes_become_notes_and_questions_get_answer_space(tmp_path):
    document = study_format.read(fx.pdf_book(tmp_path / "guide.pdf"))
    blocks = _all_blocks(document)
    notes = [b for b in blocks if b.type == "note"]
    assert notes and notes[0].marker == "1" and notes[0].text == "A footnote for page 1."
    # The footnote is not inside any paragraph.
    assert not any("A footnote" in b.text for b in blocks if b.type == "paragraph")
    questions = [b.text for b in document.sections[0].blocks if b.type == "question"]
    assert questions == ["What does walking in the light mean to you?", "How does Psalm 119:105 help?"]


def test_two_columns_are_read_left_then_right(tmp_path):
    document = study_format.read(fx.pdf_book(tmp_path / "cols.pdf", two_columns=True))
    text = _text(document)
    assert document.report["two_column_pages"] == 1
    left_last = text.index("Left column line 8")
    right_first = text.index("Right column line 1")
    assert left_last < right_first
    assert text.index("Left column line 1") < text.index("Left column line 2")


def test_a_scanned_pdf_says_it_needs_ocr(tmp_path):
    import pymupdf

    document = pymupdf.open()
    document.new_page()
    path = tmp_path / "scan.pdf"
    path.write_bytes(document.tobytes())
    with pytest.raises(study_format.FormatError, match="OCR"):
        study_format.read(path)


# --- EPUB --------------------------------------------------------------------


def test_an_epub_keeps_its_structure(tmp_path):
    document = study_format.read(fx.epub_book(tmp_path / "study.epub"))
    assert document.title == "A Made-up EPUB Study"
    assert [s.title for s in document.sections] == ["Lesson 1", "Lesson 2"]
    kinds = [b.type for b in document.sections[0].blocks]
    for kind in ("heading", "paragraph", "quote", "list", "table", "image", "question", "note"):
        assert kind in kinds, kind
    # A publisher's class names are headings, not body text.
    assert document.sections[0].blocks[1].text == "The Vine and the Branches"
    assert document.sections[0].blocks[1].type == "heading"


def test_epub_noise_is_gone_and_notes_are_lifted_out_of_the_sentence(tmp_path):
    document = study_format.read(fx.epub_book(tmp_path / "study.epub"))
    blocks = document.sections[0].blocks
    abide = next(b for b in blocks if b.text.startswith("Abide"))
    assert abide.text == "Abide in me, the study says, with a ref to John 15:5."
    assert abide.notes == ["1"]
    note = next(b for b in blocks if b.type == "note")
    assert note.marker == "1" and note.text == "A footnote about the vine."
    # The page-break marker's number is not prose.
    assert not any(b.text == "12" for b in blocks)
    cleaned = next(b for b in blocks if b.text.startswith("Ligatures"))
    assert "first and flow" in cleaned.text
    assert "God\u2019s" in cleaned.text


def test_a_spine_file_without_a_heading_continues_the_lesson(tmp_path):
    document = study_format.read(fx.epub_book(tmp_path / "study.epub"))
    assert document.sections[1].blocks[-1].text == "This file continues lesson two after a split."


def test_epub_images_are_kept_as_assets(tmp_path):
    document = study_format.read(fx.epub_book(tmp_path / "study.epub"))
    image = next(b for b in _all_blocks(document) if b.type == "image")
    assert image.text == "A vine"
    assert any(asset.key == image.src and asset.data == fx.PNG for asset in document.assets)


# --- MOBI / AZW3 -------------------------------------------------------------


def test_a_kindle_book_is_unpacked_and_read_like_an_epub(tmp_path, monkeypatch):
    epub = fx.epub_book(tmp_path / "unpacked.epub")

    def fake_extract(path):
        folder = tmp_path / "mobi-out"
        folder.mkdir()
        target = folder / "book.epub"
        target.write_bytes(epub.read_bytes())
        return str(folder), str(target)

    monkeypatch.setitem(sys.modules, "mobi", types.SimpleNamespace(extract=fake_extract))
    source = tmp_path / "study.azw3"
    source.write_bytes(b"not really a kindle file")
    document = study_format.read(source)
    assert document.report["format"] == "azw3"
    assert [s.title for s in document.sections] == ["Lesson 1", "Lesson 2"]


def test_a_kindle_book_without_a_converter_says_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mobi", None)
    monkeypatch.setattr(study_html.shutil, "which", lambda name: None)
    source = tmp_path / "study.mobi"
    source.write_bytes(b"x")
    with pytest.raises(study_format.FormatError, match="mobi"):
        study_format.read(source)


def test_a_drm_kindle_book_is_refused_plainly(tmp_path, monkeypatch):
    def locked(path):
        raise ValueError("This book has DRM")

    monkeypatch.setitem(sys.modules, "mobi", types.SimpleNamespace(extract=locked))
    source = tmp_path / "locked.azw"
    source.write_bytes(b"x")
    with pytest.raises(study_format.FormatError, match="DRM"):
        study_format.read(source)


# --- DOCX --------------------------------------------------------------------


def test_a_word_document_uses_its_styles(tmp_path):
    document = study_format.read(fx.docx_book(tmp_path / "study.docx"))
    assert [s.title for s in document.sections] == ["Lesson 1: Grace", "Lesson 2: Faith"]
    first = document.sections[0].blocks
    # "Lesson Title" is a house style based on Heading 1.
    assert any(b.type == "heading" and b.text == "Lesson 1: Grace" and b.level == 1 for b in first)
    assert any(b.type == "quote" for b in first)
    listing = next(b for b in first if b.type == "list")
    assert listing.items == ["First numbered point", "Second numbered point"] and listing.ordered
    assert [b.text for b in first if b.type == "question"] == ["What does grace mean to you?"]
    table = next(b for b in document.sections[1].blocks if b.type == "table")
    assert table.rows == [["Week", "Passage"], ["1", "Psalm 23"]]


def test_word_footnotes_follow_the_paragraph_that_cites_them(tmp_path):
    document = study_format.read(fx.docx_book(tmp_path / "study.docx"))
    blocks = document.sections[0].blocks
    cited = next(i for i, b in enumerate(blocks) if b.text.startswith("Placeholder prose about grace"))
    assert blocks[cited].notes == ["1"]
    assert blocks[cited + 1].type == "note" and blocks[cited + 1].text == "A note on grace."


# --- Plain text --------------------------------------------------------------


def test_plain_text_and_markdown(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text(
        "# Lesson 1\n\nA wrapped\nparagraph with a split hyphen-\nated word.\n\n"
        "- one point\n- another point\n\n> a quote\n\n# Lesson 2\n\nMore text.\n"
    )
    document = study_format.read(path)
    assert [s.title for s in document.sections] == ["Lesson 1", "Lesson 2"]
    paragraph = document.sections[0].blocks[1]
    assert paragraph.text == "A wrapped paragraph with a split hyphenated word."


# --- Shared rules --------------------------------------------------------------


def test_join_lines_keeps_real_hyphens():
    assert join_lines(["a self-", "control test"])[0] == "a self-control test"
    assert join_lines(["the Old Testament-", "New Testament link"])[0] == "the Old Testament- New Testament link"
    assert join_lines(["redemp-", "tion"]) == ("redemption", 1)


def test_clean_text_fixes_typography_without_changing_words():
    raw = "\ufb01rst \u00e2\u20ac\u0153quoted\u00e2\u20ac\u009d God\u00e2\u20ac\u2122s soft\u00adhyphen  spaced\u00a0out ``old''"
    cleaned = study_blocks.clean_text(raw)
    assert cleaned == "first \u201cquoted\u201d God\u2019s softhyphen spaced out \u201cold\u201d"
    assert text_fingerprint(raw.replace("\u00e2\u20ac\u0153", "").replace("\u00e2\u20ac\u009d", "").replace(
        "\u00e2\u20ac\u2122", "")) == text_fingerprint(cleaned)


@pytest.mark.parametrize(
    "text, refs",
    [
        ("Read Romans 8:28 today.", ["Romans 8:28"]),
        ("See Rom. 8:28-30 and 1 Cor 13:4.", ["Romans 8:28-30", "1 Corinthians 13:4"]),
        ("Psalm 23 is loved.", ["Psalms 23"]),
        ("Compare John 3:16; 4:1.", ["John 3:16; John 4:1"]),
        ("Job 1:21 is a lament.", ["Job 1:21"]),
        ("I took a job 4 years ago.", []),
        ("Mark 4 was the week we met.", []),
        ("Mark 4:3 is a parable.", ["Mark 4:3"]),
        ("Genesis 51:1 does not exist.", []),
        ("II Timothy 3:16 in roman numerals.", ["2 Timothy 3:16"]),
    ],
)
def test_scripture_references_are_found_and_confirmed(text, refs):
    found = find_references(text)
    assert [r["ref"] for r in found] == refs
    for ref in found:
        assert text[ref["start"]:ref["end"]].strip()


def test_block_ids_are_stable_across_a_reimport(tmp_path):
    first = study_format.read(fx.epub_book(tmp_path / "a.epub"))
    second = study_format.read(fx.epub_book(tmp_path / "b.epub"))
    assert [b.id for b in _all_blocks(first)] == [b.id for b in _all_blocks(second)]
    assert len({b.id for b in _all_blocks(first)}) == len(_all_blocks(first))


def test_mark_questions_leaves_rhetorical_questions_alone():
    blocks = [
        Block(type="paragraph", text="Who could have guessed what happened next?"),
        Block(type="heading", text="Discussion", level=2),
        Block(type="paragraph", text="Why does the shepherd leave the ninety-nine?"),
    ]
    assert study_blocks.mark_questions(blocks) == 1
    assert [b.type for b in blocks] == ["paragraph", "heading", "question"]


def test_the_normaliser_never_adds_words():
    extracted = Extracted(blocks=[Block(type="paragraph", text="One two three."), Block(type="heading", text="Four")])
    document = study_blocks.finish(extracted)
    assert text_fingerprint(_text(document)) == text_fingerprint("One two three. Four")


# --- Storage -------------------------------------------------------------------


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_an_imported_book_stores_blocks_beside_plain_text(tmp_path):
    with _session() as session:
        book = book_import.import_book(session, path=fx.docx_book(tmp_path / "Grace Study.docx"))
        chapters = book_import.chapters_for(session, book.id)
        assert book.format_version == study_blocks.FORMAT_VERSION
        assert [c.section_id for c in chapters] == ["s-lesson-1-grace", "s-lesson-2-faith"]
        blocks = json.loads(chapters[0].blocks)
        assert blocks[0]["type"] == "heading"
        # Ask, devotionals and Notes still read plain text.
        assert "Placeholder prose about grace" in chapters[0].text
        assert json.loads(chapters[0].questions) == ["What does grace mean to you?"]
        assert json.loads(book.import_report)["sections"] == 2


def test_epub_images_are_stored_with_the_book(tmp_path):
    from sqlmodel import select

    from services.bible.models import ImportedBookAsset

    with _session() as session:
        book = book_import.import_book(session, path=fx.epub_book(tmp_path / "Vine.epub"))
        assets = session.exec(select(ImportedBookAsset).where(ImportedBookAsset.book_id == book.id)).all()
        assert [a.data for a in assets] == [fx.PNG]
        assert book_import.delete_book(session, book.id)
        assert session.exec(select(ImportedBookAsset)).all() == []
