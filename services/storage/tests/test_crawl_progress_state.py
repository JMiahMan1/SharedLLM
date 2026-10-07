"""A crawl that runs out of sight still has to be able to say it is running.

``POST /index/full`` answers 202 and executes in a background task, so before
the crawl record existed there was no honest way for the UI or an operator to
tell a multi-hour Calibre crawl from an idle service: ``indexer`` only ever
read IDLE or PAUSED. These tests pin the record's contract -- phases walk
listing/extracting/syncing, counts survive completion, failures are recorded
rather than leaving ``active`` stuck true, and progress never ticks when no
crawl is running (which would make ``/status`` claim a crawl nobody started).
"""

import asyncio

import pytest

from services.storage import indexer
from services.storage import main as storage_main
from services.storage.models import ContentSection, IndexScanRequest, ProviderConfig, StorageEntry


@pytest.fixture(autouse=True)
def _clean_crawl_state():
    """The crawl record is module-global; isolate every test from the last."""
    indexer._crawl_state.clear()
    indexer._crawl_state.update({"active": False})
    yield
    indexer._crawl_state.clear()
    indexer._crawl_state.update({"active": False})


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class FakeRagClient:
    def __init__(self, sync_status=200):
        self.sync_status = sync_status
        self.syncs = []

    async def get(self, url, params=None, headers=None, timeout=None):
        return FakeResponse(200, {"status": "SUCCESS", "paths": []})

    async def post(self, url, json=None, headers=None, timeout=None):
        if "/rag/sync/files" in url:
            self.syncs.append(json or {})
            return FakeResponse(self.sync_status, {"status": "SUCCESS"})
        return FakeResponse(200, {"status": "SUCCESS"})


class _ClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


class FakeProvider:
    """One small text file, enough for the pipeline to produce real chunks."""

    async def list_entries(self, path, recursive=True):
        return [
            StorageEntry(path="/Notes/a.txt", name="a.txt", is_dir=False, size=12),
        ]

    async def get_sections(self, path):
        return [ContentSection(ordinal=1, label="Opening", text="prose worth indexing")]

    async def get_content(self, path):
        return "prose worth indexing"

    async def write_content(self, path, content):
        raise AssertionError("a crawl must never write")


def _scan_request(kind="calibre", path="/Books/Text"):
    return IndexScanRequest(
        provider=ProviderConfig(kind=kind, settings={"library_path": path}),
        path="/",
        recursive=True,
        user_id="tester",
        force=True,
    )


def _run(provider, monkeypatch, request, sync_status=200):
    client = FakeRagClient(sync_status=sync_status)
    monkeypatch.setattr(storage_main, "build_provider", lambda config: provider)
    monkeypatch.setattr(storage_main, "get_client", lambda: _ClientContext(client))
    asyncio.run(storage_main._run_full_index_task(request))
    return client


@pytest.fixture
def phase_log(monkeypatch):
    """Record every phase the task itself reports, then still apply it."""
    calls = []
    real = storage_main.crawl_phase

    def record(phase, done=None, total=None):
        calls.append((phase, done, total))
        real(phase, done, total)

    monkeypatch.setattr(storage_main, "crawl_phase", record)
    return calls


def test_begin_reports_the_running_crawl():
    indexer.crawl_begin("calibre", "/Books/Text")

    snap = indexer.crawl_snapshot()
    assert snap["active"] is True
    assert snap["kind"] == "calibre"
    assert snap["path"] == "/Books/Text"
    assert snap["phase"] == "starting"
    assert snap["done"] == 0 and snap["total"] == 0


def test_phase_is_a_noop_when_no_crawl_is_running():
    indexer.crawl_phase("extracting", 5, 10)

    snap = indexer.crawl_snapshot()
    assert snap["active"] is False
    assert "phase" not in snap or snap.get("phase") in (None, "idle")


def test_snapshot_is_detached_from_the_live_state():
    indexer.crawl_begin("calibre", "/Books/Text")

    snap = indexer.crawl_snapshot()
    snap["kind"] = "mutated-by-a-reader"

    assert indexer.crawl_snapshot()["kind"] == "calibre"


def test_end_records_what_the_crawl_produced():
    indexer.crawl_begin("calibre", "/Books/Text")
    indexer.crawl_end(files=3, chunks=9, synced=9)

    snap = indexer.crawl_snapshot()
    assert snap["active"] is False
    assert snap["phase"] == "idle"
    assert snap["files"] == 3
    assert snap["chunks"] == 9
    assert "error" not in snap


def test_end_records_a_failure_rather_than_leaving_the_crawl_running():
    indexer.crawl_begin("calibre", "/Books/Text")
    indexer.crawl_end(error="provider exploded")

    snap = indexer.crawl_snapshot()
    assert snap["active"] is False
    assert snap["error"] == "provider exploded"


def test_a_new_crawl_supersedes_the_previous_record():
    indexer.crawl_begin("calibre", "/Books/Text")
    indexer.crawl_begin("nextcloud", "/Notes")

    snap = indexer.crawl_snapshot()
    assert snap["active"] is True
    assert snap["kind"] == "nextcloud"
    assert snap["path"] == "/Notes"


def test_extraction_advances_progress_only_while_a_crawl_runs():
    from services.storage.indexer import build_content_index, extract_and_chunk_contents

    items = build_content_index(
        [StorageEntry(path="/Notes/a.txt", name="a.txt", is_dir=False, size=12)]
    )

    indexer.crawl_begin("calibre", "/Books/Text")
    indexer.crawl_phase("extracting", 0, len(items))
    asyncio.run(extract_and_chunk_contents(_SectionsNoneProvider(), items))
    snap = indexer.crawl_snapshot()
    assert snap["phase"] == "extracting"
    assert snap["done"] == len(items)
    assert snap["total"] == len(items)

    indexer.crawl_end(files=len(items))
    asyncio.run(extract_and_chunk_contents(_SectionsNoneProvider(), items))
    snap = indexer.crawl_snapshot()
    assert snap["phase"] == "idle"
    assert snap["active"] is False


class _SectionsNoneProvider:
    async def get_content(self, path):
        return "prose worth indexing"

    async def write_content(self, path, content):
        raise AssertionError("no writes")


def test_a_completed_crawl_reports_its_counts(monkeypatch, phase_log):
    _run(FakeProvider(), monkeypatch, _scan_request())

    snap = indexer.crawl_snapshot()
    assert snap["active"] is False
    assert snap.get("kind") == "calibre"
    assert snap["files"] >= 1
    assert snap["chunks"] >= 1
    assert snap["synced"] == snap["chunks"]
    assert "error" not in snap

    phases = [p for p, _, _ in phase_log]
    assert "listing" in phases, phases
    assert "extracting" in phases, phases
    assert "syncing" in phases, phases


def test_the_sync_phase_reaches_its_total(monkeypatch, phase_log):
    _run(FakeProvider(), monkeypatch, _scan_request())

    syncing = [(done, total) for phase, done, total in phase_log if phase == "syncing"]
    assert syncing, phase_log
    last_done, last_total = syncing[-1]
    assert last_total and last_done == last_total


def test_a_provider_that_refuses_to_build_fails_the_crawl_visibly(monkeypatch):
    def _boom(config):
        raise ValueError("No Calibre library path configured.")

    monkeypatch.setattr(storage_main, "build_provider", _boom)
    asyncio.run(storage_main._run_full_index_task(_scan_request()))

    snap = indexer.crawl_snapshot()
    assert snap["active"] is False
    assert "No Calibre library path configured" in snap.get("error", "")


def test_a_failed_rag_sync_is_recorded_not_swallowed(monkeypatch):
    _run(FakeProvider(), monkeypatch, _scan_request(), sync_status=500)

    snap = indexer.crawl_snapshot()
    assert snap["active"] is False
    assert "RAG sync failed" in snap.get("error", "")


def test_status_exposes_the_crawl_record(monkeypatch):
    from fastapi.testclient import TestClient

    from services.storage.main import app, _require_internal_secret

    app.dependency_overrides[_require_internal_secret] = lambda: None
    try:
        indexer.crawl_begin("calibre", "/Books/Text")
        client = TestClient(app)
        body = client.get("/status").json()
    finally:
        app.dependency_overrides.pop(_require_internal_secret, None)

    assert body["crawl"]["active"] is True
    assert body["crawl"]["kind"] == "calibre"
