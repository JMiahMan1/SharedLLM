"""Notes -> Jarvis memory: the sync actually writes to RAG, chunked, per path."""
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.responses import JSONResponse


class _Resp:
    def __init__(self, status=200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, sync_status=200):
        self.calls: list[tuple[str, dict]] = []
        self.sync_status = sync_status

    def post(self, url, json=None, **kwargs):
        self.calls.append((url, json))
        return _Resp(self.sync_status if url.endswith("/rag/sync/files") else 200)


def _request(body):
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    return req


def _install(monkeypatch, main, notes, client):
    async def fake_proxy(request, path, payload):
        assert payload["action"] == "sync_rag"
        return JSONResponse(content={"status": "SUCCESS", "detail": {"synced": notes}})

    @asynccontextmanager
    async def fake_client():
        yield client

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)
    monkeypatch.setattr(main, "_resolve_identity_from_request", AsyncMock(return_value={"user": "Michele"}))
    monkeypatch.setattr(main, "shared_http_client", fake_client)


@pytest.mark.asyncio
async def test_sync_indexes_each_note_under_the_caller(monkeypatch):
    from services.gateway import main

    client = _Client()
    notes = [
        {"path": "Notes/Groceries.md", "content": "milk " * 300},  # ~1500 chars -> 2 chunks
        {"path": "Notes/Empty.md", "content": ""},
    ]
    _install(monkeypatch, main, notes, client)

    resp = await main.proxy_sync_notes_rag(_request({"storage": "nextcloud"}))
    body = json.loads(resp.body)

    assert body["status"] == "SUCCESS"
    assert body["detail"] == {"notes": 1, "passages": 2}
    # The note's text never comes back to the browser.
    assert "milk" not in resp.body.decode()
    purge, sync = client.calls
    assert purge[0].endswith("/rag/purge/nextcloud_files")
    assert purge[1] == {"user_id": "michele", "filter": {"path": "/Notes/Groceries.md"}}
    assert sync[0].endswith("/rag/sync/files")
    assert sync[1]["user_id"] == "michele"
    assert sync[1]["collection_name"] == "nextcloud_files"
    assert [c["metadata"]["chunk_index"] for c in sync[1]["chunks"]] == [0, 1]
    assert all(c["metadata"]["path"] == "/Notes/Groceries.md" for c in sync[1]["chunks"])


@pytest.mark.asyncio
async def test_sync_reports_a_refusal_instead_of_success(monkeypatch):
    from services.gateway import main

    client = _Client(sync_status=500)
    _install(monkeypatch, main, [{"path": "Notes/A.md", "content": "a"}], client)

    resp = await main.proxy_sync_notes_rag(_request({}))
    assert resp.status_code == 502
    assert json.loads(resp.body)["status"] == "FAILURE"


@pytest.mark.asyncio
async def test_the_notes_list_passes_the_preview_request_through(monkeypatch):
    """The Notes page gets its card previews in the list reply; dropping the
    flag here sent it back to one read per card."""
    from services.gateway import main

    seen = {}

    async def fake_proxy(request, path, payload, as_user=None):
        seen.update(path=path, payload=payload, as_user=as_user)
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    req = _request({"storage": "nextcloud", "previews": True, "as_user": "admin"})
    req.method = "POST"
    await main.proxy_list_notes(req)

    assert seen["path"] == "/execute/note"
    assert seen["payload"]["previews"] is True
    assert seen["as_user"] == "admin"


@pytest.mark.asyncio
async def test_the_notes_meta_route_reads_and_writes_the_index(monkeypatch):
    from services.gateway import main

    seen: dict = {}

    async def fake_proxy(request, path, payload, as_user=None):
        seen.update(path=path, payload=payload, as_user=as_user)
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    await main.proxy_notes_meta(_request({"as_user": "admin"}))
    assert seen["path"] == "/execute/note"
    assert seen["payload"] == {"action": "meta_read", "storage": "nextcloud"}
    assert seen["as_user"] == "admin"

    meta = {"version": 1, "notes": {"Notes/A.md": {"pinned": True}}}
    await main.proxy_notes_meta(_request({"write": True, "meta": meta}))
    assert seen["payload"] == {"action": "meta_write", "meta": meta, "storage": "nextcloud"}


@pytest.mark.asyncio
async def test_the_notes_move_route_passes_path_and_destination(monkeypatch):
    from services.gateway import main

    seen: dict = {}

    async def fake_proxy(request, path, payload, as_user=None):
        seen.update(path=path, payload=payload)
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    await main.proxy_move_note(
        _request({"path": "Notes/A.md", "destination": "Notes/Trash/A.md"})
    )
    assert seen["path"] == "/execute/note"
    assert seen["payload"] == {
        "action": "move",
        "path": "Notes/A.md",
        "destination": "Notes/Trash/A.md",
        "storage": "nextcloud",
    }


@pytest.mark.asyncio
async def test_the_notes_duplicate_and_empty_trash_routes(monkeypatch):
    from services.gateway import main

    seen: list[dict] = []

    async def fake_proxy(request, path, payload, as_user=None):
        seen.append(payload)
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    await main.proxy_duplicate_note(_request({"path": "Notes/A.md", "title": "A copy"}))
    assert seen[-1] == {
        "action": "duplicate",
        "path": "Notes/A.md",
        "title": "A copy",
        "storage": "nextcloud",
    }

    await main.proxy_empty_note_trash(_request({}))
    assert seen[-1] == {"action": "empty_trash", "storage": "nextcloud"}
