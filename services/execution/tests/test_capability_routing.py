"""Every device choice by room goes through the hub's capability index and area registry.

All areas, ids and names are made up, and chosen so the old heuristics (room
words in entity ids, "tv"/"cast" in names, feature bitmasks) would pick wrong:
the entity ids name rooms the devices are not in.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import pytest

from services.execution import announce_handlers, announce_routes, capability_index, ha_client, targets
from services.execution.capability_index import CapabilityIndex, CapabilityIndexError, UnknownAreaError

HUB = "http://192.0.2.10:8123"

AREAS = [
    {"area_id": "area_1", "name": "Den", "aliases": ["Snug"]},
    {"area_id": "area_2", "name": "Loft", "aliases": []},
    {"area_id": "area_3", "name": "Guest Room", "aliases": []},
    {"area_id": "area_4", "name": "Room", "aliases": []},
]


def _cap(eid, integration, area, abilities, *, device=None, available=True, device_class=None, services=()):
    return {"entity_id": eid, "domain": eid.split(".", 1)[0], "integration": integration, "platform": integration,
            "area_id": area, "device_id": device, "available": available, "abilities": sorted(abilities),
            "details": {"device_class": device_class} if device_class else {}, "services": list(services)}


ENTITIES = [
    # In the Den: a TV (its id names the Loft), its remote, a Cast dongle, a speaker and a voice satellite.
    _cap("media_player.loft_tv", "webostv", "area_1", {"play_media", "show_url", "power", "stop"}, device="dev_tv",
         device_class="tv", services=("media_player.media_stop", "media_player.play_media")),
    _cap("remote.loft_tv_remote", "webostv", "area_1", {"remote_command", "power"}, device="dev_tv"),
    _cap("media_player.attic_dongle", "cast", "area_1", {"play_media", "cast", "show_url", "stop"},
         device="dev_cast", services=("media_player.media_stop",)),
    _cap("media_player.porch_speaker", "esphome", "area_1", {"play_media", "announce", "tts"}, device="dev_spk"),
    _cap("assist_satellite.porch_speaker", "esphome", "area_1", set(), device="dev_spk"),
    # In the Loft: a Roku (named after the Den), a Music Assistant player and an unavailable speaker.
    _cap("media_player.den_roku", "roku", "area_2", {"play_media", "launch_app", "power"}, device="dev_roku"),
    _cap("media_player.den_music", "music_assistant", "area_2",
         {"play_media", "announce", "search_media", "enqueue", "stop"}, device="dev_ma"),
    _cap("media_player.loft_spare", "dlna_dmr", "area_2", {"play_media"}, device="dev_dlna", available=False),
    # Not media targets: a TTS engine and a light.
    _cap("tts.engine", "kokoro", None, {"tts"}),
    _cap("light.den_lamp", "wiz", "area_1", {"dim", "power"}),
]


def _idx(entities=ENTITIES, areas=AREAS):
    return CapabilityIndex(list(entities), {}, list(areas))


def _serve(monkeypatch, idx):
    async def routing_index(ha_url, ha_token, purpose):
        return idx

    monkeypatch.setattr(capability_index, "routing_index", routing_index)


def _failing(monkeypatch):
    async def routing_index(ha_url, ha_token, purpose):
        raise CapabilityIndexError("the hub answered HTTP 500 for its capability index")

    monkeypatch.setattr(capability_index, "routing_index", routing_index)


# --------------------------------------------------------------------------- the index itself


def test_rooms_resolve_by_area_name_alias_and_id_never_by_entity_id():
    idx = _idx()
    assert idx.area_id_for("den") == idx.area_id_for("SNUG") == idx.area_id_for("area_1") == "area_1"
    assert idx.area_id_for("guest_room") == "area_3"  # a presence slug matches the area name
    assert idx.area_id_for("Attic") is None
    with pytest.raises(UnknownAreaError, match="Den, Guest Room, Loft, Room"):
        idx.require_area("Attic")
    # Natural language: the longest area name or alias in the phrase, as whole words.
    assert idx.area_in_text("the TV in the snug") == "area_1"
    assert idx.area_in_text("guest room speaker") == "area_3"  # not the shorter "Room"
    assert idx.area_in_text("the bedroom") is None  # "room" inside a word is not the Room
    assert idx.area_in_text("kitchen") is None


def test_media_targets_are_what_can_play_sound_and_screens_what_show_pages():
    idx = _idx()
    assert idx.media_targets("area_1") == ["media_player.attic_dongle", "media_player.loft_tv",
                                           "media_player.porch_speaker"]
    assert "tts.engine" not in idx.media_targets()  # a speech engine is not a speaker
    assert idx.is_screen("media_player.loft_tv") and idx.is_screen("media_player.den_roku")
    assert not idx.is_screen("media_player.porch_speaker")
    assert idx.nearby("media_player.loft_tv", any_of=("remote_command",)) == ["remote.loft_tv_remote"]
    assert idx.service_for("media_player.loft_tv", "media_stop") == ("media_player", "media_stop")
    assert idx.service_for("media_player.den_roku", "media_stop") is None


def test_pick_player_chooses_by_ability_kind_and_availability():
    idx = _idx()
    assert idx.pick_player("area_1", "the TV in the den") in ("media_player.attic_dongle", "media_player.loft_tv")
    assert idx.pick_player("area_1", "den speaker") == "media_player.porch_speaker"
    assert idx.pick_player("area_1", "den", "announcement") == "media_player.porch_speaker"
    assert idx.pick_player("area_2", "loft", "music") == "media_player.den_music"
    assert idx.pick_player("area_2", "loft", "video") == "media_player.den_roku"
    states = [{"entity_id": "media_player.den_roku", "state": "playing"}]
    assert idx.pick_player("area_2", "loft", None, states) == "media_player.den_roku"
    assert idx.pick_player("area_3", "guest room") is None


# --------------------------------------------------------------------------- loading


class _WSMsg:
    def __init__(self, data):
        self.data = json.dumps(data)


class _FakeWS:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def receive(self, timeout=None):
        return _WSMsg(self.replies.pop(0))

    async def send_str(self, text):
        self.sent.append(json.loads(text))


class _FakeSession:
    def __init__(self, ws):
        self.ws = ws

    def ws_connect(self, url):
        self.url = url
        return self.ws


async def test_areas_come_from_the_hub_registry_when_the_index_does_not_carry_them(monkeypatch):
    ws = _FakeWS([{"type": "auth_required"}, {"type": "auth_ok"},
                  {"id": 1, "type": "result", "success": True, "result": AREAS}])

    async def fetch_index(key, token):
        return {"entities": ENTITIES}

    async def get_session(host, verify=True):
        return _FakeSession(ws)

    import services.execution.http_client as http_client

    monkeypatch.setattr(capability_index, "_fetch_index", fetch_index)
    monkeypatch.setattr(http_client, "get_session", get_session)
    idx = await capability_index.load(HUB, "tok")
    assert idx.area_id_for("Snug") == "area_1"
    assert ws.sent[0] == {"type": "auth", "access_token": "tok"}
    assert ws.sent[1]["type"] == "config/area_registry/list"


async def test_an_area_registry_failure_is_an_error_not_a_guess(monkeypatch):
    ws = _FakeWS([{"type": "auth_required"}, {"type": "auth_invalid"}])

    async def fetch_index(key, token):
        return {"entities": ENTITIES}

    async def get_session(host, verify=True):
        return _FakeSession(ws)

    import services.execution.http_client as http_client

    monkeypatch.setattr(capability_index, "_fetch_index", fetch_index)
    monkeypatch.setattr(http_client, "get_session", get_session)
    with pytest.raises(CapabilityIndexError, match="refused"):
        await capability_index.load(HUB, "tok")


async def test_a_plain_home_assistant_is_a_logged_fallback(caplog):
    with caplog.at_level(logging.WARNING, logger="execution.capability_index"):
        assert await capability_index.routing_index(HUB, "tok", "room targeting") is None
    assert "HTTP 404" in caplog.text and "room targeting" in caplog.text


# --------------------------------------------------------------------------- targets.py


async def test_rooms_resolve_to_every_device_there_that_can_play_sound(monkeypatch):
    _serve(monkeypatch, _idx())
    monkeypatch.setattr(targets, "fetch_room_speakers", AsyncMock(return_value={}))
    out = await targets.resolve_targets(rooms=["snug"], ha_url=HUB, ha_token="t")
    assert out == ["media_player.attic_dongle", "media_player.loft_tv", "media_player.porch_speaker"]


async def test_a_device_added_on_the_hub_is_a_target_with_no_code_change(monkeypatch):
    monkeypatch.setattr(targets, "fetch_room_speakers", AsyncMock(return_value={}))
    _serve(monkeypatch, _idx())
    assert await targets.resolve_targets(rooms=["Guest Room"], ha_url=HUB, ha_token="t") == []
    added = [*ENTITIES, _cap("media_player.new_thing", "cast", "area_3", {"play_media"})]
    _serve(monkeypatch, _idx(added))
    assert await targets.resolve_targets(rooms=["Guest Room"], ha_url=HUB, ha_token="t") == [
        "media_player.new_thing"]


async def test_an_unknown_room_and_a_failed_index_raise(monkeypatch):
    monkeypatch.setattr(targets, "fetch_room_speakers", AsyncMock(return_value={}))
    legacy = AsyncMock(side_effect=AssertionError("no legacy guessing when the hub has an index"))
    monkeypatch.setattr(targets, "_legacy_speakers_for_rooms", legacy)
    _serve(monkeypatch, _idx())
    with pytest.raises(UnknownAreaError):
        await targets.resolve_targets(rooms=["Attic"], ha_url=HUB, ha_token="t")
    _failing(monkeypatch)
    with pytest.raises(CapabilityIndexError):
        await targets.resolve_targets(rooms=["Den"], ha_url=HUB, ha_token="t")


async def test_a_plain_ha_keeps_the_legacy_room_matching(monkeypatch):
    monkeypatch.setattr(targets, "fetch_room_speakers", AsyncMock(return_value={}))
    monkeypatch.setattr(ha_client, "get_areas", AsyncMock(return_value={"media_player.x": "Den"}))
    assert await targets.resolve_targets(rooms=["Den"], ha_url=HUB, ha_token="t") == ["media_player.x"]


async def test_announcing_to_an_unknown_room_says_so(monkeypatch):
    monkeypatch.setattr(targets, "fetch_room_speakers", AsyncMock(return_value={}))
    _serve(monkeypatch, _idx())
    from services.execution.schemas import UserContext

    req = type("R", (), {"target_rooms": ["Attic"], "message": "hi", "volume": None})()
    out = await announce_routes.run_announcement(req, UserContext(user="alex", ha_url=HUB, ha_token="t"),
                                                 announce_fn=AsyncMock())
    assert out["status"] == "FAILURE" and "no area 'Attic'" in out["message"]


# --------------------------------------------------------------------------- companions and stopping


def test_companions_come_from_the_same_device_and_area_not_names():
    idx = _idx()
    states = [{"entity_id": "media_player.loft_tv", "attributes": {}}]
    comp = announce_handlers.resolve_tv_companions("media_player.loft_tv", states, idx=idx)
    assert comp["remote_sibling"] == "remote.loft_tv_remote"
    assert comp["cast_sibling"] == "media_player.attic_dongle"
    assert comp["room_assist_satellite"] == "assist_satellite.porch_speaker"
    assert comp["room_esphome_speaker"] == "media_player.porch_speaker"
    assert comp["ma_sibling"] is None  # the Music Assistant player is in the Loft
    assert comp["area_id"] == "area_1"
    roku = announce_handlers.resolve_tv_companions(
        "media_player.den_roku", [{"entity_id": "media_player.den_music",
                                   "attributes": {"active_queue": "media_player.den_roku"}}], idx=idx)
    assert roku["ma_sibling"] == "media_player.den_music" and roku["cast_sibling"] is None


async def test_stopping_an_alert_uses_the_services_the_index_lists(monkeypatch):
    _serve(monkeypatch, _idx())
    calls = []

    async def call_service(url, tok, domain, service, eid, data):
        calls.append((domain, service, eid))
        return {"ok": True}

    monkeypatch.setattr(ha_client, "call_service", call_service)
    states = [{"entity_id": "media_player.loft_tv", "state": "playing", "attributes": {}}]
    out = await announce_handlers.stop_tv_or_media_alert(HUB, "t", "media_player.loft_tv", states)
    assert ("media_player", "media_stop", "media_player.loft_tv") in calls
    assert ("media_player", "media_stop", "media_player.attic_dongle") in calls
    assert out["platform"] == "webos"


async def test_classification_lists_only_what_the_index_says_can_play_sound(monkeypatch):
    _serve(monkeypatch, _idx())
    monkeypatch.setattr(announce_handlers, "_get_device_registry", lambda: None)
    states = [{"entity_id": e["entity_id"], "state": "idle", "attributes": {"friendly_name": f"Thing {i}"}}
              for i, e in enumerate(ENTITIES)]
    out = {d["entity_id"]: d for d in await announce_handlers.classify_communication_devices(HUB, "t", states)}
    assert "tts.engine" not in out and "light.den_lamp" not in out
    assert out["media_player.porch_speaker"]["kind"] == "esphome"
    assert out["media_player.den_music"]["kind"] == "speaker"
    assert out["media_player.porch_speaker"]["assist_satellite_entity"] == "assist_satellite.porch_speaker"
    assert out["media_player.den_roku"]["kind"] == "tv" and out["media_player.den_roku"]["area"] == "Loft"


async def test_classification_raises_when_the_index_fails(monkeypatch):
    _failing(monkeypatch)
    with pytest.raises(CapabilityIndexError):
        await announce_handlers.classify_communication_devices(HUB, "t", [])


# --------------------------------------------------------------------------- media handlers


def _media_req(**kw):
    from services.execution.schemas import MediaPlayRequest, UserContext

    return MediaPlayRequest(user_context=UserContext(user="alex", ha_url=HUB, ha_token="t"), **kw)


async def test_a_room_in_a_device_name_picks_the_player_there(monkeypatch):
    from services.execution.handlers import media

    _serve(monkeypatch, _idx())
    monkeypatch.setattr(ha_client, "get_states", AsyncMock(return_value=[]))
    by_name = AsyncMock(side_effect=AssertionError("a room is resolved by area, not by name"))
    monkeypatch.setattr(ha_client, "resolve_entity_by_name", by_name)
    assert await media.resolve_entity(_media_req(device_name="snug speaker"), HUB, "t") == "media_player.porch_speaker"
    assert await media.resolve_entity(_media_req(device_name="the loft"), HUB, "t", "music") == "media_player.den_music"
    with pytest.raises(ValueError, match="Guest Room"):
        await media.resolve_entity(_media_req(device_name="guest room"), HUB, "t")


async def test_with_no_device_named_the_choice_is_among_what_can_play(monkeypatch):
    from services.execution.handlers import media

    _serve(monkeypatch, _idx())
    monkeypatch.setattr(ha_client, "get_states", AsyncMock(return_value=[
        {"entity_id": "light.den_lamp", "state": "on"}, {"entity_id": "media_player.den_music", "state": "playing"}]))
    assert await media.resolve_entity(_media_req(), HUB, "t") == "media_player.den_music"


async def test_the_music_assistant_player_is_the_one_driving_the_device(monkeypatch):
    from services.execution.handlers import media

    _serve(monkeypatch, _idx())
    monkeypatch.setattr(ha_client, "get_states", AsyncMock(return_value=[
        {"entity_id": "media_player.den_music", "attributes": {"friendly_name": "Zzz",
                                                                "active_queue": "media_player.den_roku"}}]))
    ctx = type("C", (), {"ha_url": HUB, "ha_token": "t"})()
    assert await media.resolve_mass_entity(ctx, "media_player.den_roku") == "media_player.den_music"
    assert await media.resolve_mass_entity(ctx, "media_player.loft_tv") == "media_player.loft_tv"


def test_transport_reads_abilities_not_feature_bits():
    from services.execution.handlers import media

    idx = _idx()
    assert media._standard_supported(idx, "media_player.loft_tv", "stop", 0) is True
    assert media._standard_supported(idx, "media_player.den_roku", "pause", 0xFFFFFF) is False
    assert media._standard_supported(None, "media_player.x", "pause", 1) is True  # a plain HA


async def test_media_status_filters_by_area_from_the_registry(monkeypatch):
    from services.execution.handlers import media_status
    from services.execution.schemas import MediaStatusRequest, UserContext

    _serve(monkeypatch, _idx())
    monkeypatch.setattr(media_status.ha_client, "get_states", AsyncMock(return_value=[
        {"entity_id": e["entity_id"], "state": "idle", "attributes": {}} for e in ENTITIES]))
    ctx = UserContext(user="alex", ha_url=HUB, ha_token="t")
    res = await media_status.handle_media_status(MediaStatusRequest(user_context=ctx, area="Snug"))
    players = {p["entity_id"]: p for p in res.detail["all_players"]} if res.detail else {}
    assert set(players) == {"media_player.attic_dongle", "media_player.loft_tv", "media_player.porch_speaker"}
    assert players["media_player.loft_tv"]["icon_kind"] == "tv"
    assert players["media_player.porch_speaker"]["icon_kind"] == "speaker"
    bad = await media_status.handle_media_status(MediaStatusRequest(user_context=ctx, area="Attic"))
    assert bad.status == "FAILURE" and "Attic" in bad.message


def test_intercom_finds_the_satellite_by_area():
    from services.execution.handlers import intercom

    idx = _idx()
    find = intercom._find_assist_satellite_for_room_or_entities
    assert find([], "snug", [], idx=idx) == "assist_satellite.porch_speaker"
    assert find([], None, ["media_player.loft_tv"], idx=idx) == "assist_satellite.porch_speaker"
    assert find([], "Loft", [], idx=idx) is None
    with pytest.raises(UnknownAreaError):
        find([], "Attic", [], idx=idx)


async def test_platform_checks_and_siblings_come_from_the_index(monkeypatch):
    from services.execution.handlers import android_tv, roku, samsung, webos

    entities = [*ENTITIES,
                _cap("media_player.cast_box", "androidtv_remote", "area_3", {"play_media", "launch_app"},
                     device="dev_atv"),
                _cap("media_player.samsung_named", "cast", "area_3", {"play_media", "cast"}, device="dev_c2"),
                _cap("media_player.lg_hk", "homekit_controller", "area_1", {"power"}, device="dev_hk")]
    _serve(monkeypatch, _idx(entities))
    monkeypatch.setattr(ha_client, "get_state", AsyncMock(side_effect=AssertionError("no attribute guessing")))
    assert await roku.is_roku_device(HUB, "t", "media_player.den_roku") is True
    assert await roku.is_roku_device(HUB, "t", "media_player.loft_tv") is False
    assert await webos.is_webos_tv(HUB, "t", "media_player.loft_tv") is True
    assert await samsung.is_samsung_tv(HUB, "t", "media_player.samsung_named") is False
    assert await android_tv.is_android_tv(HUB, "t", "media_player.cast_box") is True
    assert await android_tv._find_cast_sibling(HUB, "t", "media_player.cast_box") == "media_player.samsung_named"
    assert await webos._find_homekit_entity(HUB, "t", "media_player.loft_tv") == "media_player.lg_hk"


async def test_the_companion_remote_is_on_the_same_device_or_an_error(monkeypatch):
    _serve(monkeypatch, _idx())
    ha_client._REMOTE_CACHE.clear()
    assert await ha_client.find_companion_remote(HUB, "t", "media_player.loft_tv") == "remote.loft_tv_remote"
    with pytest.raises(LookupError):
        await ha_client.find_companion_remote(HUB, "t", "media_player.den_roku")
    ha_client._REMOTE_CACHE.clear()


# --------------------------------------------------------------------------- timers


def test_timer_targets_ring_by_ability_and_area():
    from services.execution.handlers import timer

    idx = _idx()
    assert timer.ring_entities(idx, "media_player.porch_speaker") == (["media_player.porch_speaker"], None)
    assert timer.ring_entities(idx, "Snug")[0] == ["media_player.attic_dongle", "media_player.loft_tv",
                                                  "media_player.porch_speaker"]
    assert timer.ring_entities(idx, "den_roku") == (["media_player.den_roku"], None)
    ids, why = timer.ring_entities(idx, "light.den_lamp")
    assert ids == [] and "cannot play sound" in why
    ids, why = timer.ring_entities(idx, "Attic")
    assert ids == [] and "neither" in why
    ids, why = timer.ring_entities(None, "x", "HTTP 500")
    assert ids == [] and "HTTP 500" in why
    assert timer.ring_entities(None, "kitchen") == (["media_player.kitchen"], None)  # a plain HA


async def test_a_timer_on_a_room_rings_every_device_there(monkeypatch):
    import services.execution.main as m
    from services.execution import esphome_client

    _serve(monkeypatch, _idx())
    monkeypatch.setattr(m, "resolve_internal_user", AsyncMock(return_value={
        "user": "alex", "ha_url": HUB, "ha_token": "t"}))
    monkeypatch.setattr(m.timer, "notify_timer_done", AsyncMock())
    monkeypatch.setattr(m.timer, "get_redis", AsyncMock(side_effect=RuntimeError("no redis in tests")))
    monkeypatch.setattr(m, "text_to_speech", AsyncMock(return_value=b""))
    monkeypatch.setattr(m, "cache_temp_audio", lambda *a, **k: None)
    monkeypatch.setattr(esphome_client, "get_devices", AsyncMock(return_value={}))
    monkeypatch.setattr(esphome_client, "find_device_for_ha_entity", AsyncMock(return_value=None))
    monkeypatch.setattr(m.ha_client, "get_state", AsyncMock(return_value={"state": "idle", "attributes": {}}))
    rung = []

    async def dispatch(url, tok, eid, *a, **k):
        rung.append(eid)
        return {"ok": True}

    monkeypatch.setattr(announce_handlers, "dispatch_announce", dispatch)
    res = await m.execute_trigger({"user_id": "alex", "timer": {"title": "Tea", "target_devices": ["Loft", "Attic"]}})
    assert rung == ["media_player.den_music", "media_player.den_roku", "media_player.loft_spare"]
    assert "not rung: Attic" in res.message


async def test_stopping_a_room_stops_its_devices(monkeypatch):
    from services.execution.handlers import timer

    _serve(monkeypatch, _idx())
    stopped = []

    async def stop(url, tok, eid):
        stopped.append(eid)
        return {"ok": True}

    monkeypatch.setattr(announce_handlers, "stop_tv_or_media_alert", stop)
    r = AsyncMock()
    r.keys.return_value = []
    out = await timer.stop_ringing_devices(r, device_name="Loft", ha_url=HUB, ha_token="t")
    assert sorted(out) == ["media_player.den_music", "media_player.den_roku", "media_player.loft_spare"]


# --------------------------------------------------------------------------- entity search


def test_entity_search_filters_by_the_hubs_area_names_and_aliases(monkeypatch):
    from fastapi.testclient import TestClient

    from services.execution.main import app

    _serve(monkeypatch, _idx())
    monkeypatch.setattr(ha_client, "get_states", AsyncMock(return_value=[
        {"entity_id": e["entity_id"], "state": "idle", "attributes": {"friendly_name": e["entity_id"]}}
        for e in ENTITIES]))
    ctx = {"user": "alex", "is_admin": True, "ha_url": HUB, "ha_token": "t"}
    client = TestClient(app)
    resp = client.post("/execute/entity/search", headers={"X-Internal-Secret": "test-secret"},
                       json={"user_context": ctx, "query": "", "area": "snug", "domain": "media_player"})
    found = {e["entity_id"]: e for e in resp.json()["detail"]["entities"]}
    assert set(found) == {"media_player.loft_tv", "media_player.attic_dongle", "media_player.porch_speaker"}
    assert found["media_player.loft_tv"]["area"] == "Den" and found["media_player.loft_tv"]["platform"] == "webos"
    bad = client.post("/execute/entity/search", headers={"X-Internal-Secret": "test-secret"},
                      json={"user_context": ctx, "query": "", "area": "Attic"}).json()
    assert bad["status"] == "FAILURE" and "Attic" in bad["message"]
