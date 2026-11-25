"""``GET /hub/voice/devices``: the voice satellites, speakers and TVs identity
copies for assigning to people, one per physical device, keyed by hardware."""
from services.homehub.core import Area, DeviceEntry, EntityEntry, Hub
from services.homehub.voice_api import shared_devices


def _hub() -> Hub:
    hub = Hub()
    hub.areas["den"] = Area(area_id="den", name="Den")
    hub.devices["d-sat"] = DeviceEntry(id="d-sat", name="Test Satellite", area_id="den",
                                       connections=[["mac", "02:00:00:00:00:AA"]])
    hub.entities["assist_satellite.test_sat"] = EntityEntry(entity_id="assist_satellite.test_sat", platform="esphome",
                                                            device_id="d-sat")
    hub.entities["media_player.test_sat"] = EntityEntry(entity_id="media_player.test_sat", platform="esphome",
                                                        device_id="d-sat")
    hub.devices["d-tv"] = DeviceEntry(id="d-tv", name="Test TV", identifiers=[["cast", "uuid-1"]])
    hub.entities["media_player.test_tv"] = EntityEntry(entity_id="media_player.test_tv", platform="cast",
                                                       device_id="d-tv", original_device_class="tv")
    hub.devices["d-spk"] = DeviceEntry(id="d-spk", name="Test Speaker")
    hub.entities["media_player.test_spk"] = EntityEntry(entity_id="media_player.test_spk", platform="cast",
                                                        device_id="d-spk")
    # Not listed: a virtual player, a hidden one, a disabled one, and a light.
    hub.entities["media_player.ma_test_spk"] = EntityEntry(entity_id="media_player.ma_test_spk",
                                                           platform="music_assistant")
    hub.entities["media_player.hidden"] = EntityEntry(entity_id="media_player.hidden", platform="cast", hidden_by="user")
    hub.entities["media_player.off"] = EntityEntry(entity_id="media_player.off", platform="cast", disabled_by="user")
    hub.entities["light.lamp"] = EntityEntry(entity_id="light.lamp", platform="esphome", device_id="d-sat")
    return hub


def test_one_row_per_device_with_its_kind_area_and_stable_id():
    devices = {d["hardware_id"]: d for d in shared_devices(_hub())}
    assert set(devices) == {"mac:02:00:00:00:00:aa", "cast:uuid-1", "hub:d-spk"}
    sat = devices["mac:02:00:00:00:00:aa"]
    assert sat["kind"] == "voice_satellite" and sat["area"] == "Den" and sat["name"] == "Test Satellite"
    assert sat["entity_ids"] == ["assist_satellite.test_sat", "media_player.test_sat"]
    assert devices["cast:uuid-1"]["kind"] == "tv"
    assert devices["hub:d-spk"]["kind"] == "speaker"


def test_a_rename_does_not_change_the_stable_id():
    hub = _hub()
    before = {d["hardware_id"] for d in shared_devices(hub)}
    hub.devices["d-sat"].name_by_user = "Renamed"
    after = {d["hardware_id"]: d["name"] for d in shared_devices(hub)}
    assert set(after) == before and after["mac:02:00:00:00:00:aa"] == "Renamed"
