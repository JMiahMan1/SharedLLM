"""The storage-status proxy and the identity it must not skip.

``POST /api/storage/index`` answers 202 and crawls in the background, so this
route is the only window onto a running crawl -- and like every other storage
route it answers 401 rather than serving index state to an unproven caller.
The crawl payload itself is produced by ``services/storage``; what is pinned
here is that it arrives intact and that no key means no view.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

client = TestClient(app)
AUTH = {"Authorization": "Bearer test-token"}

CRAWL_PAYLOAD = {
    "status": "SUCCESS",
    "indexer": "IDLE",
    "crawl": {
        "active": True,
        "kind": "calibre",
        "path": "/Books/Text",
        "phase": "extracting",
        "done": 120,
        "total": 3079,
    },
    "checkpointed_files": 42,
}


@pytest.fixture(autouse=True)
def _no_identity_cache(monkeypatch):
    """Bypass resolve_identity's body-keyed cache for this file.

    Several storage test files present the same ``test-token``; whichever
    resolves first would otherwise answer for the others."""
    from services.gateway import cache as gw_cache

    async def _uncached(_body, factory):
        return await factory()

    monkeypatch.setattr(gw_cache, "get_cached_identity", _uncached)


def _aio_resp(status=200, json_data=None):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data if json_data is not None else {"status": "SUCCESS"})
    m.text = AsyncMock(return_value="")
    return m


def _session(status_payload=None, status_code=200, validate_status=200):
    async def post_side_effect(url, **kwargs):
        return _aio_resp(200, {"user": "alice", "user_id": 1, "is_admin": False})

    async def get_side_effect(url, **kwargs):
        if "validate-api-key" in url:
            if validate_status != 200:
                return _aio_resp(validate_status, {"detail": "bad key"})
            return _aio_resp(200, {"user": "alice", "user_id": 1, "is_admin": False})
        if url.rstrip("/").endswith("/status"):
            return _aio_resp(status_code, status_payload if status_payload is not None else CRAWL_PAYLOAD)
        return _aio_resp(200, {})

    sess = AsyncMock()
    sess.post.side_effect = post_side_effect
    sess.get.side_effect = get_side_effect
    sess.__aenter__.return_value = sess
    sess.__aexit__.return_value = False
    return sess


def _patch(monkeypatch, **kwargs):
    sess = _session(**kwargs)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    return sess


def _storage_status_calls(sess):
    return [
        c for c in sess.get.call_args_list
        if str(c[0][0]).rstrip("/").endswith("/status")
    ]


def test_status_forwards_the_crawl_record(monkeypatch):
    sess = _patch(monkeypatch)
    resp = client.get("/api/storage/status", headers=AUTH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["crawl"]["active"] is True
    assert body["crawl"]["kind"] == "calibre"
    assert body["crawl"]["done"] == 120
    assert body["checkpointed_files"] == 42
    assert _storage_status_calls(sess), "storage /status was never called"


def test_status_needs_a_real_key(monkeypatch):
    sess = _patch(monkeypatch, validate_status=401)
    resp = client.get("/api/storage/status", headers=AUTH)

    assert resp.status_code == 401
    assert not _storage_status_calls(sess), "unauthenticated caller reached storage"


def test_status_without_any_key_is_refused(monkeypatch):
    sess = _patch(monkeypatch)
    resp = client.get("/api/storage/status")

    assert resp.status_code == 401
    assert not _storage_status_calls(sess)


def test_an_upstream_failure_keeps_its_status(monkeypatch):
    sess = _patch(monkeypatch, status_payload={"detail": "storage down"}, status_code=503)
    resp = client.get("/api/storage/status", headers=AUTH)

    assert resp.status_code == 503
    assert resp.json().get("detail") == "storage down"
