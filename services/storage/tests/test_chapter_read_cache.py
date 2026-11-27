"""A chapter read is fast after the first: the catalogue and the opened book are kept.

Measured live, one PDF chapter took 27 s on every request: metadata.db downloaded
and parsed, the book downloaded, and pypdf extracting every page to answer for one
chapter. Here a fake slow WebDAV server counts what is downloaded and stat'ed, and
every book is built in the test (a fake Calibre catalogue and a bookmarked PDF).
"""
from __future__ import annotations

import asyncio
import io
import sqlite3
import time

from fastapi.testclient import TestClient

from services.storage import book_cache, book_chapters, library_api
from services.storage.book_cache import ByteLRU
from services.storage.models import ProviderConfig
from services.storage.providers import build_provider
from services.storage.providers_impl import calibre

LIB = "/Lib"
H = {"X-Internal-Secret": "test-secret"}
CHAPTERS = ["The Garden", "The Wild One", "The Question", "The Wound", "The Battle"]
BOOK_PATH = f"{LIB}/Fake Author/A Fake Book (7)/A Fake Book - Fake Author.pdf"
DELAY_S = 0.3


def _catalogue_db(extra_title: str | None = None) -> bytes:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, path TEXT, series TEXT,"
        " series_index REAL, pubdate TEXT);"
        "CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);"
        "CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);"
        "CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, uncompressed_size INTEGER,"
        " name TEXT);"
    )
    conn.execute("INSERT INTO authors (id, name) VALUES (1, 'Fake Author')")
    rows = [(7, "A Fake Book")] + ([(8, extra_title)] if extra_title else [])
    for bid, title in rows:
        conn.execute("INSERT INTO books (id, title, path) VALUES (?, ?, ?)",
                     (bid, title, f"Fake Author/{title} ({bid})"))
        conn.execute("INSERT INTO books_authors_link (book, author) VALUES (?, 1)", (bid,))
        conn.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?, 'PDF', 1000, ?)",
                     (bid, f"{title} - Fake Author"))
    conn.commit()
    data = conn.serialize()
    conn.close()
    return data


def _pdf(marker: str = "") -> bytes:
    """Two front pages, then five chapters of two pages each, bookmarked."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    texts = ["Copyright page", "Contents page"]
    for n in range(1, 6):
        texts += [f"Chapter {n} opens {marker}", f"Chapter {n} closes {marker}"]
    for text in texts:
        page = writer.add_blank_page(612, 792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_outline_item("Copyright", 0)
    for i, title in enumerate(CHAPTERS):
        writer.add_outline_item(f"Chapter {i + 1}: {title}", 2 + 2 * i)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class SlowDav:
    """A WebDAV server that takes ``DELAY_S`` per download and answers stat at once."""

    def __init__(self) -> None:
        self.base_url = "https://cloud.example"
        self.username = "reader"
        self.files: dict[str, bytes] = {f"{LIB}/metadata.db": _catalogue_db(), BOOK_PATH: _pdf()}
        self.etags: dict[str, str] = {path: "v1" for path in self.files}
        self.downloads: list[str] = []
        self.stats: list[str] = []

    async def get_file_bytes(self, remote_path, timeout=None):
        self.downloads.append(remote_path)
        await asyncio.sleep(DELAY_S)
        return self.files.get(remote_path)

    async def stat(self, remote_path, timeout=10.0):
        self.stats.append(remote_path)
        if remote_path not in self.files:
            return None
        return {"etag": self.etags[remote_path], "size": str(len(self.files[remote_path])), "mtime": "Mon"}

    def replace(self, path: str, data: bytes, etag: str) -> None:
        self.files[path] = data
        self.etags[path] = etag

    async def close(self):
        pass


def _library(monkeypatch) -> SlowDav:
    provider = build_provider(ProviderConfig(kind="calibre", settings={
        "library_path": LIB, "url": "https://cloud.example", "username": "reader", "password": "x"}))
    dav = SlowDav()
    provider.client = dav
    monkeypatch.setattr(library_api, "_provider", lambda path: provider)
    return dav


def _read(client: TestClient, chapter: str = "4") -> tuple[dict, float]:
    started = time.perf_counter()
    resp = client.post("/calibre/books/7/chapters", json={"library_path": LIB, "chapter": chapter}, headers=H)
    assert resp.status_code == 200, resp.text
    return resp.json(), time.perf_counter() - started


def test_the_second_read_of_a_book_is_served_from_the_cache(monkeypatch):
    from services.storage.main import app

    dav = _library(monkeypatch)
    client = TestClient(app)
    first, first_s = _read(client)
    assert "Chapter 4 opens" in first["chapter"]["text"] and "Chapter 5" not in first["chapter"]["text"]
    assert dav.downloads.count(BOOK_PATH) == 1 and first_s >= DELAY_S
    second, second_s = _read(client, "5")
    assert "Chapter 5 closes" in second["chapter"]["text"]
    assert dav.downloads.count(BOOK_PATH) == 1, "the book was downloaded again"
    assert dav.downloads.count(f"{LIB}/metadata.db") == 1, "the catalogue was downloaded again"
    assert second_s < DELAY_S, f"a cached read took {second_s:.2f} s"


def test_a_changed_etag_reads_the_book_again(monkeypatch):
    from services.storage.main import app

    dav = _library(monkeypatch)
    client = TestClient(app)
    _read(client)
    dav.replace(BOOK_PATH, _pdf("revised"), "v2")
    body, _ = _read(client)
    assert dav.downloads.count(BOOK_PATH) == 2
    assert "revised" in body["chapter"]["text"]
    assert len(book_cache.BOOKS) == 1  # the old copy was replaced, not kept beside it


def test_a_changed_size_alone_reads_the_book_again(monkeypatch):
    from services.storage.main import app

    dav = _library(monkeypatch)
    client = TestClient(app)
    _read(client)
    dav.files[BOOK_PATH] = _pdf("longer edition")  # same ETag, different size
    body, _ = _read(client)
    assert dav.downloads.count(BOOK_PATH) == 2 and "longer edition" in body["chapter"]["text"]


async def test_the_catalogue_is_not_downloaded_again_while_metadata_db_is_unchanged(monkeypatch):
    dav = _library(monkeypatch)
    monkeypatch.setattr(calibre, "_catalog_check_s", lambda: 0.0)  # check on every read
    provider = library_api._provider(LIB)
    for _ in range(3):
        catalog = await provider._load_catalog(refresh=True)
    assert set(catalog) == {7}
    assert dav.downloads.count(f"{LIB}/metadata.db") == 1
    assert dav.stats.count(f"{LIB}/metadata.db") == 3
    dav.replace(f"{LIB}/metadata.db", _catalogue_db(extra_title="A Second Book"), "v2")
    catalog = await provider._load_catalog(refresh=True)
    assert set(catalog) == {7, 8} and dav.downloads.count(f"{LIB}/metadata.db") == 2


async def test_the_catalogue_is_checked_at_most_every_interval_and_survives_a_new_provider(monkeypatch):
    dav = _library(monkeypatch)
    provider = library_api._provider(LIB)
    await provider._load_catalog(refresh=True)
    await provider._load_catalog(refresh=True)
    assert dav.stats.count(f"{LIB}/metadata.db") == 1  # the second read is inside the interval
    fresh = calibre.CalibreStorageProvider({"url": "https://cloud.example", "username": "reader",
                                            "password": "x", "library_path": LIB})
    fresh.client = dav
    assert set(await fresh._load_catalog(refresh=True)) == {7}
    assert dav.downloads.count(f"{LIB}/metadata.db") == 1


async def test_concurrent_reads_of_one_book_share_one_download(monkeypatch):
    dav = _library(monkeypatch)
    results = await asyncio.gather(*(library_api._opened_book(LIB, 7) for _ in range(5)))
    assert dav.downloads.count(BOOK_PATH) == 1
    assert len({id(opened) for _book, _fmt, opened in results}) == 1


def test_only_the_named_chapters_pages_are_extracted(monkeypatch):
    from pypdf import PageObject

    extracted: list[int] = []
    original = PageObject.extract_text

    def counting(self, *args, **kwargs):
        extracted.append(self.page_number)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", counting)
    opened = book_chapters.open_book(_pdf(), "PDF")
    assert opened.source == "pdf-outline" and extracted == []  # the outline alone gives the chapters
    rows = [c.row(i) for i, c in enumerate(opened.chapters)]
    assert [r["number"] for r in rows if r["number"]] == [1, 2, 3, 4, 5]
    assert all(r["chars"] is None for r in rows)  # not counted without reading
    four = book_chapters.find_chapter(opened.chapters, "four")
    assert "Chapter 4 opens" in opened.chapter_text(four)
    assert sorted(extracted) == [8, 9]  # chapter 4's two pages, nothing else
    opened.chapter_text(four)
    assert sorted(extracted) == [8, 9]  # read once
    assert opened.chapters[four].row(four)["chars"] > 0


def test_the_lru_evicts_the_least_recently_used_by_bytes():
    cache = ByteLRU(100)
    cache.put("a", 1, "A", 60)
    cache.put("b", 1, "B", 30)
    assert cache.get("a", 1) == "A"  # a is now the most recently used
    cache.put("c", 1, "C", 30)
    assert "b" not in cache and "a" in cache and "c" in cache and cache.total_bytes == 90
    cache.put("huge", 1, "H", 101)
    assert "huge" not in cache and cache.total_bytes == 90
    assert cache.get("a", 2) is None and "a" not in cache  # a different signature is a miss, and dropped
    cache.put("d", 1, "D", 50)
    cache.resize("c", 70)  # c grew past the budget with d beside it: d goes, c stays
    assert "c" in cache and "d" not in cache and cache.total_bytes == 70


def test_the_cache_budget_comes_from_the_setting():
    from services.config import CALIBRE_BOOK_CACHE_MB

    assert book_cache.BOOKS.max_bytes == int(CALIBRE_BOOK_CACHE_MB * 1024 * 1024)


def test_a_propfind_answer_carries_the_etag():
    from services.storage.nextcloud_client import NextCloudClient

    xml = (
        b'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:"><d:response>'
        b"<d:href>/remote.php/dav/files/u/Lib/metadata.db</d:href><d:propstat><d:prop>"
        b'<d:getetag>"abc123"</d:getetag><d:getcontentlength>4096</d:getcontentlength>'
        b"<d:getlastmodified>Mon, 23 Nov 2026 10:00:00 GMT</d:getlastmodified><d:resourcetype/>"
        b"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
    )
    props = NextCloudClient("https://cloud.example", "u", "p")._parse_dav_response(xml)[0]["props"]
    assert props["etag"] == '"abc123"' and props["size"] == "4096" and props["mtime"].startswith("Mon")


async def test_a_provider_dropped_by_build_provider_is_closed(monkeypatch):
    from services.storage import providers

    monkeypatch.setattr(providers, "_CLOSE_DELAY_S", 0.0)
    config = ProviderConfig(kind="calibre", settings={
        "library_path": LIB, "url": "https://cloud.example", "username": "reader", "password": "x"})
    old = build_provider(config)
    closed = []

    async def close():
        closed.append(True)

    old.close = close
    key = next(iter(providers._PROVIDERS))
    providers._PROVIDERS[key] = (old, time.monotonic() - providers._PROVIDER_TTL_S - 1)
    assert build_provider(config) is not old
    await asyncio.sleep(0.01)
    assert closed == [True]
