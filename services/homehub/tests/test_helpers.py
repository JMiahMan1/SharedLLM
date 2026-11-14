"""HA's helpers on the hub: configs, services, state shapes, timers and their events, restore, the API, import.

Every name here is made up.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from services.homehub import helpers_api
from services.homehub.auth import Principal
from services.homehub.automation import AutomationEngine, FakeClock
from services.homehub.core import EntityEntry, Hub, HubError, ServiceNotFound, State
from services.homehub.helpers import (
    DOMAINS,
    STORE_KEY,
    HelperError,
    Helpers,
    fmt_duration,
    from_ha_storage,
    validate_config,
)
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.test_automation import START, FakeStore, settle

T0 = datetime(2026, 3, 4, 17, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


async def make(items: dict[str, list[dict[str, Any]]] | None = None, *, hub: Hub | None = None,
               store: Any = None, clock: Clock | None = None) -> tuple[Helpers, Hub, Clock]:
    hub = hub or Hub(time_zone="Etc/GMT+7")
    store = store or FakeStore()
    if items is not None:
        store.set_kv(STORE_KEY, items)
    clock = clock or Clock()
    m = Helpers(hub, store, now=clock)
    await m.start()
    return m, hub, clock


def events(hub: Hub, prefix: str = "timer.") -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    hub.bus.subscribe(lambda t, d: seen.append((t, dict(d))) if t.startswith(prefix) else None)
    return seen


async def call(hub: Hub, service: str, entity_id: str, **data: Any) -> None:
    domain, name = service.split(".")
    await hub.call_service(domain, name, {"entity_id": entity_id, **data})


# -- configuration ---------------------------------------------------------------------------------

def test_validate_config_keeps_has_fields_and_refuses_with_reasons() -> None:
    assert validate_config("input_number", {"name": "Volume", "min": 0, "max": 10, "step": 0.5})["mode"] == "slider"
    assert validate_config("timer", {"name": "Tea", "duration": "00:03:00"})["duration"] == "0:03:00"
    assert validate_config("timer", {"name": "Tea", "duration": 90})["duration"] == "0:01:30"
    assert validate_config("counter", {"name": "Laps"}) == {"name": "Laps", "initial": 0, "step": 1, "restore": True}
    bad = [
        ("input_number", {"name": "x", "min": 5, "max": 5}, "greater than min"),
        ("input_number", {"name": "x", "min": 0, "max": 5, "initial": 9}, "between"),
        ("input_text", {"name": "x", "pattern": "("}, "regular expression"),
        ("input_text", {"name": "x", "max": 300}, "0 to 255"),
        ("input_select", {"name": "x", "options": []}, "at least one option"),
        ("input_select", {"name": "x", "options": ["a", "a"]}, "different"),
        ("input_select", {"name": "x", "options": ["a"], "initial": "b"}, "not one of"),
        ("input_datetime", {"name": "x"}, "date, a time, or both"),
        ("input_datetime", {"name": "x", "has_date": True, "initial": "soon"}, "not a valid date"),
        ("counter", {"name": "x", "minimum": 5, "maximum": 1}, "below minimum"),
        ("counter", {"name": "x", "step": 0}, "1 or more"),
        ("timer", {"name": "x", "duration": "soon"}, "not a duration"),
        ("input_boolean", {"name": ""}, "needs a name"),
        ("input_boolean", {"name": "x", "initial": "yes"}, "true or false"),
        ("climate_thing", {"name": "x"}, "unknown helper type"),
    ]
    for domain, raw, words in bad:
        with pytest.raises(HelperError, match=words):
            validate_config(domain, raw)


# -- each domain's services and state ----------------------------------------------------------------

async def test_input_boolean_states_services_and_homeassistant_toggle() -> None:
    m, hub, _ = await make({"input_boolean": [{"id": "guest_mode", "name": "Guest mode", "icon": "mdi:account"}]})
    st = hub.states.get("input_boolean.guest_mode")
    assert st.state == "off" and st.attributes == {"editable": True, "icon": "mdi:account", "friendly_name": "Guest mode"}
    await call(hub, "input_boolean.turn_on", "input_boolean.guest_mode")
    assert hub.states.get("input_boolean.guest_mode").state == "on"
    await call(hub, "input_boolean.toggle", "input_boolean.guest_mode")
    assert hub.states.get("input_boolean.guest_mode").state == "off"
    await hub.call_service("homeassistant", "turn_on", {"entity_id": "input_boolean.guest_mode"})
    assert hub.states.get("input_boolean.guest_mode").state == "on"
    with pytest.raises(ServiceNotFound):
        await call(hub, "input_boolean.press", "input_boolean.guest_mode")
    # The hub lists HA's services with their fields.
    services = {s["domain"]: s["services"] for s in hub.list_services()}
    assert {"turn_on", "toggle", "reload"} <= set(services["input_boolean"])
    assert services["timer"]["start"]["fields"]["duration"]
    await m.stop()


async def test_input_number_set_increment_and_bounds() -> None:
    m, hub, _ = await make({"input_number": [{"id": "fan_speed", "name": "Fan speed", "min": 0, "max": 3, "step": 1,
                                               "unit_of_measurement": "level"}]})
    st = hub.states.get("input_number.fan_speed")
    assert st.state == "0.0"
    assert st.attributes["min"] == 0 and st.attributes["max"] == 3 and st.attributes["mode"] == "slider"
    assert st.attributes["unit_of_measurement"] == "level"
    await call(hub, "input_number.set_value", "input_number.fan_speed", value=2)
    await call(hub, "input_number.increment", "input_number.fan_speed")
    assert hub.states.get("input_number.fan_speed").state == "3.0"
    with pytest.raises(HubError, match="outside"):
        await call(hub, "input_number.increment", "input_number.fan_speed")
    with pytest.raises(HubError, match="outside"):
        await call(hub, "input_number.set_value", "input_number.fan_speed", value=-1)
    await call(hub, "input_number.decrement", "input_number.fan_speed")
    assert hub.states.get("input_number.fan_speed").state == "2.0"
    await m.stop()


async def test_input_text_length_and_pattern() -> None:
    m, hub, _ = await make({"input_text": [{"id": "where", "name": "Where", "max": 8, "pattern": "[a-z ]*"}]})
    assert hub.states.get("input_text.where").state == "unknown"
    await call(hub, "input_text.set_value", "input_text.where", value="kitchen")
    st = hub.states.get("input_text.where")
    assert st.state == "kitchen" and st.attributes["max"] == 8 and st.attributes["mode"] == "text"
    with pytest.raises(HubError, match="characters"):
        await call(hub, "input_text.set_value", "input_text.where", value="the long hallway")
    with pytest.raises(HubError, match="pattern"):
        await call(hub, "input_text.set_value", "input_text.where", value="Room 2")
    await call(hub, "input_text.set_value", "input_text.where", value="")
    assert hub.states.get("input_text.where").state == ""
    await m.stop()


async def test_input_select_moves_through_options() -> None:
    m, hub, _ = await make({"input_select": [{"id": "mode", "name": "Mode", "options": ["home", "away", "sleep"]}]})
    eid = "input_select.mode"
    assert hub.states.get(eid).state == "home" and hub.states.get(eid).attributes["options"] == ["home", "away", "sleep"]
    await call(hub, "input_select.select_next", eid)
    assert hub.states.get(eid).state == "away"
    await call(hub, "input_select.select_last", eid)
    await call(hub, "input_select.select_next", eid)
    assert hub.states.get(eid).state == "home"  # cycles by default
    await call(hub, "input_select.select_previous", eid, cycle=False)
    assert hub.states.get(eid).state == "home"
    await call(hub, "input_select.select_option", eid, option="sleep")
    with pytest.raises(HubError, match="not an option"):
        await call(hub, "input_select.select_option", eid, option="party")
    await call(hub, "input_select.set_options", eid, options=["day", "night"])
    assert hub.states.get(eid).state == "day" and hub.states.get(eid).attributes["options"] == ["day", "night"]
    await call(hub, "input_select.select_first", eid)
    await m.stop()


async def test_input_datetime_shapes_and_set_datetime() -> None:
    m, hub, _ = await make({"input_datetime": [
        {"id": "both", "name": "Both", "has_date": True, "has_time": True},
        {"id": "day", "name": "Day", "has_date": True, "has_time": False, "initial": "2026-05-01"},
        {"id": "alarm", "name": "Alarm", "has_date": False, "has_time": True},
    ]})
    assert hub.states.get("input_datetime.both").state == "1970-01-01 00:00:00"
    day = hub.states.get("input_datetime.day")
    assert day.state == "2026-05-01" and day.attributes["year"] == 2026 and "hour" not in day.attributes
    await call(hub, "input_datetime.set_datetime", "input_datetime.alarm", time="06:45")
    alarm = hub.states.get("input_datetime.alarm")
    assert alarm.state == "06:45:00" and alarm.attributes["timestamp"] == 6 * 3600 + 45 * 60
    await call(hub, "input_datetime.set_datetime", "input_datetime.both", datetime="2026-07-04 20:30:00")
    both = hub.states.get("input_datetime.both")
    assert both.state == "2026-07-04 20:30:00"
    # Local time in the hub's zone (UTC-7 here).
    assert both.attributes["timestamp"] == datetime(2026, 7, 5, 3, 30, tzinfo=UTC).timestamp()
    await call(hub, "input_datetime.set_datetime", "input_datetime.both", date="2026-08-01")
    assert hub.states.get("input_datetime.both").state == "2026-08-01 20:30:00"
    await call(hub, "input_datetime.set_datetime", "input_datetime.both", timestamp=0)
    assert hub.states.get("input_datetime.both").state == "1969-12-31 17:00:00"
    with pytest.raises(HubError, match="has no date"):
        await call(hub, "input_datetime.set_datetime", "input_datetime.alarm", date="2026-01-01")
    with pytest.raises(HubError, match="needs a date"):
        await call(hub, "input_datetime.set_datetime", "input_datetime.alarm")
    await m.stop()


async def test_input_button_press_records_when() -> None:
    m, hub, clock = await make({"input_button": [{"id": "doorbell_test", "name": "Doorbell test"}]})
    assert hub.states.get("input_button.doorbell_test").state == "unknown"
    await call(hub, "input_button.press", "input_button.doorbell_test")
    assert hub.states.get("input_button.doorbell_test").state == clock().isoformat(timespec="microseconds")
    await m.stop()


async def test_counter_steps_clamps_resets_and_sets() -> None:
    m, hub, _ = await make({"counter": [{"id": "laps", "name": "Laps", "initial": 2, "step": 2, "maximum": 6,
                                          "minimum": 0}]})
    eid = "counter.laps"
    st = hub.states.get(eid)
    assert st.state == "2" and st.attributes == {"editable": True, "initial": 2, "step": 2, "minimum": 0,
                                                 "maximum": 6, "friendly_name": "Laps"}
    for _ in range(3):
        await call(hub, "counter.increment", eid)
    assert hub.states.get(eid).state == "6"  # clamped at the maximum, as HA does
    await call(hub, "counter.reset", eid)
    assert hub.states.get(eid).state == "2"
    await call(hub, "counter.set_value", eid, value=4)
    with pytest.raises(HubError, match="outside"):
        await call(hub, "counter.set_value", eid, value=9)
    await call(hub, "counter.decrement", eid)
    assert hub.states.get(eid).state == "2"
    await m.stop()


# -- timers ---------------------------------------------------------------------------------------------

async def test_timer_start_pause_resume_change_finish_with_events() -> None:
    m, hub, clock = await make({"timer": [{"id": "tea", "name": "Tea", "duration": "0:05:00"}]})
    seen = events(hub)
    eid = "timer.tea"
    st = hub.states.get(eid)
    assert st.state == "idle" and st.attributes == {"editable": True, "duration": "0:05:00", "friendly_name": "Tea"}
    await call(hub, "timer.start", eid)
    st = hub.states.get(eid)
    assert st.state == "active" and st.attributes["remaining"] == "0:05:00"
    assert st.attributes["finishes_at"] == (T0 + timedelta(minutes=5)).isoformat()
    clock.advance(60)
    await call(hub, "timer.pause", eid)
    st = hub.states.get(eid)
    assert st.state == "paused" and st.attributes["remaining"] == "0:04:00" and "finishes_at" not in st.attributes
    clock.advance(600)  # paused: time does not count
    await call(hub, "timer.start", eid)  # resumes
    assert hub.states.get(eid).attributes["finishes_at"] == (clock() + timedelta(minutes=4)).isoformat()
    assert hub.states.get(eid).attributes["duration"] == "0:05:00"
    await call(hub, "timer.change", eid, duration="-0:01:00")
    assert hub.states.get(eid).attributes["remaining"] == "0:03:00"
    with pytest.raises(HubError, match="longer than its duration"):
        await call(hub, "timer.change", eid, duration="0:10:00")
    await call(hub, "timer.start", eid, duration="0:00:30")  # a new run while active is a restart
    clock.advance(29)
    m.run_due()
    assert hub.states.get(eid).state == "active"
    clock.advance(1)
    m.run_due()
    assert hub.states.get(eid).state == "idle"
    assert [t for t, _ in seen] == ["timer.started", "timer.paused", "timer.started", "timer.changed",
                                    "timer.restarted", "timer.finished"]
    assert seen[-1][1] == {"entity_id": eid, "finished_at": clock().isoformat()}
    await m.stop()


async def test_timer_cancel_and_finish_and_idle_calls() -> None:
    m, hub, _ = await make({"timer": [{"id": "oven", "name": "Oven", "duration": 600}]})
    seen = events(hub)
    await call(hub, "timer.cancel", "timer.oven")  # idle: nothing to cancel, no event
    await call(hub, "timer.start", "timer.oven")
    await call(hub, "timer.cancel", "timer.oven")
    await call(hub, "timer.start", "timer.oven")
    await call(hub, "timer.finish", "timer.oven")
    assert [t for t, _ in seen] == ["timer.started", "timer.cancelled", "timer.started", "timer.finished"]
    with pytest.raises(HubError, match="not running"):
        await call(hub, "timer.change", "timer.oven", duration=10)
    await m.stop()


async def test_timer_without_a_duration_needs_one() -> None:
    m, hub, _ = await make({"timer": [{"id": "free", "name": "Free"}]})
    with pytest.raises(HubError, match="no duration"):
        await call(hub, "timer.start", "timer.free")
    await call(hub, "timer.start", "timer.free", duration="00:00:10")
    assert hub.states.get("timer.free").attributes["duration"] == "0:00:10"
    await m.stop()


async def test_a_running_timer_finishes_on_its_own() -> None:
    hub = Hub()
    store = FakeStore()
    store.set_kv(STORE_KEY, {"timer": [{"id": "quick", "name": "Quick", "duration": 1}]})
    m = Helpers(hub, store)  # the real clock
    await m.start()
    seen = events(hub)
    await call(hub, "timer.start", "timer.quick")
    for _ in range(300):
        if seen and seen[-1][0] == "timer.finished":
            break
        await asyncio.sleep(0.01)
    assert [t for t, _ in seen] == ["timer.started", "timer.finished"]
    await m.stop()


# -- restore across a restart ----------------------------------------------------------------------------

def restarted_hub(old: Hub) -> Hub:
    """A new hub with the old registry and its last states restored (as Runtime.load_registry does)."""
    new = Hub(time_zone=old.time_zone)
    new.entities = dict(old.entities)
    for st in old.states.all():
        new.states.set(st.entity_id, st.state, st.attributes, restore=State.from_dict(st.as_dict()))
    return new


async def test_values_and_timers_come_back_after_a_restart() -> None:
    items = {
        "input_boolean": [{"id": "flag", "name": "Flag"}, {"id": "forced", "name": "Forced", "initial": False}],
        "input_number": [{"id": "level", "name": "Level", "min": 0, "max": 10}],
        "input_text": [{"id": "note", "name": "Note"}],
        "input_select": [{"id": "pick", "name": "Pick", "options": ["a", "b"]}],
        "counter": [{"id": "kept", "name": "Kept"}, {"id": "fresh", "name": "Fresh", "restore": False}],
        "timer": [{"id": "keeps", "name": "Keeps", "duration": 600, "restore": True},
                  {"id": "paused", "name": "Paused", "duration": 600, "restore": True},
                  {"id": "expires", "name": "Expires", "duration": 60, "restore": True},
                  {"id": "forgets", "name": "Forgets", "duration": 600}],
    }
    store = FakeStore()
    m, hub, clock = await make(items, store=store)
    for eid in ("input_boolean.flag", "input_boolean.forced"):
        await call(hub, "input_boolean.turn_on", eid)
    await call(hub, "input_number.set_value", "input_number.level", value=7)
    await call(hub, "input_text.set_value", "input_text.note", value="hello")
    await call(hub, "input_select.select_option", "input_select.pick", option="b")
    for eid in ("counter.kept", "counter.fresh"):
        await call(hub, "counter.increment", eid)
    for eid in ("timer.keeps", "timer.paused", "timer.expires", "timer.forgets"):
        await call(hub, "timer.start", eid)
    clock.advance(30)
    await call(hub, "timer.pause", "timer.paused")
    await m.stop()

    clock.advance(120)  # the hub is down for two minutes
    hub2 = restarted_hub(hub)
    seen = events(hub2)
    m2, hub2, _ = await make(hub=hub2, store=store, clock=clock)
    state = {s.entity_id: s.state for s in hub2.states.all()}
    assert state["input_boolean.flag"] == "on"
    assert state["input_boolean.forced"] == "off"  # initial wins over the restored value, as in HA
    assert state["input_number.level"] == "7.0" and state["input_text.note"] == "hello"
    assert state["input_select.pick"] == "b"
    assert state["counter.kept"] == "1" and state["counter.fresh"] == "0"
    assert state["timer.keeps"] == "active"
    assert hub2.states.get("timer.keeps").attributes["remaining"] == "0:07:30"
    assert state["timer.paused"] == "paused" and hub2.states.get("timer.paused").attributes["remaining"] == "0:09:30"
    assert state["timer.expires"] == "idle" and seen == [("timer.finished", {
        "entity_id": "timer.expires", "finished_at": clock().isoformat()})]
    assert state["timer.forgets"] == "idle"
    await m2.stop()


async def test_restored_through_the_real_store_and_runtime(tmp_path) -> None:
    path = str(tmp_path / "hub.db")
    store = Store(path)
    hub = Hub()
    rt = Runtime(hub, store)
    rt.load_registry()
    rt.start_recording()
    store.set_kv(STORE_KEY, {"input_boolean": [{"id": "porch_auto", "name": "Porch auto"}]})
    m = Helpers(hub, store)
    await m.start()
    await call(hub, "input_boolean.turn_on", "input_boolean.porch_auto")
    await m.stop()
    store.close()

    store2 = Store(path)
    hub2 = Hub()
    Runtime(hub2, store2).load_registry()
    assert hub2.entities["input_boolean.porch_auto"].platform == "input_boolean"
    m2 = Helpers(hub2, store2)
    await m2.start()
    assert hub2.states.get("input_boolean.porch_auto").state == "on"
    await m2.stop()
    store2.close()


# -- admin CRUD ------------------------------------------------------------------------------------------

async def test_create_update_delete_and_registry_names() -> None:
    hub = Hub()
    # HA's registry kept the helper (renamed by a person) though its config is gone: it is reused.
    hub.entities["input_text.front_note"] = EntityEntry("input_text.front_note", "input_text", "note",
                                                       name="Front door note")
    m, hub, _ = await make({}, hub=hub)
    h = await m.create("input_text", {"name": "Note", "id": "note"})
    assert h.entity_id == "input_text.front_note"
    assert hub.states.get("input_text.front_note").attributes["friendly_name"] == "Front door note"
    a = await m.create("counter", {"name": "Glasses of water"})
    b = await m.create("counter", {"name": "Glasses of water"})
    assert (a.id, b.id) == ("glasses_of_water", "glasses_of_water_2")
    with pytest.raises(HelperError, match="already exists"):
        await m.create("counter", {"name": "x", "id": "glasses_of_water"})
    await call(hub, "counter.increment", a.entity_id)
    await m.update("counter", a.id, {"name": "Water", "step": 3})
    assert hub.states.get(a.entity_id).state == "1"  # an edit keeps the count
    await call(hub, "counter.increment", a.entity_id)
    assert hub.states.get(a.entity_id).state == "4"
    with pytest.raises(HelperError, match="no counter helper"):
        await m.update("counter", "nope", {"name": "x"})
    await m.delete("counter", b.id)
    assert hub.states.get(b.entity_id) is None and b.entity_id not in hub.entities
    rows = m.list()
    assert [r["entity_id"] for r in rows] == ["input_text.front_note", "counter.glasses_of_water"]
    assert rows[1]["state"]["state"] == "4" and rows[1]["config"]["step"] == 3
    await m.stop()


async def test_a_bad_stored_config_is_left_out_and_reported() -> None:
    m, hub, _ = await make({"input_number": [{"id": "broken", "name": "Broken", "min": 3, "max": 1}],
                            "input_boolean": [{"id": "Bad Id", "name": "Bad"}]})
    assert hub.states.get("input_number.broken") is None
    assert any("max must be greater than min" in p for p in m.problems)
    assert any("not a slug" in p for p in m.problems)
    await m.stop()


async def test_reload_service_picks_up_store_changes() -> None:
    store = FakeStore()
    m, hub, _ = await make({"input_boolean": [{"id": "one", "name": "One"}]}, store=store)
    store.set_kv(STORE_KEY, {"input_boolean": [{"id": "two", "name": "Two"}]})
    await hub.call_service("input_boolean", "reload", {})
    assert hub.states.get("input_boolean.one") is None and hub.states.get("input_boolean.two").state == "off"
    await m.stop()


# -- automations that use helpers ----------------------------------------------------------------------------

def assist_items() -> tuple[list[dict], dict[str, dict]]:
    """An automation and a script shaped like HA's Assist timer blueprints (made-up names)."""
    autos = [
        {"id": "reached", "alias": "Timer reached", "triggers": [{"trigger": "event", "event_type": "timer.finished"}],
         "actions": [{"action": "input_text.set_value", "target": {"entity_id": "{{ 'input_text.' ~ 'kitchen_where' }}"},
                      "data": {"value": ""}}]},
        {"id": "named", "alias": "Named timer", "triggers": [{"trigger": "state", "entity_id": "timer.kitchen",
                                                              "to": "idle"}],
         "actions": [{"action": "timer.start", "target": {"entity_id": "timer.kitchen"}}]},
    ]
    scripts = {"start_timer": {"sequence": [{"action": "timer.start", "target": {"entity_id": "{{ t }}"},
                                             "data": {"duration": "{{ d }}"}}]}}
    return autos, scripts


async def test_helper_items_load_only_when_their_helpers_exist() -> None:
    hub = Hub(time_zone="Etc/GMT+7")
    hub.entities["light.lamp"] = EntityEntry("light.lamp", "test", "lamp")  # the hub has a registry
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    autos, scripts = assist_items()
    await eng.load(autos, scripts, [])
    st = {s["id"]: s for s in eng.status()}
    assert "timer helper" in st["reached"]["unsupported_reason"]
    assert "timer.kitchen" in st["named"]["unsupported_reason"]
    assert "timer helper" in st["start_timer"]["unsupported_reason"]

    m, hub, _ = await make({"timer": [{"id": "kitchen", "name": "Kitchen", "duration": 60}],
                            "input_text": [{"id": "kitchen_where", "name": "Kitchen where"}]}, hub=hub)
    await eng.load(autos, scripts, [])
    st = {s["id"]: s for s in eng.status()}
    assert all(s["unsupported_reason"] is None for s in st.values()), st
    await eng.start()
    calls: list[str] = []
    hub.bus.subscribe(lambda _t, d: calls.append(d["service"]), "call_service")
    await eng.run_script("start_timer", {"t": "timer.kitchen", "d": "0:00:10"})
    assert hub.states.get("timer.kitchen").state == "active"
    assert hub.states.get("timer.kitchen").attributes["duration"] == "0:00:10"
    await call(hub, "input_text.set_value", "input_text.kitchen_where", value="stove")
    await call(hub, "timer.finish", "timer.kitchen")  # fires timer.finished -> "reached" clears the text
    await settle()
    assert hub.states.get("input_text.kitchen_where").state == ""
    await eng.stop()
    await m.stop()


async def test_the_api_reloads_automations_after_a_change() -> None:
    hub = Hub(time_zone="Etc/GMT+7")
    hub.entities["light.lamp"] = EntityEntry("light.lamp", "test", "lamp")
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    autos, _ = assist_items()
    await eng.load([autos[1]], {}, [])
    m, hub, _ = await make({}, hub=hub)
    async with client(SimpleNamespace(hub=hub, helpers=m, automations=eng)) as api:
        assert eng.status()[0]["unsupported_reason"]
        r = await api.post("/hub/helpers/timer", json={"name": "Kitchen", "duration": "0:01:00"}, headers=ADMIN)
        assert r.status_code == 200, r.text
        assert eng.status()[0]["unsupported_reason"] is None
        await api.delete("/hub/helpers/timer/kitchen", headers=ADMIN)
        assert "timer.kitchen" in eng.status()[0]["unsupported_reason"]
    await m.stop()


# -- the HTTP API -------------------------------------------------------------------------------------------

class FakeAuth:
    def authenticate(self, token: str | None) -> Principal | None:
        return {"admin": Principal("a", "Admin", True), "kid": Principal("k", "Kid", False)}.get(token or "")


ADMIN = {"Authorization": "Bearer admin"}
KID = {"Authorization": "Bearer kid"}


def client(runtime: Any) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(helpers_api.build_router(runtime, FakeAuth()))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub")


async def test_api_crud_and_calls_for_admins_only() -> None:
    m, hub, _ = await make({})
    async with client(SimpleNamespace(hub=hub, helpers=m, automations=None)) as api:
        assert (await api.get("/hub/helpers")).status_code == 401
        assert (await api.get("/hub/helpers", headers=KID)).status_code == 401
        body = (await api.get("/hub/helpers", headers=ADMIN)).json()
        assert body["helpers"] == [] and [t["domain"] for t in body["types"]] == list(DOMAINS)
        assert {f["name"] for f in next(t for t in body["types"] if t["domain"] == "timer")["fields"]} == {
            "duration", "restore"}
        r = await api.post("/hub/helpers/input_select", json={"name": "Scene", "options": ["day", "night"]},
                           headers=ADMIN)
        assert r.status_code == 200 and r.json()["entity_id"] == "input_select.scene"
        assert r.json()["state"]["state"] == "day"
        r = await api.post("/hub/helpers/input_select", json={"name": "Scene", "options": []}, headers=ADMIN)
        assert r.status_code == 400 and "at least one option" in r.json()["message"]
        assert (await api.post("/hub/helpers/thermostat", json={"name": "x"}, headers=ADMIN)).status_code == 404
        r = await api.post("/hub/helpers/input_select/scene/call", json={"service": "select_next"}, headers=ADMIN)
        assert r.json()["state"]["state"] == "night"
        r = await api.post("/hub/helpers/input_select/scene/call",
                           json={"service": "select_option", "data": {"option": "dusk"}}, headers=ADMIN)
        assert r.status_code == 400 and "not an option" in r.json()["message"]
        r = await api.post("/hub/helpers/input_select/scene/call", json={"service": "explode"}, headers=ADMIN)
        assert r.status_code == 400
        r = await api.put("/hub/helpers/input_select/scene", json={"name": "Scene", "options": ["night", "dawn"]},
                          headers=ADMIN)
        assert r.json()["state"]["attributes"]["options"] == ["night", "dawn"]
        assert (await api.put("/hub/helpers/input_select/nope", json={"name": "x", "options": ["a"]},
                              headers=ADMIN)).status_code == 404
        assert (await api.delete("/hub/helpers/input_select/scene", headers=ADMIN)).json() == {
            "deleted": "input_select.scene"}
        assert (await api.post("/hub/helpers/counter", content="[]", headers=ADMIN)).status_code == 400
    async with client(SimpleNamespace(hub=hub, helpers=None, automations=None)) as api:
        assert (await api.get("/hub/helpers", headers=ADMIN)).status_code == 503
    await m.stop()


# -- import from HA's storage -------------------------------------------------------------------------------

def test_import_reads_the_helper_storage_files(tmp_path) -> None:
    assert from_ha_storage(str(tmp_path)) is None
    sdir = tmp_path / ".storage"
    sdir.mkdir()
    (sdir / "timer").write_text(json.dumps({"version": 1, "key": "timer", "data": {"items": [
        {"id": "pasta", "name": "Pasta", "duration": "0:10:00", "restore": True}]}}))
    (sdir / "input_text").write_text(json.dumps({"data": {"items": [{"id": "x", "name": "X", "max": 50}]}}))
    out = from_ha_storage(str(tmp_path))
    assert out == {"timer": [{"id": "pasta", "name": "Pasta", "duration": "0:10:00", "restore": True}],
                   "input_text": [{"id": "x", "name": "X", "max": 50}]}


def test_importer_stores_the_helpers(tmp_path) -> None:
    from services.homehub.importer import import_config_dir

    cfg = tmp_path / "ha"
    (cfg / ".storage").mkdir(parents=True)
    (cfg / ".storage" / "counter").write_text(json.dumps({"data": {"items": [{"id": "c", "name": "C"}]}}))
    from cryptography.fernet import Fernet

    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    report = import_config_dir(store, str(cfg))
    assert report["helpers"] == 1 and store.get_kv(STORE_KEY)["counter"] == [{"id": "c", "name": "C"}]
    store.close()


def test_fmt_duration() -> None:
    assert fmt_duration(timedelta(hours=1, seconds=5)) == "1:00:05"
    assert fmt_duration(timedelta(seconds=-3)) == "0:00:00"
