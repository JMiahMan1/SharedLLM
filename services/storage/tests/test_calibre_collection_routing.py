"""A provider kind decides its own RAG collection, and a Calibre crawl must land in ``calibre_files``.

The storage service derives the collection name from the provider kind, so adding
a provider kind routes it with no further wiring -- which is the whole reason the
Calibre integration needed no change inside ``services/rag``. That property is
invisible from the provider's own tests, because they never reach the sync step,
so it is pinned here: if the derivation is ever hardcoded to ``nextcloud``, a
Calibre crawl would silently overwrite the Nextcloud index rather than sit beside
it, and every assertion in the Calibre test files would still pass.
"""
import asyncio
import logging

from services.storage import main as storage_main
from services.storage.models import ContentSection, IndexScanRequest, ProviderConfig, StorageEntry

log = logging.getLogger(__name__)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class FakeRagClient:
    def __init__(self):
        self.indexed_paths_queries = []
        self.syncs = []
        self.purges = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.indexed_paths_queries.append((url, params or {}))
        return FakeResponse(200, {"status": "SUCCESS", "paths": ["/shelf/a.txt"]})

    async def post(self, url, json=None, headers=None, timeout=None):
        payload = json or {}
        if "/rag/sync/files" in url:
            self.syncs.append(payload)
            return FakeResponse(200, {"status": "SUCCESS"})
        self.purges.append((url, payload))
        return FakeResponse(200, {"status": "SUCCESS"})


class FakeCalibreProvider:
    """A provider that answers the crawl and hands back one labelled section."""

    def __init__(self):
        self.requested = []

    async def list_entries(self, path, recursive=True):
        self.requested.append(path)
        return [
            StorageEntry(
                path="/Books/Text/An Author/A Book (617).txt",
                name="A Book (617).txt",
                is_dir=False,
                size=None,
                metadata={"calibre_id": 617, "title": "A Book", "author": "An Author"},
            )
        ]

    async def get_sections(self, path):
        return [ContentSection(ordinal=1, label="1st Day.", text="body text")]

    async def get_content(self, path):
        return "body text"

    async def write_content(self, path, content):
        raise AssertionError("a read-only crawl must never write")


class _ClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


def _run_scan(provider, monkeypatch, request):
    client = FakeRagClient()
    monkeypatch.setattr(storage_main, "build_provider", lambda config: provider)
    monkeypatch.setattr(storage_main, "get_client", lambda: _ClientContext(client))
    asyncio.run(storage_main._run_full_index_task(request))
    return client


def _scan_request(kind, path="/Books/Text"):
    return IndexScanRequest(
        provider=ProviderConfig(kind=kind, settings={"library_path": path}),
        path="/",
        recursive=True,
        user_id="tester",
        force=True,
    )


def test_a_calibre_crawl_syncs_into_calibre_files(monkeypatch):
    client = _run_scan(FakeCalibreProvider(), monkeypatch, _scan_request("calibre"))

    assert client.syncs, "the crawl never reached the RAG sync"
    assert {s["collection_name"] for s in client.syncs} == {"calibre_files"}


def test_a_nextcloud_crawl_still_syncs_into_nextcloud_files(monkeypatch):
    client = _run_scan(FakeCalibreProvider(), monkeypatch, _scan_request("nextcloud"))

    assert {s["collection_name"] for s in client.syncs} == {"nextcloud_files"}


def test_the_two_providers_never_share_a_collection(monkeypatch):
    calibre = _run_scan(FakeCalibreProvider(), monkeypatch, _scan_request("calibre"))
    nextcloud = _run_scan(FakeCalibreProvider(), monkeypatch, _scan_request("nextcloud"))

    calibre_collections = {s["collection_name"] for s in calibre.syncs}
    nextcloud_collections = {s["collection_name"] for s in nextcloud.syncs}
    assert calibre_collections.isdisjoint(nextcloud_collections)


def test_calibre_chunks_carry_the_book_and_section_into_the_sync_payload(monkeypatch):
    client = _run_scan(FakeCalibreProvider(), monkeypatch, _scan_request("calibre"))

    chunks = [chunk for sync in client.syncs for chunk in sync["chunks"]]
    labelled = [c for c in chunks if c["metadata"].get("chapter") == "1st Day."]

    assert labelled, f"no section label survived the sync: {[c['metadata'] for c in chunks]}"
    assert labelled[0]["metadata"]["title"] == "A Book"
    assert labelled[0]["metadata"]["calibre_id"] == 617
    assert labelled[0]["metadata"]["author"] == "An Author"


def test_every_synced_chunk_metadata_value_is_a_scalar(monkeypatch):
    """``/rag/sync/files`` coerces non-scalars with ``str()``.

    A list or dict handed to it comes back as a quoted string, so the
    flattening has to happen before the sync, not after it.
    """
    client = _run_scan(FakeCalibreProvider(), monkeypatch, _scan_request("calibre"))

    for sync in client.syncs:
        for chunk in sync["chunks"]:
            for key, value in chunk["metadata"].items():
                assert isinstance(value, (str, int, float, bool)), (
                    f"{key}={value!r} would be stringified by the RAG sync"
                )


def test_a_calibre_crawl_asks_rag_about_its_own_collection(monkeypatch):
    client = _run_scan(FakeCalibreProvider(), monkeypatch, _scan_request("calibre"))

    assert client.indexed_paths_queries, "stale-path bookkeeping never consulted RAG"
    url, params = client.indexed_paths_queries[0]
    assert "/rag/indexed-paths" in url
    assert params["collection_name"] == "calibre_files"