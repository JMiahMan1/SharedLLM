import pytest
import aiohttp
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch
from services.gateway import main as gateway_main
from services.gateway.main import app, IDENTITY_SVC, STORAGE_SVC, RAG_SVC, INTERNAL_SECRET


def _aio_resp(status=200, json_data=None, text=""):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data if json_data is not None else {"status": "SUCCESS"})
    m.text = AsyncMock(return_value=text)
    return m


client = TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}


def _make_session(resolve_data, storage_post_data=None, storage_post_status=200,
                  rag_stats=None):
    """Build a mock aiohttp session; monkeypatch get_http_client to return it."""
    async def post_side_effect(url, **kwargs):
        if "resolve" in url:
            return _aio_resp(200, resolve_data)
        if "providers/list" in url or "index/full" in url:
            return _aio_resp(storage_post_status, storage_post_data)
        return _aio_resp(200, {})

    async def get_side_effect(url, **kwargs):
        if "rag/stats" in url:
            return _aio_resp(200, rag_stats or {"total_chunks": 100, "total_documents": 10})
        return _aio_resp(200, {})

    sess = AsyncMock()
    sess.post.side_effect = post_side_effect
    sess.get.side_effect = get_side_effect
    sess.__aenter__.return_value = sess
    sess.__aexit__.return_value = False
    return sess


@pytest.fixture
def patched_session(monkeypatch):
    """Helper fixture factory isn't used directly; see individual tests."""
    return None


def test_storage_list_proxy(auth_headers, monkeypatch):
    sess = _make_session(
        resolve_data={"user": "testuser", "nextcloud_url": "http://nc.local",
                      "nextcloud_user": "ncuser", "nextcloud_pass": "ncpass"},
        storage_post_data={"status": "SUCCESS",
                           "entries": [{"path": "/test.txt", "name": "test.txt", "is_dir": False}]},
    )
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    response = client.post("/api/storage/list", json={"path": "/"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert len(data["entries"]) == 1
    storage_calls = [c for c in sess.post.call_args_list if "providers/list" in str(c[0][0])]
    assert storage_calls, "storage providers/list was not called"
    sent_payload = storage_calls[0][1]["json"]
    assert sent_payload["provider"]["settings"]["username"] == "ncuser"
    assert sent_payload["provider"]["settings"]["password"] == "ncpass"


def test_storage_index_proxy(auth_headers, monkeypatch):
    sess = _make_session(
        resolve_data={"user": "testuser", "nextcloud_url": "http://nc.local",
                      "nextcloud_user": "ncuser", "nextcloud_pass": "ncpass"},
        storage_post_data={"status": "ACCEPTED"},
        storage_post_status=202,
    )
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    response = client.post("/api/storage/index", json={"path": "/"}, headers=auth_headers)
    assert response.status_code == 202
    assert response.json()["status"] == "ACCEPTED"


def test_storage_stats_proxy(monkeypatch):
    sess = _make_session(
        resolve_data={"user": "testuser", "nextcloud_user": "ncuser"},
        rag_stats={"total_chunks": 100, "total_documents": 10},
    )
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    response = client.get("/api/storage/stats")
    assert response.status_code == 200
    assert response.json()["total_chunks"] == 100
