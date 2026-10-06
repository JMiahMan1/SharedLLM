import asyncio
import io
import sqlite3
import zipfile

import pytest
from pydantic import ValidationError

from services.storage.models import ProviderConfig, StorageEntry
from services.storage.providers import StorageProvider, build_provider
from services.storage.providers_impl.calibre import (
    CalibreLibraryError,
    CalibreStorageProvider,
    _parse_catalog,
)

_BOOKS_SQL = """
CREATE TABLE books (
    id INTEGER PRIMARY KEY,
    title TEXT,
    sort TEXT,
    path TEXT,
    series TEXT,
    series_index REAL,
    pubdate TEXT
)
"""
_AUTHORS_SQL = "CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT)"
_BOOKS_AUTHORS_SQL = "CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER)"
_TAGS_SQL = "CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT)"
_BOOKS_TAGS_SQL = "CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER)"
_DATA_SQL = "CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, uncompressed_size INTEGER, name TEXT)"


def _epub(title="A Book", heading="1st Day.", body="Some prose for the day."):
    """A minimal but genuinely readable EPUB, spine-first."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{title}</dc:title></metadata>"
            '<manifest><item id="cover" href="cover.jpg" media-type="image/jpeg"/>'
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>',
        )
        zf.writestr("OEBPS/cover.jpg", b"\xff\xd8\xff not really an image")
        zf.writestr(
            "OEBPS/c1.xhtml",
            f"<html><body><h1>{title}</h1><p>Opening words of the book.</p>"
            f"<h2>{heading}</h2><p>{body}</p></body></html>",
        )
    return buf.getvalue()


def _make_db(books, authors=((1, "John R. Macduff"),), tags=((1, "Devotional"),), extra_sql=True):
    """Build a Calibre-shaped metadata.db.

    ``books`` rows are ``(id, title, path, series, series_index, author_ids, tag_ids, formats)``
    where each format is ``(FORMAT, data_name, size)``.
    """
    conn = sqlite3.connect(":memory:")
    for statement in (_BOOKS_SQL, _AUTHORS_SQL, _DATA_SQL):
        conn.executescript(statement)
    if extra_sql:
        for statement in (_BOOKS_AUTHORS_SQL, _TAGS_SQL, _BOOKS_TAGS_SQL):
            conn.executescript(statement)
    for aid, name in authors:
        conn.execute("INSERT INTO authors (id, name) VALUES (?, ?)", (aid, name))
    for tid, name in tags:
        conn.execute("INSERT INTO tags (id, name) VALUES (?, ?)", (tid, name))
    for (bid, title, path, series, sidx, author_ids, tag_ids, formats) in books:
        conn.execute(
            "INSERT INTO books (id, title, path, series, series_index, pubdate) VALUES (?,?,?,?,?,?)",
            (bid, title, path, series, sidx, "2010-02-17 00:00:00+00:00"),
        )
        for order, aid in enumerate(author_ids or []):
            conn.execute(
                "INSERT INTO books_authors_link (book, author) VALUES (?,?)", (bid, aid)
            )
        for tid in tag_ids or []:
            conn.execute("INSERT INTO books_tags_link (book, tag) VALUES (?,?)", (bid, tid))
        for fmt, name, size in formats:
            conn.execute(
                "INSERT INTO data (book, format, uncompressed_size, name) VALUES (?,?,?,?)",
                (bid, fmt, size, name),
            )
    raw = conn.serialize() if hasattr(conn, "serialize") else None
    if raw is None:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
            path = fh.name
        conn.commit()
        import os

        conn.close()
        data = open(path, "rb").read()
        os.unlink(path)
        return data
    conn.commit()
    conn.close()
    return raw


class FakeClient:
    """Serves bytes by remote path, and records what was asked for."""

    def __init__(self, files):
        self.files = files
        self.requested = []
        self.closed = False

    async def get_file_bytes(self, remote_path, timeout=None):
        self.requested.append(remote_path)
        return self.files.get(remote_path)

    async def close(self):
        self.closed = True


def _provider(files, library_path="/Books/Text", **extra):
    settings = {
        "url": "https://cloud.example",
        "username": "summers",
        "password": "secret",
        "library_path": library_path,
    }
    settings.update(extra)
    provider = CalibreStorageProvider(settings)
    provider.client = FakeClient(files)
    return provider


def _standard_files(book_bytes=None):
    book_bytes = book_bytes if book_bytes is not None else _epub()
    db = _make_db(
        [
            (617, "The Faithful Promiser", "John R. Macduff/The Faithful Promiser (617)", "", None, [1], [1],
             [("EPUB", "The Faithful Promiser - John R. Macduff", 43521)]),
        ]
    )
    return {
        "/Books/Text/metadata.db": db,
        "/Books/Text/John R. Macduff/The Faithful Promiser (617)/The Faithful Promiser - John R. Macduff.epub": book_bytes,
    }


def test_catalog_parses_books_authors_tags_and_formats():
    catalog = _parse_catalog(_make_db([
        (1, "T", "A/T (1)", "", None, [1], [1], [("EPUB", "T - A", 10)]),
    ]), "/Books/Text")
    assert list(catalog) == [1]
    book = catalog[1]
    assert book["title"] == "T"
    assert book["author"] == "John R. Macduff"
    assert book["tags"] == ["Devotional"]
    assert book["formats"] == ["EPUB"]


def test_data_name_carries_no_extension_so_the_path_adds_it():
    catalog = _parse_catalog(_make_db([
        (1, "T", "A/T (1)", "", None, [1], [], [("EPUB", "T - A", 10)]),
    ]), "/Books/Text")
    assert catalog[1]["text_path"] == "/Books/Text/A/T (1)/T - A.epub"


def test_extension_case_follows_the_format_not_the_name():
    catalog = _parse_catalog(_make_db([
        (1, "T", "A/T (1)", "", None, [1], [], [("EPUB", "T - A", 10)]),
    ]), "/Books/Text")
    assert catalog[1]["text_path"].endswith(".epub")


def test_a_pdf_only_book_is_metadata_only_not_a_format_we_cannot_read():
    catalog = _parse_catalog(_make_db([
        (1, "Scanned", "A/Scanned (1)", "", None, [1], [], [("PDF", "Scanned - A", 10)]),
    ]), "/Books/Text")
    book = catalog[1]
    assert book["formats"] == ["PDF"]
    assert book["text_format"] is None
    assert book["text_path"] is None


def test_epub_is_preferred_when_a_book_also_has_docx():
    catalog = _parse_catalog(_make_db([
        (1, "Both", "A/Both (1)", "", None, [1], [],
         [("DOCX", "Both - A", 10), ("EPUB", "Both - A", 10)]),
    ]), "/Books/Text")
    assert catalog[1]["text_format"] == "EPUB"
    assert catalog[1]["text_path"].endswith(".epub")


def test_a_trimmed_database_without_the_tag_tables_still_parses():
    db = _make_db([(1, "T", "A/T (1)", "", None, [1], [1], [("EPUB", "T - A", 10)])])
    conn = sqlite3.connect(":memory:")
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    open(path, "wb").write(db)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE books_tags_link")
    conn.execute("DROP TABLE tags")
    conn.commit()
    trimmed = conn.serialize()
    conn.close()
    import os

    os.unlink(path)

    catalog = _parse_catalog(trimmed, "/Books/Text")
    assert catalog[1]["author"] == "John R. Macduff"
    assert catalog[1]["tags"] == []
    assert catalog[1]["text_format"] == "EPUB"


def test_a_database_with_no_books_table_is_refused_rather_than_read_as_empty():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    raw = conn.serialize()
    conn.close()
    with pytest.raises(CalibreLibraryError, match="not a Calibre library"):
        _parse_catalog(raw, "/Books/Text")


def test_series_and_multiple_authors_reach_the_metadata():
    catalog = _parse_catalog(
        _make_db(
            [(1, "T", "A/T (1)", "A Series", 2.0, [1, 2], [], [("EPUB", "T - A", 10)])],
            authors=((1, "First Author"), (2, "Second Author")),
        ),
        "/Books/Text",
    )
    meta = catalog[1]["metadata"]
    assert meta["author"] == "First Author"
    assert meta["authors"] == "First Author; Second Author"
    assert meta["series"] == "A Series"
    assert meta["series_index"] == 2.0


def test_metadata_is_all_scalars_because_rag_stringifies_the_rest():
    catalog = _parse_catalog(
        _make_db([(1, "T", "A/T (1)", "", None, [1], [1, 2], [("EPUB", "T - A", 10)])],
                 tags=((1, "One"), (2, "Two"))),
        "/Books/Text",
    )
    meta = catalog[1]["metadata"]
    assert all(isinstance(v, (str, int, float, bool)) for v in meta.values())
    assert meta["tags"] == "One; Two"


def test_series_index_is_omitted_when_there_is_no_series():
    catalog = _parse_catalog(
        _make_db([(1, "T", "A/T (1)", "", 1.0, [1], [], [("EPUB", "T - A", 10)])]),
        "/Books/Text",
    )
    assert "series" not in catalog[1]["metadata"]
    assert "series_index" not in catalog[1]["metadata"]


def test_list_entries_synthesises_one_text_path_per_book():
    provider = _provider(_standard_files())
    entries = asyncio.run(provider.list_entries("/"))
    assert len(entries) == 1
    entry = entries[0]
    assert entry.path == "/Books/Text/John R. Macduff/The Faithful Promiser (617).txt"
    assert entry.is_dir is False
    assert entry.metadata["calibre_id"] == 617
    assert entry.metadata["title"] == "The Faithful Promiser"


def test_list_entries_can_be_scoped_to_one_author_folder():
    provider = _provider(_standard_files())
    assert len(asyncio.run(provider.list_entries("/Books/Text/John R. Macduff"))) == 1
    assert asyncio.run(provider.list_entries("/Books/Text/Somebody Else")) == []


def test_a_non_ascii_author_survives_into_the_synthesised_path():
    db = _make_db(
        [(1, "T", "Macduff’s Works/T (1)", "", None, [1], [], [("EPUB", "T - A", 10)])],
        authors=((1, "Macduff"),),
    )
    files = {"/Books/Text/metadata.db": db}
    entries = asyncio.run(_provider(files).list_entries("/"))
    assert entries[0].path == "/Books/Text/Macduff’s Works/T (1).txt"


def test_get_sections_returns_labelled_sections():
    provider = _provider(_standard_files())
    sections = asyncio.run(provider.get_sections("/Books/Text/John R. Macduff/The Faithful Promiser (617).txt"))
    assert sections is not None
    assert [s.label for s in sections] == ["A Book", "1st Day."]
    assert all(s.ordinal >= 1 for s in sections)
    assert "Some prose for the day." in sections[1].text


def test_get_content_joins_the_section_bodies():
    provider = _provider(_standard_files())
    text = asyncio.run(provider.get_content("/Books/Text/John R. Macduff/The Faithful Promiser (617).txt"))
    assert "Some prose for the day." in text
    assert "\n\n" in text


def test_a_path_without_a_book_id_is_not_treated_as_a_book_locator():
    provider = _provider(_standard_files())
    assert asyncio.run(provider.get_sections("/Books/Text/some/other/file.txt")) is None
    assert asyncio.run(provider.get_content("/Books/Text/some/other/file.txt")) is None


def test_a_pdf_only_book_yields_no_sections_but_still_appears_as_an_entry():
    db = _make_db([(1, "Scanned", "A/Scanned (1)", "", None, [1], [], [("PDF", "Scanned - A", 10)])])
    provider = _provider({"/Books/Text/metadata.db": db})
    assert len(asyncio.run(provider.list_entries("/"))) == 1
    assert asyncio.run(provider.get_sections("/Books/Text/A/Scanned (1).txt")) is None


def test_a_corrupt_epub_yields_no_sections_rather_than_an_empty_book():
    files = _standard_files(book_bytes=b"this is not a zip file")
    provider = _provider(files)
    assert asyncio.run(provider.get_sections("/Books/Text/John R. Macduff/The Faithful Promiser (617).txt")) is None


def test_writing_to_the_library_is_refused_with_a_reason():
    provider = _provider(_standard_files())
    result = asyncio.run(provider.write_content("/Books/Text/x.txt", "nope"))
    assert result["status"] == "FAILURE"
    assert "read-only" in result["message"]
    assert "metadata.db" in result["message"]


def test_an_unreachable_metadata_db_names_the_path_it_tried():
    provider = _provider({}, library_path="/Books/Text")
    with pytest.raises(CalibreLibraryError, match="/Books/Text/metadata.db"):
        asyncio.run(provider.list_entries("/"))


def test_the_catalogue_is_downloaded_once_per_index_run_not_once_per_book():
    provider = _provider(_standard_files())
    asyncio.run(provider.list_entries("/"))
    assert provider.client.requested.count("/Books/Text/metadata.db") == 1

    for _ in range(5):
        asyncio.run(provider.get_content("/Books/Text/John R. Macduff/The Faithful Promiser (617).txt"))
    assert provider.client.requested.count("/Books/Text/metadata.db") == 1

    asyncio.run(provider.list_entries("/"))
    assert provider.client.requested.count("/Books/Text/metadata.db") == 2


def test_a_single_file_read_without_a_prior_listing_still_loads_the_catalogue():
    provider = _provider(_standard_files())
    sections = asyncio.run(provider.get_sections("/Books/Text/John R. Macduff/The Faithful Promiser (617).txt"))
    assert [s.label for s in sections] == ["A Book", "1st Day."]


def test_build_provider_builds_calibre_from_the_nextcloud_credentials():
    from services.config import NEXTCLOUD_USER

    provider = build_provider(ProviderConfig(kind="calibre", settings={"library_path": "/Books/Text"}))
    assert isinstance(provider, CalibreStorageProvider)
    assert provider.library_path == "/Books/Text"
    assert provider.client.username == NEXTCLOUD_USER


def test_build_provider_refuses_a_calibre_provider_with_no_library_path():
    with pytest.raises(ValueError, match="library_path"):
        build_provider(ProviderConfig(kind="calibre", settings={}))


def test_the_provider_kind_literal_is_closed_so_an_unknown_source_fails_at_validation():
    with pytest.raises(ValidationError):
        ProviderConfig(kind="nonsense", settings={})


def test_the_base_provider_declines_to_imagine_structure():
    class Bare(StorageProvider):
        async def list_entries(self, path="/", recursive=False):
            return []

        async def get_content(self, path):
            return "text"

        async def write_content(self, path, content, create_parents=True, verify=True, is_binary=False):
            return {}

    assert asyncio.run(Bare().get_sections("/anything")) is None