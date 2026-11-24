"""The library index holds each book's catalogue card, kept current with no manual step.

Measured live (2026-11-24): a whole-library crawl embedded every passage of every
book (about 600 per book, 1.6 s each on the household server's CPU), had carded 29
of 3,086 books after six and a half hours, and started again from the first author
on every crawl, because a Calibre entry carried no ``mtime`` and the checkpoint was
never consulted. Now each book is one card (title, author, series, tags, publisher,
description, chapter titles); a book is read again only when Calibre's
``last_modified`` for it changes, a book is checkpointed only once its card is in
the index, and storage checks the catalogue by itself.
"""
from __future__ import annotations

import asyncio
import io
import sqlite3
import zipfile

from services.storage import main as storage_main
from services.storage.book_chapters import contents_titles
from services.storage.indexer import CheckpointManager
from services.storage.models import IndexScanRequest, ProviderConfig
from services.storage.providers_impl.calibre import CATALOGUE_CARD_VERSION, CalibreStorageProvider
from services.storage.tests.test_calibre_shared_library_index import FakeRag, _Ctx

LIBRARY = "/Books/Text"


def _catalog(books: list[dict]) -> bytes:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, path TEXT,
                            pubdate TEXT, series_index REAL, last_modified TEXT);
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
        CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT,
                           uncompressed_size INTEGER, name TEXT);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER);
        CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER, series INTEGER);
        CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INTEGER, publisher INTEGER);
        CREATE TABLE comments (id INTEGER PRIMARY KEY, book INTEGER, text TEXT);
        """
    )
    for n, b in enumerate(books, start=1):
        con.execute("INSERT INTO books VALUES (?,?,?,?,?,?,?)",
                    (b["id"], b["title"], b["title"], f"{b['author']}/{b['title']} ({b['id']})",
                     "2001-01-01", b.get("series_index", 1.0), b.get("modified", "2026-01-01 00:00:00")))
        con.execute("INSERT INTO authors VALUES (?,?)", (n, b["author"]))
        con.execute("INSERT INTO books_authors_link VALUES (?,?,?)", (n, b["id"], n))
        con.execute("INSERT INTO data VALUES (?,?,?,?,?)",
                    (n, b["id"], b.get("format", "EPUB"), 100, f"{b['title']} - {b['author']}"))
        for t, tag in enumerate(b.get("tags", []), start=1):
            con.execute("INSERT OR IGNORE INTO tags VALUES (?,?)", (n * 10 + t, tag))
            con.execute("INSERT INTO books_tags_link (book, tag) VALUES (?,?)", (b["id"], n * 10 + t))
        if b.get("series"):
            con.execute("INSERT INTO series VALUES (?,?)", (n, b["series"]))
            con.execute("INSERT INTO books_series_link (book, series) VALUES (?,?)", (b["id"], n))
        if b.get("publisher"):
            con.execute("INSERT INTO publishers VALUES (?,?)", (n, b["publisher"]))
            con.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?,?)", (b["id"], n))
        if b.get("comments"):
            con.execute("INSERT INTO comments (book, text) VALUES (?,?)", (b["id"], b["comments"]))
    con.commit()
    blob = con.serialize()
    con.close()
    return blob


def _epub_with_contents(titles: list[str]) -> bytes:
    """An EPUB 2 whose NCX lists ``titles``, one spine document per chapter."""
    manifest = "".join(
        f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(len(titles))
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(titles)))
    points = "".join(
        f'<navPoint id="n{i}" playOrder="{i + 1}"><navLabel><text>{t}</text></navLabel>'
        f'<content src="c{i}.xhtml"/></navPoint>' for i, t in enumerate(titles)
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0"><metadata/>'
            f'<manifest>{manifest}<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            f'</manifest><spine toc="ncx">{spine}</spine></package>',
        )
        zf.writestr(
            "OEBPS/toc.ncx",
            '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            f"<navMap>{points}</navMap></ncx>",
        )
        for i, t in enumerate(titles):
            zf.writestr(f"OEBPS/c{i}.xhtml",
                        f'<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>{t}</h1>'
                        f"<p>The secret prose of {t}.</p></body></html>")
    return buf.getvalue()


BOOKS = [
    {"id": 1, "title": "Harbor Lights", "author": "Ann Example", "tags": ["Fiction", "Sea"],
     "series": "Coast Tales", "series_index": 2.0, "publisher": "Example House",
     "comments": "<div><p>A <b>lighthouse</b> keeper's year.</p></div>"},
    {"id": 2, "title": "Quiet Fields", "author": "Ben Sample", "tags": ["Farming"]},
    {"id": 3, "title": "River Notes", "author": "Cy Placeholder"},
]
CONTENTS = ["Spring", "Summer", "Autumn"]


class FakeLibrary:
    """Nextcloud serving metadata.db and one EPUB per book, counting book downloads."""

    def __init__(self, books=BOOKS):
        self.catalog = _catalog(books)
        self.book_reads: list[str] = []

    async def get_file_bytes(self, remote_path, timeout=None):
        if remote_path.endswith("metadata.db"):
            return self.catalog
        self.book_reads.append(remote_path)
        return _epub_with_contents(CONTENTS)

    async def close(self):
        return None


def _provider(library: FakeLibrary) -> CalibreStorageProvider:
    provider = CalibreStorageProvider({"url": "https://cloud.example", "username": "rivera", "password": "x",
                                       "library_path": LIBRARY})
    provider.client = library
    return provider


def _crawl(monkeypatch, rag, provider, checkpoint_file, force=False):
    monkeypatch.setattr(storage_main, "build_provider", lambda config: provider)
    monkeypatch.setattr(storage_main, "get_client", lambda: _Ctx(rag))
    monkeypatch.setattr(storage_main, "_checkpoint_path", lambda: str(checkpoint_file))
    req = IndexScanRequest(provider=ProviderConfig(kind="calibre", settings={"library_path": LIBRARY}),
                           path=LIBRARY, recursive=True, force=force)
    asyncio.run(storage_main._run_full_index_task(req))


# --- the card ---------------------------------------------------------------------------

def test_a_card_holds_the_catalogue_and_the_chapter_titles_but_not_the_text():
    provider = _provider(FakeLibrary())
    entries = asyncio.run(provider.list_entries("/"))
    card = asyncio.run(provider.catalogue_card(entries[0].path))

    assert card.splitlines()[0] == "Book: Harbor Lights"
    assert "Author: Ann Example" in card
    assert "Series: Coast Tales (book 2)" in card
    assert "Tags: Fiction; Sea" in card
    assert "Publisher: Example House" in card
    assert "Description: A lighthouse keeper's year." in card, "the HTML of Calibre's comments is stripped"
    assert "Contents: Spring; Summer; Autumn" in card
    assert "secret prose" not in card


def test_a_long_contents_is_cut_with_a_count():
    from services.storage.providers_impl import calibre

    titles = [f"Part {i}" for i in range(1, calibre._CONTENTS_MAX_TITLES + 6)]
    library = FakeLibrary()
    library.get_file_bytes = _serving(library, _epub_with_contents(titles))
    provider = _provider(library)
    entries = asyncio.run(provider.list_entries("/"))
    card = asyncio.run(provider.catalogue_card(entries[0].path))
    assert card.rstrip().endswith("; and 5 more")


def _serving(library, epub):
    async def get_file_bytes(remote_path, timeout=None):
        if remote_path.endswith("metadata.db"):
            return library.catalog
        return epub
    return get_file_bytes


def test_contents_titles_never_raise_on_a_broken_file():
    assert contents_titles(b"not a zip", "EPUB") == []
    assert contents_titles(b"%PDF-broken", "PDF") == []
    assert contents_titles(_epub_with_contents(["One", "Two"]), "EPUB") == ["One", "Two"]


def test_an_entry_is_stamped_with_its_last_modified_and_the_card_version():
    provider = _provider(FakeLibrary())
    entries = asyncio.run(provider.list_entries("/"))
    assert entries[0].mtime == f"2026-01-01 00:00:00|{CATALOGUE_CARD_VERSION}"


# --- resuming and keeping up -------------------------------------------------------------

def test_a_second_crawl_reads_no_book_that_has_not_changed(monkeypatch, tmp_path):
    rag, library = FakeRag(), FakeLibrary()
    checkpoint = tmp_path / "checkpoint.json"
    _crawl(monkeypatch, rag, _provider(library), checkpoint)
    assert len(library.book_reads) == 3
    assert len(rag.rows) == 3

    library.book_reads.clear()
    _crawl(monkeypatch, rag, _provider(library), checkpoint)
    assert library.book_reads == [], "an unchanged library is not read again"
    assert len(rag.rows) == 3, "and its cards stay"


def test_an_edited_book_is_carded_again_and_only_it(monkeypatch, tmp_path):
    rag, library = FakeRag(), FakeLibrary()
    checkpoint = tmp_path / "checkpoint.json"
    _crawl(monkeypatch, rag, _provider(library), checkpoint)

    edited = [dict(b) for b in BOOKS]
    edited[1]["modified"] = "2026-02-02 00:00:00"
    library2 = FakeLibrary(edited)
    _crawl(monkeypatch, rag, _provider(library2), checkpoint)
    assert [p.rsplit("/", 1)[-1] for p in library2.book_reads] == ["Quiet Fields - Ben Sample.epub"]


def test_a_removed_book_loses_its_card(monkeypatch, tmp_path):
    rag, library = FakeRag(), FakeLibrary()
    checkpoint = tmp_path / "checkpoint.json"
    _crawl(monkeypatch, rag, _provider(library), checkpoint)
    _crawl(monkeypatch, rag, _provider(FakeLibrary(BOOKS[:2])), checkpoint)
    assert {r["meta"]["title"] for r in rag.rows.values()} == {"Harbor Lights", "Quiet Fields"}


def test_a_book_is_checkpointed_only_once_its_card_is_in_the_index(monkeypatch, tmp_path):
    class FailingRag(FakeRag):
        async def post(self, url, json=None, headers=None, timeout=None):
            if url.endswith("/rag/sync/files"):
                from services.storage.tests.test_calibre_shared_library_index import _Resp
                return _Resp(500)
            return await super().post(url, json=json, headers=headers, timeout=timeout)

    checkpoint = tmp_path / "checkpoint.json"
    _crawl(monkeypatch, FailingRag(), _provider(FakeLibrary()), checkpoint)
    assert CheckpointManager(str(checkpoint)).data == {}, "a book whose sync failed must be read again"

    rag, library = FakeRag(), FakeLibrary()
    _crawl(monkeypatch, rag, _provider(library), checkpoint)
    assert len(library.book_reads) == 3 and len(rag.rows) == 3


def test_force_reads_every_book_again(monkeypatch, tmp_path):
    rag, library = FakeRag(), FakeLibrary()
    checkpoint = tmp_path / "checkpoint.json"
    _crawl(monkeypatch, rag, _provider(library), checkpoint)
    library.book_reads.clear()
    _crawl(monkeypatch, rag, _provider(library), checkpoint, force=True)
    assert len(library.book_reads) == 3
    library.book_reads.clear()
    _crawl(monkeypatch, rag, _provider(library), checkpoint)
    assert library.book_reads == [], "a forced crawl still records what it indexed"


# --- storage keeps it current by itself ---------------------------------------------------

def _tick_setup(monkeypatch, library_path, provider):
    from services import config

    monkeypatch.setattr(config, "CALIBRE_LIBRARY_PATH", library_path)
    monkeypatch.setattr(storage_main, "build_provider", lambda cfg: provider)
    runs: list[IndexScanRequest] = []

    async def run(req):
        runs.append(req)

    monkeypatch.setattr(storage_main, "_run_full_index_task", run)
    monkeypatch.setattr(storage_main, "crawl_snapshot", lambda: {"active": False})
    return runs


def test_the_catalogue_check_indexes_at_startup_then_only_on_a_change(monkeypatch):
    library = FakeLibrary()
    provider = _provider(library)
    runs = _tick_setup(monkeypatch, LIBRARY, provider)

    digest = asyncio.run(storage_main._calibre_catalogue_tick(None))
    assert len(runs) == 1
    assert runs[0].path == LIBRARY and runs[0].recursive and not runs[0].force
    assert runs[0].provider.kind == "calibre"

    assert asyncio.run(storage_main._calibre_catalogue_tick(digest)) == digest
    assert len(runs) == 1, "an unchanged metadata.db starts no crawl"

    library.catalog = _catalog([*BOOKS, {"id": 4, "title": "New Arrival", "author": "Di Sample"}])
    provider._catalog = {}
    from services.storage.providers_impl import calibre

    calibre._CATALOGS.clear()
    new_digest = asyncio.run(storage_main._calibre_catalogue_tick(digest))
    assert len(runs) == 2 and new_digest != digest


def test_the_catalogue_check_does_nothing_without_a_library_path(monkeypatch):
    runs = _tick_setup(monkeypatch, "", _provider(FakeLibrary()))
    assert asyncio.run(storage_main._calibre_catalogue_tick(None)) is None
    assert runs == []


def test_the_catalogue_check_waits_for_a_running_crawl(monkeypatch):
    runs = _tick_setup(monkeypatch, LIBRARY, _provider(FakeLibrary()))
    monkeypatch.setattr(storage_main, "crawl_snapshot", lambda: {"active": True})
    assert asyncio.run(storage_main._calibre_catalogue_tick("old")) == "old"
    assert runs == []


def test_a_failed_crawl_is_tried_again_on_the_next_check(monkeypatch):
    runs = _tick_setup(monkeypatch, LIBRARY, _provider(FakeLibrary()))
    monkeypatch.setattr(storage_main, "crawl_snapshot", lambda: {"active": False, "error": "RAG down"})
    assert asyncio.run(storage_main._calibre_catalogue_tick(None)) is None
    assert len(runs) == 1


def test_a_card_row_points_at_the_book_id_and_its_stored_file(monkeypatch, tmp_path):
    rag = FakeRag()
    _crawl(monkeypatch, rag, _provider(FakeLibrary()), tmp_path / "checkpoint.json")
    meta = next(r["meta"] for r in rag.rows.values() if r["meta"]["title"] == "Harbor Lights")
    assert meta["calibre_id"] == 1
    assert meta["file"] == f"{LIBRARY}/Ann Example/Harbor Lights (1)/Harbor Lights - Ann Example.epub"
    assert meta["file_format"] == "EPUB"
