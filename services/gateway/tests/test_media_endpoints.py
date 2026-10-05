"""Unified media endpoints (docs/MEDIA_OVERHAUL.md §7.5).

Each test drives one endpoint through the authenticated ``client`` fixture and
mocks the two upstreams at their real boundaries: ``main._ma_rpc`` for Music
Assistant (a recorder keyed by command) and aioresponses for the execution
service's ABS proxy. The endpoints' promise is that one dead upstream never
fails the whole response -- it lands in ``errors`` and the rest still renders.
"""
import aiohttp
import pytest
from conftest_media import mock_upstream
from fastapi.responses import JSONResponse

from services.gateway import main as gateway_main


@pytest.fixture
def ma(monkeypatch):
    """Answer MA JSON-RPC commands from a dict; record every call.

    ``_ma_rpc`` receives the command name and args as arguments, so a recorder
    keyed by command is both more precise and less brittle than matching JSON
    bodies against aiohttp mock URLs.
    """
    calls: list[tuple[str, dict]] = []
    responses: dict[str, object] = {}

    async def fake_ma_rpc(
        mass_url, mass_token, command, args=None, *, message_id=None, timeout=10.0
    ):
        calls.append((command, args or {}))
        handler = responses.get(command)
        if isinstance(handler, BaseException):
            raise handler
        if callable(handler):
            return handler(args or {})
        if handler is None:
            return []
        return handler

    monkeypatch.setattr(gateway_main, "_ma_rpc", fake_ma_rpc)
    return {"calls": calls, "responses": responses}


ABS_DETAIL_URL = f"{gateway_main.EXECUTION_SVC}/execute/audiobookshelf"


def test_home_merges_ma_shelves_with_abs_last_played(client, upstream, ma):
    ma["responses"]["music/recently_played_items"] = [
        {"uri": "library://track/1", "name": "Recent Song"}
    ]
    ma["responses"]["music/in_progress_items"] = [
        {"uri": "library://audiobook/9", "name": "Continue Book", "media_type": "audiobook"}
    ]
    ma["responses"]["music/playlists/library_items"] = [
        {"uri": "library://playlist/6", "name": "Road Trip"}
    ]
    ma["responses"]["music/tracks/library_items"] = [
        {"uri": "library://track/2", "name": "Fav Song", "favorite": True}
    ]
    ma["responses"]["music/radios/library_items"] = []
    mock_upstream(
        upstream,
        "GET",
        ABS_DETAIL_URL,
        payload={
            "status": "SUCCESS",
            "detail": {
                "books": [{"id": "b1", "title": "Abs Book", "author": "A", "duration": 3600}]
            },
        },
    )

    resp = client.get("/api/media/home")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["name"] for item in body["recent"]] == ["Recent Song", "Abs Book"]
    assert body["recent"][1]["uri"] == "abs://b1"
    assert body["continue"][0]["name"] == "Continue Book"
    assert body["playlists"][0]["name"] == "Road Trip"
    assert body["favorites"][0]["name"] == "Fav Song"
    assert body["radio"] == []
    assert body["errors"] == {"ma": None, "abs": None}
    # The favorites shelf must ask MA for favorites (live-verified param).
    favorite_calls = [
        args
        for command, args in ma["calls"]
        if command == "music/tracks/library_items"
    ]
    assert favorite_calls and favorite_calls[0]["favorite"] is True


def test_home_partial_when_ma_is_unreachable(client, upstream, ma):
    for command in (
        "music/recently_played_items",
        "music/in_progress_items",
        "music/playlists/library_items",
        "music/tracks/library_items",
        "music/radios/library_items",
    ):
        ma["responses"][command] = aiohttp.ClientConnectionError("boom")
    mock_upstream(
        upstream,
        "GET",
        ABS_DETAIL_URL,
        payload={
            "status": "SUCCESS",
            "detail": {"books": [{"id": "b1", "title": "Abs Book"}]},
        },
    )

    resp = client.get("/api/media/home")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["name"] for item in body["recent"]] == ["Abs Book"]
    assert body["continue"] == []
    assert "Music Assistant is unreachable" in body["errors"]["ma"]
    assert body["errors"]["abs"] is None


def test_home_partial_when_abs_is_down(client, upstream, ma):
    ma["responses"]["music/recently_played_items"] = [
        {"uri": "library://track/1", "name": "Recent Song"}
    ]
    mock_upstream(upstream, "GET", ABS_DETAIL_URL, payload={"status": "FAILURE"}, status=500)

    resp = client.get("/api/media/home")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["name"] for item in body["recent"]] == ["Recent Song"]
    assert body["errors"]["ma"] is None
    assert "Audiobookshelf is unreachable" in body["errors"]["abs"]


def test_search_buckets_and_exact_name_top(client, upstream, ma):
    ma["responses"]["music/search"] = {
        "tracks": [
            {
                "uri": "library://track/1",
                "name": "Blue Train",
                "artists": [{"name": "John Coltrane"}],
                "duration": 640,
            }
        ],
        "albums": [{"uri": "library://album/1", "name": "Blue Train (Deluxe)"}],
    }
    mock_upstream(
        upstream,
        "GET",
        ABS_DETAIL_URL,
        payload={
            "status": "SUCCESS",
            "detail": {
                "books": [{"id": "b1", "title": "Blue Train: The Book", "author": "A"}],
                "podcasts": [],
                "authors": [{"id": "a1", "name": "John Coltrane"}],
            },
        },
    )

    resp = client.get("/api/media/search", params={"q": "Blue Train"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["top"]["uri"] == "library://track/1"
    assert [item["name"] for item in body["tracks"]] == ["Blue Train"]
    assert body["tracks"][0]["artist"] == "John Coltrane"
    assert [item["name"] for item in body["albums"]] == ["Blue Train (Deluxe)"]
    assert [item["name"] for item in body["audiobooks"]] == ["Blue Train: The Book"]
    assert body["authors"][0]["uri"] == "abs://author/a1"
    assert body["errors"] == {"ma": None, "abs": None}
    ma_args = [args for command, args in ma["calls"] if command == "music/search"][0]
    assert ma_args["search_query"] == "Blue Train"
    assert ma_args["config"] == {"providers": ["library"]}


def test_search_requires_a_query(client):
    resp = client.get("/api/media/search")
    assert resp.status_code == 422


def test_search_with_ma_only_types_skips_abs(client, upstream, ma, monkeypatch):
    called: list[dict] = []

    async def fake_abs(creds, params, *, timeout=10.0):
        called.append(params)
        return {}

    monkeypatch.setattr(gateway_main, "_media_exec_audiobookshelf", fake_abs)
    ma["responses"]["music/search"] = {
        "tracks": [{"uri": "library://track/1", "name": "x"}]
    }

    resp = client.get("/api/media/search", params={"q": "x", "types": "tracks"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert called == []
    assert body["errors"]["abs"] is None
    assert body["audiobooks"] == []


def test_item_ma_returns_children(client, ma):
    ma["responses"]["music/item_by_uri"] = {
        "uri": "library://playlist/6",
        "name": "Road Trip",
        "media_type": "playlist",
        "item_id": 6,
        "provider": "library",
    }
    ma["responses"]["music/playlists/playlist_tracks"] = [
        {"uri": "library://track/1", "name": "Track One", "duration": 120}
    ]

    resp = client.get("/api/media/item", params={"uri": "library://playlist/6"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Road Trip"
    assert body["media_type"] == "playlist"
    assert [child["name"] for child in body["children"]] == ["Track One"]
    child_args = [
        args for command, args in ma["calls"] if command == "music/playlists/playlist_tracks"
    ][0]
    # Live-verified 2026-10-05: the child commands want the provider instance
    # id or domain, not a "provider" key.
    assert child_args == {"item_id": "6", "provider_instance_id_or_domain": "library"}


def test_item_abs_podcast_detail(client, upstream, ma):
    from conftest_media import TEST_ABS_URL

    mock_upstream(
        upstream,
        "GET",
        f"{TEST_ABS_URL}/api/items/pod1",
        payload={
            "media": {
                "metadata": {"title": "The Daily", "authorName": "NYT"},
                "episodes": [
                    {"id": "ep1", "title": "Episode One", "duration": 60, "episode": 1}
                ],
            }
        },
    )

    resp = client.get("/api/media/item", params={"uri": "abs://pod1"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["media_type"] == "podcast"
    assert body["name"] == "The Daily"
    assert body["children"][0]["uri"] == "abs://pod1/ep1"
    assert body["children"][0]["media_type"] == "podcast_episode"


def test_item_unknown_ma_uri_is_404(client, ma):
    ma["responses"]["music/item_by_uri"] = {}

    resp = client.get("/api/media/item", params={"uri": "library://track/missing"})

    assert resp.status_code == 404


def test_item_ma_unreachable_is_502(client, ma):
    ma["responses"]["music/item_by_uri"] = aiohttp.ClientConnectionError("boom")

    resp = client.get("/api/media/item", params={"uri": "library://track/1"})

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"].lower()


def test_item_requires_a_uri(client):
    resp = client.get("/api/media/item")
    assert resp.status_code == 422


def test_library_tab_uses_its_ma_command(client, ma):
    ma["responses"]["music/tracks/library_items"] = [
        {"uri": "library://track/1", "name": "Track One"}
    ]

    resp = client.get(
        "/api/media/library/tracks",
        params={"offset": 5, "limit": 10, "order_by": "name"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tab"] == "tracks"
    assert [item["name"] for item in body["items"]] == ["Track One"]
    assert body["offset"] == 5
    assert body["limit"] == 10
    command, args = [call for call in ma["calls"] if call[0] == "music/tracks/library_items"][0]
    assert args == {"limit": 10, "offset": 5, "order_by": "name"}


def test_library_audiobooks_returns_abs_libraries(client, upstream, ma):
    mock_upstream(
        upstream,
        "GET",
        ABS_DETAIL_URL,
        payload={
            "status": "SUCCESS",
            "detail": {"libraries": [{"id": "lib-1", "name": "Books", "type": "book"}]},
        },
    )

    resp = client.get("/api/media/library/audiobooks")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tab"] == "audiobooks"
    assert body["libraries"] == [{"id": "lib-1", "name": "Books", "media_type": "book"}]
    assert body["errors"] == {"ma": None, "abs": None}


def test_library_rejects_an_unknown_tab(client):
    resp = client.get("/api/media/library/movies")
    assert resp.status_code == 422


def test_favorites_merges_all_media_types(client, ma):
    names = {
        "music/tracks/library_items": "Fav Track",
        "music/albums/library_items": "Fav Album",
        "music/artists/library_items": "Fav Artist",
        "music/playlists/library_items": "Fav Playlist",
    }
    for command, name in names.items():
        ma["responses"][command] = [{"uri": f"library://x/{name}", "name": name}]

    resp = client.get("/api/media/favorites")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {item["name"] for item in body["items"]} == set(names.values())
    assert body["errors"] == {"ma": None, "abs": None}
    for command, _name in names.items():
        args = [args for cmd, args in ma["calls"] if cmd == command][0]
        assert args["favorite"] is True


def test_abs_progress_forwards_the_update(client, monkeypatch):
    recorded: dict = {}

    async def fake_proxy(request, endpoint, payload=None, **kwargs):
        recorded["endpoint"] = endpoint
        recorded["payload"] = payload
        return JSONResponse({"status": "SUCCESS", "message": "Progress saved"})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", fake_proxy)

    resp = client.post(
        "/api/media/abs/progress",
        json={
            "item_id": "b1",
            "episode_id": "ep1",
            "current_time": 12.5,
            "duration": 100,
            "is_finished": False,
        },
    )

    assert resp.status_code == 200, resp.text
    assert recorded["endpoint"] == "/execute/audiobookshelf"
    assert recorded["payload"] == {
        "action": "update_progress",
        "item_id": "b1",
        "episode_id": "ep1",
        "current_time": 12.5,
        "duration": 100,
        "is_finished": False,
    }


def test_abs_progress_validates_its_body(client):
    assert client.post("/api/media/abs/progress", json={}).status_code == 422
    assert (
        client.post(
            "/api/media/abs/progress",
            json={"item_id": "b1", "current_time": 0, "duration": 0},
        ).status_code
        == 422
    )
