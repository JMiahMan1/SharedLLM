"""The capability index: abilities derived from states, registry, protocol and drivers; queries; the API.

Every id and name here is made up.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.homehub import capabilities as caps
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.automation_editor import catalog
from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.dashboard_api import summarize
from services.homehub.runtime import Runtime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
MEDIA_BASIC = 1 | 4 | 8 | 16 | 32 | 128 | 256 | 512 | 2048 | 16384  # pause, volume, mute, tracks, power, play_media...


@pytest.fixture
def hub():
    h = Hub(time_zone="Etc/GMT+7")
    h.areas["den"] = Area("den", "Den")
    h.areas["porch"] = Area("porch", "Porch")

    def entity(eid, platform, entry_id, device_id=None, state="on", **attrs):
        h.entities[eid] = EntityEntry(eid, platform, f"uid-{eid}", entry_id, device_id)
        h.publish(eid, state, attrs)  # what a driver does: the device's own attributes

    h.config_entries["c1"] = ConfigEntry("c1", "cast", "Cast")
    h.config_entries["w1"] = ConfigEntry("w1", "wiz", "Lamp")
    h.config_entries["e1"] = ConfigEntry("e1", "esphome", "Thermostat")
    h.config_entries["r1"] = ConfigEntry("r1", "reolink", "Hub")
    h.devices["d-speaker"] = DeviceEntry("d-speaker", name="Den speaker", area_id="den", config_entries=["c1"])
    h.devices["d-lamp"] = DeviceEntry("d-lamp", name="Porch lamp", area_id="porch", config_entries=["w1"])
    entity("media_player.den_speaker", "cast", "c1", "d-speaker", state="idle",
           supported_features=MEDIA_BASIC | 1048576, source_list=["Radio"])
    entity("light.porch_lamp", "wiz", "w1", "d-lamp", supported_color_modes=["color_temp", "rgb"], effect_list=["Ocean"],
           min_color_temp_kelvin=2200, supported_features=4)
    entity("light.porch_plain", "wiz", "w1", "d-lamp", supported_color_modes=["onoff"])
    entity("climate.hall", "esphome", "e1", state="cool", supported_features=1 | 8, hvac_modes=["off", "cool", "heat"],
           fan_modes=["auto", "low"], min_temp=50, max_temp=90)
    entity("cover.garage", "esphome", "e1", state="closed", supported_features=1 | 2)
    entity("camera.yard", "reolink", "r1", state="idle")
    return h


def test_media_player_abilities_come_from_the_feature_flags_and_the_protocol(hub):
    cap = caps.entity_capabilities(hub, "media_player.den_speaker")
    for a in ("play_media", "pause", "volume", "mute", "next_track", "power", "select_source", "announce", "tts",
              "cast", "show_url"):
        assert a in cap["abilities"], a
    assert "seek" not in cap["abilities"]
    assert "media_player.play_media" in cap["services"] and "media_player.media_seek" not in cap["services"]
    assert cap["details"]["sources"] == ["Radio"] and cap["platform"] == "cast" and cap["area_id"] == "den"


def test_lights_dim_and_colour_only_when_their_modes_say_so(hub):
    lamp = caps.entity_capabilities(hub, "light.porch_lamp")
    assert {"dim", "color", "color_temp", "effect"} <= set(lamp["abilities"])
    assert lamp["details"]["effects"] == ["Ocean"] and lamp["details"]["min_color_temp_kelvin"] == 2200
    plain = caps.entity_capabilities(hub, "light.porch_plain")
    assert "dim" not in plain["abilities"] and {"power", "toggle"} <= set(plain["abilities"])


def test_climate_cover_and_camera(hub):
    hvac = caps.entity_capabilities(hub, "climate.hall")
    assert {"set_temperature", "fan_mode", "hvac_mode"} <= set(hvac["abilities"])
    assert "preset_mode" not in hvac["abilities"]
    assert hvac["details"]["min_temp"] == 50 and hvac["details"]["hvac_modes"] == ["off", "cool", "heat"]
    assert "climate.set_fan_mode" in hvac["services"]
    cover = caps.entity_capabilities(hub, "cover.garage")
    assert set(cover["abilities"]) == {"open", "close"} and "cover.set_cover_position" not in cover["services"]
    cam = caps.entity_capabilities(hub, "camera.yard")
    assert cam["details"]["stream_types"] == ["snapshot"]
    hub.drivers["r1"] = SimpleNamespace(async_camera_stream_sources=lambda e: {}, async_open_recording=lambda e: None)
    cam = caps.entity_capabilities(hub, "camera.yard")
    assert "rtsp" in cam["details"]["stream_types"] and "camera_recordings" in cam["abilities"]


def test_protocol_detail_from_the_unique_id(hub):
    hub.entities["switch.relay"] = EntityEntry("switch.relay", "esphome", "AA:BB:CC:00:00:01/0/switch/Relay", "e1")
    hub.entities["light.hk"] = EntityEntry("light.hk", "homekit_controller", "aa:bb:cc:00:00:02_1_10", None)
    hub.entities["light.mt"] = EntityEntry("light.mt", "matter", "deviceid_ABC-000000000000000A-MatterNodeDevice-1-light",
                                           None)
    assert caps.entity_capabilities(hub, "switch.relay")["protocol"]["esphome_entity_type"] == "switch"
    assert caps.entity_capabilities(hub, "light.hk")["protocol"]["homekit"] == {"aid": 1, "iid": 10}
    assert caps.entity_capabilities(hub, "light.mt")["protocol"]["matter"] == {"node_id": 10, "endpoint": 1,
                                                                              "key": "light"}


def test_a_driver_can_add_its_own_abilities(hub):
    hub.drivers["e1"] = SimpleNamespace(entity_abilities=lambda ent: ["announce"] if ent.domain == "cover" else [])
    assert "announce" in caps.abilities_of(hub, "cover.garage")


def test_find_answers_which_devices_can_do_something(hub):
    assert [c["entity_id"] for c in caps.find(hub, ["announce"])] == ["media_player.den_speaker"]
    assert [c["entity_id"] for c in caps.find(hub, ["dim"], area="Porch")] == ["light.porch_lamp"]
    assert caps.find(hub, ["dim"], area="den") == []
    with pytest.raises(ValueError):
        caps.find(hub, ["teleport"])
    with pytest.raises(LookupError):
        caps.find(hub, ["dim"], area="Attic")
    assert caps.device_capabilities(hub, "d-lamp")["by_ability"]["dim"] == ["light.porch_lamp"]


def test_a_new_device_is_in_the_index_as_soon_as_it_is_registered(hub):
    assert caps.find(hub, ["lock"]) == []
    hub.entities["lock.side_door"] = EntityEntry("lock.side_door", "matter", "u", None)
    hub.publish("lock.side_door", "locked", {"supported_features": 1})
    found = caps.find(hub, ["lock"])
    assert [c["entity_id"] for c in found] == ["lock.side_door"] and "open_door" in found[0]["abilities"]
    hub.entities["lock.side_door"].disabled_by = "user"
    assert caps.find(hub, ["lock"]) == []


def test_pages_and_the_automation_editor_get_abilities(hub):
    assert "dim" in summarize(hub, "light.porch_lamp")["abilities"]
    ents = {e["entity_id"]: e for e in catalog(hub)["entities"]}
    assert "light.turn_on" in ents["light.porch_plain"]["services"]
    assert "cover.set_cover_position" not in ents["cover.garage"]["services"]


def test_the_api(hub):
    rt = Runtime(hub, SimpleNamespace(get_kv=lambda *a, **k: None))
    client = TestClient(build_app(rt, Auth(hub, SECRET)))
    assert client.get("/api/homehub/capabilities").status_code == 401
    full = client.get("/api/homehub/capabilities", headers=H).json()
    assert full["counts"]["dim"] == 1 and "announce" in full["abilities"]
    one = client.get("/api/homehub/capabilities", headers=H, params=[("ability", "power"), ("ability", "dim")]).json()
    assert [e["entity_id"] for e in one["entities"]] == ["light.porch_lamp"]
    assert client.get("/api/homehub/capabilities", headers=H, params={"ability": "fly"}).status_code == 400
    assert client.get("/api/homehub/capabilities", headers=H, params={"area": "Attic"}).status_code == 404
    assert client.get("/api/homehub/capabilities/light.porch_lamp", headers=H).json()["platform"] == "wiz"
    assert client.get("/api/homehub/capabilities/light.none", headers=H).status_code == 404
    dev = client.get("/api/homehub/capabilities/devices/d-speaker", headers=H).json()
    assert "announce" in dev["abilities"]


def test_imported_registry_values_and_restored_attributes_are_never_abilities(hub):
    """What only the Home Assistant import said is shown as imported, never trusted, until the device says it."""
    hub.config_entries["c2"] = ConfigEntry("c2", "cast", "Cast 2")
    hub.entities["media_player.attic"] = EntityEntry(
        "media_player.attic", "cast", "uid-attic", "c2", supported_features=MEDIA_BASIC,
        original_device_class="speaker", capabilities={"source_list": ["Radio"]})
    # A last state restored from the import (the runtime restores without publishing).
    hub.states.set("media_player.attic", "idle", {"supported_features": MEDIA_BASIC, "source_list": ["Radio"]})
    cap = caps.entity_capabilities(hub, "media_player.attic")
    assert cap["confirmed"] is False and cap["source"] == "imported"
    assert "play_media" not in cap["abilities"] and "volume" not in cap["abilities"]
    assert set(cap["abilities"]) == {"cast", "show_url", "tts"}  # the protocol's own, from the integration
    assert {"supported_features", "capabilities", "original_device_class", "state_attributes"} <= set(
        cap["imported_only"])
    assert "registry" not in cap["protocol"] and cap["protocol"]["registry_imported"] == {"source_list": ["Radio"]}
    assert caps.provenance(hub)["imported_only_entities"] == ["media_player.attic"]
    # The registry fill never copies an imported value into a published state either.
    hub.publish("media_player.attic", "idle", {})
    assert "supported_features" not in hub.states.get("media_player.attic").attributes
    # The driver connects and the device reports its own features: now they count and are marked.
    hub.publish("media_player.attic", "idle", {"supported_features": 512 | 4})
    cap = caps.entity_capabilities(hub, "media_player.attic")
    assert cap["confirmed"] is True and {"play_media", "volume"} <= set(cap["abilities"])
    assert "pause" not in cap["abilities"]
    ent = hub.entities["media_player.attic"]
    assert ent.supported_features == 512 | 4 and ent.info_source()["supported_features"] == "device"
    assert ent.info_source()["original_device_class"] == "imported"


def test_the_api_lists_the_live_areas(hub):
    hub.areas["den"].aliases = ["Family room"]
    rt = Runtime(hub, SimpleNamespace(get_kv=lambda *a, **k: None))
    client = TestClient(build_app(rt, Auth(hub, SECRET)))
    body = client.get("/api/homehub/capabilities", headers=H).json()
    assert body["areas"] == [{"area_id": "den", "name": "Den", "aliases": ["Family room"]},
                             {"area_id": "porch", "name": "Porch", "aliases": []}]
    hub.areas["attic"] = Area("attic", "Attic")  # added at runtime: in the next answer, no restart
    names = [a["name"] for a in client.get("/api/homehub/capabilities?ability=dim", headers=H).json()["areas"]]
    assert names == ["Attic", "Den", "Porch"]


def test_a_voice_satellite_announces_from_its_flags_or_its_driver(hub):
    hub.config_entries["v1"] = ConfigEntry("v1", "esphome", "Voice")
    hub.entities["assist_satellite.hall"] = EntityEntry("assist_satellite.hall", "esphome", "u-sat", "v1")
    # Nothing published and no driver: no ability is claimed.
    assert caps.entity_capabilities(hub, "assist_satellite.hall")["abilities"] == []
    # The satellite's own published flags: announce (1) and start conversation (2).
    hub.publish("assist_satellite.hall", "idle", {"supported_features": 1 | 2})
    cap = caps.entity_capabilities(hub, "assist_satellite.hall")
    assert {"announce", "converse", "tts"} <= set(cap["abilities"])
    assert "assist_satellite.announce" in cap["services"] and "assist_satellite.start_conversation" in cap["services"]
    assert [c["entity_id"] for c in caps.find(hub, ["announce"], domain="assist_satellite")] == [
        "assist_satellite.hall"]


def test_an_esphome_satellite_announces_when_the_node_says_so_and_never_converses():
    from aioesphomeapi import DeviceInfo, VoiceAssistantFeature

    from services.homehub.drivers.esphome import ESPHomeDriver

    h = Hub()
    entry = ConfigEntry("v2", "esphome", "Voice", data={"host": "192.0.2.61", "device_name": "voice-pe"})
    h.config_entries["v2"] = entry
    h.entities["assist_satellite.kitchen"] = EntityEntry("assist_satellite.kitchen", "esphome",
                                                         "02:00:00:00:00:61-assist_satellite", "v2")
    drv = ESPHomeDriver(h, entry)
    h.drivers["v2"] = drv
    assert caps.entity_capabilities(h, "assist_satellite.kitchen")["abilities"] == []
    flags = int(VoiceAssistantFeature.VOICE_ASSISTANT | VoiceAssistantFeature.ANNOUNCE
                | VoiceAssistantFeature.START_CONVERSATION)
    drv._device_info = DeviceInfo(name="voice-pe", voice_assistant_feature_flags=flags)
    abilities = caps.entity_capabilities(h, "assist_satellite.kitchen")["abilities"]
    assert "announce" in abilities and "tts" in abilities and "converse" not in abilities
    drv._device_info = DeviceInfo(name="voice-pe", voice_assistant_feature_flags=int(
        VoiceAssistantFeature.VOICE_ASSISTANT))
    assert caps.entity_capabilities(h, "assist_satellite.kitchen")["abilities"] == []


def test_a_music_assistant_player_without_an_area_inherits_the_one_it_drives(hub):
    hub.config_entries["m1"] = ConfigEntry("m1", "music_assistant", "MA")
    cast_uuid = "uid-media_player.den_speaker"  # the den speaker's unique_id doubles as MA's player id here
    # 1. MA names the player by the wrapped player's id.
    hub.devices["d-ma1"] = DeviceEntry("d-ma1", name="Den speaker (MA)", config_entries=["m1"])
    hub.entities["media_player.den_speaker_ma"] = EntityEntry("media_player.den_speaker_ma", "music_assistant",
                                                              cast_uuid, "m1", "d-ma1")
    hub.publish("media_player.den_speaker_ma", "idle", {"supported_features": 512})
    cap = caps.entity_capabilities(hub, "media_player.den_speaker_ma")
    assert cap["area_id"] == "den" and cap["area_source"] == "inherited"
    assert hub.entities["media_player.den_speaker_ma"].area_id is None  # never written into the registry
    assert hub.devices["d-ma1"].area_id is None
    assert [c["entity_id"] for c in caps.find(hub, ["play_media"], area="Den", platform="music_assistant")] == [
        "media_player.den_speaker_ma"]
    # 2. Another device that shares its MAC (what MA reports for the player).
    hub.devices["d-porch-amp"] = DeviceEntry("d-porch-amp", name="Porch amp", area_id="porch",
                                             connections=[["mac", "02:00:00:00:00:71"]])
    hub.devices["d-ma2"] = DeviceEntry("d-ma2", name="Porch amp (MA)", config_entries=["m1"],
                                       connections=[["mac", "02:00:00:00:00:71"]])
    hub.entities["media_player.porch_amp_ma"] = EntityEntry("media_player.porch_amp_ma", "music_assistant",
                                                            "up000000000071", "m1", "d-ma2")
    cap = caps.entity_capabilities(hub, "media_player.porch_amp_ma")
    assert cap["area_id"] == "porch" and cap["area_source"] == "inherited"
    # 3. Its own area always wins, and a player that drives nothing known has none.
    hub.entities["media_player.porch_amp_ma"].area_id = "den"
    cap = caps.entity_capabilities(hub, "media_player.porch_amp_ma")
    assert cap["area_id"] == "den" and cap["area_source"] == "registry"
    hub.entities["media_player.lonely_ma"] = EntityEntry("media_player.lonely_ma", "music_assistant", "up9", "m1")
    cap = caps.entity_capabilities(hub, "media_player.lonely_ma")
    assert cap["area_id"] is None and cap["area_source"] is None
    # Other integrations never inherit.
    hub.entities["media_player.stray_cast"] = EntityEntry("media_player.stray_cast", "cast", "x", "c1", "d-ma2")
    assert caps.entity_capabilities(hub, "media_player.stray_cast")["area_source"] is None
