"""The automation, script, scene and scheduler engine, on a fake hub and a fake clock."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from services.homehub.automation import (
    AutomationEngine,
    Clock,
    FakeClock,
    Scheduler,
    parse_duration,
    parse_time,
)
from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, Hub, ServiceCall, State

# The household's real automations, scripts and a registry slice. They name
# family members, places and routines, so they live outside git (this repo is
# public): data/homehub-fixtures, or wherever HOMEHUB_HOUSEHOLD_DIR points.
# The values those tests check (aliases, entity ids, notify targets) come from
# expectations.json in the same directory, so none of them is written here.
# Without the directory or that file the household tests skip.
FIXTURES = os.getenv("HOMEHUB_HOUSEHOLD_DIR") or os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "homehub-fixtures")
EXPECTATIONS = os.path.join(FIXTURES, "expectations.json")
needs_household = pytest.mark.skipif(
    not os.path.isfile(EXPECTATIONS),
    reason="household fixtures are not checked in (set HOMEHUB_HOUSEHOLD_DIR to a directory with expectations.json)")


def household_expectations() -> dict:
    with open(EXPECTATIONS, encoding="utf-8") as f:
        return json.load(f)

# Wednesday 2026-11-11, 12:00 at UTC-7 (no DST).
START = datetime(2026, 11, 11, 19, 0, tzinfo=UTC)
LOCATION = {"latitude": 40.0209, "longitude": -105.4997, "elevation": 331}


class FakeStore:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    def get_kv(self, key: str, default: Any = None) -> Any:
        return self.kv.get(key, default)

    def set_kv(self, key: str, value: Any) -> None:
        self.kv[key] = json.loads(json.dumps(value))


async def settle(n: int = 40) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def advance(eng: AutomationEngine, seconds: float) -> None:
    """Move the fake clock forward, stopping at every due timer so each fires at its own time."""
    clock = eng.clock
    assert isinstance(clock, FakeClock)
    target = clock.now() + timedelta(seconds=seconds)
    await settle()
    while True:
        nxt = eng.scheduler.next_due()
        if nxt is None or nxt > target:
            break
        clock.set(max(nxt, clock.now()))
        eng.scheduler.run_due()
        await settle()
    clock.set(target)
    eng.scheduler.run_due()
    await settle()


def record(hub: Hub, *services: str, response: Any = None, fail: bool = False) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    for name in services:
        domain, service = name.split(".", 1)

        async def handler(_hub: Hub, call: ServiceCall, name: str = name) -> Any:
            calls.append((name, dict(call.data)))
            if fail:
                raise RuntimeError(f"{name} failed")
            return response(call) if callable(response) else response

        hub.register_service(domain, service, handler)
    return calls


def names(calls: list[tuple[str, dict]]) -> list[str]:
    return [c[0] for c in calls]


@pytest.fixture
def hub() -> Hub:
    hub = Hub(time_zone="Etc/GMT+7", location=dict(LOCATION))
    hub.areas["office"] = Area("office", "Office")
    return hub


@pytest.fixture
async def engine(hub: Hub):
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    yield eng
    await eng.stop()


async def run(eng: AutomationEngine, automations: list[dict] | None = None, scripts: dict | None = None,
              scenes: list[dict] | None = None, schedules: Any = None) -> AutomationEngine:
    await eng.load(automations or [], scripts or {}, scenes or [], schedules)
    await eng.start()
    await settle()
    return eng


def auto(triggers: Any, actions: Any, conditions: Any = None, **extra: Any) -> dict:
    d = {"id": extra.pop("id", "a1"), "alias": extra.pop("alias", "Test"), "triggers": triggers,
         "actions": actions, "mode": extra.pop("mode", "single")}
    if conditions is not None:
        d["conditions"] = conditions
    d.update(extra)
    return d


NOTE = {"action": "test.note", "data": {"msg": "{{ trigger.id }}"}}


# -- parsing --------------------------------------------------------------------------------

def test_durations_and_times() -> None:
    assert parse_duration("00:15:00") == timedelta(minutes=15)
    assert parse_duration("-00:20:00") == timedelta(minutes=-20)
    assert parse_duration({"hours": 0, "minutes": 6, "seconds": 0}) == timedelta(minutes=6)
    assert parse_duration(90) == timedelta(seconds=90)
    assert parse_duration("5") == timedelta(seconds=5)
    assert parse_duration(0) == timedelta(0)
    assert parse_time("09:00:00").hour == 9
    assert parse_time("21:30").minute == 30
    assert parse_time(75600).hour == 21  # PyYAML's sexagesimal reading of an unquoted 21:00:00
    with pytest.raises(ValueError):
        parse_time("soon")


# -- triggers --------------------------------------------------------------------------------

async def test_time_trigger_fires_daily_at_local_time_without_drift(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"trigger": "time", "at": "23:00:00"}],
                            [{"action": "test.note", "data": {"at": "{{ trigger.now.isoformat() }}"}}])])
    await advance(engine, 3 * 86400)
    assert [c[1]["at"] for c in calls] == ["2026-11-11T23:00:00-07:00", "2026-11-12T23:00:00-07:00",
                                           "2026-11-13T23:00:00-07:00"]


async def test_time_trigger_old_syntax_and_entity(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("input_datetime.wake", "06:30:00", {"has_date": False, "has_time": True})
    hub.states.set("sensor.alarm", "2026-11-11T20:00:00+00:00", {"device_class": "timestamp"})
    await run(engine, [
        {"id": "x", "alias": "x", "trigger": [{"platform": "time", "at": "input_datetime.wake"}],
         "action": [{"service": "test.note", "data_template": {"src": "dt"}}]},
        auto([{"trigger": "time", "at": {"entity_id": "sensor.alarm", "offset": "-00:05:00"}}],
             [{"action": "test.note", "data": {"src": "sensor"}}], id="y"),
    ])
    await advance(engine, 3600)
    assert [c[1]["src"] for c in calls] == ["sensor"]  # 12:55 local
    hub.states.set("input_datetime.wake", "14:00:00", {"has_date": False, "has_time": True})
    await advance(engine, 3600)
    assert [c[1]["src"] for c in calls] == ["sensor", "dt"]
    assert engine.clock.now().astimezone().year == 2026


async def test_time_pattern(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"platform": "time_pattern", "minutes": "/10"}], [NOTE])])
    await advance(engine, 3600)
    assert len(calls) == 6  # 12:10 ... 13:00


async def test_sun_trigger_with_offset(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"trigger": "sun", "event": "sunset", "offset": "-00:20:00"}],
                            [{"action": "test.note", "data": {"t": "{{ now().isoformat() }}"}}])])
    sunset = engine._sun_times(START.astimezone(engine.tz).date())["sunset"]
    await advance(engine, 86400)
    assert len(calls) == 1
    fired = datetime.fromisoformat(calls[0][1]["t"])
    assert fired == sunset - timedelta(minutes=20)
    assert 16 <= fired.hour <= 17  # a November evening at the hub location


async def test_state_trigger_from_to_and_for(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("binary_sensor.door", "off")
    await run(engine, [
        auto([{"trigger": "state", "entity_id": "binary_sensor.door", "from": "off", "to": "on", "id": "open"}],
             [NOTE], id="a"),
        auto([{"trigger": "state", "entity_id": ["binary_sensor.door"], "to": "on", "for": {"minutes": 15},
               "id": "long"}], [NOTE], id="b"),
    ])
    hub.states.set("binary_sensor.door", "on")
    await settle()
    assert names(calls) == ["test.note"] and calls[0][1]["msg"] == "open"
    await advance(engine, 600)
    hub.states.set("binary_sensor.door", "on", {"rssi": -50})  # attribute change: timer keeps running
    await advance(engine, 299)
    assert len(calls) == 1
    await advance(engine, 2)
    assert [c[1]["msg"] for c in calls] == ["open", "long"]
    # closing before the 15 minutes are up cancels it
    hub.states.set("binary_sensor.door", "off")
    hub.states.set("binary_sensor.door", "on")
    await advance(engine, 600)
    hub.states.set("binary_sensor.door", "off")
    await advance(engine, 1200)
    assert [c[1]["msg"] for c in calls] == ["open", "long", "open"]


async def test_state_trigger_variables_attribute_and_any_change(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("media_player.tv", "on", {"volume_level": 0.1})
    await run(engine, [
        auto([{"trigger": "state", "entity_id": "media_player.tv", "attribute": "volume_level"}],
             [{"action": "test.note", "data": {"v": "{{ trigger.to_state.attributes.volume_level }}",
                                               "o": "{{ trigger.from_state.state }}"}}], id="a"),
        auto([{"trigger": "state", "entity_id": "media_player.tv", "to": None}],
             [{"action": "test.note", "data": {"any": "{{ trigger.to_state.state }}"}}], id="b"),
        auto([{"trigger": "state", "entity_id": "media_player.tv", "not_to": ["off"]}],
             [{"action": "test.note", "data": {"not_to": "{{ trigger.to_state.state }}"}}], id="c"),
    ])
    hub.states.set("media_player.tv", "on", {"volume_level": 0.5})
    await settle()
    assert calls == [("test.note", {"v": 0.5, "o": "on"})]
    hub.states.set("media_player.tv", "off", {"volume_level": 0.5})
    await settle()
    assert ("test.note", {"any": "off"}) in calls and not any("not_to" in c[1] for c in calls)
    hub.states.set("media_player.tv", "playing", {"volume_level": 0.5})
    await settle()
    assert ("test.note", {"not_to": "playing"}) in calls


async def test_numeric_state_trigger_arms_and_fires_once_per_crossing(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("sensor.power", "3")
    hub.states.set("sensor.limit", "9")
    await run(engine, [
        auto([{"trigger": "numeric_state", "entity_id": "sensor.power", "above": "sensor.limit",
               "for": "00:05:00", "id": "high"}], [NOTE], id="a"),
        auto([{"trigger": "numeric_state", "entity_id": "sensor.power", "below": 2,
               "value_template": "{{ state.state | float / 2 }}", "id": "half"}], [NOTE], id="b"),
    ])
    hub.states.set("sensor.power", "10")
    await advance(engine, 200)
    hub.states.set("sensor.power", "11")  # still above: no restart, no second fire
    await advance(engine, 101)
    assert [c[1]["msg"] for c in calls] == ["high"]
    hub.states.set("sensor.power", "12")
    await advance(engine, 400)
    assert len(calls) == 1
    hub.states.set("sensor.power", "unavailable")
    hub.states.set("sensor.power", "3")  # 1.5 by the template: below 2
    await settle()
    assert [c[1]["msg"] for c in calls] == ["high", "half"]
    hub.states.set("sensor.power", "10")
    await advance(engine, 120)
    hub.states.set("sensor.power", "5")  # dropped out before 5 minutes
    await advance(engine, 600)
    assert [c[1]["msg"] for c in calls] == ["high", "half"]


async def test_numeric_state_attribute(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("climate.up", "cool", {"current_temperature": 70})
    await run(engine, [auto([{"trigger": "numeric_state", "entity_id": "climate.up",
                              "attribute": "current_temperature", "above": 80}], [NOTE])])
    hub.states.set("climate.up", "cool", {"current_temperature": 81})
    await settle()
    assert len(calls) == 1


async def test_zone_trigger_by_gps_and_by_state_name(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("zone.work", "0", {"latitude": 39.8725, "longitude": -105.2257, "radius": 100,
                                      "friendly_name": "Robin Work"})
    hub.states.set("person.robin", "not_home", {"latitude": 39.5725, "longitude": -104.4257})
    hub.states.set("device_tracker.phone", "home")
    await run(engine, [
        auto([{"trigger": "zone", "entity_id": "person.robin", "zone": "zone.work", "event": "enter"}],
             [{"action": "test.note", "data": {"m": "enter"}}], id="a"),
        auto([{"trigger": "zone", "entity_id": "person.robin", "zone": "zone.work", "event": "leave"}],
             [{"action": "test.note", "data": {"m": "leave"}}], id="b"),
        auto([{"platform": "zone", "entity_id": "device_tracker.phone", "zone": "zone.home", "event": "leave"}],
             [{"action": "test.note", "data": {"m": "phone left"}}], id="c"),
    ])
    hub.states.set("person.robin", "Robin Work", {"latitude": 39.8726, "longitude": -105.2258,
                                                  "gps_accuracy": 10})
    await settle()
    hub.states.set("person.robin", "not_home", {"latitude": 39.7725, "longitude": -105.1257})
    hub.states.set("device_tracker.phone", "not_home")
    await settle()
    assert [c[1]["m"] for c in calls] == ["enter", "leave", "phone left"]


async def test_template_trigger_fires_on_rising_edge_and_honours_for(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("sensor.t", "60")
    await run(engine, [
        auto([{"trigger": "template", "value_template": "{{ states('sensor.t') | float > 80 }}"}], [NOTE], id="a"),
        auto([{"trigger": "template", "value_template": "{{ states('sensor.t') | float > 90 }}", "for": 60,
               "id": "hot"}], [NOTE], id="b"),
    ])
    hub.states.set("sensor.t", "85")
    hub.states.set("sensor.t", "86")
    await settle()
    assert len(calls) == 1
    hub.states.set("sensor.t", "95")
    await advance(engine, 30)
    hub.states.set("sensor.t", "85")
    await advance(engine, 60)
    assert len(calls) == 1
    hub.states.set("sensor.t", "95")
    await advance(engine, 61)
    assert [c[1]["msg"] for c in calls] == [0, "hot"]  # "0" renders back to a number, as in HA


async def test_template_trigger_on_time(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"trigger": "template", "value_template": "{{ now().hour == 13 }}"}], [NOTE])])
    await advance(engine, 7200)
    assert len(calls) == 1


async def test_event_trigger_matches_event_data(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"platform": "event", "event_type": "button_pressed",
                              "event_data": {"button": "a"}}],
                            [{"action": "test.note", "data": {"d": "{{ trigger.event.data.n }}"}}])])
    hub.bus.fire("button_pressed", {"button": "b", "n": 1})
    hub.bus.fire("button_pressed", {"button": "a", "n": 2})
    hub.bus.fire("other", {"button": "a", "n": 3})
    await settle()
    assert calls == [("test.note", {"d": 2})]


async def test_homeassistant_start_trigger(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"platform": "homeassistant", "event": "start"}], [NOTE])])
    assert len(calls) == 1


async def test_device_triggers_translate_through_registry_ids(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note", "switch.turn_on")
    hub.entities["binary_sensor.garage"] = EntityEntry("binary_sensor.garage", "esphome", "g", device_id="d1")
    hub.entities["sensor.ahu"] = EntityEntry("sensor.ahu", "emporia_vue", "p", device_id="d2")
    hub.entities["switch.garage"] = EntityEntry("switch.garage", "esphome", "s", device_id="d1")
    hub.devices["d1"] = DeviceEntry("d1")
    hub.entity_registry_ids = {"aa" * 16: "binary_sensor.garage", "bb" * 16: "sensor.ahu",
                               "cc" * 16: "switch.garage"}
    hub.states.set("binary_sensor.garage", "on")
    hub.states.set("sensor.ahu", "0.2")
    await run(engine, [
        auto([{"type": "not_opened", "device_id": "d1", "entity_id": "aa" * 16, "domain": "binary_sensor",
               "trigger": "device", "for": {"hours": 0, "minutes": 15, "seconds": 0}}],
             [{"type": "turn_on", "device_id": "d1", "entity_id": "cc" * 16, "domain": "switch"}], id="garage"),
        auto([{"type": "power", "device_id": "d2", "entity_id": "bb" * 16, "domain": "sensor", "trigger": "device",
               "above": 1.5, "for": {"minutes": 6}}],
             [NOTE], [{"type": "is_power", "condition": "device", "device_id": "d2", "entity_id": "bb" * 16,
                       "domain": "sensor", "above": 1}], id="ahu"),
        auto([{"type": "opened", "device_id": "dx", "entity_id": "ff" * 16, "domain": "binary_sensor",
               "trigger": "device"}], [NOTE], id="ghost"),
    ])
    status = {s["id"]: s for s in engine.status()}
    assert status["garage"]["enabled"] and status["ahu"]["enabled"]
    assert not status["ghost"]["enabled"] and "registry id" in status["ghost"]["unsupported_reason"]
    hub.states.set("binary_sensor.garage", "off")
    hub.states.set("sensor.ahu", "2.0")
    await advance(engine, 6 * 60 + 1)
    assert names(calls) == ["test.note"]
    await advance(engine, 9 * 60)
    assert names(calls) == ["test.note", "switch.turn_on"]
    assert calls[1][1] == {"entity_id": "switch.garage"}


async def test_untranslatable_and_unsupported_triggers_disable_with_a_reason(hub: Hub,
                                                                             engine: AutomationEngine) -> None:
    hub.entities["sensor.x"] = EntityEntry("sensor.x", "esphome", "x")
    await run(engine, [
        auto([{"trigger": "calendar", "event": "start", "entity_id": "calendar.x"}], [NOTE], id="cal"),
        auto([{"trigger": "device", "domain": "esphome", "type": "button", "device_id": "d",
               "entity_id": "sensor.x"}], [NOTE], id="dev"),
        auto([{"trigger": "state", "entity_id": "sensor.deleted"}], [NOTE], id="gone"),
    ])
    reasons = {s["id"]: s["unsupported_reason"] for s in engine.status()}
    assert "calendar" in reasons["cal"]
    assert "esphome/button" in reasons["dev"]
    assert "sensor.deleted" in reasons["gone"]
    assert hub.states.get("automation.test").state == "off"
    assert "sensor.deleted" in hub.states.get("automation.test_3").attributes["unsupported_reason"]


# -- conditions --------------------------------------------------------------------------------

async def cond(engine: AutomationEngine, condition: Any, variables: dict | None = None) -> bool:
    await engine.load([auto([], [], [condition])], {}, [], None)
    item = engine.automations["a1"]
    assert item.runnable, item.unsupported_reason
    return engine.check_conditions(item.conditions, variables or {"trigger": {"id": "t1"}})


async def test_state_and_numeric_conditions(hub: Hub, engine: AutomationEngine) -> None:
    hub.states.set("light.a", "", restore=State("light.a", "on", {"brightness": 200}, START, START))
    hub.states.set("light.b", "off")
    hub.states.set("sensor.p", "4.5")
    assert await cond(engine, {"condition": "state", "entity_id": "light.a", "state": "on"})
    assert not await cond(engine, {"condition": "state", "entity_id": ["light.a", "light.b"], "state": "on"})
    assert await cond(engine, {"condition": "state", "entity_id": ["light.a", "light.b"], "state": "on",
                               "match": "any"})
    assert await cond(engine, {"condition": "state", "entity_id": "light.b", "state": ["off", "unavailable"]})
    assert await cond(engine, {"condition": "state", "entity_id": "light.a", "attribute": "brightness",
                               "state": "200"})
    assert await cond(engine, {"condition": "state", "entity_id": "light.b", "state": False})  # YAML's off
    assert not await cond(engine, {"condition": "state", "entity_id": "light.a", "state": "on", "for": 60})
    engine.clock.advance(61)
    assert await cond(engine, {"condition": "state", "entity_id": "light.a", "state": "on", "for": 60})
    assert await cond(engine, {"condition": "numeric_state", "entity_id": "sensor.p", "below": 5})
    assert not await cond(engine, {"condition": "numeric_state", "entity_id": "sensor.p", "above": 4.5})
    assert await cond(engine, {"condition": "numeric_state", "entity_id": "light.a", "attribute": "brightness",
                               "above": 100, "below": 255})


async def test_time_conditions(hub: Hub, engine: AutomationEngine) -> None:
    weekday = ["mon", "tue", "wed", "thu", "fri"]
    assert await cond(engine, {"condition": "time", "after": "11:00:00", "before": "13:00:00", "weekday": weekday})
    assert not await cond(engine, {"condition": "time", "after": "14:00:00", "before": "22:00:00"})
    assert not await cond(engine, {"condition": "time", "weekday": "sat"})
    assert await cond(engine, {"condition": "time", "after": "08:59:00"})
    assert not await cond(engine, {"condition": "time", "before": "08:00"})
    # crossing midnight
    assert not await cond(engine, {"condition": "time", "after": "22:00", "before": "06:00"})
    engine.clock.set(datetime(2026, 11, 12, 6, 0, tzinfo=UTC))  # 23:00 local
    assert await cond(engine, {"condition": "time", "after": "22:00", "before": "06:00"})
    hub.states.set("input_datetime.start", "22:30:00", {"has_date": False, "has_time": True})
    assert await cond(engine, {"condition": "time", "after": "input_datetime.start"})


async def test_sun_template_logic_zone_trigger_conditions(hub: Hub, engine: AutomationEngine) -> None:
    hub.states.set("light.a", "on")
    hub.states.set("person.p", "home")
    assert await cond(engine, {"condition": "sun", "after": "sunrise", "before": "sunset"})
    assert not await cond(engine, {"condition": "sun", "after": "sunset"})
    assert await cond(engine, "{{ is_state('light.a', 'on') }}")
    assert await cond(engine, {"condition": "template", "value_template": "{{ 1 + 1 == 2 }}"})
    assert await cond(engine, {"condition": "or", "conditions": ["{{ false }}", "{{ true }}"]})
    assert not await cond(engine, {"condition": "and", "conditions": ["{{ false }}", "{{ true }}"]})
    assert await cond(engine, {"condition": "not", "conditions": ["{{ false }}"]})
    assert await cond(engine, {"or": ["{{ true }}"]})
    assert await cond(engine, {"condition": "zone", "entity_id": "person.p", "zone": "zone.home"})
    assert await cond(engine, {"condition": "trigger", "id": ["t0", "t1"]})
    assert not await cond(engine, {"condition": "trigger", "id": "t2"})
    assert await cond(engine, {"condition": "template", "value_template": "{{ false }}", "enabled": False})


async def test_device_conditions(hub: Hub, engine: AutomationEngine) -> None:
    hub.entities["climate.up"] = EntityEntry("climate.up", "homekit_controller", "c")
    hub.entities["binary_sensor.door"] = EntityEntry("binary_sensor.door", "esphome", "d")
    hub.entity_registry_ids = {"11" * 16: "climate.up", "22" * 16: "binary_sensor.door"}
    hub.states.set("climate.up", "off", {"preset_mode": "NoHold"})
    hub.states.set("binary_sensor.door", "off")
    assert await cond(engine, {"condition": "device", "domain": "climate", "type": "is_hvac_mode",
                               "entity_id": "11" * 16, "device_id": "x", "hvac_mode": "off"})
    assert await cond(engine, {"condition": "device", "domain": "climate", "type": "is_preset_mode",
                               "entity_id": "11" * 16, "device_id": "x", "preset_mode": "NoHold"})
    assert await cond(engine, {"condition": "device", "domain": "binary_sensor", "type": "is_not_open",
                               "entity_id": "22" * 16, "device_id": "x"})


# -- actions ---------------------------------------------------------------------------------------

async def run_script(engine: AutomationEngine, sequence: list[dict], variables: dict | None = None,
                     **extra: Any) -> Any:
    await engine.load([], {"s": {"sequence": sequence, **extra}}, [], None)
    if not engine._running:
        await engine.start()
    item = engine.scripts["s"]
    assert item.runnable, item.unsupported_reason
    return await engine.run_script("s", variables or {})


async def test_service_calls_render_data_target_and_entity_id(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "light.turn_on", "light.turn_off")
    await run_script(engine, [
        {"action": "light.turn_on", "metadata": {}, "data": {"brightness": "{{ 40 + 2 }}"},
         "target": {"entity_id": "{{ 'light.' ~ which }}", "area_id": "office"}},
        {"service": "light.turn_off", "entity_id": "light.x", "data_template": {"transition": "{{ 2 }}"}},
        {"action": "{{ 'light.turn_' ~ 'on' }}", "data": {}},
    ], {"which": "desk"})
    assert calls == [("light.turn_on", {"brightness": 42, "entity_id": "light.desk", "area_id": "office"}),
                     ("light.turn_off", {"transition": 2, "entity_id": "light.x"}),
                     ("light.turn_on", {})]


async def test_response_variable_and_stop_response(hub: Hub, engine: AutomationEngine) -> None:
    record(hub, "music_assistant.get_library", response={"items": [{"name": "Mix", "uri": "lib://1"}]})
    out = await run_script(engine, [
        {"action": "music_assistant.get_library", "data": {"media_type": "playlist"}, "response_variable": "pl"},
        {"variables": {"result": {"first": "{{ pl['items'][0]['name'] }}", "n": "{{ pl['items'] | length }}"}}},
        {"stop": "", "response_variable": "result"},
        {"action": "music_assistant.get_library"},
    ])
    assert out == {"first": "Mix", "n": 1}


async def test_continue_on_error(hub: Hub, engine: AutomationEngine) -> None:
    record(hub, "bad.call", fail=True)
    calls = record(hub, "test.note")
    out = await run_script(engine, [
        {"action": "bad.call", "response_variable": "r", "continue_on_error": True},
        {"variables": {"result": "{{ r is defined }}"}},
        {"action": "test.note"},
        {"stop": "", "response_variable": "result"},
    ])
    assert out is False and names(calls) == ["test.note"]
    await run_script(engine, [{"action": "bad.call"}, {"action": "test.note"}])
    assert names(calls) == ["test.note"]
    assert "bad.call failed" in engine.scripts["s"].last_error


async def test_delay_and_wait_template(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("light.a", "off")
    await engine.load([], {"s": {"sequence": [
        {"delay": "00:01:00"},
        {"action": "test.note", "data": {"step": 1}},
        {"wait_template": "{{ is_state('light.a', 'on') }}", "timeout": 600},
        {"action": "test.note", "data": {"step": "{{ wait.completed }}"}},
        {"wait_template": "{{ is_state('light.a', 'off') }}", "timeout": "00:00:30", "continue_on_timeout": False},
        {"action": "test.note", "data": {"step": "never"}},
    ]}}, [], None)
    await engine.start()
    task = asyncio.ensure_future(engine.run_script("s"))
    await advance(engine, 59)
    assert calls == []
    await advance(engine, 1)
    assert calls == [("test.note", {"step": 1})]
    await advance(engine, 100)
    hub.states.set("light.a", "on")
    await settle()
    assert calls[-1] == ("test.note", {"step": True})
    await advance(engine, 31)
    await task
    assert len(calls) == 2


async def test_wait_for_trigger(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("binary_sensor.m", "off")
    await engine.load([], {"s": {"sequence": [
        {"wait_for_trigger": [{"platform": "state", "entity_id": "binary_sensor.m", "to": "on"}], "timeout": 60},
        {"action": "test.note", "data": {"by": "{{ wait.trigger.entity_id if wait.trigger else 'timeout' }}"}},
        {"wait_for_trigger": [{"trigger": "event", "event_type": "go"}], "timeout": 10},
        {"action": "test.note", "data": {"by": "{{ wait.trigger.event.event_type if wait.trigger else 'timeout' }}"}},
    ]}}, [], None)
    await engine.start()
    task = asyncio.ensure_future(engine.run_script("s"))
    await advance(engine, 5)
    hub.states.set("binary_sensor.m", "on")
    await settle()
    await advance(engine, 11)
    await task
    assert [c[1]["by"] for c in calls] == ["binary_sensor.m", "timeout"]


async def test_choose_if_sequence_and_condition_steps(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    hub.states.set("light.a", "on")
    await run_script(engine, [
        {"choose": [{"conditions": [{"condition": "state", "entity_id": "light.a", "state": "off"}],
                     "sequence": [{"action": "test.note", "data": {"m": "c1"}}]},
                    {"conditions": "{{ is_state('light.a', 'on') }}",
                     "sequence": [{"action": "test.note", "data": {"m": "c2"}}]}],
         "default": [{"action": "test.note", "data": {"m": "default"}}]},
        {"choose": [{"conditions": ["{{ false }}"], "sequence": []}],
         "default": [{"action": "test.note", "data": {"m": "default"}}]},
        {"if": ["{{ true }}"], "then": [{"action": "test.note", "data": {"m": "then"}}],
         "else": [{"action": "test.note", "data": {"m": "else"}}]},
        {"if": [{"condition": "state", "entity_id": "light.a", "state": "off"}],
         "then": [{"action": "test.note", "data": {"m": "then"}}],
         "else": [{"action": "test.note", "data": {"m": "else"}}]},
        {"sequence": [{"condition": "template", "value_template": "{{ false }}"},
                      {"action": "test.note", "data": {"m": "skipped"}}]},
        {"action": "test.note", "data": {"m": "after nested"}},
        {"condition": "state", "entity_id": "light.a", "state": "off"},
        {"action": "test.note", "data": {"m": "never"}},
    ])
    assert [c[1]["m"] for c in calls] == ["c2", "default", "then", "else", "after nested"]


async def test_repeat_forms(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run_script(engine, [
        {"repeat": {"count": "{{ 1 + 2 }}", "sequence": [
            {"action": "test.note", "data": {"c": "{{ repeat.index }}{{ 'F' if repeat.first }}{{ 'L' if repeat.last }}"}}]}},
        {"repeat": {"for_each": ["a", "b"], "sequence": [{"action": "test.note", "data": {"c": "{{ repeat.item }}"}}]}},
        {"variables": {"n": 0}},
        {"repeat": {"while": ["{{ repeat.index <= 2 }}"], "sequence": [
            {"action": "test.note", "data": {"c": "w{{ repeat.index }}"}}]}},
        {"repeat": {"until": ["{{ repeat.index >= 2 }}"], "sequence": [
            {"action": "test.note", "data": {"c": "u{{ repeat.index }}"}}]}},
        {"repeat": {"for_each": "{{ ['x', 'y'] }}", "sequence": [{"action": "test.note", "data": {"c": "{{ repeat.item }}"}}]}},
    ])
    assert [c[1]["c"] for c in calls] == ["1F", 2, "3L", "a", "b", "w1", "w2", "u1", "u2", "x", "y"]


async def test_variables_event_parallel(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    seen: list[dict] = []
    hub.bus.subscribe(lambda _t, d: seen.append(d), "my_event")
    await run_script(engine, [
        {"variables": {"a": 2, "b": "{{ a * 3 }}"}},
        {"event": "my_event", "event_data": {"b": "{{ b }}"}},
        {"parallel": [
            {"sequence": [{"delay": 0}, {"action": "test.note", "data": {"p": 1}}]},
            {"action": "test.note", "data": {"p": 2}},
        ]},
    ])
    assert seen == [{"b": 6}]
    assert sorted(c[1]["p"] for c in calls) == [1, 2]


async def test_stop_with_error_records_last_error(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run_script(engine, [{"stop": "no luck", "error": True}, {"action": "test.note"}])
    assert calls == [] and engine.scripts["s"].last_error == "no luck"


# -- scripts and modes ---------------------------------------------------------------------------------

async def test_scripts_fields_direct_call_turn_on_and_states(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "tts.speak")
    hub.entities["script.say_it"] = EntityEntry("script.say_it", "script", "say", name="Say It Loud")
    scripts = {"say": {"alias": "Say", "mode": "queued", "fields": {"message": {"required": True},
                                                                     "volume": {"default": 0.4}},
                       "sequence": [{"delay": 1},
                                    {"action": "tts.speak", "data": {"message": "{{ message }}", "v": "{{ volume }}"}}]}}
    await run(engine, [auto([{"trigger": "event", "event_type": "go"}],
                            [{"action": "script.say", "data": {"message": "bed time"}},
                             {"action": "script.turn_on", "target": {"entity_id": "script.say_it"},
                              "data": {"variables": {"message": "second", "volume": 1}}}])], scripts)
    st = hub.states.get("script.say_it")
    assert st.state == "off" and st.attributes["friendly_name"] == "Say It Loud" and st.attributes["mode"] == "queued"
    hub.bus.fire("go", {})
    await settle()
    assert hub.states.get("script.say_it").state == "on"
    await advance(engine, 1)
    await advance(engine, 1)
    assert calls == [("tts.speak", {"message": "bed time", "v": 0.4}), ("tts.speak", {"message": "second", "v": 1})]
    assert hub.states.get("script.say_it").state == "off"
    assert hub.states.get("script.say_it").attributes["last_triggered"]


async def test_script_turn_off_cancels_and_response_via_hub(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "test.note")
    await run(engine, [], {"slow": {"sequence": [{"delay": 100}, {"action": "test.note"}]},
                           "answer": {"sequence": [{"variables": {"r": {"x": "{{ x }}"}}},
                                                   {"stop": "", "response_variable": "r"}]}})
    await hub.call_service("script", "turn_on", {"entity_id": "script.slow"})
    await settle()
    assert hub.states.get("script.slow").state == "on"
    await hub.call_service("script", "turn_off", {"entity_id": "script.slow"})
    await advance(engine, 200)
    assert calls == [] and hub.states.get("script.slow").state == "off"
    res = await hub.call_service("script", "answer", {"x": 5}, return_response=True)
    assert res["response"] == {"x": 5}


@pytest.mark.parametrize(("mode", "expected"), [("single", [0]), ("restart", [2]), ("queued", [0, 1]),
                                                ("parallel", [0, 1])])
async def test_modes(hub: Hub, engine: AutomationEngine, mode: str, expected: list[int]) -> None:
    calls = record(hub, "test.note")
    await run(engine, [auto([{"trigger": "event", "event_type": "go"}],
                            [{"delay": 10}, {"action": "test.note", "data": {"n": "{{ trigger.event.data.n }}"}}],
                            mode=mode, max=2)])
    for n in range(3):
        hub.bus.fire("go", {"n": n})
        await settle()
    assert hub.states.get("automation.test").attributes["current"] >= 1
    await advance(engine, 40)
    assert sorted(c[1]["n"] for c in calls) == expected


# -- automation services and persistence ---------------------------------------------------------------

async def test_automation_services_states_and_persistence(hub: Hub) -> None:
    store = FakeStore()
    hub.entities["automation.lights_off_late"] = EntityEntry("automation.lights_off_late", "automation",
                                                              "1700000000001", name="Bedtime Lights")
    eng = AutomationEngine(hub, store, clock=FakeClock(START))
    calls = record(hub, "test.note")
    conf = [auto([{"trigger": "event", "event_type": "go"}], [NOTE], ["{{ false }}"], id="1700000000001",
                 alias="Lights Off Late")]
    try:
        await run(eng, conf)
        st = hub.states.get("automation.lights_off_late")
        assert st.state == "on"
        assert st.attributes == {"last_triggered": None, "mode": "single", "current": 0,
                                 "friendly_name": "Bedtime Lights", "id": "1700000000001"}
        hub.bus.fire("go", {})
        await settle()
        assert calls == []  # condition false
        await hub.call_service("automation", "trigger", {"entity_id": "automation.lights_off_late"})
        await settle()
        assert len(calls) == 1  # skip_condition defaults to true
        await hub.call_service("automation", "trigger", {"entity_id": "automation.lights_off_late",
                                                         "skip_condition": False})
        await settle()
        assert len(calls) == 1
        assert hub.states.get("automation.lights_off_late").attributes["last_triggered"] == START.isoformat()
        await hub.call_service("automation", "turn_off", {"entity_id": "automation.lights_off_late"})
        assert hub.states.get("automation.lights_off_late").state == "off"
        assert store.kv["automation:1700000000001"] == {"enabled": False, "last_triggered": START.isoformat()}
        await hub.call_service("automation", "toggle", {"entity_id": "automation.lights_off_late"})
        assert hub.states.get("automation.lights_off_late").state == "on"
        await hub.call_service("automation", "turn_off", {"entity_id": "automation.lights_off_late"})
    finally:
        await eng.stop()
    # A new engine (a restart) keeps it disabled and remembers last_triggered.
    eng2 = AutomationEngine(hub, store, clock=FakeClock(START))
    try:
        await run(eng2, conf)
        item = eng2.automations["1700000000001"]
        assert item.enabled is False and item.last_triggered == START
        hub.bus.fire("go", {})
        await hub.call_service("automation", "turn_on", {"entity_id": "automation.lights_off_late"})
        await hub.call_service("automation", "reload", {})
        assert eng2.automations["1700000000001"].enabled is True
        assert {s["id"] for s in eng2.status()} == {"1700000000001"}
    finally:
        await eng2.stop()


async def test_restored_off_state_disables_an_automation_with_no_saved_flag(hub: Hub) -> None:
    hub.states.set("automation.test", "off", {"last_triggered": "2026-01-01T00:00:00+00:00"})
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    try:
        await run(eng, [auto([{"trigger": "event", "event_type": "go"}], [NOTE])])
        assert eng.automations["a1"].enabled is False
        assert eng.automations["a1"].last_triggered.year == 2026
    finally:
        await eng.stop()


async def test_missing_helpers_and_entities_disable_with_reasons(hub: Hub, engine: AutomationEngine) -> None:
    hub.entities["light.real"] = EntityEntry("light.real", "wiz", "r")
    await run(engine, [
        auto([{"trigger": "event", "event_type": "timer.finished"}], [NOTE], id="timer"),
        auto([{"trigger": "event", "event_type": "go"}], [{"action": "input_text.set_value", "data": {"value": ""},
                                                           "target": {"entity_id": "{{ x }}"}}], id="helper"),
        auto([{"trigger": "event", "event_type": "go"}], [{"action": "light.turn_on",
                                                           "target": {"entity_id": ["light.gone"]}}], id="only_gone"),
        auto([{"trigger": "event", "event_type": "go"}], [{"action": "light.turn_on",
                                                           "target": {"entity_id": ["light.gone", "light.real"]}}],
             id="partly"),
        auto([{"trigger": "event", "event_type": "go"}], [{"action": "script.nope"}], id="noscript"),
    ])
    st = {s["id"]: s for s in engine.status()}
    assert "timer integration" in st["timer"]["unsupported_reason"]
    assert "input_text integration" in st["helper"]["unsupported_reason"]
    assert "none of which exist" in st["only_gone"]["unsupported_reason"]
    assert st["partly"]["enabled"] and "light.gone" in st["partly"]["warnings"][0]
    assert "script.nope" in st["noscript"]["unsupported_reason"]


async def test_blueprint_scripts_expand_inputs(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "tts.speak")
    bp = {"blueprint": {"name": "Announce", "domain": "script", "input": {
        "engine": {"selector": {}}, "player": {"default": ["media_player.x"]}}},
        "mode": "queued", "fields": {"message": {}},
        "sequence": [{"service": "tts.speak", "data": {"media_player_entity_id": {"__tag__": "!input",
                                                                                  "value": "player"},
                                                      "message": "{{ message }}"},
                      "target": {"entity_id": {"__tag__": "!input", "value": "engine"}}}]}
    await engine.load([], {"announce": {"alias": "Announce", "use_blueprint": {
        "path": "me/announce.yaml", "input": {"engine": "tts.piper"}}}}, [], None,
        blueprints={"me/announce.yaml": bp})
    await engine.start()
    assert engine.scripts["announce"].mode == "queued"
    await hub.call_service("script", "announce", {"message": "hi"})
    assert calls == [("tts.speak", {"media_player_entity_id": ["media_player.x"], "message": "hi",
                                    "entity_id": "tts.piper"})]
    await engine.load([], {"other": {"use_blueprint": {"path": "missing.yaml"}}}, [], None)
    assert "missing.yaml" in engine.scripts["other"].unsupported_reason


# -- scenes --------------------------------------------------------------------------------------------

async def test_scenes(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "light.turn_on", "light.turn_off", "climate.set_hvac_mode", "climate.set_temperature",
                   "cover.close_cover")
    await run(engine, scenes=[{"id": "1", "name": "Movie Night", "entities": {
        "light.a": {"state": "on", "brightness": 50, "friendly_name": "A"},
        "light.b": "off", "climate.c": {"state": "cool", "temperature": 74}, "cover.d": "closed"}}])
    assert hub.states.get("scene.movie_night").state == "unknown"
    await hub.call_service("scene", "turn_on", {"entity_id": "scene.movie_night", "transition": 2})
    assert sorted(calls, key=lambda c: c[0]) == sorted([
        ("light.turn_on", {"entity_id": "light.a", "brightness": 50, "transition": 2}),
        ("light.turn_off", {"entity_id": "light.b"}),
        ("climate.set_hvac_mode", {"entity_id": "climate.c", "hvac_mode": "cool"}),
        ("climate.set_temperature", {"entity_id": "climate.c", "temperature": 74}),
        ("cover.close_cover", {"entity_id": "cover.d"})], key=lambda c: c[0])
    assert hub.states.get("scene.movie_night").state == START.isoformat()
    calls.clear()
    await hub.call_service("scene", "apply", {"entities": {"light.z": "on"}})
    assert calls == [("light.turn_on", {"entity_id": "light.z"})]


# -- the scheduler -------------------------------------------------------------------------------------

def schedule(**over: Any) -> dict:
    s = {"schedule_id": "abc123", "name": "Week Day", "weekdays": ["workday"], "repeat_type": "repeat",
         "enabled": True, "start_date": None, "end_date": None,
         "timeslots": [
             {"start": "05:00:00", "stop": "09:00:00", "conditions": [], "condition_type": None,
              "track_conditions": False, "actions": [{"service": "climate.set_temperature",
                                                      "entity_id": "climate.hallway",
                                                      "service_data": {"temperature": 85, "hvac_mode": "off"}}]},
             {"start": "17:00:00", "stop": "00:00:00", "conditions": [], "condition_type": None,
              "track_conditions": False, "actions": [{"service": "climate.set_temperature",
                                                      "entity_id": "climate.hallway",
                                                      "service_data": {"temperature": 73, "hvac_mode": "cool"}}]}]}
    s.update(over)
    return s


async def test_scheduler_runs_slots_on_workdays_and_publishes_switches(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "climate.set_temperature")
    await run(engine, schedules={"schedules": [schedule()], "time_shutdown": START.isoformat()})
    st = hub.states.get("switch.schedule_week_day")
    assert st.state == "on"
    assert st.attributes["next_trigger"] == "2026-11-11T17:00:00-07:00"
    assert st.attributes["timeslots"] == ["05:00:00 - 09:00:00", "17:00:00 - 00:00:00"]
    await advance(engine, 5 * 3600)  # 17:00
    assert calls == [("climate.set_temperature", {"temperature": 73, "hvac_mode": "cool",
                                                  "entity_id": "climate.hallway"})]
    # Thursday 05:00 and 17:00, Friday both, nothing on the weekend, then Monday 05:00
    await advance(engine, 4 * 86400 - 3600 * 5 + 18 * 3600)
    temps = [c[1]["temperature"] for c in calls]
    assert temps == [73, 85, 73, 85, 73, 85]
    assert hub.states.get("switch.schedule_week_day").attributes["next_trigger"] == "2026-11-16T17:00:00-07:00"


async def test_scheduler_uses_the_workday_sensor_and_date_range(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "climate.set_temperature")
    hub.states.set("binary_sensor.workday_sensor", "off", {"workdays": ["mon", "tue", "wed", "thu", "fri"]})
    await run(engine, schedules=[schedule(start_date="2026-11-13", end_date="2026-11-13")])
    await advance(engine, 86400)  # today is a holiday by the sensor, tomorrow before the start date
    assert calls == []
    hub.states.set("binary_sensor.workday_sensor", "on", {"workdays": ["mon", "tue", "wed", "thu", "fri"]})
    await advance(engine, 86400 * 2)  # Friday the 13th, inside the range
    assert len(calls) == 2
    await advance(engine, 7 * 86400)
    assert len(calls) == 2
    assert hub.states.get("switch.schedule_week_day").attributes["next_trigger"] is None


async def test_scheduler_startup_runs_the_current_slot_unless_it_ran_before_shutdown(hub: Hub) -> None:
    calls = record(hub, "climate.set_temperature")
    later = START + timedelta(hours=6)  # 18:00 local, inside 17:00-00:00
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(later))
    try:
        await run(eng, schedules={"schedules": [schedule()], "time_shutdown": START.isoformat()})
        assert [c[1]["temperature"] for c in calls] == [73]
    finally:
        await eng.stop()
    calls.clear()
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(later))
    try:
        shutdown = (later - timedelta(minutes=30)).isoformat()  # shut down after the slot began
        await run(eng, schedules={"schedules": [schedule()], "time_shutdown": shutdown})
        assert calls == []
    finally:
        await eng.stop()


async def test_scheduler_switch_turn_off_and_conditions(hub: Hub, engine: AutomationEngine) -> None:
    calls = record(hub, "climate.set_temperature")
    hub.config_entries["sched"] = ConfigEntry("sched", "scheduler")
    hub.entities["switch.schedule_abc123"] = EntityEntry("switch.schedule_abc123", "scheduler", "abc123",
                                                         config_entry_id="sched")
    hub.states.set("binary_sensor.home", "off")
    hub.states.set("climate.hallway", "cool")
    slots = schedule()["timeslots"]
    slots[1]["conditions"] = [{"entity_id": "binary_sensor.home", "value": "on", "match_type": "is"}]
    slots[1]["track_conditions"] = True
    await run(engine, schedules=[schedule(timeslots=slots, weekdays=["daily"])])
    assert hub.states.get("switch.schedule_abc123").state == "on"  # the registry's entity id wins
    await advance(engine, 5 * 3600 + 60)
    assert calls == []  # condition false at 17:00
    hub.states.set("binary_sensor.home", "on")
    await settle()
    assert len(calls) == 1  # track_conditions runs it once the condition holds
    await hub.call_service("switch", "turn_off", {"entity_id": "switch.schedule_abc123"})
    assert hub.states.get("switch.schedule_abc123").state == "off"
    assert engine.store.kv["schedule:abc123"]["enabled"] is False
    await advance(engine, 86400)
    assert len(calls) == 1


# -- the clock -----------------------------------------------------------------------------------------

async def test_scheduler_loop_on_the_real_clock() -> None:
    sched = Scheduler(Clock())
    sched.start()
    fired: list[datetime] = []
    try:
        sched.after(0.05, fired.append)
        await sched.sleep(0.12)
        assert len(fired) == 1
    finally:
        await sched.stop()


async def test_fake_clock_scheduler_loop_wakes_on_advance() -> None:
    clock = FakeClock(START)
    sched = Scheduler(clock)
    sched.start()
    fired: list[datetime] = []
    try:
        sched.at(START + timedelta(hours=1), fired.append)
        await settle()
        clock.advance(3599)
        await settle()
        assert fired == []
        clock.advance(1)
        await settle()
        assert fired == [START + timedelta(hours=1)]
    finally:
        await sched.stop()


# -- the household's own configuration ----------------------------------------------------------------

def household_hub() -> Hub:
    hub = Hub(time_zone="Etc/GMT+7", location=dict(LOCATION))
    with open(os.path.join(FIXTURES, "registry.json"), encoding="utf-8") as f:
        reg = json.load(f)
    for e in reg["entities"]:
        hub.entities[e["entity_id"]] = EntityEntry(**e)
    for d in reg["devices"]:
        hub.devices[d["id"]] = DeviceEntry(**d)
    for a in reg["areas"]:
        hub.areas[a["area_id"]] = Area(**a)
    hub.entity_registry_ids = reg["entity_registry_ids"]
    return hub


@needs_household
async def test_household_configuration_loads_every_item_or_says_why() -> None:
    exp = household_expectations()
    hub = household_hub()
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    try:
        await eng.load_config_dir(FIXTURES)
        await eng.start()
        await settle()
        status = eng.status()
        autos = [s for s in status if s["kind"] == "automation"]
        scripts = [s for s in status if s["kind"] == "script"]
        counts = exp["counts"]
        assert len(autos) == counts["automation"] and len(scripts) == counts["script"]
        assert len([s for s in status if s["kind"] == "schedule"]) == counts["schedule"]
        for s in autos + scripts:
            assert s["unsupported_reason"] is None or s["unsupported_reason"].strip(), s
            if s["unsupported_reason"] is None:
                assert s["enabled"] or s["kind"] == "script", s
        by_alias = {s["alias"]: s for s in autos}
        # The ones that must keep working.
        for alias in exp["enabled_aliases"]:
            assert by_alias[alias]["enabled"], (alias, by_alias[alias]["unsupported_reason"])
        # HA Assist timers and their input_text helpers are gone.
        for want in exp["unsupported"]:
            if "alias" in want:
                item = by_alias[want["alias"]]
            else:
                item = next(s for s in scripts if s["id"] == want["script_id"])
            assert want["contains"] in item["unsupported_reason"], (want, item["unsupported_reason"])
        # Entity ids are HA's, found by unique_id, including user renames.
        ids = {s["entity_id"] for s in status}
        assert set(exp["entity_ids"]) <= ids
        for eid, name in exp["friendly_names"].items():
            assert hub.states.get(eid).attributes["friendly_name"] == name
        # The garage device trigger became a state trigger on the ESPHome door sensor.
        g = exp["garage"]
        garage = next(i for i in eng.automations.values() if i.alias.startswith(g["alias_prefix"]))
        assert garage.triggers[0]["trigger"] == "state"
        assert garage.triggers[0]["entity_id"] == g["sensor"]
        assert garage.triggers[0]["to"] == "off"
        assert garage.actions[0]["action"] == "switch.turn_on"
        assert garage.actions[0]["entity_id"] == g["switch"]
        # Schedules: the one in season has a next trigger, an ended one has none.
        for eid, nxt in exp["schedules"]["next_trigger"].items():
            assert hub.states.get(eid).attributes["next_trigger"] == nxt
        for eid in exp["schedules"]["off"]:
            assert hub.states.get(eid).state == "off"
    finally:
        await eng.stop()


@needs_household
async def test_household_garage_automation_end_to_end() -> None:
    g = household_expectations()["garage"]
    hub = household_hub()
    calls = record(hub, "switch.turn_on", *g["notify"])
    hub.states.set(g["sensor"], "on")
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    try:
        await eng.load_config_dir(FIXTURES)
        await eng.start()
        hub.states.set(g["sensor"], "off")
        await advance(eng, g["open_for_s"] - 1)
        assert calls == []
        await advance(eng, 2)
        assert names(calls) == ["switch.turn_on", *g["notify"]]
        assert calls[0][1] == {"entity_id": g["switch"]}
    finally:
        await eng.stop()


@needs_household
async def test_household_cyd_scripts_and_syncs() -> None:
    c = household_expectations()["cyd"]
    hub = household_hub()
    library = {"items": [{"name": f"Playlist number {i}", "uri": f"library://playlist/{i}"} for i in range(8)]}
    record(hub, "music_assistant.get_library", response=library)
    pushes = record(hub, c["playlists_service"], c["weather_service"])
    forecast = {"weather.home": {"forecast": [
        {"datetime": f"2026-11-{11 + i}T12:00:00-07:00", "condition": "sunny", "temperature": 80 + i,
         "templow": 60 + i} for i in range(6)]}}
    record(hub, "weather.get_forecasts", response=forecast)
    hub.states.set(c["media_entity"], "playing", {"friendly_name": c["media_name"], "media_title": "Song",
                                                  "volume_level": 0.3})
    hub.states.set("media_player.kitchen", "idle", {"friendly_name": "Kitchen Speaker Big Name"})
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    try:
        await eng.load_config_dir(FIXTURES)
        await eng.start()  # both syncs have a homeassistant start trigger
        await settle()
        sent = {call[0]: call[1] for call in pushes}
        assert sent[c["playlists_service"]]["p1"] == "Playlist number 0"
        assert sent[c["playlists_service"]]["u3"] == "library://playlist/2"
        assert sent[c["weather_service"]]["n1"] == "Thu"
        assert sent[c["weather_service"]]["h2"] == 82
        res = await hub.call_service("script", c["script"], {"media_entity": c["media_entity"]},
                                     return_response=True)
        out = res["response"]
        assert out["devices"][0] == {"e": c["media_entity"], "n": c["media_name"]}
        assert out["devices"][1]["n"] == "Kitchen Sp.."
        assert out["now"] == {"state": "playing", "title": "Song", "artist": ""}
        assert len(out["playlists"]) == 6 and out["playlists"][0] == "Playlist number 0"
        assert out["vol"] == 0.3 and out["cl"] is False
        await advance(eng, 600)
        assert len([p for p in pushes if p[0] == c["playlists_service"]]) == 2  # time_pattern /10
    finally:
        await eng.stop()
