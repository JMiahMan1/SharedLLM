"""Nextcloud sync endpoints, sync_mode validation and multi-file uploads."""

import base64
import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, StaticPool, create_engine

# main.py must load before sync_ops/upload_ops (it imports them at its bottom,
# like git_ops), so import it first.
import services.workspace_runtime.main  # noqa: F401
from services.workspace_runtime.tests.test_nextcloud_sync import FakeRemote

_TEST_WS_ROOT = os.path.abspath(".tmp/workspaces-sync-api")
os.makedirs(_TEST_WS_ROOT, exist_ok=True)

H = {"X-Internal-Secret": "test-secret"}
NC_USER = {
    "user": "alice",
    "is_admin": False,
    "nextcloud_url": "https://cloud.example.com",
    "nextcloud_user": "alice",
    "nextcloud_pass": "pw",
}


@pytest.fixture
def remote():
    return FakeRemote()


@pytest.fixture
def scheduled(monkeypatch):
    calls = []
    import services.workspace_runtime.sync_ops as sync_ops

    def fake_schedule(workspace):
        if workspace.get("sync_mode") in sync_ops.NEXTCLOUD_MODES:
            calls.append(workspace["id"])

    # main.py and upload_ops.py hold their own references.
    monkeypatch.setattr("services.workspace_runtime.main.schedule_sync_after_write", fake_schedule)
    monkeypatch.setattr("services.workspace_runtime.upload_ops.schedule_sync_after_write", fake_schedule)
    return calls


@pytest.fixture
def client(monkeypatch, remote, scheduled):
    import services.workspace_runtime.database as database
    import services.workspace_runtime.main as main
    import services.workspace_runtime.sync_ops as sync_ops

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setenv("WORKSPACE_RUNTIME_ROOT", _TEST_WS_ROOT)
    monkeypatch.setattr(main, "WORKSPACE_ROOT", main.Path(_TEST_WS_ROOT))
    real_client = sync_ops._nextcloud_client

    def fake_client(identity):
        real_client(identity)  # keeps the real "credentials missing" error; no I/O
        return remote

    monkeypatch.setattr(sync_ops, "_nextcloud_client", fake_client)
    shutil.rmtree(_TEST_WS_ROOT, ignore_errors=True)
    os.makedirs(_TEST_WS_ROOT, exist_ok=True)
    yield TestClient(main.app)
    shutil.rmtree(_TEST_WS_ROOT, ignore_errors=True)


def _create(client, **fields):
    body = {"id": "nc_ws", "display_name": "NC", "local_path": "nc_ws", "scope": "user", **fields}
    return client.post("/workspaces", json=body, headers=H)


def _ws(client, ws_id="nc_ws"):
    resp = client.get("/workspaces", headers=H)
    return next(w for w in resp.json()["workspaces"] if w["id"] == ws_id)


def test_nextcloud_mode_requires_a_folder(client):
    resp = _create(client, sync_mode="nextcloud")
    assert resp.status_code == 400
    assert "nextcloud_path" in resp.json()["detail"]


def test_unknown_sync_mode_is_rejected(client):
    resp = _create(client, sync_mode="dropbox")
    assert resp.status_code == 400
    assert "Unknown sync_mode" in resp.json()["detail"]


def test_git_only_workspace_still_needs_no_folder(client):
    assert _create(client, sync_mode="local_git_authoritative").status_code == 200


def test_two_way_sync_endpoint_moves_files_and_records_outcome(client, remote):
    assert _create(client, sync_mode="nextcloud", nextcloud_path="/Work/NC").status_code == 200
    w = client.post(
        "/files/write",
        json={"workspace_id": "nc_ws", "relative_path": "local.txt", "content": "from here", "user_context": NC_USER},
        headers=H,
    )
    assert w.status_code == 200, w.text
    remote.put("/Work/NC/cloud/remote.txt", b"from cloud")

    resp = client.post("/provider/sync/workspace", json={"workspace_id": "nc_ws", "user_context": NC_USER}, headers=H)
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["direction"] == "both"
    assert result["uploaded"] == ["local.txt"]
    assert result["downloaded"] == ["cloud/remote.txt"]
    assert remote.items["/Work/NC/local.txt"] == b"from here"
    with open(os.path.join(_TEST_WS_ROOT, "nc_ws/cloud/remote.txt"), "rb") as fh:
        assert fh.read() == b"from cloud"

    ws = _ws(client)
    assert ws["last_sync_status"] == "ok"
    assert ws["sync_owner"] == "alice"
    assert ws["last_sync_at"]

    # The history persisted: a second run is a no-op.
    again = client.post("/provider/sync/workspace", json={"workspace_id": "nc_ws", "user_context": NC_USER}, headers=H)
    assert again.json()["result"]["changed"] is False


def test_git_workspace_defaults_to_one_way_push(client, remote):
    assert _create(client, sync_mode="local_git_authoritative", nextcloud_path="/Backup/G").status_code == 200
    remote.put("/Backup/G/stray.txt", b"x")
    resp = client.post("/provider/sync/workspace", json={"workspace_id": "nc_ws", "user_context": NC_USER}, headers=H)
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["direction"] == "push"
    assert resp.json()["result"]["downloaded"] == []


def test_sync_without_nextcloud_credentials_fails_loudly(client):
    assert _create(client, sync_mode="nextcloud", nextcloud_path="/Work/NC").status_code == 200
    no_nc = {"user": "bob", "is_admin": False}
    resp = client.post("/provider/sync/workspace", json={"workspace_id": "nc_ws", "user_context": no_nc}, headers=H)
    assert resp.status_code == 502
    assert "nextcloud_url" in resp.json()["detail"]
    ws = _ws(client)
    assert ws["last_sync_status"] == "error"
    assert "nextcloud_url" in ws["last_sync_error"]


def test_changing_the_folder_forgets_the_sync_history(client, remote):
    from services.workspace_runtime.sync_ops import _load_records

    assert _create(client, sync_mode="nextcloud", nextcloud_path="/Work/NC").status_code == 200
    remote.put("/Work/NC/a.txt", b"a")
    client.post("/provider/sync/workspace", json={"workspace_id": "nc_ws", "user_context": NC_USER}, headers=H)
    assert _load_records("nc_ws")

    resp = client.patch("/workspaces/nc_ws", json={"nextcloud_path": "/Work/Elsewhere"}, headers=H)
    assert resp.status_code == 200
    assert _load_records("nc_ws") == {}
    assert resp.json()["workspace"]["sync_owner"] is None


def test_switching_to_nextcloud_mode_without_folder_is_rejected(client):
    assert _create(client).status_code == 200
    resp = client.patch("/workspaces/nc_ws", json={"sync_mode": "git_and_nextcloud"}, headers=H)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------


def _ctx(user=NC_USER):
    return {"X-Workspace-User-Context": base64.b64encode(json.dumps(user).encode()).decode(), **H}


def _upload(client, entries, **form):
    files = [("files", (os.path.basename(path), data, "application/octet-stream")) for path, data in entries]
    data = {"workspace_id": "nc_ws", "paths": [path for path, _ in entries], **form}
    return client.post("/files/upload", data=data, files=files, headers=_ctx())


def test_upload_many_files_and_a_folder(client):
    assert _create(client).status_code == 200
    resp = _upload(
        client,
        [("one.txt", b"1"), ("photos/2024/pic.jpg", b"\xff\xd8\xff binary"), ("photos/notes.md", b"# n")],
        relative_path="incoming",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert sorted(u["relative_path"] for u in body["uploaded"]) == [
        "incoming/one.txt", "incoming/photos/2024/pic.jpg", "incoming/photos/notes.md",
    ]
    with open(os.path.join(_TEST_WS_ROOT, "nc_ws/incoming/photos/2024/pic.jpg"), "rb") as fh:
        assert fh.read() == b"\xff\xd8\xff binary"


def test_upload_refuses_traversal_and_git(client):
    assert _create(client).status_code == 200
    resp = _upload(client, [("../escape.txt", b"x"), (".git/hooks/pre-commit", b"x"), ("ok.txt", b"ok")])
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "PARTIAL"
    assert [u["relative_path"] for u in body["uploaded"]] == ["ok.txt"]
    assert len(body["errors"]) == 2
    assert not os.path.exists(os.path.join(_TEST_WS_ROOT, "escape.txt"))


def test_upload_overwrite_false_skips_existing(client):
    assert _create(client).status_code == 200
    _upload(client, [("a.txt", b"first")])
    resp = _upload(client, [("a.txt", b"second")], overwrite="false")
    assert resp.json()["skipped"] == [{"relative_path": "a.txt", "skipped": True, "reason": "exists"}]
    with open(os.path.join(_TEST_WS_ROOT, "nc_ws/a.txt"), "rb") as fh:
        assert fh.read() == b"first"


def test_upload_over_the_limit_is_refused(client, monkeypatch):
    import services.config as config

    assert _create(client).status_code == 200
    monkeypatch.setattr(config, "WORKSPACE_UPLOAD_MAX_BYTES", 10)
    resp = _upload(client, [("big.bin", b"x" * 100)])
    assert resp.status_code == 413
    assert "WORKSPACE_UPLOAD_MAX_BYTES" in resp.json()["detail"]


def test_upload_needs_an_identity_for_authenticated_workspaces(client):
    assert _create(client).status_code == 200
    resp = client.post(
        "/files/upload",
        data={"workspace_id": "nc_ws", "paths": ["a.txt"]},
        files=[("files", ("a.txt", b"x", "text/plain"))],
        headers=H,
    )
    assert resp.status_code == 400
    assert "User context" in resp.json()["detail"]


def test_upload_rejects_a_malformed_identity_header(client):
    resp = client.post(
        "/files/upload",
        data={"workspace_id": "nc_ws"},
        files=[("files", ("a.txt", b"x", "text/plain"))],
        headers={**H, "X-Workspace-User-Context": "%%%not-base64"},
    )
    assert resp.status_code == 400


def test_upload_into_a_nextcloud_workspace_schedules_a_sync(client, scheduled):
    assert _create(client, sync_mode="nextcloud", nextcloud_path="/Work/NC").status_code == 200
    assert _upload(client, [("a.txt", b"x")]).status_code == 200
    assert scheduled == ["nc_ws"]
