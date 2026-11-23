"""ESPHome driver: EventInfo, UpdateInfo, tag_scanned, and arbitration_key handling."""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from aioesphomeapi import (
    Event,
    EventInfo,
    HomeassistantServiceCall,
    UpdateCommand,
    UpdateInfo,
    UpdateState,
)

from services.homehub.core import HubError, ServiceNotFound
from services.homehub.drivers.esphome import ESPHomeDriver, _is_firmware_uid
from services.homehub.tests.conftest import make_hub

MAC = "02:00:00:00:01:02"
DATA = {"host": "192.0.2.127", "port": 6053, "password": "", "noise_psk": "not-a-real-key",
        "device_name": "smart-speaker"}

EVENT_INFOS = [
    EventInfo(object_id="event", key=20, name="Event", event_types=["double", "hold", "single"]),
]

UPDATE_INFOS = [
    UpdateInfo(object_id="update", key=21, name="Update"),
]

EVENT_ENTITIES = [
    {"entity_id": "event.smart_speaker_event", "unique_id": f"{MAC}/0/event/Event"},
]

UPDATE_ENTITIES = [
    {"entity_id": "update.smart_speaker_update", "unique_id": f"{MAC}/0/update/Update"},
]


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []

    def __init__(self) -> None:
        self.commands: list[tuple[str, tuple, dict]] = []
        FakeClient.instances.append(self)

    async def connect(self, on_stop=None, login=False):
        self.on_stop = on_stop

    async def device_info_and_list_entities(self):
        from aioesphomeapi import DeviceInfo
        return DeviceInfo(name="smart-speaker", mac_address=MAC), list(EVENT_INFOS) + list(UPDATE_INFOS), []

    def subscribe_home_assistant_states_and_services(self, on_state, on_service_call, on_state_sub,
                                                      on_state_request=None):
        self.on_state = on_state
        self.on_service_call = on_service_call
        self.on_state_sub = on_state_sub
        self.on_state_request = on_state_request

    async def disconnect(self, force=False):
        pass

    def send_home_assistant_state(self, entity_id, attribute, state):
        self.commands.append(("send_home_assistant_state", (entity_id, attribute, state), {}))

    def __getattr__(self, name):
        if not name.endswith("_command"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.commands.append((name, args, kwargs))
        return record


@pytest.fixture
def driver(monkeypatch, event_infos=None, update_infos=None, entities=None, options=None):
    ent_specs = list(entities or [])
    hub, entry = make_hub("esphome", dict(DATA), ent_specs, options=options or {})
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: FakeClient()  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    monkeypatch.setattr("services.homehub.drivers.esphome.COMMAND_SETTLE", 0.05)
    return hub, drv


async def test_event_info_init_is_unknown_with_event_types(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "event")
    st = hub.states.get(ent.entity_id)
    assert st.state == "unknown"
    assert st.attributes["event_types"] == ["double", "hold", "single"]


async def test_event_state_emits_iso_timestamp_with_event_type(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "event")
    drv._client.on_state(Event(key=20, event_type="single"))
    st = hub.states.get(ent.entity_id)
    assert st.state != "unknown"
    assert st.attributes["event_type"] == "single"


async def test_consecutive_identical_events_have_strictly_increasing_timestamps(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "event")
    drv._client.on_state(Event(key=20, event_type="single"))
    first = hub.states.get(ent.entity_id).state
    drv._client.on_state(Event(key=20, event_type="single"))
    second = hub.states.get(ent.entity_id).state
    assert second > first


async def test_event_empty_event_type_remains_unknown(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "event")
    drv._client.on_state(Event(key=20, event_type=""))
    st = hub.states.get(ent.entity_id)
    assert st.state == "unknown"


async def test_update_info_on_when_latest_differs(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "update" and not _is_firmware_uid(e))
    drv._client.on_state(UpdateState(key=21, current_version="2024.1", latest_version="2024.2"))
    st = hub.states.get(ent.entity_id)
    assert st.state == "on"
    assert st.attributes["installed_version"] == "2024.1"
    assert st.attributes["latest_version"] == "2024.2"
    assert st.attributes["update_percentage"] is None


async def test_update_info_off_when_latest_matches(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "update" and not _is_firmware_uid(e))
    drv._client.on_state(UpdateState(key=21, current_version="2024.2", latest_version="2024.2"))
    st = hub.states.get(ent.entity_id)
    assert st.state == "off"


async def test_update_info_with_progress(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "update" and not _is_firmware_uid(e))
    drv._client.on_state(UpdateState(key=21, current_version="2024.1", latest_version="2024.2",
                                     in_progress=True, has_progress=True, progress=45.6))
    st = hub.states.get(ent.entity_id)
    assert st.attributes["update_percentage"] == 45.6
    assert st.attributes["in_progress"] is True


async def test_update_install_command(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "update" and not _is_firmware_uid(e))
    drv._client.on_state(UpdateState(key=21, current_version="2024.1", latest_version="2024.2"))
    await drv.async_call(ent, "update", "install", {})
    assert drv._client.commands[-1] == ("update_command", (21, UpdateCommand.INSTALL), {"device_id": 0})


async def test_update_check_command(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "update" and not _is_firmware_uid(e))
    await drv.async_call(ent, "update", "check", {})
    assert drv._client.commands[-1] == ("update_command", (21, UpdateCommand.CHECK), {"device_id": 0})


async def test_update_unknown_service_raises(driver):
    hub, drv = driver
    await drv.async_setup()
    ent = next(e for e in hub.entities.values() if e.domain == "update" and not _is_firmware_uid(e))
    with pytest.raises(ServiceNotFound, match=r"update\.fake"):
        await drv.async_call(ent, "update", "fake", {})


async def test_tag_scanned_when_allow_service_calls_is_false(driver):
    hub, drv = driver
    await drv.async_setup()
    drv.allow_service_calls = False
    events: list[dict] = []
    hub.bus.subscribe(lambda t, d: events.append(d), "tag_scanned")
    drv._client.on_state_sub("tag.hassmic_abc123", None)
    drv._client.on_service_call(HomeassistantServiceCall(
        service="esphome.tag_scanned", is_event=True, data={"tag_id": "hassmic_abc123"}))
    await asyncio.sleep(0)
    tag_st = hub.states.get("tag.hassmic_abc123")
    assert tag_st is not None
    assert tag_st.attributes["tag_id"] == "hassmic_abc123"
    assert tag_st.attributes["last_scanned_by_device_id"] is not None
    assert len(events) == 1
    assert events[0]["tag_id"] == "hassmic_abc123"


async def test_tag_scanned_forwards_via_on_state_sub(driver):
    _hub, drv = driver
    await drv.async_setup()
    drv.allow_service_calls = False
    drv._client.on_state_sub("tag.hassmic_abc123", None)
    drv._client.on_service_call(HomeassistantServiceCall(
        service="esphome.tag_scanned", is_event=True, data={"tag_id": "hassmic_abc123"}))
    await asyncio.sleep(0)
    assert any(c[0] == "send_home_assistant_state"
               and c[1][:2] == ("tag.hassmic_abc123", None)
               and isinstance(c[1][2], str)
               for c in drv._client.commands)


async def test_tag_scanned_ignores_invalid_tag_id(driver):
    hub, drv = driver
    await drv.async_setup()
    drv.allow_service_calls = False
    drv._client.on_service_call(HomeassistantServiceCall(
        service="esphome.tag_scanned", is_event=True, data={"tag_id": "tag with spaces!"}))
    await asyncio.sleep(0)
    assert hub.states.get("tag.tag with spaces!") is None


async def test_arbitration_key_allowed_with_echo_mesh_vouch_default(driver):
    hub, drv = driver
    await drv.async_setup()
    drv.allow_service_calls = False
    seen = []

    async def handler(_hub, call):
        seen.append(call.service)

    hub.register_service("esphome", "kitchen_echo_arbitration_key", handler)
    drv._client.on_service_call(HomeassistantServiceCall(
        service="esphome.kitchen_echo_arbitration_key", data={"key": "abc"}))
    await asyncio.sleep(0)
    assert seen == ["kitchen_echo_arbitration_key"]


async def test_arbitration_key_blocked_when_echo_mesh_vouch_false(driver):
    hub, drv = driver
    await drv.async_setup()
    drv.allow_service_calls = False
    drv.entry.options["echo_mesh_vouch"] = False
    seen = []

    async def handler(_hub, call):
        seen.append(call.service)

    hub.register_service("esphome", "kitchen_echo_arbitration_key", handler)
    drv._client.on_service_call(HomeassistantServiceCall(
        service="esphome.kitchen_echo_arbitration_key", data={"key": "abc"}))
    await asyncio.sleep(0)
    assert seen == []


async def test_light_turn_on_still_blocked_when_allow_service_calls_false(driver):
    hub, drv = driver
    await drv.async_setup()
    drv.allow_service_calls = False
    seen = []

    async def handler(_hub, call):
        seen.append(call.service)

    hub.register_service("light", "turn_on", handler)
    drv._client.on_service_call(HomeassistantServiceCall(
        service="light.turn_on", data={"entity_id": "light.x"}))
    await asyncio.sleep(0)
    assert seen == []


async def test_builder_firmware_and_node_native_update_coexist(driver):
    hub, drv = driver
    from services.homehub.core import EntityEntry

    fw = EntityEntry(entity_id="update.smart_speaker_firmware", platform="esphome",
                     unique_id=MAC, config_entry_id=drv.entry.entry_id, device_id="dev1")
    hub.entities[fw.entity_id] = fw
    await drv.async_setup()
    node_ent = next(e for e in hub.entities.values()
                    if e.domain == "update" and not _is_firmware_uid(e))
    fw_ent = next(e for e in hub.entities.values() if _is_firmware_uid(e))
    assert fw_ent is not None
    with pytest.raises(HubError):
        await drv.async_call(fw_ent, "update", "install", {})
    drv._client.on_state(UpdateState(key=21, current_version="2024.1", latest_version="2024.2"))
    await drv.async_call(node_ent, "update", "install", {})
    assert drv._client.commands[-1][0] == "update_command"
