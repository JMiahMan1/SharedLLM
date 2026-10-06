"""End-to-end: a Calibre library walked by the real indexer, chunked for RAG.

These exercise the whole Phase 1 path in the order the indexer runs it, so a
regression in any one seam -- the provider's synthetic paths, the metadata
threading, the section labels, the scalar flattening that ``/rag/sync/files``
depends on -- fails here rather than silently degrading citations in production.
"""

import io
import sqlite3
import zipfile

import pytest

from services.storage.indexer import build_content_index, extract_and_chunk_contents
from services.storage.models import ContentSection, ProviderConfig
from services.storage.providers import build_provider
from services.storage.providers_impl.calibre import CalibreStorageProvider

LIBRARY = "/Books/Text"


def _epub(body: str = "Body prose that is long enough to be worth indexing.") -> bytes:
    document = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        f"<h1>A Book</h1><p>{body}</p>"
        "<h2>1st Day.</h2><p>Second section prose, also long enough to index.</p>"
        "</body></html>"
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="2.0"><metadata/>'
        '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/c1.xhtml", document)
    return buf.getvalue()


def _db(books, formats) -> bytes:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                            path TEXT, author TEXT, pubdate TEXT,
                            last_modified TIMESTAMP);
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER,
                                         author INTEGER);
        CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT,
                           uncompressed_size INTEGER, name TEXT);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER);
        CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER,
                                        series INTEGER);
        """
    )
    for row in books:
        con.execute("INSERT INTO books VALUES (?,?,?,?,?,?,?)", row)
    for row in formats:
        con.execute("INSERT INTO data VALUES (?,?,?,?,?)", row)
    con.commit()
    blob = con.serialize()
    con.close()
    return blob


class FakeClient:
    """Serves the library the way a mounted Nextcloud directory would."""

    def __init__(self, catalog: bytes, epub: bytes | None):
        self.username = "summers"
        self.password = "secret"
        self.catalog = catalog
        self.epub = epub
        self.requested: list[str] = []

    async def get_file_bytes(self, remote_path, timeout=None):
        self.requested.append(remote_path)
        if remote_path.endswith("metadata.db"):
            return self.catalog
        if self.epub is None:
            return None
        return self.epub


class SectionOnlyCalibreProvider(CalibreStorageProvider):
    """Exercises the section path without re-reading the archive."""

    def __init__(self, settings):
        super().__init__(settings)
        self.sections: dict[str, list[ContentSection]] = {}

    async def get_sections(self, path):
        return self.sections.get(path)


def _provider(client, **overrides):
    settings = {
        "url": "https://cloud.example",
        "username": "summers",
        "password": "secret",
        "library_path": LIBRARY,
        **overrides,
    }
    provider = SectionOnlyCalibreProvider(settings)
    provider.client = client
    return provider


CATALOG = _db(
    books=[
        (617, "The Faithful Promiser", "Promiser, The Faithful",
         "John R. Macduff/The Faithful Promiser (617)",
         "John R. Macduff", "1894", None),
    ],
    formats=[(1, 617, "EPUB", 43521, "The Faithful Promiser - John R. Macduff")],
)


@pytest.mark.asyncio
async def test_a_book_becomes_labelled_chunks_carrying_its_bibliographic_metadata():
    client = FakeClient(CATALOG, _epub())
    provider = _provider(client)
    entries = await provider.list_entries(LIBRARY)

    assert [e.path for e in entries] == [
        "/Books/Text/John R. Macduff/The Faithful Promiser (617).txt"
    ]
    assert all(e.is_dir is False for e in entries)

    provider.sections = {entries[0].path: [
        ContentSection(ordinal=1, label="1st Day.", text="First day prose."),
        ContentSection(ordinal=2, label="2d Day.", text="Second day prose."),
    ]}

    items = build_content_index(entries)
    chunks = await extract_and_chunk_contents(provider, items)

    body = [c for c in chunks if not c["metadata"].get("is_metadata")]
    assert [c["metadata"]["chapter"] for c in body] == ["1st Day.", "2d Day."]
    assert [c["metadata"]["chapter_ordinal"] for c in body] == [1, 2]
    assert all(c["metadata"]["calibre_id"] == 617 for c in body)
    assert all(c["metadata"]["title"] == "The Faithful Promiser" for c in body)
    assert all(c["metadata"]["author"] == "John R. Macduff" for c in body)


@pytest.mark.asyncio
async def test_every_chunk_metadata_value_survives_the_rag_ingest_coercion():
    client = FakeClient(CATALOG, _epub())
    provider = _provider(client)
    entries = await provider.list_entries(LIBRARY)
    provider.sections = {
        entries[0].path: [ContentSection(ordinal=1, label="1st Day.", text="Prose.")]
    }

    chunks = await extract_and_chunk_contents(provider, build_content_index(entries))

    assert chunks
    for chunk in chunks:
        for key, value in chunk["metadata"].items():
            assert isinstance(value, (str, int, float, bool)), (
                f"{key}={value!r} would be coerced to str() by /rag/sync/files"
            )


@pytest.mark.asyncio
async def test_the_skeleton_row_names_the_book_so_an_author_search_can_match_it():
    client = FakeClient(CATALOG, _epub())
    provider = _provider(client)
    entries = await provider.list_entries(LIBRARY)

    chunks = await extract_and_chunk_contents(provider, build_content_index(entries))

    skeleton = [c for c in chunks if c["metadata"].get("is_metadata")]
    assert len(skeleton) == 1
    body = skeleton[0]["content"]
    assert "The Faithful Promiser" in body
    assert "John R. Macduff" in body
    assert "Calibre id: 617" in body


@pytest.mark.asyncio
async def test_a_book_keeps_its_author_when_the_author_link_is_empty():
    catalog = _db(
        books=[
            (88, "An Orphaned Book", "Orphaned Book, An",
             "Anon/An Orphaned Book (88)", "Anon", "1999", None),
        ],
        formats=[(1, 88, "EPUB", 100, "An Orphaned Book - Anon")],
    )
    client = FakeClient(catalog, _epub())
    provider = _provider(client)
    entries = await provider.list_entries(LIBRARY)

    chunks = await extract_and_chunk_contents(provider, build_content_index(entries))

    body = [c for c in chunks if not c["metadata"].get("is_metadata")]
    assert [c["metadata"]["author"] for c in body] == ["Anon"]


@pytest.mark.asyncio
async def test_an_unreadable_format_still_leaves_a_searchable_metadata_row():
    catalog = _db(
        books=[
            (99, "A Scanned Commentary", "Commentary, A Scanned",
             "Anon/A Scanned Commentary (99)", "Anon", "1901", None),
        ],
        formats=[(1, 99, "PDF", 900000, "A Scanned Commentary - Anon")],
    )
    client = FakeClient(catalog, None)
    provider = _provider(client)
    entries = await provider.list_entries(LIBRARY)

    assert entries, "a PDF-only book is still worth listing"
    chunks = await extract_and_chunk_contents(provider, build_content_index(entries))

    assert all(c["metadata"].get("is_metadata") for c in chunks)
    assert any("A Scanned Commentary" in c["content"] for c in chunks)


@pytest.mark.asyncio
async def test_a_long_section_is_windowed_without_losing_its_label():
    client = FakeClient(CATALOG, _epub())
    provider = _provider(client)
    entries = await provider.list_entries(LIBRARY)
    long_section = "word " * 600
    provider.sections = {
        entries[0].path: [
            ContentSection(ordinal=1, label="1st Day.", text=long_section),
        ]
    }

    chunks = await extract_and_chunk_contents(provider, build_content_index(entries))
    body = [c for c in chunks if not c["metadata"].get("is_metadata")]

    assert len(body) > 1, "a 3000 character section must not be one oversized chunk"
    assert {c["metadata"]["chapter"] for c in body} == {"1st Day."}


@pytest.mark.asyncio
async def test_a_section_in_the_named_skip_list_is_still_indexed():
    catalog = _db(
        books=[
            (5, "A Book About Libs", "Book About Libs, A",
             "lib/A Book About Libs (5)", "Anon", "2020", None),
        ],
        formats=[(1, 5, "EPUB", 1000, "A Book About Libs - Anon")],
    )
    client = FakeClient(catalog, _epub())
    provider = _provider(client)
    entries = await provider.list_entries(LIBRARY)

    chunks = await extract_and_chunk_contents(provider, build_content_index(entries))

    assert chunks, "a provider document must not be dropped by the code-tree skip list"


@pytest.mark.asyncio
async def test_the_catalogue_is_downloaded_once_however_many_books_are_indexed():
    catalog = _db(
        books=[
            (1, "One", "One", "A/One (1)", "A", "1900", None),
            (2, "Two", "Two", "A/Two (2)", "A", "1901", None),
            (3, "Three", "Three", "A/Three (3)", "A", "1902", None),
        ],
        formats=[
            (1, 1, "EPUB", 10, "One - A"),
            (2, 2, "EPUB", 10, "Two - A"),
            (3, 3, "EPUB", 10, "Three - A"),
        ],
    )
    client = FakeClient(catalog, _epub())
    provider = _provider(client)
    entries = await provider.list_entries(LIBRARY)
    provider.sections = {
        e.path: [ContentSection(ordinal=1, label="Only.", text="Prose.")] for e in entries
    }

    await extract_and_chunk_contents(provider, build_content_index(entries))

    assert client.requested.count(f"{LIBRARY}/metadata.db") == 1


@pytest.mark.asyncio
async def test_a_provider_built_from_config_reaches_the_calibre_implementation():
    client = FakeClient(CATALOG, _epub())
    provider = build_provider(
        ProviderConfig(kind="calibre", settings={"library_path": LIBRARY})
    )
    provider.client = client

    entries = await provider.list_entries(LIBRARY)

    assert isinstance(provider, CalibreStorageProvider)
    assert len(entries) == 1