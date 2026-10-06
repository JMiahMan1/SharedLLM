from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


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
        # The gateway validates a presented API key against Identity's strict,
        # no-fallback endpoint before it will serve a route. Model a real key.
        if "validate-api-key" in url:
            return _aio_resp(200, {"user": "testuser", "user_id": 1, "is_admin": False})
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


def test_storage_stats_proxy(monkeypatch, auth_headers):
    sess = _make_session(
        resolve_data={"user": "testuser", "nextcloud_user": "ncuser"},
        rag_stats={"total_chunks": 100, "total_documents": 10},
    )
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    response = client.get("/api/storage/stats", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total_chunks"] == 100


def test_storage_stats_needs_a_real_key(monkeypatch):
    """No key, no stats: this route used to answer with the admin's counts."""
    sess = _make_session(
        resolve_data={"user": "default", "nextcloud_user": "ncuser"},
        rag_stats={"total_chunks": 100, "total_documents": 10},
    )
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    response = client.get("/api/storage/stats")
    assert response.status_code == 401


def _index_payload(sess):
    calls = [c for c in sess.post.call_args_list if "index/full" in str(c[0][0])]
    assert calls, "storage /index/full was not called"
    return calls[0][1]["json"]


def _nextcloud_creds():
    return {"user": "testuser", "nextcloud_url": "http://nc.local",
            "nextcloud_user": "ncuser", "nextcloud_pass": "ncpass"}


def test_storage_index_defaults_to_nextcloud_and_sends_no_library_path(auth_headers, monkeypatch):
    """The Calibre-only field must not travel on a Nextcloud crawl.

    Sending ``library_path`` to the nextcloud provider is harmless today, but it
    would become a silent second source of truth the moment the nextcloud
    provider grew a path option of its own.
    """
    sess = _make_session(resolve_data=_nextcloud_creds(), storage_post_data={"status": "ACCEPTED"},
                         storage_post_status=202)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    response = client.post("/api/storage/index", json={"path": "/"}, headers=auth_headers)
    assert response.status_code == 202
    payload = _index_payload(sess)
    assert payload["provider"]["kind"] == "nextcloud"
    assert "library_path" not in payload["provider"]["settings"]


def test_storage_index_forwards_a_calibre_crawl_with_the_request_library_path(auth_headers, monkeypatch):
    """The gateway used to hardcode ``kind: nextcloud``, so a Calibre library
    was unreachable from the only surface the UI and Raven are allowed to use."""
    sess = _make_session(resolve_data=_nextcloud_creds(), storage_post_data={"status": "ACCEPTED"},
                         storage_post_status=202)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    monkeypatch.setattr(gateway_main, "fetch_global_setting", _never_asked)
    response = client.post(
        "/api/storage/index",
        json={"provider_kind": "calibre", "library_path": "/Books/Text"},
        headers=auth_headers,
    )
    assert response.status_code == 202
    settings = _index_payload(sess)["provider"]
    assert settings["kind"] == "calibre"
    assert settings["settings"]["library_path"] == "/Books/Text"
    assert settings["settings"]["username"] == "ncuser"


def test_storage_index_reads_the_calibre_library_path_setting_when_the_request_omits_it(auth_headers, monkeypatch):
    sess = _make_session(resolve_data=_nextcloud_creds(), storage_post_data={"status": "ACCEPTED"},
                         storage_post_status=202)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    seen = []

    async def fake_setting(key):
        seen.append(key)
        return "/Books/Text"

    monkeypatch.setattr(gateway_main, "fetch_global_setting", fake_setting)
    response = client.post("/api/storage/index", json={"provider_kind": "calibre"},
                           headers=auth_headers)
    assert response.status_code == 202
    assert seen == ["calibre_library_path"]
    assert _index_payload(sess)["provider"]["settings"]["library_path"] == "/Books/Text"


def test_storage_index_refuses_a_calibre_crawl_with_no_library_path_anywhere(auth_headers, monkeypatch):
    """A guessed library path would index the wrong shelf in silence."""
    sess = _make_session(resolve_data=_nextcloud_creds(), storage_post_data={"status": "ACCEPTED"},
                         storage_post_status=202)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    monkeypatch.setattr(gateway_main, "fetch_global_setting", _setting_is_blank)
    response = client.post("/api/storage/index", json={"provider_kind": "calibre"},
                           headers=auth_headers)
    assert response.status_code == 400
    assert "calibre_library_path" in response.json()["detail"]
    assert not [c for c in sess.post.call_args_list if "index/full" in str(c[0][0])]


def test_storage_index_treats_a_whitespace_library_path_as_unset(auth_headers, monkeypatch):
    sess = _make_session(resolve_data=_nextcloud_creds(), storage_post_data={"status": "ACCEPTED"},
                         storage_post_status=202)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    monkeypatch.setattr(gateway_main, "fetch_global_setting", _setting_is_blank)
    response = client.post("/api/storage/index",
                           json={"provider_kind": "calibre", "library_path": "   "},
                           headers=auth_headers)
    assert response.status_code == 400


def test_storage_index_rejects_an_unknown_provider_kind(auth_headers, monkeypatch):
    sess = _make_session(resolve_data=_nextcloud_creds(), storage_post_data={"status": "ACCEPTED"},
                         storage_post_status=202)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    response = client.post("/api/storage/index", json={"provider_kind": "dropbox"},
                           headers=auth_headers)
    assert response.status_code == 422


async def _never_asked(key):
    """Assert the request already carried the path, so config is not consulted."""
    raise AssertionError(f"the global setting {key!r} should not have been consulted")


async def _setting_is_blank(key):
    return ""
