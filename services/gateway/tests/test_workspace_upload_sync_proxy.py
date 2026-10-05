"""Gateway routes for workspace uploads, Nextcloud sync and file moves.

Pins that uploads stream through as multipart with the caller's identity in a
header the browser cannot set, and that the sync routes always resolve the
caller instead of trusting a client-supplied user_context.
"""
import base64
import json


def test_upload_streams_multipart_with_identity_header(client, runtime_upstream):
    resp = client.post(
        "/api/workspaces/files/upload",
        data={"workspace_id": "w1", "paths": ["dir/a.txt"], "dirs": ["dir/empty"]},
        files=[("files", ("a.txt", b"hello", "text/plain"))],
        headers={"X-Workspace-User-Context": base64.b64encode(b'{"user":"mallory"}').decode()},
    )
    assert resp.status_code == 200
    assert runtime_upstream["url"].endswith("/files/upload")
    headers = runtime_upstream["headers"]
    assert headers["Content-Type"].startswith("multipart/form-data")
    # The identity is the gateway's, not whatever the browser sent.
    assert json.loads(base64.b64decode(headers["X-Workspace-User-Context"])) == runtime_upstream["caller"]
    assert b"hello" in runtime_upstream["body"] and b"dir/a.txt" in runtime_upstream["body"]


def test_upload_rejects_non_multipart(client, runtime_upstream):
    resp = client.post("/api/workspaces/files/upload", json={"workspace_id": "w1"})
    assert resp.status_code == 415


def test_sync_resolves_the_caller_and_ignores_a_supplied_user_context(client, runtime_upstream):
    resp = client.post(
        "/api/workspaces/sync",
        json={"workspace_id": "w1", "direction": "both", "user_context": {"user": "mallory", "is_admin": True}},
    )
    assert resp.status_code == 200
    assert runtime_upstream["url"].endswith("/provider/sync/workspace")
    assert runtime_upstream["json"]["user_context"] == runtime_upstream["caller"]
    assert runtime_upstream["json"]["direction"] == "both"


def test_sync_reset_route(client, runtime_upstream):
    resp = client.post("/api/workspaces/sync/reset", json={"workspace_id": "w1", "confirm": True})
    assert resp.status_code == 200
    assert runtime_upstream["url"].endswith("/provider/sync/workspace/reset")


def test_move_route_exists(client, runtime_upstream):
    resp = client.post(
        "/api/workspaces/files/move",
        json={"workspace_id": "w1", "relative_path": "a.txt", "new_relative_path": "b.txt"},
    )
    assert resp.status_code == 200
    assert runtime_upstream["url"].endswith("/files/move")
