"""The podcast routes: the show report for a member, and one setting to change.

"How come my podcasts are months old" has three unrelated answers (no feed
address, a feed nobody polls, nothing downloading the new episodes), so the
route returns them apart rather than a single "stale" flag. Nothing on this
path may know a show by name.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.gateway import main as gateway_main

REPORT = {
    "libraries": [{"id": "pods", "name": "Podcasts"}],
    "shows": [
        {
            "library_id": "pods",
            "library_name": "Podcasts",
            "id": "show-1",
            "title": "A Show",
            "author": "Someone",
            "cover_path": "/metadata/items/show-1/cover.jpg",
            "episodes": 11,
            "newest_episode_at": "2026-07-28T07:00:00Z",
            "newest_episode_title": "An Episode",
            "last_checked_at": None,
            "days_since_episode": 120,
            "days_since_check": None,
            "auto_download": False,
            "has_feed": False,
            "needs_feed": True,
            "never_checked": True,
            "stale_check": True,
            "behind": True,
        }
    ],
    "behind_days": 21,
    "stale_check_days": 7,
}


def test_the_report_requires_a_caller(client):
    """An unproven caller is a 401, never an empty list that looks like "no podcasts"."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            gateway_main,
            "_resolve_identity_from_request",
            AsyncMock(side_effect=HTTPException(status_code=401, detail="Authentication required")),
        )
        resp = client.get("/api/media/audiobookshelf/podcasts")

    assert resp.status_code == 401


def test_the_report_is_read_from_the_callers_own_server(client):
    asked = {}

    async def exec_abs(creds, params, *, timeout=10.0):
        asked["params"] = params
        asked["user"] = creds.get("user")
        asked["timeout"] = timeout
        return REPORT

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        resp = client.get("/api/media/audiobookshelf/podcasts")

    assert resp.status_code == 200
    assert asked["params"] == {"action": "podcasts"}
    assert asked["user"] == "testuser"
    # The report is one round trip per show, so it is not held to a listing's timeout.
    assert asked["timeout"] > 10.0

    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["shows"][0]["title"] == "A Show"
    assert data["behind_days"] == 21
    assert data["stale_check_days"] == 7


def test_an_unreachable_server_says_so_without_inventing_shows(client):
    async def exec_abs(creds, params, *, timeout=10.0):
        raise gateway_main._MediaUpstreamError("Audiobookshelf is unreachable (TimeoutError)")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        resp = client.get("/api/media/audiobookshelf/podcasts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["shows"] == []
    assert data["notice"] == "ABS unavailable"


def test_auto_download_is_sent_for_one_show(client):
    # The identity lookup shares this client, so keep every post and pick the ABS one.
    posts = []

    class _Resp:
        status = 200

        async def json(self):
            return {"status": "SUCCESS", "detail": {"item_id": "show-1", "auto_download": True}}

    class _Client:
        async def post(self, url, **kwargs):
            posts.append({"url": url, "json": kwargs.get("json")})
            return _Resp()

    class _Ctx:
        async def __aenter__(self):
            return _Client()

        async def __aexit__(self, *exc):
            return False

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx())
        resp = client.post(
            "/api/media/audiobookshelf/podcasts/show-1/auto-download", json={"enabled": True}
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "SUCCESS", "item_id": "show-1", "auto_download": True}
    sent = [post for post in posts if (post["json"] or {}).get("action") == "podcast_auto_download"]
    assert len(sent) == 1
    assert sent[0]["json"]["item_id"] == "show-1"
    assert sent[0]["json"]["auto_download"] is True


def test_a_refused_auto_download_is_reported_not_swallowed(client):
    class _Resp:
        status = 200

        async def json(self):
            return {"status": "FAILURE", "message": "ABS returned 403: Forbidden"}

    class _Client:
        async def post(self, url, **kwargs):
            return _Resp()

    class _Ctx:
        async def __aenter__(self):
            return _Client()

        async def __aexit__(self, *exc):
            return False

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx())
        resp = client.post(
            "/api/media/audiobookshelf/podcasts/show-1/auto-download", json={"enabled": False}
        )

    assert resp.status_code == 502
    assert "Forbidden" in resp.json()["message"]
