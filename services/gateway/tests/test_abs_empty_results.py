"""BUG-15 (P2-T6): an EMPTY result list is SUCCESS, not "ABS unavailable".

The gateway endpoints treated `detail.get("books")`/`detail.get("libraries")`
falsy as "execution never answered" and appended a bogus notice, so an empty
library/last-played looked like the whole ABS integration was down. Presence
of the key (`in detail`) is the real signal that execution answered.
"""
from unittest.mock import patch

from services.gateway import main as gateway_main


class _Resp:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    async def json(self):
        return self._data


class _ExecClient:
    """Stands in for the pooled session yielded by shared_http_client."""

    def __init__(self, data, status=200):
        self._data = data
        self._status = status

    async def get(self, url, **kwargs):
        return _Resp(self._status, self._data)


def _ctx(client):
    class _Ctx:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc):
            return False

    return _Ctx()


def _patch_exec(data, status=200):
    return patch.object(
        gateway_main,
        "shared_http_client",
        new=lambda: _ctx(_ExecClient(data, status)),
    )


def test_abs_library_empty_books_is_success(client):
    payload = {"status": "SUCCESS", "detail": {"books": []}}
    with _patch_exec(payload):
        resp = client.get("/api/media/audiobookshelf/library/lib1")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "SUCCESS", "books": []}
    assert "notice" not in data


def test_abs_last_played_empty_books_is_success(client):
    payload = {"status": "SUCCESS", "detail": {"books": []}}
    with _patch_exec(payload):
        resp = client.get("/api/media/audiobookshelf/last-played")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "SUCCESS", "books": []}
    assert "notice" not in data


def test_abs_libraries_empty_list_is_success(client):
    payload = {"status": "SUCCESS", "detail": {"libraries": []}}
    with _patch_exec(payload):
        resp = client.get("/api/media/audiobookshelf/libraries")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "SUCCESS", "libraries": []}
    assert "notice" not in data


def test_abs_unreachable_execution_still_reports_unavailable(client):
    """The notice stays for the genuine failure path (execution errored)."""
    payload = {"detail": {}}
    with _patch_exec(payload, status=500):
        resp = client.get("/api/media/audiobookshelf/library/lib1")

    assert resp.status_code == 200
    assert resp.json().get("notice") == "ABS unavailable"
