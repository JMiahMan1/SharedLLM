"""A whole Calibre library is indexed, once, where every household member can search it.

Measured live: ``calibre_files`` answered 100 of 100 hits from one book although the
library holds many. The books were filed under whichever account crawled them (the
Knowledge Hub crawl under that person's Nextcloud login, the scoped crawl after
"Add book" under ``admin``), and ``/rag/search`` only returns a person's own rows
plus ``default``'s, so a household member saw just the one book added through the
page. The crawl also held every book's text in memory until the end and lost all of
it to one unreadable book. These tests run the real crawl task and the real Calibre
provider against a fake Nextcloud and a fake RAG that stores rows the way
``/rag/sync/files`` keys them.
"""
from __future__ import annotations

import asyncio
import hashlib

from services.storage import main as storage_main
from services.storage.models import IndexScanRequest, ProviderConfig
from services.storage.providers_impl.calibre import CalibreStorageProvider
from services.storage.tests.test_calibre_index_end_to_end import _db, _epub

LIBRARY = "/Books/Text"

CATALOG = _db(
    books=[
        (1, "Memories of Bethany", "Memories of Bethany", "John R. Macduff/Memories of Bethany (1)",
         "John R. Macduff", "1857", None),
        (2, "Wild at Heart", "Wild at Heart", "John Eldredge/Wild at Heart (2)",
         "John Eldredge", "2001", None),
        (3, "The Faithful Promiser", "Faithful Promiser, The", "John R. Macduff/The Faithful Promiser (3)",
         "John R. Macduff", "1894", None),
    ],
    formats=[
        (1, 1, "EPUB", 100, "Memories of Bethany - John R. Macduff"),
        (2, 2, "EPUB", 100, "Wild at Heart - John Eldredge"),
        (3, 3, "EPUB", 100, "The Faithful Promiser - John R. Macduff"),
    ],
)


class FakeNextcloud:
    async def get_file_bytes(self, remote_path, timeout=None):
        if remote_path.endswith("metadata.db"):
            return CATALOG
        return _epub()

    async def close(self):
        return None


class ExplodingProvider(CalibreStorageProvider):
    """One book whose reader fails with something other than EpubTextError."""

    async def get_sections(self, path):
        if "Wild at Heart" in path:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return await super().get_sections(path)


def _provider(cls=CalibreStorageProvider, client=None):
    provider = cls({"url": "https://cloud.example", "username": "rivera", "password": "x",
                    "library_path": LIBRARY})
    provider.client = client or FakeNextcloud()
    return provider


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload


class FakeRag:
    """Stores rows keyed exactly as ``/rag/sync/files`` keys them."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.syncs = 0

    def _put(self, user, collection, chunk):
        meta = chunk["metadata"]
        path_hash = hashlib.md5(meta["path"].encode()).hexdigest()
        suffix = "meta" if meta.get("is_metadata") else meta.get("chunk_index", 0)
        self.rows[f"file:{user}:{path_hash}:{suffix}"] = {"user": user, "collection": collection, "meta": meta}

    async def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        if url.endswith("/rag/indexed-paths"):
            paths = {r["meta"]["path"] for r in self.rows.values()
                     if r["user"] == params["user_id"] and r["collection"] == params["collection_name"]}
            return _Resp(200, {"paths": sorted(paths)})
        if url.endswith("/rag/owners"):
            owners: dict[str, int] = {}
            for r in self.rows.values():
                if r["collection"] == params["collection_name"]:
                    owners[r["user"]] = owners.get(r["user"], 0) + 1
            return _Resp(200, {"owners": owners})
        return _Resp(404)

    async def post(self, url, json=None, headers=None, timeout=None):
        body = json or {}
        if url.endswith("/rag/sync/files"):
            self.syncs += 1
            for chunk in body["chunks"]:
                self._put(body["user_id"], body["collection_name"], chunk)
            return _Resp(200, {"status": "SUCCESS"})
        if "/rag/purge/" in url:
            collection = url.rsplit("/", 1)[-1]
            flt = body.get("filter") or {}
            for key in [k for k, r in self.rows.items()
                        if r["user"] == body["user_id"] and r["collection"] == collection
                        and all(r["meta"].get(fk) == fv for fk, fv in flt.items())]:
                del self.rows[key]
            return _Resp(200, {"status": "SUCCESS"})
        return _Resp(404)

    def books(self, user="default"):
        return {(r["meta"]["title"], r["meta"]["author"]) for r in self.rows.values()
                if r["user"] == user and r["collection"] == "calibre_files"}


class _Ctx:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *exc):
        return False


def _crawl(monkeypatch, rag, provider, path="/", user_id=None, settings=None):
    monkeypatch.setattr(storage_main, "build_provider", lambda config: provider)
    monkeypatch.setattr(storage_main, "get_client", lambda: _Ctx(rag))
    req = IndexScanRequest(
        provider=ProviderConfig(kind="calibre", settings=settings or {"library_path": LIBRARY, "username": "rivera"}),
        path=path, recursive=True, user_id=user_id, force=True,
    )
    asyncio.run(storage_main._run_full_index_task(req))


EXPECTED = {
    ("Memories of Bethany", "John R. Macduff"),
    ("Wild at Heart", "John Eldredge"),
    ("The Faithful Promiser", "John R. Macduff"),
}


def test_every_book_in_the_library_is_indexed_with_its_title_and_author(monkeypatch):
    rag = FakeRag()
    _crawl(monkeypatch, rag, _provider())

    assert rag.books() == EXPECTED
    passages = [r for r in rag.rows.values() if not r["meta"].get("is_metadata")]
    assert {r["meta"]["title"] for r in passages} == {t for t, _ in EXPECTED}
    assert all(r["meta"]["author"] for r in passages)


def test_the_library_is_filed_under_the_shared_owner_whoever_crawls_it(monkeypatch):
    rag = FakeRag()
    _crawl(monkeypatch, rag, _provider(), user_id="Rivera")

    assert {r["user"] for r in rag.rows.values()} == {"default"}


def test_a_second_crawl_does_not_duplicate_a_single_row(monkeypatch):
    rag = FakeRag()
    _crawl(monkeypatch, rag, _provider())
    first = dict(rag.rows)
    _crawl(monkeypatch, rag, _provider())

    assert set(rag.rows) == set(first)
    assert rag.books() == EXPECTED


def test_one_unreadable_book_does_not_cost_the_others(monkeypatch):
    rag = FakeRag()
    _crawl(monkeypatch, rag, _provider(ExplodingProvider))

    assert rag.books() == EXPECTED, "the broken book keeps its metadata row and the others their text"
    texts = {r["meta"]["title"] for r in rag.rows.values() if not r["meta"].get("is_metadata")}
    assert texts == {"Memories of Bethany", "The Faithful Promiser"}


def test_the_crawl_syncs_as_it_goes_rather_than_holding_the_whole_library(monkeypatch):
    rag = FakeRag()
    monkeypatch.setattr(storage_main, "_INDEX_GROUP_FILES", 1)
    _crawl(monkeypatch, rag, _provider())

    assert rag.syncs == 3
    assert rag.books() == EXPECTED


def test_a_whole_library_crawl_drops_copies_filed_under_one_persons_account(monkeypatch):
    rag = FakeRag()
    # What production holds: one book an "Add book" crawl filed under admin, and an
    # older Knowledge Hub crawl under a person's login.
    rag._put("admin", "calibre_files", {"metadata": {"path": f"{LIBRARY}/John R. Macduff/Memories of Bethany (1).txt",
                                                     "title": "Memories of Bethany", "author": "John R. Macduff",
                                                     "is_metadata": True}})
    rag._put("rivera", "calibre_files", {"metadata": {"path": f"{LIBRARY}/Old/Gone (9).txt",
                                                      "title": "Gone", "author": "Old", "chunk_index": 0}})
    rag._put("rivera", "nextcloud_files", {"metadata": {"path": "/Notes/a.md", "chunk_index": 0}})

    _crawl(monkeypatch, rag, _provider(), path=LIBRARY)

    owners = {(r["user"], r["collection"]) for r in rag.rows.values()}
    assert owners == {("default", "calibre_files"), ("rivera", "nextcloud_files")}
    assert rag.books() == EXPECTED


def test_a_scoped_crawl_after_add_book_leaves_the_older_copies_alone(monkeypatch):
    rag = FakeRag()
    rag._put("rivera", "calibre_files", {"metadata": {"path": f"{LIBRARY}/John Eldredge/Wild at Heart (2).txt",
                                                      "title": "Wild at Heart", "author": "John Eldredge",
                                                      "chunk_index": 0}})

    _crawl(monkeypatch, rag, _provider(), path=f"{LIBRARY}/John R. Macduff")

    assert ("rivera", "calibre_files") in {(r["user"], r["collection"]) for r in rag.rows.values()}
    assert rag.books() == {("Memories of Bethany", "John R. Macduff"), ("The Faithful Promiser", "John R. Macduff")}


def test_the_add_book_crawl_with_no_login_in_its_settings_is_shared_too(monkeypatch):
    """``library_api._index_folder`` passes only ``library_path``: it used to land under ``admin``."""
    rag = FakeRag()
    _crawl(monkeypatch, rag, _provider(), path=f"{LIBRARY}/John Eldredge", settings={"library_path": LIBRARY})

    assert {r["user"] for r in rag.rows.values()} == {"default"}
    assert rag.books() == {("Wild at Heart", "John Eldredge")}
