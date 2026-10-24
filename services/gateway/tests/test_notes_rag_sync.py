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


@pytest.mark.asyncio
async def test_the_write_route_passes_the_opened_version_through(monkeypatch):
    """Without if_match the editor's conflict check never reaches Nextcloud."""
    from services.gateway import main

    seen: dict = {}

    async def fake_proxy(request, path, payload, as_user=None):
        seen.update(path=path, payload=payload, as_user=as_user)
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    await main.proxy_write_note(_request({"title": "A", "content": "x", "path": "Notes/A.md", "if_match": '"v1"'}))
    assert seen["payload"]["action"] == "write"
    assert seen["payload"]["if_match"] == '"v1"'


@pytest.mark.asyncio
async def test_the_versions_route_maps_list_read_and_restore(monkeypatch):
    from services.gateway import main

    seen: list[tuple[dict, str | None]] = []

    async def fake_proxy(request, path, payload, as_user=None):
        seen.append((payload, as_user))
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    await main.proxy_note_versions(_request({"path": "Notes/A.md", "as_user": "admin"}))
    await main.proxy_note_versions(_request({"path": "Notes/A.md", "action": "read", "version": "1700000000"}))
    await main.proxy_note_versions(_request({"path": "Notes/A.md", "action": "restore", "version": "1700000000"}))
    assert [p["action"] for p, _ in seen] == ["versions", "version_read", "version_restore"]
    assert seen[0][1] == "admin"
    assert seen[2][0]["version"] == "1700000000"

    refused = await main.proxy_note_versions(_request({"path": "Notes/A.md", "action": "delete"}))
    assert refused.status_code == 400
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_a_note_reminder_reaches_the_timers_with_its_link(monkeypatch):
    from services.gateway import main

    seen: dict = {}

    async def fake_proxy(request, path, payload, as_user=None):
        seen.update(path=path, payload=payload)
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    await main.proxy_add_timer(_request({
        "title": "📝 Trip ideas", "type": "reminder", "time_str": "2099-01-01T09:00:00Z",
        "url": "/notes?note=Notes%2FTrip%20ideas.md",
    }))
    assert seen["path"] == "/execute/timer"
    assert seen["payload"]["type"] == "reminder"
    assert seen["payload"]["url"] == "/notes?note=Notes%2FTrip%20ideas.md"


@pytest.mark.asyncio
async def test_the_versions_route_refuses_an_unknown_action(monkeypatch):
    from services.gateway import main

    seen: list = []

    async def fake_proxy(request, path, payload, as_user=None):
        seen.append(payload)
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)
    refused = await main.proxy_note_versions(_request({"path": "Notes/A.md", "action": "delete"}))
    assert refused.status_code == 400
    assert seen == []


@pytest.mark.asyncio
async def test_the_share_route_maps_each_action_as_the_caller(monkeypatch):
    from services.gateway import main

    seen: list[tuple[dict, str | None]] = []

    async def fake_proxy(request, path, payload, as_user=None):
        seen.append((payload, as_user))
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    await main.proxy_note_share(_request({"path": "Notes/A.md"}))
    await main.proxy_note_share(_request({"action": "people", "search": "mi"}))
    await main.proxy_note_share(_request({"action": "add", "path": "Notes/A.md", "share_with": "michele", "can_edit": True, "as_user": "admin"}))
    await main.proxy_note_share(_request({"action": "remove", "share_id": 11}))
    assert [p["action"] for p, _ in seen] == ["shares", "sharees", "share_add", "share_remove"]
    assert seen[2][0]["share_with"] == "michele" and seen[2][0]["can_edit"] is True and seen[2][1] == "admin"
    assert seen[3][0]["share_id"] == "11"

    refused = await main.proxy_note_share(_request({"action": "publish"}))
    assert refused.status_code == 400
    assert len(seen) == 4


@pytest.mark.asyncio
async def test_the_attachment_route_maps_upload_read_and_delete(monkeypatch):
    from services.gateway import main

    seen: list[tuple[dict, str | None]] = []

    async def fake_proxy(request, path, payload, as_user=None):
        seen.append((payload, as_user))
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)

    await main.proxy_note_attachment(_request({"action": "upload", "path": "Notes/A.md", "filename": "a.png", "data": "aGk="}))
    await main.proxy_note_attachment(_request({"action": "read", "path": "Notes/A.md", "attachment": ".attachments.7/a.png", "as_user": "admin"}))
    await main.proxy_note_attachment(_request({"action": "delete", "path": "Notes/A.md", "attachment": ".attachments.7/a.png"}))
    assert [p["action"] for p, _ in seen] == ["attachment_upload", "attachment_read", "attachment_delete"]
    assert seen[0][0]["data"] == "aGk=" and seen[0][0]["filename"] == "a.png"
    assert seen[1][0]["attachment"] == ".attachments.7/a.png" and seen[1][1] == "admin"

    refused = await main.proxy_note_attachment(_request({"action": "list"}))
    assert refused.status_code == 400
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_the_delete_route_passes_where_a_renamed_notes_attachments_go(monkeypatch):
    from services.gateway import main

    seen: list[dict] = []

    async def fake_proxy(request, path, payload, as_user=None):
        seen.append(payload)
        return JSONResponse(content={"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", fake_proxy)
    await main.proxy_delete_note(_request({"path": "Notes/Old.md", "attachments_to": "Notes/Work/New.md"}))
    assert seen[0]["action"] == "delete" and seen[0]["attachments_to"] == "Notes/Work/New.md"
