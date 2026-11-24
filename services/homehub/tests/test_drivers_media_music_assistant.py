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
STUDY = "media_player.den_speaker"
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
        self.server_info = {"base_url": "http://192.0.2.205:8095", "schema_version": 28}
        self.connected = False
        self.sent: list[tuple[str, dict]] = []
        self.players = [player(STUDY, "Den Speaker Smart Speaker"),
                        player(ROKU, "Playroom TV", supported_features=["power", "volume_mute"], powered=False)]
        self.queues = [queue(STUDY), queue(ROKU, active=False, state="idle", current_item=None)]
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
        {"entity_id": "media_player.study_speaker", "unique_id": STUDY},
        {"entity_id": "button.study_speaker_favorite_current_song", "unique_id": STUDY + "_favorite_now_playing"},
        {"entity_id": "media_player.playroom_tv", "unique_id": ROKU},
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
    _, drv, _ = build()
    assert drv.url == "http://d5369777-music-assistant:8094"
    # The config entry is the only source: an environment variable never overrides it.
    monkeypatch.setenv("MUSIC_ASSISTANT_URL", "http://192.0.2.205:8095")
    monkeypatch.setenv("MUSIC_ASSISTANT_TOKEN", "env-token")
    _, drv, _ = build()
    assert drv.url == "http://d5369777-music-assistant:8094"
    assert drv.token == TOKEN


def test_an_imported_entrys_options_are_folded_into_its_data():
    """HA's import kept the add-on address in data and the real one in options: the real one wins, stored once."""
    from services.homehub import music_assistant_server

    _, drv, _ = build(options={"url": "http://other:8095", "token": "new-token", "keep": 1})
    entry = drv.entry
    assert music_assistant_server.fold_entry(entry) is True
    assert entry.data["url"] == "http://other:8095" and entry.data["token"] == "new-token"
    assert entry.options == {"keep": 1}
    assert music_assistant_server.fold_entry(entry) is False  # once only
    drv2 = MusicAssistantDriver(drv.hub, entry)
    assert drv2.url == "http://other:8095" and drv2.token == "new-token"


async def test_state_mapping():
    hub, drv, _made = build()
    await drv.async_setup()
    st = hub.states.get("media_player.study_speaker")
    assert st.state == "playing"
    a = st.attributes
    assert a["media_title"] == "Song" and a["media_artist"] == "Band" and a["media_album_name"] == "LP"
    assert a["media_content_id"] == "library://track/7"
    assert a["media_duration"] == 200 and a["media_position"] == 42.0
    assert a["entity_picture"].startswith("http://192.0.2.205:8095/imageproxy?path=/art/7.jpg&provider=filesystem")
    assert a["volume_level"] == 0.35 and a["is_volume_muted"] is False
    assert a["app_id"] == "music_assistant" and a["active_queue"] == STUDY and a["mass_player_id"] == STUDY
    assert a["shuffle"] is False and a["repeat"] == "off"
    assert a["supported_features"] == 7796671  # the value HA stored for a powered, volume-capable MA player
    roku = hub.states.get("media_player.playroom_tv")
    assert roku.state == "off"
    assert roku.attributes["supported_features"] == 7795643  # HA's value for the playroom TV
    assert roku.attributes["active_queue"] is None
    assert hub.states.get("media_player.rv_tv_2").state == "unavailable"
    assert hub.states.get("button.study_speaker_favorite_current_song").state == "unknown"
    for s in ("play_media", "play_announcement", "get_library", "search", "get_queue", "transfer_queue"):
        assert ("music_assistant", s) in hub.services


async def test_a_powered_off_player_is_on_no_source():
    """A sleeping Google TV still names its last app to Music Assistant; Jarvis showed Spotify on it."""
    _hub, drv, _made = build()
    await drv.async_setup()
    sources = [{"id": "spotify", "name": "Spotify"}, {"id": "youtube", "name": "YouTube"}]
    state, attrs = drv.player_state(player("tv1", "Some TV", powered=False, active_source="spotify", source_list=sources))
    assert state == "off" and "source" not in attrs and "app_id" not in attrs
    assert attrs["source_list"] == ["Spotify", "YouTube"]  # still offered to pick from
    state, attrs = drv.player_state(player("tv1", "Some TV", active_source="spotify", source_list=sources))
    assert attrs["source"] == "Spotify" and attrs["app_id"] == "spotify"


async def test_events_update_state():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    c.on_event({"event": "queue_updated", "object_id": STUDY, "data": queue(STUDY, state="paused")})
    assert hub.states.get("media_player.study_speaker").state == "paused"
    c.on_event({"event": "queue_time_updated", "object_id": STUDY, "data": 99})
    assert hub.states.get("media_player.study_speaker").attributes["media_position"] == 99
    c.on_event({"event": "player_updated", "object_id": STUDY,
                "data": player(STUDY, "Study", available=False)})
    assert hub.states.get("media_player.study_speaker").state == "unavailable"
    c.on_close()
    assert hub.states.get("media_player.playroom_tv").state == "unavailable"


async def test_media_player_services():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    ent = hub.entities["media_player.study_speaker"]
    c.sent.clear()
    await drv.async_call(ent, "media_player", "media_pause", {})
    await drv.async_call(ent, "media_player", "volume_set", {"volume_level": 0.42})
    await drv.async_call(ent, "media_player", "volume_mute", {"is_volume_muted": True})
    await drv.async_call(ent, "media_player", "shuffle_set", {"shuffle": True})
    await drv.async_call(ent, "media_player", "repeat_set", {"repeat": "all"})
    await drv.async_call(ent, "media_player", "turn_off", {})
    await drv.async_call(ent, "media_player", "media_seek", {"seek_position": 61.6})
    assert c.sent == [
        ("players/cmd/pause", {"player_id": STUDY}),
        ("players/cmd/volume_set", {"player_id": STUDY, "volume_level": 42}),
        ("players/cmd/volume_mute", {"player_id": STUDY, "muted": True}),
        ("player_queues/shuffle", {"queue_id": STUDY, "shuffle_enabled": True}),
        ("player_queues/repeat", {"queue_id": STUDY, "repeat_mode": "all"}),
        ("players/cmd/power", {"player_id": STUDY, "powered": False}),
        ("players/cmd/seek", {"player_id": STUDY, "position": 62}),
    ]
    c.sent.clear()
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "library://playlist/3",
                                                             "media_content_type": "playlist", "enqueue": "add"})
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "http://j/tts.wav",
                                                             "media_content_type": "music", "announce": True,
                                                             "extra": {"announce_volume": 60}})
    assert c.sent == [
        ("player_queues/play_media", {"queue_id": STUDY, "media": ["library://playlist/3"], "option": "add"}),
        ("players/cmd/play_announcement", {"player_id": STUDY, "url": "http://j/tts.wav", "volume_level": 60}),
    ]
    btn = hub.entities["button.study_speaker_favorite_current_song"]
    await drv.async_call(btn, "button", "press", {})
    assert c.sent[-1] == ("players/add_currently_playing_to_favorites", {"player_id": STUDY})
    assert hub.states.get("button.study_speaker_favorite_current_song").state != "unknown"
    with pytest.raises(DeviceUnavailable):
        await drv.async_call(hub.entities["media_player.rv_tv_2"], "media_player", "media_play", {})


async def test_music_assistant_play_media_resolves_names():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    c.replies["music/item_by_name"] = {"uri": "spotify://track/abc", "name": "Song"}
    c.sent.clear()
    await hub.call_service("music_assistant", "play_media", {
        "entity_id": "media_player.study_speaker", "media_id": "Amazing Grace", "media_type": "track",
        "artist": "Choir", "enqueue": "replace", "radio_mode": True})
    assert c.sent == [
        ("music/item_by_name", {"name": "Amazing Grace", "artist": "Choir", "media_type": "track"}),
        ("player_queues/play_media", {"queue_id": STUDY, "media": ["spotify://track/abc"], "option": "replace",
                                      "radio_mode": True}),
    ]
    c.replies["music/item_by_name"] = None
    with pytest.raises(HubError, match="found nothing"):
        await hub.call_service("music_assistant", "play_media", {"entity_id": "media_player.study_speaker",
                                                                 "media_id": "nonexistent"})


async def test_play_announcement_service_keys():
    hub, drv, made = build()
    await drv.async_setup()
    c = made["c"]
    await hub.call_service("music_assistant", "play_announcement", {
        "entity_id": "media_player.study_speaker", "url": "http://j/a.wav", "use_pre_announce": False})
    assert c.sent[-1] == ("players/cmd/play_announcement", {"player_id": STUDY, "url": "http://j/a.wav",
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

    c.replies["player_queues/get_active_queue"] = queue(STUDY)
    res = await hub.call_service("music_assistant", "get_queue", {"entity_id": "media_player.study_speaker"},
                                 return_response=True)
    q = res["response"]["media_player.study_speaker"]
    assert q["queue_id"] == STUDY and q["items"] == 12
    assert q["current_item"]["media_item"]["album"]["name"] == "LP"

    await hub.call_service("music_assistant", "transfer_queue", {"entity_id": "media_player.playroom_tv",
                                                                 "auto_play": True})
    assert c.sent[-1] == ("player_queues/transfer", {"source_queue_id": STUDY, "target_queue_id": ROKU,
                                                     "auto_play": True})
    await hub.call_service("music_assistant", "transfer_queue", {"entity_id": "media_player.study_speaker",
                                                                 "source_player": "media_player.playroom_tv"})
    assert c.sent[-1] == ("player_queues/transfer", {"source_queue_id": ROKU, "target_queue_id": STUDY})


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


async def test_join_and_unjoin_group_players_through_set_members():
    """media_player.join/unjoin (Jarvis's speaker grouping, MA's own HA player provider) reach MA."""
    from services.homehub.core import DeviceEntry, EntityEntry

    hub, drv, made = build()
    hub.devices["dev2"] = DeviceEntry(id="dev2", name="Living Room TV", area_id="study", config_entries=["cast1"])
    hub.entities["media_player.playroom_tv"].device_id = "dev2"
    hub.entities["media_player.living_room_cast"] = EntityEntry(
        entity_id="media_player.living_room_cast", unique_id="02000000-0000-0000-0000-000000000001",
        platform="cast", config_entry_id="cast1", device_id="dev2")
    await drv.async_setup()
    c = made["c"]
    c.players.append(player("media_player.attic_speaker", "Attic Speaker"))  # MA's HA player provider: id = entity id
    await drv.refresh()
    c.sent.clear()
    await hub.call_service("media_player", "join", {
        "entity_id": "media_player.study_speaker",
        # an MA entity, the Cast entity of a device MA also plays on, and an HA-provider player id
        "group_members": ["media_player.playroom_tv", "media_player.living_room_cast", "media_player.attic_speaker"]})
    assert c.sent[-1] == ("players/cmd/set_members", {"target_player": STUDY,
                                                      "player_ids_to_add": [ROKU, "media_player.attic_speaker"]})
    with pytest.raises(HubError, match="not a Music Assistant player"):
        await hub.call_service("media_player", "join", {"entity_id": "media_player.study_speaker",
                                                        "group_members": ["media_player.nowhere"]})
    with pytest.raises(HubError, match="other than the leader"):
        await hub.call_service("media_player", "join", {"entity_id": "media_player.study_speaker",
                                                        "group_members": "media_player.study_speaker"})
    # a member leaves its leader's group; a leader dissolves its own
    drv.players[ROKU]["synced_to"] = STUDY
    await hub.call_service("media_player", "unjoin", {"entity_id": "media_player.playroom_tv"})
    assert c.sent[-1] == ("players/cmd/set_members", {"target_player": STUDY, "player_ids_to_remove": [ROKU]})
    await hub.call_service("media_player", "unjoin", {"entity_id": "media_player.study_speaker"})
    assert c.sent[-1] == ("players/cmd/ungroup", {"player_id": STUDY})


async def test_gone_entities_are_only_players_music_assistant_no_longer_lists():
    hub, drv, made = build()
    assert drv.gone_entities() == {}  # nothing listed yet: nothing is known to be gone
    await drv.async_setup()
    gone = drv.gone_entities()
    assert set(gone) == {"media_player.rv_tv_2"}
    assert "upa4e88d93e22f" in gone["media_player.rv_tv_2"]
    # A player MA lists but cannot reach is unavailable, not gone; so is its favourite button.
    made["c"].players[1]["available"] = False
    await drv.refresh()
    assert hub.states.get("media_player.playroom_tv").state == "unavailable"
    assert set(drv.gone_entities()) == {"media_player.rv_tv_2"}
    # When MA drops a player, its button goes with it; with MA unreachable nothing is called gone.
    made["c"].players = made["c"].players[1:]
    await drv.refresh()
    assert set(drv.gone_entities()) == {"media_player.rv_tv_2", "media_player.study_speaker",
                                        "button.study_speaker_favorite_current_song"}
    made["c"].connected = False
    assert drv.gone_entities() == {}



# -- a satellite that connects itself to Music Assistant (Sendspin) -----------------------------------
# The live shape (MA 2.10.9): the universal player and its hidden Sendspin protocol player share the
# satellite's name and report neither MAC nor IP. Names here are made up.
SAT_UP = "up0000000abc"
SAT_PROTO = "Ip_0000000abc"
SAT_INFO = {"model": "Echo Dot 3 (hassmic)", "manufacturer": "Amazon", "mac_address": None, "ip_address": None}


def sat_players(name="Echo Dot 3 abc123"):
    return [player(SAT_UP, name, provider="universal_player", type="player", device_info=dict(SAT_INFO),
                   output_protocols=[{"output_protocol_id": SAT_PROTO, "protocol_domain": "sendspin"}]),
            player(SAT_PROTO, name, provider="sendspin", type="protocol", device_info=dict(SAT_INFO),
                   active_source=SAT_UP)]


def add_esphome_node(hub, dev_id, node, *, name=None, host="10.0.0.21", entry_id=None):
    """An ESPHome node's entry, device and media_player, as the ESPHome integration registers them."""
    from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry

    entry = ConfigEntry(entry_id=entry_id or f"esphome_{dev_id}", domain="esphome", title=node,
                        data={"host": host, "port": 6053, "device_name": node})
    hub.config_entries[entry.entry_id] = entry
    hub.devices[dev_id] = DeviceEntry(id=dev_id, name=name or node, area_id="office",
                                      config_entries=[entry.entry_id], manufacturer="Espressif",
                                      model="hassmic")
    eid = f"media_player.{dev_id}_media_player"
    hub.entities[eid] = EntityEntry(entity_id=eid, unique_id=f"{dev_id}-media_player", platform="esphome",
                                    config_entry_id=entry.entry_id, device_id=dev_id)
    return hub.devices[dev_id]


def build_sat(players):
    """A Music Assistant entry with no players registered yet, listing ``players``."""
    hub, entry = make_hub("music_assistant", {"url": "http://ma.example.test:8095", "token": TOKEN})
    del hub.devices["dev1"]
    drv = MusicAssistantDriver(hub, entry)
    made = {}

    def factory(*a):
        conn = FakeConn(*a)
        conn.players, conn.queues = players, []
        made["c"] = conn
        return conn
    drv.connection_factory = factory
    hub.drivers[entry.entry_id] = drv
    return hub, drv, made


def ma_entities(hub, drv):
    return {e.unique_id: e for e in hub.entities.values() if e.config_entry_id == drv.entry.entry_id}


async def test_a_satellites_universal_player_joins_its_esphome_device_by_name():
    hub, drv, _ = build_sat(sat_players())
    node = add_esphome_node(hub, "dev_sat", "echo-dot-3-abc123", name="Office Satellite")
    await drv.async_setup()
    ents = ma_entities(hub, drv)
    assert set(ents) == {SAT_UP}  # the hidden Sendspin protocol player gets no entity
    ent = ents[SAT_UP]
    assert ent.device_id == "dev_sat"
    assert drv.entry.entry_id in node.config_entries
    st = hub.states.get(ent.entity_id)
    assert st.attributes["mass_player_id"] == SAT_UP and st.attributes["mass_player_type"] == "player"
    # The node's own maker and model stay: MA's guess about the hardware does not replace them.
    assert (node.manufacturer, node.model) == ("Espressif", "hassmic")
    assert len(hub.devices) == 1


async def test_a_player_whose_name_fits_two_esphome_devices_links_to_neither():
    hub, drv, _ = build_sat(sat_players())
    add_esphome_node(hub, "dev_a", "echo-dot-3-abc123", host="10.0.0.21")
    add_esphome_node(hub, "dev_b", "Echo Dot 3 ABC123", host="10.0.0.22")
    await drv.async_setup()
    ent = ma_entities(hub, drv)[SAT_UP]
    assert ent.device_id not in ("dev_a", "dev_b")
    own = hub.devices[ent.device_id]
    assert own.config_entries == [drv.entry.entry_id] and ["music_assistant", SAT_UP] in own.identifiers


async def test_a_player_with_no_esphome_namesake_gets_a_device_of_its_own():
    hub, drv, _ = build_sat([player("up0000000def", "Kitchen Speaker", type="player")])
    add_esphome_node(hub, "dev_sat", "echo-dot-3-abc123")
    await drv.async_setup()
    ent = ma_entities(hub, drv)["up0000000def"]
    assert ent.device_id != "dev_sat"
    assert hub.devices[ent.device_id].name == "Kitchen Speaker"


async def test_a_player_links_by_ip_before_name():
    players = sat_players()
    players[0]["device_info"] = {**SAT_INFO, "ip_address": "10.0.0.31"}
    hub, drv, _ = build_sat(players)
    add_esphome_node(hub, "dev_by_name", "echo-dot-3-abc123", host="10.0.0.30")
    add_esphome_node(hub, "dev_by_ip", "office-node", host="10.0.0.31")
    await drv.async_setup()
    assert ma_entities(hub, drv)[SAT_UP].device_id == "dev_by_ip"


async def test_a_player_ma_adds_later_gets_its_entity_and_moves_onto_the_node_once_seen():
    hub, drv, made = build_sat([])
    await drv.async_setup()
    assert ma_entities(hub, drv) == {}
    up, proto = sat_players()
    made["c"].on_event({"event": "player_added", "object_id": SAT_PROTO, "data": proto})
    assert ma_entities(hub, drv) == {}
    made["c"].on_event({"event": "player_added", "object_id": SAT_UP, "data": up})
    ent = ma_entities(hub, drv)[SAT_UP]
    assert hub.states.get(ent.entity_id).state == "idle"
    own = ent.device_id
    # The ESPHome node is adopted afterwards: the next change of the player's info links it.
    add_esphome_node(hub, "dev_sat", "echo-dot-3-abc123")
    made["c"].on_event({"event": "player_updated", "object_id": SAT_UP,
                        "data": {**up, "device_info": {**SAT_INFO, "software_version": "2.0"}}})
    assert ent.device_id == "dev_sat" and own != "dev_sat"
    # Unavailable in MA is unavailable here.
    made["c"].on_event({"event": "player_updated", "object_id": SAT_UP, "data": {**up, "available": False}})
    assert hub.states.get(ent.entity_id).state == "unavailable"


async def test_player_updated_with_unchanged_info_does_no_registry_work(monkeypatch):
    hub, drv, made = build_sat(sat_players())
    add_esphome_node(hub, "dev_sat", "echo-dot-3-abc123")
    await drv.async_setup()
    ent = ma_entities(hub, drv)[SAT_UP]
    calls = []
    monkeypatch.setattr(drv, "_player_device", lambda *a, **k: calls.append(a))
    saves = []
    monkeypatch.setattr(hub, "_save", lambda *a, **k: saves.append(a))
    up = sat_players()[0]
    for vol in (10, 20, 30):
        made["c"].on_event({"event": "player_updated", "object_id": SAT_UP, "data": {**up, "volume_level": vol}})
    assert hub.states.get(ent.entity_id).attributes["volume_level"] == 0.3
    await drv.async_poll()  # the 15 s poll relists every player: no registry work either
    assert calls == [] and [s for s in saves if s[0] in ("device", "entity")] == []
    made["c"].on_event({"event": "player_updated", "object_id": SAT_UP, "data": {**up, "name": "Renamed"}})
    assert len(calls) == 1
