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
    ma_args = next(args for command, args in ma["calls"] if command == "music/search")
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
    child_args = next(
        args for command, args in ma["calls"] if command == "music/playlists/playlist_tracks"
    )
    # Live-verified 2026-10-05: the child commands want the provider instance
    # id or domain, not a "provider" key.
    assert child_args == {"item_id": "6", "provider_instance_id_or_domain": "library"}


def test_items_carry_artist_and_album_uris(client, ma):
    """P5-T7: "Go to artist/album" needs the URIs MA sends, not just names."""
    track = {
        "uri": "library://track/1",
        "name": "Track One",
        "media_type": "track",
        "duration": 120,
        "artists": [{"name": "Band", "uri": "library://artist/4"}],
        "album": {"name": "Debut", "uri": "library://album/9"},
    }
    ma["responses"]["music/item_by_uri"] = {
        "uri": "library://playlist/6", "name": "Road Trip", "media_type": "playlist", "item_id": 6, "provider": "library",
    }
    ma["responses"]["music/playlists/playlist_tracks"] = [track]
    ma["responses"]["music/favorites/library_items"] = []
    ma["responses"]["music/tracks/library_items"] = [track]

    child = client.get("/api/media/item", params={"uri": "library://playlist/6"}).json()["children"][0]
    assert child["artist"] == "Band"
    assert child["artist_uri"] == "library://artist/4"
    assert child["album"] == "Debut"
    assert child["album_uri"] == "library://album/9"

    item = client.get("/api/media/library/tracks").json()["items"][0]
    assert item["artist_uri"] == "library://artist/4"
    assert item["album_uri"] == "library://album/9"


def test_album_tracks_are_numbered_by_track_not_disc(client, ma):
    """An album's tracks carry their track number; every one is on disc 1."""
    ma["responses"]["music/item_by_uri"] = {
        "uri": "library://album/9", "name": "Debut", "media_type": "album", "item_id": 9, "provider": "library",
    }
    ma["responses"]["music/albums/album_tracks"] = [
        {"uri": "library://track/1", "name": "One", "disc_number": 1, "track_number": 1},
        {"uri": "library://track/2", "name": "Two", "disc_number": 1, "track_number": 2},
    ]
    children = client.get("/api/media/item", params={"uri": "library://album/9"}).json()["children"]
    assert [c["index"] for c in children] == [1, 2]


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
    _command, args = next(call for call in ma["calls"] if call[0] == "music/tracks/library_items")
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


def test_library_audiobooks_pages_through_abs(client, upstream, ma):
    """A >50-book library must page, not silently truncate at the first 50."""
    mock_upstream(
        upstream,
        "GET",
        ABS_DETAIL_URL,
        payload={
            "status": "SUCCESS",
            "detail": {"books": [{"id": "b1", "title": "Book", "author": "A"}]},
        },
    )

    resp = client.get(
        "/api/media/library/audiobooks",
        params={"library_id": "lib-1", "offset": 50, "limit": 50},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["name"] for item in body["items"]] == ["Book"]
    # offset 50 with a 50-book page must ask ABS for page 1, not page 0.
    queries = [str(key[1]) for key in upstream.requests]
    assert any(
        "action=list" in q and "library_id=lib-1" in q and "page=1" in q for q in queries
    ), queries


def test_abs_book_cover_and_duration_survive_normalization(client, upstream, ma):
    """`cover_path` from execution becomes the item's image; duration is kept."""
    ma["responses"]["music/recently_played_items"] = []
    ma["responses"]["music/in_progress_items"] = []
    ma["responses"]["music/playlists/library_items"] = []
    ma["responses"]["music/tracks/library_items"] = []
    ma["responses"]["music/radios/library_items"] = []
    mock_upstream(
        upstream,
        "GET",
        ABS_DETAIL_URL,
        payload={
            "status": "SUCCESS",
            "detail": {
                "books": [
                    {
                        "id": "b1",
                        "title": "Abs Book",
                        "author": "A",
                        "duration": 3600,
                        "cover_path": "/covers/b1.jpg",
                    }
                ]
            },
        },
    )

    body = client.get("/api/media/home").json()

    book = body["recent"][0]
    assert book["image"] == "/covers/b1.jpg"
    assert book["duration"] == 3600


def test_abs_search_book_cover_uses_the_cover_field(client, upstream, ma):
    """Execution's search summaries name the cover `cover`; it must still resolve."""
    ma["responses"]["music/search"] = {}
    mock_upstream(
        upstream,
        "GET",
        ABS_DETAIL_URL,
        payload={
            "status": "SUCCESS",
            "detail": {
                "books": [
                    {"id": "b1", "title": "Blue Train: The Book", "author": "A", "cover": "/covers/b1.jpg"}
                ],
                "podcasts": [],
                "authors": [],
            },
        },
    )

    body = client.get("/api/media/search", params={"q": "Blue Train"}).json()

    assert body["audiobooks"][0]["image"] == "/covers/b1.jpg"


def test_a_music_assistant_cover_uses_the_proxy_id_the_image_proxy_can_fetch(client, ma):
    """MA's ``path`` is the provider's URL, which the image proxy refuses to fetch.

    Measured live 2026-10-26: every MA album and artist cover came back ``404
    Failed to fetch image from upstream`` because the proxy only contacts hosts
    the household configured (SSRF) and ``r2.theaudiodb.com`` is not one, so the
    URL was rebased onto a configured base. ``proxy_id`` is MA's own cached copy
    and MA serves it from its own host at ``/imageproxy/<proxy_id>``.
    """
    ma["responses"]["music/albums/library_items"] = [
        {
            "media_item": {
                "uri": "library://album/1",
                "name": "House of Miracles",
                "media_type": "album",
                "image": {
                    "type": "thumb",
                    "path": "https://r2.theaudiodb.com/images/media/album/thumb/rowlk61611438892.jpg",
                    "provider": "theaudiodb",
                    "remotely_accessible": True,
                    "proxy_id": "f41a3f56c834d4b25a492e26285f6214bed22e4f8099f68e7008656a4f676809",
                },
            }
        }
    ]

    body = client.get("/api/media/library/albums").json()

    assert body["items"][0]["image"] == (
        "/imageproxy/f41a3f56c834d4b25a492e26285f6214bed22e4f8099f68e7008656a4f676809"
    )


def test_a_plain_string_music_assistant_cover_is_left_alone(client, ma):
    """Some providers hand back a bare string; it must not be rewritten."""
    ma["responses"]["music/albums/library_items"] = [
        {"media_item": {"uri": "library://album/2", "name": "B", "media_type": "album", "image": "https://example.test/a.jpg"}}
    ]

    body = client.get("/api/media/library/albums").json()

    assert body["items"][0]["image"] == "https://example.test/a.jpg"


def _ma_artist(name: str, uri: str, artist_type: str) -> dict:
    return {
        "media_item": {
            "uri": uri,
            "name": name,
            "media_type": "artist",
            "artist_type": artist_type,
            "is_playable": True,
            "provider_mappings": [
                {
                    "provider_domain": "opensubsonic" if artist_type == "singer" else "audiobookshelf",
                    "provider_instance": "library",
                }
            ],
        }
    }


def test_the_artists_tab_leaves_out_book_authors_and_narrators(client, ma):
    """The Artists tab is music.

    Measured live 2026-10-26: MA's 500 artists are 198 ``singer`` and 301
    ``author``/``narrator`` from the audiobook library. An author has no albums
    to open and nothing to play, so tapping one was a dead end. ``is_playable``
    is True for all of them, so ``artist_type`` is the only honest signal.
    """
    ma["responses"]["music/artists/library_items"] = [
        _ma_artist("Brandon Lake", "library://artist/11", "singer"),
        _ma_artist("Adam Verner", "library://artist/343", "narrator"),
        _ma_artist("Andy Crouch", "library://artist/220", "author"),
    ]

    body = client.get("/api/media/library/artists").json()

    assert [item["name"] for item in body["items"]] == ["Brandon Lake"]


def test_an_artist_ma_does_not_label_is_still_listed(client, ma):
    """Only the two known book roles are dropped; anything else is left alone."""
    entry = _ma_artist("Someone", "library://artist/9", "singer")
    del entry["media_item"]["artist_type"]
    ma["responses"]["music/artists/library_items"] = [entry]

    body = client.get("/api/media/library/artists").json()

    assert [item["name"] for item in body["items"]] == ["Someone"]


def test_search_keeps_book_authors_out_of_the_artists_bucket(client, ma):
    ma["responses"]["music/search"] = {
        "artists": [
            _ma_artist("Brandon Lake", "library://artist/11", "singer"),
            _ma_artist("Alex Jennings", "library://artist/344", "narrator"),
        ]
    }

    body = client.get("/api/media/search", params={"q": "brandon"}).json()

    assert [item["name"] for item in body["artists"]] == ["Brandon Lake"]


def test_a_favorited_book_author_is_not_a_favorite_artist(client, ma):
    ma["responses"]["music/tracks/library_items"] = []
    ma["responses"]["music/albums/library_items"] = []
    ma["responses"]["music/playlists/library_items"] = []
    ma["responses"]["music/artists/library_items"] = [
        _ma_artist("Brandon Lake", "library://artist/11", "singer"),
        _ma_artist("Amy Crouch - Foreword", "library://artist/221", "author"),
    ]

    body = client.get("/api/media/favorites").json()

    assert [item["name"] for item in body["items"]] == ["Brandon Lake"]


def test_media_is_book_person_shapes():
    from services.gateway.main import _media_is_book_person

    assert _media_is_book_person({"artist_type": "author"}) is True
    assert _media_is_book_person({"artist_type": "NARRATOR"}) is True
    assert _media_is_book_person({"artist_type": " narrator "}) is True
    assert _media_is_book_person({"media_item": {"artist_type": "author"}}) is True
    assert _media_is_book_person({"artist_type": "singer"}) is False
    assert _media_is_book_person({"artist_type": ""}) is False
    assert _media_is_book_person({}) is False
    assert _media_is_book_person({"artist_type": None}) is False
    assert _media_is_book_person("Adam Verner") is False
    assert _media_is_book_person(None) is False


def test_media_ma_image_shapes():
    """Every shape MA sends, including the ones that used to become "" silently."""
    from services.gateway.main import _media_ma_image

    assert _media_ma_image({"proxy_id": "abc"}) == "/imageproxy/abc"
    assert _media_ma_image({"proxy_id": "  abc  "}) == "/imageproxy/abc"
    # No proxy_id: fall back to whatever URL the provider gave.
    assert _media_ma_image({"path": "https://x.test/a.jpg"}) == "https://x.test/a.jpg"
    assert _media_ma_image({"url": "https://x.test/b.jpg"}) == "https://x.test/b.jpg"
    assert _media_ma_image("https://x.test/c.jpg") == "https://x.test/c.jpg"
    # A list of candidates: the first usable one wins.
    assert _media_ma_image([None, {}, {"proxy_id": "z"}]) == "/imageproxy/z"
    assert _media_ma_image(None) == ""
    assert _media_ma_image(7) == ""
    assert _media_ma_image({"proxy_id": "   "}) == ""


def test_library_rejects_an_unknown_tab(client):
    resp = client.get("/api/media/library/movies")
    assert resp.status_code == 422


def test_library_surfaces_an_ma_error_frame(client, ma):
    """MA answers a rejected read with HTTP 200 and an error frame.

    It must become an error state, not a silent empty list.
    """
    ma["responses"]["music/tracks/library_items"] = {
        "error_code": 400,
        "error": "Library is unavailable",
    }

    resp = client.get("/api/media/library/tracks")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert "Library is unavailable" in body["errors"]["ma"]


def test_home_surfaces_an_ma_error_frame(client, upstream, ma):
    """One shelf failing with an MA error frame is reported, not swallowed."""
    ma["responses"]["music/recently_played_items"] = {"error_code": 400, "details": "No provider"}
    mock_upstream(
        upstream,
        "GET",
        ABS_DETAIL_URL,
        payload={"status": "SUCCESS", "detail": {"books": []}},
    )

    resp = client.get("/api/media/home")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "No provider" in body["errors"]["ma"]


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
        args = next(args for cmd, args in ma["calls"] if cmd == command)
        assert args["favorite"] is True


def test_ma_command_relays_an_allowlisted_player_command(client, ma):
    ma["responses"]["players/cmd/pause"] = {"ok": True}

    resp = client.post(
        "/api/media/ma-command",
        json={"command": "players/cmd/pause", "args": {"player_id": "kitchen"}},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "SUCCESS", "result": {"ok": True}}
    assert ma["calls"] == [("players/cmd/pause", {"player_id": "kitchen"})]


def test_ma_command_rejects_a_command_outside_the_allowlist(client, ma):
    resp = client.post(
        "/api/media/ma-command",
        json={"command": "config/save", "args": {}},
    )

    assert resp.status_code == 403
    assert "not allowed" in resp.json()["detail"].lower()
    assert ma["calls"] == []


def test_ma_command_refuses_a_player_outside_the_callers_scope(client, ma, monkeypatch):
    async def deny(_raw, *, user, is_admin):
        return '{"error_code": "forbidden"}'

    monkeypatch.setattr(gateway_main, "check_ma_frame_scope", deny)

    resp = client.post(
        "/api/media/ma-command",
        json={"command": "players/cmd/play", "args": {"player_id": "someone-else"}},
    )

    assert resp.status_code == 403
    assert "not available" in resp.json()["detail"].lower()
    assert ma["calls"] == []


def test_ma_command_reports_an_unreachable_upstream(client, ma):
    ma["responses"]["players/cmd/play"] = aiohttp.ClientConnectionError("boom")

    resp = client.post(
        "/api/media/ma-command",
        json={"command": "players/cmd/play", "args": {"player_id": "kitchen"}},
    )

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"].lower()


def test_ma_command_surfaces_an_ma_error_frame(client, ma):
    ma["responses"]["players/cmd/play"] = {
        "error_code": 400,
        "error": "Player is unavailable",
    }

    resp = client.post(
        "/api/media/ma-command",
        json={"command": "players/cmd/play", "args": {"player_id": "kitchen"}},
    )

    assert resp.status_code == 502
    assert "Player is unavailable" in resp.json()["detail"]


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
