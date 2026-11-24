"""A book's chapters as its author numbered them: EPUB nav, toc.ncx, PDF outline, headings.

Every fixture is built here, in memory: a tiny EPUB 3 with a nav document and
front matter, an EPUB 2 with only toc.ncx, a plain-text book whose headings
read "CHAPTER FOUR", and a PDF with bookmarks.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from services.storage import book_chapters, library_api
from services.storage.book_chapters import (
    ChapterError,
    epub_chapters,
    find_chapter,
    parse_chapter_ref,
    pdf_chapters,
    split_parts,
    text_chapters,
)

TITLES = [
    "Wild at Heart",
    "The Wild One Whose Image We Bear",
    "The Question That Haunts Every Man",
    "The Wound",
    "The Battle for a Man's Heart",
]


def _xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x</title></head>'
        f"<body><h1>{title}</h1><p>{body}</p></body></html>"
    )


def _epub(files: dict[str, str], manifest: list[tuple[str, str, str, str]], spine: list[str], spine_toc: str = "") -> bytes:
    """``manifest`` rows are (id, href, media-type, properties)."""
    items = "".join(
        f'<item id="{i}" href="{h}" media-type="{m}"' + (f' properties="{p}"' if p else "") + "/>"
        for i, h, m, p in manifest
    )
    refs = "".join(f'<itemref idref="{i}"/>' for i in spine)
    toc_attr = f' toc="{spine_toc}"' if spine_toc else ""
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>'
        f"<manifest>{items}</manifest><spine{toc_attr}>{refs}</spine></package>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr("OEBPS/content.opf", opf)
        for name, body in files.items():
            z.writestr(f"OEBPS/{name}", body)
    return buf.getvalue()


def nav_epub() -> bytes:
    """EPUB 3: cover, copyright, dedication, introduction, then five untitled-number chapters."""
    front = [("cover", "Cover", "A picture."), ("copyright", "Copyright", "All rights reserved 2001."),
             ("dedication", "Dedication", "For my sons."), ("intro", "Introduction", "Every man was once a boy.")]
    files = {f"{n}.xhtml": _xhtml(t, b) for n, t, b in front}
    for i, title in enumerate(TITLES, 1):
        files[f"ch{i}.xhtml"] = _xhtml(title, f"Body of chapter {i}. " * 20)
    links = "".join(f'<li><a href="{n}.xhtml">{t}</a></li>' for n, t, _b in front)
    links += "".join(f'<li><a href="ch{i}.xhtml">{t}</a></li>' for i, t in enumerate(TITLES, 1))
    links += '<li><a href="toc.xhtml">Contents</a></li>'
    files["toc.xhtml"] = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        '<head><title>Contents</title></head><body>'
        '<nav epub:type="landmarks"><ol><li><a href="cover.xhtml">Cover</a></li></ol></nav>'
        f'<nav epub:type="toc"><h1>Contents</h1><ol>{links}</ol></nav></body></html>'
    )
    ids = [n for n, _t, _b in front] + [f"ch{i}" for i in range(1, 6)]
    manifest = [("nav", "toc.xhtml", "application/xhtml+xml", "nav")]
    manifest += [(i, f"{i}.xhtml", "application/xhtml+xml", "") for i in ids]
    return _epub(files, manifest, ["cover", "copyright", "nav", "dedication", "intro", *ids[4:]])


def ncx_epub() -> bytes:
    """EPUB 2: only toc.ncx; chapters 1-3 share one file, split by anchors; chapters 4-5 nested in a Part."""
    one = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<h2 id="c1">Chapter One: Wild at Heart</h2><p>' + "First chapter words. " * 10 + "</p>"
        '<h2 id="c2">Chapter Two: The Wild One</h2><p>' + "Second chapter words. " * 10 + "</p>"
        '<h2 id="c3">Chapter Three: The Question</h2><p>' + "Third chapter words. " * 10 + "</p>"
        "</body></html>"
    )
    two = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Part Two</h1>'
        '<h2 id="c4">Chapter Four: The Wound</h2><p>' + "Fourth chapter words. " * 10 + "</p>"
        '<h2 id="c5">Chapter Five: The Battle</h2><p>' + "Fifth chapter words. " * 10 + "</p></body></html>"
    )

    def point(label: str, src: str, children: str = "") -> str:
        return f"<navPoint><navLabel><text>{label}</text></navLabel><content src=\"{src}\"/>{children}</navPoint>"

    ncx = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
        + point("Title Page", "title.xhtml")
        + point("Chapter One: Wild at Heart", "text/one.xhtml#c1")
        + point("Chapter Two: The Wild One", "text/one.xhtml#c2")
        + point("Chapter Three: The Question", "text/one.xhtml#c3")
        + point("Part Two", "text/two.xhtml",
                point("Chapter Four: The Wound", "text/two.xhtml#c4") + point("Chapter Five: The Battle", "text/two.xhtml#c5"))
        + "</navMap></ncx>"
    )
    files = {"title.xhtml": _xhtml("Wild at Heart", "John Eldredge"), "text/one.xhtml": one, "text/two.xhtml": two, "toc.ncx": ncx}
    manifest = [
        ("ncx", "toc.ncx", "application/x-dtbncx+xml", ""),
        ("title", "title.xhtml", "application/xhtml+xml", ""),
        ("one", "text/one.xhtml", "application/xhtml+xml", ""),
        ("two", "text/two.xhtml", "application/xhtml+xml", ""),
    ]
    return _epub(files, manifest, ["title", "one", "two"], spine_toc="ncx")


WORDS = ["ONE", "TWO", "THREE", "FOUR", "FIVE"]


def plain_text() -> str:
    contents = "CONTENTS\n\n" + "\n".join(f"CHAPTER {w}" for w in WORDS)
    chapters = "\n\n".join(
        f"CHAPTER {w}\n\n{TITLES[i]}\n\n" + f"Prose of chapter {i + 1}. " * 30 for i, w in enumerate(WORDS)
    )
    return f"Wild at Heart\n\nCopyright 2001 by the author. All rights reserved.\n\n{contents}\n\n{chapters}\n"


def outline_pdf() -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    texts = ["Copyright page", "Contents page", *[f"Chapter page {i}" for i in range(1, 6)], "Chapter five continues"]
    for text in texts:
        page = writer.add_blank_page(612, 792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_outline_item("Copyright", 0)
    writer.add_outline_item("Contents", 1)
    for i, title in enumerate(TITLES):
        writer.add_outline_item(title, 2 + i)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _numbered(chapters):
    return [(c.number, c.title) for c in chapters if c.number is not None]


# --- Listing ---


def test_nav_epub_lists_front_matter_unnumbered_and_counts_from_the_authors_chapter_one():
    source, chapters = epub_chapters(nav_epub())
    assert source == "epub-nav"
    assert _numbered(chapters) == list(enumerate(TITLES, 1))
    front = [c.label for c in chapters if c.matter]
    assert front == ["Cover", "Copyright", "Dedication", "Introduction"]
    assert "Body of chapter 4" in chapters[find_chapter(chapters, 4)].text
    assert "Body of chapter 3" not in chapters[find_chapter(chapters, 4)].text


def test_ncx_only_epub_splits_at_anchors_and_reads_chapters_inside_a_part():
    source, chapters = epub_chapters(ncx_epub())
    assert source == "epub-ncx"
    assert _numbered(chapters) == [(1, "Wild at Heart"), (2, "The Wild One"), (3, "The Question"), (4, "The Wound"), (5, "The Battle")]
    four = chapters[find_chapter(chapters, "four")]
    assert "Fourth chapter words." in four.text
    assert "Third chapter words." not in four.text and "Fifth chapter words." not in four.text
    assert [c.label for c in chapters if c.number is None] == ["Title Page", "Part Two"]


def test_text_headings_in_words_number_the_chapters_and_ignore_the_contents_page():
    chapters = text_chapters(plain_text())
    assert _numbered(chapters) == list(enumerate(TITLES, 1))
    assert chapters[0].matter and "Copyright" in chapters[0].text
    four = chapters[find_chapter(chapters, 4)]
    assert "Prose of chapter 4." in four.text and "Prose of chapter 5." not in four.text


def test_text_headings_as_digits_and_roman_numerals():
    digits = "\n".join(f"{i}. {t}\n" + "Some text here. " * 5 for i, t in enumerate(TITLES, 1))
    assert _numbered(text_chapters(digits)) == list(enumerate(TITLES, 1))
    roman = "\n".join(f"{r}\n{t}\n" + "Some text here. " * 5 for r, t in zip(["I", "II", "III", "IV", "V"], TITLES, strict=True))
    assert _numbered(text_chapters(roman)) == list(enumerate(TITLES, 1))


def test_pdf_outline_gives_the_chapters_and_their_pages():
    source, chapters = pdf_chapters(outline_pdf())
    assert source == "pdf-outline"
    assert _numbered(chapters) == list(enumerate(TITLES, 1))
    assert [c.label for c in chapters if c.matter] == ["Copyright", "Contents"]
    assert "Chapter page 4" in chapters[find_chapter(chapters, "IV")].text
    last = chapters[find_chapter(chapters, 5)].text
    assert "Chapter page 5" in last and "Chapter five continues" in last


# --- Choosing one ---


@pytest.mark.parametrize("ref", [4, "4", "four", "Four", "fourth", "IV", "iv", "chapter 4", "Chapter Four", "4th"])
def test_chapter_four_by_number_word_and_roman_numeral(ref):
    _source, chapters = epub_chapters(nav_epub())
    assert chapters[find_chapter(chapters, ref)].title == "The Wound"


@pytest.mark.parametrize("ref", ["The Wound", "the wound", "wound", "The Wond"])
def test_a_chapter_by_its_title_loosely(ref):
    _source, chapters = epub_chapters(nav_epub())
    assert chapters[find_chapter(chapters, ref)].number == 4


def test_front_matter_is_reachable_by_name_but_never_counted():
    _source, chapters = epub_chapters(nav_epub())
    intro = chapters[find_chapter(chapters, "introduction")]
    assert intro.matter and intro.number is None
    with pytest.raises(ChapterError, match=r"no chapter 9.*1 to 5"):
        find_chapter(chapters, 9)


def test_parse_chapter_ref_reads_every_spelling():
    assert [parse_chapter_ref(r) for r in ("12", "twelve", "XII", "chapter twenty-one", "the fourth chapter")] == [12, 12, 12, 21, 4]
    assert parse_chapter_ref("The Wound") is None


def test_a_long_chapter_splits_into_parts_at_paragraphs():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 100 for i in range(30))
    parts = split_parts(text, 2000)
    assert len(parts) > 1 and all(len(p) <= 2000 for p in parts)
    assert "\n\n".join(parts) == text
    assert split_parts("short", 2000) == ["short"]


# --- The storage route ---

BOOK = {"id": 7, "title": "Wild at Heart", "sort": "Wild at Heart", "authors": ["John Eldredge"], "tags": [],
        "series": "", "series_index": None, "formats": ["EPUB"], "path": "John Eldredge/Wild at Heart (7)",
        "files": [{"path": "/Books/Text/John Eldredge/Wild at Heart (7)/Wild at Heart - John Eldredge.epub", "size": 1}]}

H = {"X-Internal-Secret": "test-secret"}


def _fake_file(monkeypatch, data: bytes, fmt: str = "EPUB"):
    async def book_file(library_path, book_id):
        assert book_id == 7
        return BOOK, fmt, data

    monkeypatch.setattr(library_api, "_book_file", book_file)


def test_the_route_lists_chapters_without_text(monkeypatch):
    from services.storage.main import app

    _fake_file(monkeypatch, nav_epub())
    body = TestClient(app).post("/calibre/books/7/chapters", json={"library_path": "/Books/Text"}, headers=H).json()
    assert body["source"] == "epub-nav" and body["chapter"] is None
    assert [c["number"] for c in body["chapters"] if c["number"]] == [1, 2, 3, 4, 5]
    assert all("text" not in c for c in body["chapters"])


def test_the_route_returns_one_chapter_in_parts(monkeypatch):
    from services.storage.main import app

    long_chapter = _epub(
        {"a.xhtml": _xhtml("Chapter 1: Short", "x " * 50),
         "b.xhtml": "<html><body><h1>Chapter 2: Long</h1>" + "".join(f"<p>Para {i} " + "word " * 100 + "</p>" for i in range(20)) + "</body></html>",
         "nav.xhtml": '<html><body><nav epub:type="toc"><ol><li><a href="a.xhtml">Chapter 1: Short</a></li>'
                      '<li><a href="b.xhtml">Chapter 2: Long</a></li></ol></nav></body></html>'},
        [("nav", "nav.xhtml", "application/xhtml+xml", "nav"), ("a", "a.xhtml", "application/xhtml+xml", ""),
         ("b", "b.xhtml", "application/xhtml+xml", "")],
        ["a", "b"],
    )
    _fake_file(monkeypatch, long_chapter)
    app_client = TestClient(app)
    first = app_client.post("/calibre/books/7/chapters", json={"library_path": "/B", "chapter": "two", "max_chars": 3000}, headers=H).json()
    chapter = first["chapter"]
    assert chapter["number"] == 2 and chapter["title"] == "Long"
    assert chapter["part"] == 1 and chapter["parts"] >= 3 and len(chapter["text"]) <= 3000
    second = app_client.post("/calibre/books/7/chapters", json={"library_path": "/B", "chapter": "2", "part": 2, "max_chars": 3000}, headers=H).json()
    assert second["chapter"]["part"] == 2 and second["chapter"]["text"] != chapter["text"]
    beyond = app_client.post("/calibre/books/7/chapters", json={"library_path": "/B", "chapter": "2", "part": 99, "max_chars": 3000}, headers=H)
    assert beyond.status_code == 422 and "only" in beyond.json()["detail"]
    missing = app_client.post("/calibre/books/7/chapters", json={"library_path": "/B", "chapter": "9"}, headers=H)
    assert missing.status_code == 404 and "no chapter 9" in missing.json()["detail"]


def test_a_format_without_a_chapter_reader_is_refused():
    with pytest.raises(ChapterError, match="MOBI"):
        book_chapters.read_book_chapters(b"x", "MOBI")
