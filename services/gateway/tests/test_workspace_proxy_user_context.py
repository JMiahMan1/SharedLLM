"""Workspace proxies must act as the authenticated caller.

A browser-supplied user_context used to pass straight through to the
workspace runtime when present, so a caller could claim to be another user
(or an admin). The gateway now always replaces it with the resolved caller.
"""
import pytest

from services.gateway import main as gateway_main


@pytest.mark.parametrize(
    "path,runtime_path",
    [
        ("/api/workspaces/files/write", "/files/write"),
        ("/api/workspaces/files/raw", "/files/raw"),
        ("/api/workspaces/files/zip", "/files/zip"),
    ],
)
def test_workspace_proxies_replace_a_client_supplied_user_context(client, runtime_upstream, path, runtime_path):
    client.post(path, json={"workspace_id": "w1", "user_context": {"user": "mallory", "is_admin": True}})
    assert runtime_upstream["url"].endswith(runtime_path)
    assert runtime_upstream["json"]["user_context"] == runtime_upstream["caller"]


def test_anonymous_proxy_call_carries_no_user_context(client, runtime_upstream, monkeypatch):
    from fastapi import HTTPException

    async def _anonymous(request, body):
        raise HTTPException(status_code=401, detail="Authentication required")

    monkeypatch.setattr(gateway_main, "_resolve_user_context", _anonymous)
    client.post("/api/workspaces/files/read", json={"workspace_id": "w1", "user_context": {"user": "mallory"}})
    assert "user_context" not in runtime_upstream["json"]
