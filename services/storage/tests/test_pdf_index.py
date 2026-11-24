"""PDFs are indexed by their text: Calibre books stored only as PDF, and Nextcloud PDFs.

Every PDF here is built in the test with pypdf; no household file is used.
A PDF with bookmarks becomes one section per chapter, titled by the bookmark;
one without becomes one section per page ("Page 3"); a scan with no text layer
keeps its metadata row and the log says it has no text layer.
"""
from __future__ import annotations

import asyncio
import io
import logging
import sqlite3

import pytest

from services.storage import book_chapters
from services.storage.indexer import (
    CheckpointManager,
    build_content_index,
    extract_and_chunk_contents,
)
from services.storage.models import StorageEntry
from services.storage.providers_impl.calibre import CalibreStorageProvider
from services.storage.providers_impl.nextcloud import NextcloudStorageProvider

LIBRARY = "/Books/Text"
SETTINGS = {"url": "https://cloud.example", "username": "rivera", "password": "secret"}


def make_pdf(pages: list[str], outline: list[tuple[str, int]] = ()) -> bytes:
    """A PDF with one line of text per page ("" = an image-only page) and optional bookmarks."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for text in pages:
        page = writer.add_blank_page(612, 792)
        if not text:
            continue
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    for title, page_index in outline:
        writer.add_outline_item(title, page_index)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


BOOKMARKED = make_pdf(
    ["Title page words", "Chapter one opening", "More of chapter one", "Chapter two opening", "Chapter three opening"],
    outline=[("The Call", 1), ("The Wilderness", 3), ("The Return", 4)],
)
PLAIN = make_pdf(["First page prose", "", "Third page prose"])
SCANNED = make_pdf(["", "", ""])


# --- The reader (book_chapters.pdf_sections) ----------------------------------


def test_bookmarks_become_chapters_titled_by_the_bookmark():
    source, sections = book_chapters.pdf_sections(BOOKMARKED)
    assert source == "pdf-outline"
    assert [s.label for s in sections] == ["Page 1", "The Call", "The Wilderness", "The Return"]
    assert sections[0].matter, "the pages before the first bookmark are kept, unnumbered"
    call = sections[1]
    assert "Chapter one opening" in call.text and "More of chapter one" in call.text
    assert "Chapter two opening" not in call.text
    assert [s.number for s in sections[1:]] == [1, 2, 3]


def test_no_bookmarks_gives_one_section_per_page_with_its_number():
    source, sections = book_chapters.pdf_sections(PLAIN)
    assert source == "pdf-pages"
    assert [(s.label, s.text) for s in sections] == [("Page 1", "First page prose"), ("Page 3", "Third page prose")]


def test_a_scan_with_no_text_layer_says_so():
    with pytest.raises(book_chapters.PdfNoTextLayer, match="no text layer on any of its 3 pages"):
        book_chapters.pdf_sections(SCANNED)


def test_reading_stops_at_the_character_budget():
    pdf = make_pdf([f"Page {i} " + "word " * 40 for i in range(1, 51)])
    doc = book_chapters.read_pdf_pages(pdf, max_chars=500)
    assert doc.truncated and doc.page_count == 50
    assert sum(len(p) for p in doc.pages) <= 500
    assert len(doc.pages) < 5, "pages past the budget are never extracted"
    _, sections = book_chapters.pdf_sections(pdf, max_chars=500)
    assert sections and sections[-1].label.startswith("Page ")


def test_not_a_pdf_is_a_chapter_error_not_a_crash():
    with pytest.raises(book_chapters.ChapterError):
        book_chapters.pdf_sections(b"not a pdf at all")


# --- The Calibre shelf ---------------------------------------------------------


def _catalog(formats: list[tuple[str, str]]) -> bytes:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, path TEXT, author TEXT, pubdate TEXT);
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
        CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, uncompressed_size INTEGER, name TEXT);
        """
    )
    con.execute("INSERT INTO books VALUES (7, 'Desert Fathers', 'Desert Fathers', 'Anon/Desert Fathers (7)', 'Anon', '1900')")
    for i, (fmt, name) in enumerate(formats, start=1):
        con.execute("INSERT INTO data VALUES (?, 7, ?, 1000, ?)", (i, fmt, name))
    con.commit()
    blob = con.serialize()
    con.close()
    return blob


class FakeClient:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.requested: list[str] = []

    async def get_file_bytes(self, remote_path, timeout=None):
        self.requested.append(remote_path)
        return self.files.get(remote_path)

    async def get_file_content(self, remote_path):
        self.requested.append(remote_path)
        data = self.files.get(remote_path)
        return None if data is None else data.decode("utf-8", errors="replace")

    async def list_entries(self, path="/", recursive=False):
        return []

    async def close(self):
        pass


def _calibre(pdf: bytes, **settings) -> CalibreStorageProvider:
    provider = CalibreStorageProvider({**SETTINGS, "library_path": LIBRARY, **settings})
    provider.client = FakeClient({
        f"{LIBRARY}/metadata.db": _catalog([("PDF", "Desert Fathers - Anon")]),
        f"{LIBRARY}/Anon/Desert Fathers (7)/Desert Fathers - Anon.pdf": pdf,
    })
    return provider


BOOK = f"{LIBRARY}/Anon/Desert Fathers (7).txt"


def test_a_pdf_only_book_with_bookmarks_is_indexed_by_chapter():
    provider = _calibre(BOOKMARKED)
    entries = asyncio.run(provider.list_entries("/"))
    chunks = asyncio.run(extract_and_chunk_contents(provider, build_content_index(entries)))
    body = [c for c in chunks if not c["metadata"].get("is_metadata")]
    assert [c["metadata"]["chapter"] for c in body] == ["Page 1", "The Call", "The Wilderness", "The Return"]
    assert "Chapter two opening" in body[2]["content"]
    assert all(c["metadata"]["title"] == "Desert Fathers" for c in body)
    assert any(c["metadata"].get("is_metadata") for c in chunks), "the metadata row is still there"


def test_a_pdf_only_book_without_bookmarks_is_indexed_by_page():
    provider = _calibre(PLAIN)
    sections = asyncio.run(provider.get_sections(BOOK))
    assert [(s.ordinal, s.label) for s in sections] == [(1, "Page 1"), (2, "Page 3")]
    assert asyncio.run(provider.get_content(BOOK)) == "First page prose\n\nThird page prose"
    assert asyncio.run(provider.get_bytes(BOOK)) == b"First page prose\n\nThird page prose"


def test_a_scanned_pdf_book_is_metadata_only_and_the_log_says_why(caplog):
    provider = _calibre(SCANNED)
    entries = asyncio.run(provider.list_entries("/"))
    with caplog.at_level(logging.WARNING):
        chunks = asyncio.run(extract_and_chunk_contents(provider, build_content_index(entries)))
    assert len(chunks) == 1 and chunks[0]["metadata"]["is_metadata"]
    assert "Desert Fathers" in chunks[0]["content"]
    said = caplog.text
    assert "no text layer" in said and "Desert Fathers" in said
    assert "no text extractor" not in said
    pdf_reads = [p for p in provider.client.requested if p.endswith(".pdf")]
    assert len(pdf_reads) == 1, "a scan is downloaded and parsed once, not again for get_content"


def test_the_pdf_text_budget_is_a_setting():
    pdf = make_pdf([f"Page {i} " + "word " * 40 for i in range(1, 30)])
    provider = _calibre(pdf, pdf_max_chars=600)
    text = asyncio.run(provider.get_content(BOOK))
    assert 0 < len(text) <= 700


def test_an_epub_book_is_read_as_before():
    from services.storage.tests.test_calibre_provider import _epub

    provider = CalibreStorageProvider({**SETTINGS, "library_path": LIBRARY})
    provider.client = FakeClient({
        f"{LIBRARY}/metadata.db": _catalog([("EPUB", "Desert Fathers - Anon"), ("PDF", "Desert Fathers - Anon")]),
        f"{LIBRARY}/Anon/Desert Fathers (7)/Desert Fathers - Anon.epub": _epub(),
        f"{LIBRARY}/Anon/Desert Fathers (7)/Desert Fathers - Anon.pdf": BOOKMARKED,
    })
    sections = asyncio.run(provider.get_sections(BOOK))
    assert any("Some prose for the day." in s.text for s in sections)
    assert not any(p.endswith(".pdf") for p in provider.client.requested)


# --- Nextcloud files (the nextcloud_files collection) ---------------------------


def _nextcloud(files: dict[str, bytes]) -> NextcloudStorageProvider:
    provider = NextcloudStorageProvider(dict(SETTINGS))
    provider.client = FakeClient(files)
    return provider


def _entry(path: str, content_type: str = "application/pdf") -> StorageEntry:
    return StorageEntry(path=path, name=path.rsplit("/", 1)[-1], is_dir=False, size=1000,
                        mtime="Mon, 01 Jan 2026 00:00:00 GMT", content_type=content_type)


def test_a_nextcloud_pdf_is_indexed_by_its_chapters():
    provider = _nextcloud({"/Documents/Guide.pdf": BOOKMARKED})
    chunks = asyncio.run(extract_and_chunk_contents(provider, build_content_index([_entry("/Documents/Guide.pdf")])))
    body = [c for c in chunks if not c["metadata"].get("is_metadata")]
    assert [c["metadata"]["chapter"] for c in body] == ["Page 1", "The Call", "The Wilderness", "The Return"]
    assert all("\ufffd" not in c["content"] and "%PDF" not in c["content"] for c in body)


def test_a_nextcloud_pdf_without_bookmarks_is_indexed_by_page():
    provider = _nextcloud({"/Documents/Notes.pdf": PLAIN})
    chunks = asyncio.run(extract_and_chunk_contents(provider, build_content_index([_entry("/Documents/Notes.pdf")])))
    body = [c for c in chunks if not c["metadata"].get("is_metadata")]
    assert [(c["metadata"]["chapter"], c["content"]) for c in body] == [
        ("Page 1", "First page prose"), ("Page 3", "Third page prose"),
    ]


def test_a_scanned_nextcloud_pdf_keeps_only_its_metadata_and_is_never_decoded(caplog):
    provider = _nextcloud({"/Scans/Receipt.pdf": SCANNED})
    with caplog.at_level(logging.WARNING):
        chunks = asyncio.run(extract_and_chunk_contents(provider, build_content_index([_entry("/Scans/Receipt.pdf")])))
    assert len(chunks) == 1 and chunks[0]["metadata"]["is_metadata"]
    assert "no text layer" in caplog.text and "/Scans/Receipt.pdf" in caplog.text
    assert provider.client.requested == ["/Scans/Receipt.pdf"], "read once as bytes, never as text"


def test_nextcloud_text_files_are_read_as_text_as_before():
    provider = _nextcloud({"/Notes/todo.md": b"# Todo\nbuy bread"})
    assert asyncio.run(provider.get_sections("/Notes/todo.md")) is None
    assert asyncio.run(provider.get_content("/Notes/todo.md")) == "# Todo\nbuy bread"


def test_a_pdf_checkpointed_before_pdfs_were_read_is_read_once_more(tmp_path):
    """A checkpoint holding only the mtime was written when PDFs were metadata-only."""
    path = "/Documents/Guide.pdf"
    checkpoint = CheckpointManager(str(tmp_path / "checkpoint.json"))
    checkpoint.mark_indexed(path, "Mon, 01 Jan 2026 00:00:00 GMT")
    provider = _nextcloud({path: BOOKMARKED})
    items = build_content_index([_entry(path)])
    first = asyncio.run(extract_and_chunk_contents(provider, items, checkpoint=checkpoint))
    assert any(not c["metadata"].get("is_metadata") for c in first)
    again = asyncio.run(extract_and_chunk_contents(provider, items, checkpoint=checkpoint))
    assert again == [], "after one read, an unchanged PDF is skipped again"
