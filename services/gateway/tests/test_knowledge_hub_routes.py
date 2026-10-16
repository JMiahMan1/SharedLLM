"""The Knowledge Hub's gateway routes: who may call them and what they send.

Verified live before these were tightened, with no credentials at all:

    GET /api/storage/learning                        -> 200, every Raven lesson
    GET /api/storage/collection/system_learnings     -> 200, raw collection rows

and PATCH/DELETE ``/api/storage/learning/{id}`` had no identity check at all.
Purging ``nextcloud_files`` removed nothing for anyone whose Nextcloud
username differs from their Jarvis one (storage indexes under the Nextcloud
name), and the stats merge counted the system-wide collections twice.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

client = TestClient(app, raise_server_exceptions=False)
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def _no_identity_cache(monkeypatch):
    from services.gateway import cache as gw_cache

    async def _uncached(_body, factory):
        return await factory()

    monkeypatch.setattr(gw_cache, "get_cached_identity", _uncached)


def _resp(status=200, data=None):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=data if data is not None else {"status": "SUCCESS"})
    m.text = AsyncMock(return_value="")
    return m


def _session(monkeypatch, *, is_admin=False, rag_stats=None, purge_removed=None, search=None):
    """Identity says the key is ``jeremiah`` (Nextcloud ``summers``)."""

    async def post(url, **kwargs):
        if "resolve" in url and "validate" not in url:
            return _resp(200, {"user": "jeremiah", "nextcloud_user": "summers", "is_admin": is_admin})
        if "rag/purge" in url:
            owner = kwargs["json"]["user_id"]
            return _resp(200, {"status": "SUCCESS", "removed": (purge_removed or {}).get(owner, 0)})
        if "rag/search" in url:
            return _resp(200, {"results": (search or {}).get(kwargs["json"]["user_id"], [])})
        return _resp(200, {})

    async def get(url, **kwargs):
        if "validate-api-key" in url:
            return _resp(200, {"user": "jeremiah", "user_id": 1, "is_admin": is_admin})
        if "rag/stats" in url:
            owner = url.split("user_id=")[1]
            return _resp(200, (rag_stats or {}).get(owner, {"status": "SUCCESS", "breakdown": {}}))
        return _resp(200, {"status": "SUCCESS", "items": []})

    sess = AsyncMock()
    sess.post.side_effect = post
    sess.get.side_effect = get
    sess.patch.return_value = _resp(200, {"status": "SUCCESS"})
    sess.delete.return_value = _resp(200, {"status": "SUCCESS"})
    sess.__aenter__.return_value = sess
    sess.__aexit__.return_value = False
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    return sess


# --- who may call -------------------------------------------------------------

@pytest.mark.parametrize("verb,path", [
    ("get", "/api/storage/learning"),
    ("get", "/api/storage/collection/system_learnings"),
    ("patch", "/api/storage/learning/abc"),
    ("delete", "/api/storage/learning/abc"),
])
def test_anonymous_callers_are_refused(monkeypatch, verb, path):
    sess = _session(monkeypatch)
    kwargs = {"json": {"content": "x"}} if verb == "patch" else {}
    resp = getattr(client, verb)(path, **kwargs)
    assert resp.status_code == 401, resp.text
    assert not sess.patch.called and not sess.delete.called


@pytest.mark.parametrize("verb,path", [
    ("get", "/api/storage/collection/nextcloud_files?user_id=michele"),
    ("patch", "/api/storage/learning/abc"),
    ("delete", "/api/storage/learning/abc"),
])
def test_family_members_cannot_inspect_collections_or_rewrite_lessons(monkeypatch, verb, path):
    sess = _session(monkeypatch, is_admin=False)
    kwargs = {"json": {"content": "x"}} if verb == "patch" else {}
    resp = getattr(client, verb)(path, headers=AUTH, **kwargs)
    assert resp.status_code == 403, resp.text
    assert not sess.patch.called and not sess.delete.called


def test_a_signed_in_user_can_read_lessons(monkeypatch):
    _session(monkeypatch, is_admin=False)
    assert client.get("/api/storage/learning", headers=AUTH).status_code == 200


def test_an_admin_can_edit_and_delete_a_lesson(monkeypatch):
    sess = _session(monkeypatch, is_admin=True)
    assert client.patch("/api/storage/learning/abc", json={"content": "x"}, headers=AUTH).status_code == 200
    assert client.delete("/api/storage/learning/abc", headers=AUTH).status_code == 200
    assert sess.patch.call_args[0][0].endswith("/rag/learning/abc")
    assert sess.delete.call_args[0][0].endswith("/rag/learning/abc")


def test_the_unused_open_applied_route_is_gone(monkeypatch):
    """Only the agent loop marks a lesson applied, and it calls RAG directly."""
    sess = _session(monkeypatch)
    resp = client.patch("/api/storage/learning/abc/applied")
    assert resp.status_code in (404, 405)
    assert not sess.patch.called


# --- what is sent -------------------------------------------------------------

def test_purge_covers_the_nextcloud_username_too(monkeypatch):
    sess = _session(monkeypatch, purge_removed={"summers": 2610})
    resp = client.post("/api/storage/purge/nextcloud_files", json={}, headers=AUTH)

    assert resp.status_code == 200, resp.text
    owners = [c[1]["json"]["user_id"] for c in sess.post.call_args_list if "rag/purge" in c[0][0]]
    assert owners == ["jeremiah", "summers"]
    assert resp.json()["removed"] == 2610


def test_stats_do_not_count_system_collections_twice(monkeypatch):
    shared = {"chunks": 62, "documents": 62}
    _session(monkeypatch, rag_stats={
        "jeremiah": {"status": "SUCCESS", "total_chunks": 62, "total_documents": 62,
                     "breakdown": {"system_capabilities": shared}},
        "summers": {"status": "SUCCESS", "total_chunks": 2672, "total_documents": 162,
                    "breakdown": {"system_capabilities": shared,
                                  "nextcloud_files": {"chunks": 2610, "documents": 100}}},
    })
    body = client.get("/api/storage/stats", headers=AUTH).json()

    assert body["breakdown"]["system_capabilities"] == shared
    assert body["breakdown"]["nextcloud_files"]["chunks"] == 2610
    assert body["total_chunks"] == 62 + 2610
    assert body["total_documents"] == 62 + 100


def test_search_hits_carry_their_own_text_and_collection(monkeypatch):
    _session(monkeypatch, search={"jeremiah": [
        {"content": "Kitchen light is in the kitchen", "score": 0.9,
         "metadata": {"entity_id": "light.kitchen", "friendly_name": "Kitchen", "collection_name": "ha_entities"}},
        {"content": "Budget notes", "score": 0.5,
         "metadata": {"path": "/Budget.md", "collection_name": "nextcloud_files"}},
    ]})
    body = client.get("/api/search?q=kitchen", headers=AUTH).json()

    assert [f["collection"] for f in body["files"]] == ["ha_entities", "nextcloud_files"]
    assert body["files"][1]["snippet"] == "Budget notes"
