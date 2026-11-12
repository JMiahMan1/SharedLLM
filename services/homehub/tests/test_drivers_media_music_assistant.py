"""music_assistant: the WS JSON-RPC client, player state mapping, media services, music_assistant.* services."""
from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from services.homehub.core import DeviceUnavailable, HubError
from services.homehub.drivers.media_common import F
from services.homehub.drivers.music_assistant import MAConnection, MusicAssistantDriver
from services.homehub.tests.conftest import make_hub

TOKEN = "ma-token"
OFFICE = "media_player.office_jarvis"
ROKU = "ROKU_2N0000000001"


def player(pid, name, **kw):
    p = {"player_id": pid, "provider": "x", "type": "player", "name": name, "available": True, "enabled": True,
         "supported_features": ["power", "volume_set", "volume_mute", "pause"], "playback_state": "idle",
         "powered": True, "volume_level": 35, "volume_muted": False, "group_members": [], "active_source": pid,
         "source_list": [], "current_media": None}
    p.update(kw)
    return p


def queue(qid, **kw):
    q = {"queue_id": qid, "active": True, "display_name": "Q", "available": True, "items": 12,
         "shuffle_enabled": False, "repeat_mode": "off", "current_index": 3, "elapsed_time": 42.0,
         "elapsed_time_last_updated": 1_700_000_000.0, "state": "playing",
         "current_item": {"queue_item_id": "qi1", "name": "Band - Song", "duration": 200,
                          "media_item": {"media_type": "track", "uri": "library://track/7", "name": "Song",
                                         "version": "", "artists": [{"media_type": "artist", "name": "Band",
                                                                     "uri": "library://artist/1"}],
                                         "album": {"media_type": "album", "name": "LP", "uri": "library://album/2"},
                                         "image": {"type": "thumb", "path": "/art/7.jpg", "provider": "filesystem",
                                                   "remotely_accessible": False}}},
         "next_item": None}
    q.update(kw)
    return q


class FakeConn:
    def __init__(self, url, token, on_event, on_close):
        self.url, self.token, self.on_event, self.on_close = url, token, on_event, on_close
        self.server_info = {"base_url": "http://192.168.2.205:8095", "schema_version": 28}
        self.connected = False
        self.sent: list[tuple[str, dict]] = []
        self.players = [player(OFFICE, "Office Jarvis Smart Speaker"),
                        player(ROKU, "Gracies TV", supported_features=["power", "volume_mute"], powered=False)]
        self.queues = [queue(OFFICE), queue(ROKU, active=False, state="idle", current_item=None)]
        self.replies: dict[str, object] = {}

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def command(self, command, **args):
        self.sent.append((command, {k: v for k, v in args.items() if v is not None}))
        if command == "players/all":
            return self.players
        if command == "player_queues/all":
            return self.queues
        reply = self.replies.get(command)
        if isinstance(reply, Exception):
            raise reply
        return reply


def build(options=None):
    hub, entry = make_hub("music_assistant", {"url": "http://d5369777-music-assistant:8094", "token": TOKEN}, [
        {"entity_id": "media_player.office_speaker", "unique_id": OFFICE},
        {"entity_id": "button.office_speaker_favorite_current_song", "unique_id": OFFICE + "_favorite_now_playing"},
        {"entity_id": "media_player.gracies_tv", "unique_id": ROKU},
        {"entity_id": "media_player.rv_tv_2", "unique_id": "upa4e88d93e22f"},
    ], options=options)
    drv = MusicAssistantDriver(hub, entry)
    made = {}

    def factory(*a):
        made["c"] = FakeConn(*a)
        return made["c"]
    drv.connection_factory = factory
    hub.drivers[entry.entry_id] = drv
    return hub, drv, made


def test_url_precedence(monkeypatch):
    monkeypatch.delenv("MUSIC_ASSISTANT_URL", raising=False)
    _, drv, _ = build()
    assert drv.url == "http://d5369777-music-assistant:8094"
    monkeypatch.setenv("MUSIC_ASSISTANT_URL", "http://192.168.2.205:8095")
    _, drv, _ = build()
    assert drv.url == "http://192.168.2.205:8095"
    _, drv, _ = build(options={"url": "http://other:8095"})
    assert drv.url == "http://other:8095"


async def test_state_mapping():
    hub, drv, _made = build()
    await drv.async_setup()
    st = hub.states.get("media_player.office_speaker")
    assert st.state == "playing"
    a = st.attributes
    assert a["media_title"] == "Song" and a["media_artist"] == "Band" and a["media_album_name"] == "LP"
    assert a["media_content_id"] == "library://track/7"
    assert a["media_duration"] == 200 and a["media_position"] == 42.0
    assert a["entity_picture"].startswith("http://192.168.2.205:8095/imageproxy?path=/art/7.jpg&provider=filesystem")
    assert a["volume_level"] == 0.35 and a["is_volume_muted"] is False
    assert a["app_id"] == "music_assistant" and a["active_queue"] == OFFICE and a["mass_player_id"] == OFFICE
    assert a["shuffle"] is False and a["repeat"] == "off"
    assert a["supported_features"] == 7796671  # the value HA stored for a powered, volume-capable MA player
    roku = hub.states.get("media_player.gracies_tv")
    assert roku.state == "off"
    assert roku.attributes["supported_features"] == 7795643  # HA's value for Gracie's TV
    assert roku.attributes["active_queue"] is None
    assert hub.states.get("media_player.rv_tv_2").state == "unavailable"
    assert hub.states.get("button.office_speaker_favorite_current_song").state == "unknown"
    for s in ("play_media", "play_announcement", "get_library", "search", "get_queue", "transfer_queue"):
        assert ("music_assistant", s) in hub.services


async def test_events_update_state():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    c.on_event({"event": "queue_updated", "object_id": OFFICE, "data": queue(OFFICE, state="paused")})
    assert hub.states.get("media_player.office_speaker").state == "paused"
    c.on_event({"event": "queue_time_updated", "object_id": OFFICE, "data": 99})
    assert hub.states.get("media_player.office_speaker").attributes["media_position"] == 99
    c.on_event({"event": "player_updated", "object_id": OFFICE,
                "data": player(OFFICE, "Office", available=False)})
    assert hub.states.get("media_player.office_speaker").state == "unavailable"
    c.on_close()
    assert hub.states.get("media_player.gracies_tv").state == "unavailable"


async def test_media_player_services():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    ent = hub.entities["media_player.office_speaker"]
    c.sent.clear()
    await drv.async_call(ent, "media_player", "media_pause", {})
    await drv.async_call(ent, "media_player", "volume_set", {"volume_level": 0.42})
    await drv.async_call(ent, "media_player", "volume_mute", {"is_volume_muted": True})
    await drv.async_call(ent, "media_player", "shuffle_set", {"shuffle": True})
    await drv.async_call(ent, "media_player", "repeat_set", {"repeat": "all"})
    await drv.async_call(ent, "media_player", "turn_off", {})
    await drv.async_call(ent, "media_player", "media_seek", {"seek_position": 61.6})
    assert c.sent == [
        ("players/cmd/pause", {"player_id": OFFICE}),
        ("players/cmd/volume_set", {"player_id": OFFICE, "volume_level": 42}),
        ("players/cmd/volume_mute", {"player_id": OFFICE, "muted": True}),
        ("player_queues/shuffle", {"queue_id": OFFICE, "shuffle_enabled": True}),
        ("player_queues/repeat", {"queue_id": OFFICE, "repeat_mode": "all"}),
        ("players/cmd/power", {"player_id": OFFICE, "powered": False}),
        ("players/cmd/seek", {"player_id": OFFICE, "position": 62}),
    ]
    c.sent.clear()
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "library://playlist/3",
                                                             "media_content_type": "playlist", "enqueue": "add"})
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "http://j/tts.wav",
                                                             "media_content_type": "music", "announce": True,
                                                             "extra": {"announce_volume": 60}})
    assert c.sent == [
        ("player_queues/play_media", {"queue_id": OFFICE, "media": ["library://playlist/3"], "option": "add"}),
        ("players/cmd/play_announcement", {"player_id": OFFICE, "url": "http://j/tts.wav", "volume_level": 60}),
    ]
    btn = hub.entities["button.office_speaker_favorite_current_song"]
    await drv.async_call(btn, "button", "press", {})
    assert c.sent[-1] == ("players/add_currently_playing_to_favorites", {"player_id": OFFICE})
    assert hub.states.get("button.office_speaker_favorite_current_song").state != "unknown"
    with pytest.raises(DeviceUnavailable):
        await drv.async_call(hub.entities["media_player.rv_tv_2"], "media_player", "media_play", {})


async def test_music_assistant_play_media_resolves_names():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    c.replies["music/item_by_name"] = {"uri": "spotify://track/abc", "name": "Song"}
    c.sent.clear()
    await hub.call_service("music_assistant", "play_media", {
        "entity_id": "media_player.office_speaker", "media_id": "Amazing Grace", "media_type": "track",
        "artist": "Choir", "enqueue": "replace", "radio_mode": True})
    assert c.sent == [
        ("music/item_by_name", {"name": "Amazing Grace", "artist": "Choir", "media_type": "track"}),
        ("player_queues/play_media", {"queue_id": OFFICE, "media": ["spotify://track/abc"], "option": "replace",
                                      "radio_mode": True}),
    ]
    c.replies["music/item_by_name"] = None
    with pytest.raises(HubError, match="found nothing"):
        await hub.call_service("music_assistant", "play_media", {"entity_id": "media_player.office_speaker",
                                                                 "media_id": "nonexistent"})


async def test_play_announcement_service_keys():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    await hub.call_service("music_assistant", "play_announcement", {
        "entity_id": "media_player.office_speaker", "url": "http://j/a.wav", "use_pre_announce": False})
    assert c.sent[-1] == ("players/cmd/play_announcement", {"player_id": OFFICE, "url": "http://j/a.wav",
                                                            "pre_announce": False})


async def test_search_get_library_get_queue_transfer():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    c.replies["music/search"] = {
        "tracks": [{"media_type": "track", "uri": "library://track/1", "name": "Song",
                    "artists": [{"media_type": "artist", "uri": "library://artist/1", "name": "Band"}],
                    "image": {"path": "https://img/x.jpg", "remotely_accessible": True}}],
        "artists": [], "albums": [], "playlists": [], "radio": []}
    res = await hub.call_service("music_assistant", "search", {"config_entry_id": "whatever", "name": "Song",
                                                               "artist": "Band", "media_type": ["track"], "limit": 5},
                                 return_response=True)
    assert c.sent[-1] == ("music/search", {"search_query": "Band - Song", "media_types": ["track"], "limit": 5,
                                           "library_only": False})
    r = res["response"]
    assert r["tracks"][0]["uri"] == "library://track/1"
    assert r["tracks"][0]["image"] == "https://img/x.jpg"
    assert r["tracks"][0]["artists"][0]["name"] == "Band"
    assert set(r) == {"artists", "albums", "tracks", "playlists", "radio", "audiobooks", "podcasts"}

    c.replies["music/playlists/library_items"] = [{"media_type": "playlist", "uri": "library://playlist/3",
                                                   "name": "Worship"}]
    res = await hub.call_service("music_assistant", "get_library", {"media_type": "playlist", "limit": 50},
                                 return_response=True)
    assert c.sent[-1] == ("music/playlists/library_items", {"limit": 50, "offset": 0})
    assert res["response"]["items"][0]["name"] == "Worship"
    assert res["response"]["media_type"] == "playlist"

    c.replies["player_queues/get_active_queue"] = queue(OFFICE)
    res = await hub.call_service("music_assistant", "get_queue", {"entity_id": "media_player.office_speaker"},
                                 return_response=True)
    q = res["response"]["media_player.office_speaker"]
    assert q["queue_id"] == OFFICE and q["items"] == 12
    assert q["current_item"]["media_item"]["album"]["name"] == "LP"

    await hub.call_service("music_assistant", "transfer_queue", {"entity_id": "media_player.gracies_tv",
                                                                 "auto_play": True})
    assert c.sent[-1] == ("player_queues/transfer", {"source_queue_id": OFFICE, "target_queue_id": ROKU,
                                                     "auto_play": True})
    await hub.call_service("music_assistant", "transfer_queue", {"entity_id": "media_player.office_speaker",
                                                                 "source_player": "media_player.gracies_tv"})
    assert c.sent[-1] == ("player_queues/transfer", {"source_queue_id": ROKU, "target_queue_id": OFFICE})


async def test_setup_fails_cleanly_when_ma_is_down():
    _hub, drv, _ = build()

    class Down(FakeConn):
        async def connect(self):
            raise DeviceUnavailable("down")
    drv.connection_factory = Down
    with pytest.raises(DeviceUnavailable):
        await drv.async_setup()


# -- the real MAConnection against a local WebSocket server (loopback only) ----------------------


async def test_connection_auth_partial_results_events_and_errors():
    received: list[dict] = []

    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(json.dumps({"server_id": "s", "server_version": "2.10.9", "schema_version": 28,
                                      "min_supported_schema_version": 24, "base_url": "http://h:8095"}))
        async for msg in ws:
            data = json.loads(msg.data)
            received.append(data)
            mid, cmd = data["message_id"], data["command"]
            if cmd == "auth":
                ok = data["args"]["token"] == TOKEN
                await ws.send_str(json.dumps({"message_id": mid, "result": {"user": "x"} if ok else None}))
            elif cmd == "players/all":
                await ws.send_str(json.dumps({"message_id": mid, "result": [{"player_id": "a"}], "partial": True}))
                await ws.send_str(json.dumps({"event": "player_updated", "object_id": "a", "data": {"player_id": "a"}}))
                await ws.send_str(json.dumps({"message_id": mid, "result": [{"player_id": "b"}]}))
            else:
                await ws.send_str(json.dumps({"message_id": mid, "error_code": 999, "details": "nope"}))
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    events: list[dict] = []
    closed = asyncio.Event()
    conn = MAConnection(f"http://127.0.0.1:{server.port}", TOKEN, events.append, closed.set)
    try:
        await conn.connect()
        assert conn.server_info["server_version"] == "2.10.9"
        assert received[0]["command"] == "auth"
        players = await conn.command("players/all")
        assert [p["player_id"] for p in players] == ["a", "b"]
        assert events and events[0]["event"] == "player_updated"
        with pytest.raises(HubError, match="999"):
            await conn.command("players/cmd/play", player_id="a")
        assert received[-1]["args"] == {"player_id": "a"}
    finally:
        await conn.close()
        await server.close()
    assert closed.is_set()

    bad = MAConnection("http://127.0.0.1:9", TOKEN, events.append, lambda: None)
    with pytest.raises(DeviceUnavailable):
        await bad.connect()


async def test_connection_rejects_bad_token():
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(json.dumps({"server_version": "2.10.9", "schema_version": 28}))
        async for msg in ws:
            data = json.loads(msg.data)
            await ws.send_str(json.dumps({"message_id": data["message_id"], "error_code": 20,
                                          "details": "Invalid token"}))
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    conn = MAConnection(f"http://127.0.0.1:{server.port}", "wrong", lambda e: None, lambda: None)
    try:
        with pytest.raises(HubError, match="refused the token"):
            await conn.connect()
    finally:
        await conn.close()
        await server.close()


def test_features_constant_matches_ha():
    assert MusicAssistantDriver._features({"supported_features": []}) == 7795251
    assert MusicAssistantDriver._features({"supported_features": ["volume_set", "volume_mute", "set_members"]}) \
        == 8320575
    assert F.GROUPING == 524288
