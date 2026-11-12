"""Thermostat programs, holds, away, the energy rule, and the household-shaped schedules migrating unchanged.

The schedules below have the exact shape of the household's scheduler.storage
(three programs, each setting two thermostats), with made-up entity ids.
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest

from services.homehub import automation_api
from services.homehub.automation_editor import EditorError
from services.homehub.climate_schedule import program_from_schedule, schedule_from_program
from services.homehub.tests.test_automation import advance, record, settle
from services.homehub.tests.test_automation_editor import Env, make_env

A, B = "climate.hall", "climate.loft"


def slot(start: str, stop: str, temp: int, mode: str) -> dict:
    return {"start": start, "stop": stop, "conditions": [], "condition_type": None, "track_conditions": False,
            "actions": [{"service": "climate.set_temperature", "entity_id": e,
                         "service_data": {"temperature": temp, "hvac_mode": mode}} for e in (A, B)]}


HOUSEHOLD = {"schedules": [
    {"schedule_id": "aa0001", "name": "Winter/Spring Week Day", "weekdays": ["workday"],
     "start_date": "2026-11-01", "end_date": "2027-04-30", "repeat_type": "pause", "enabled": True,
     "timeslots": [slot("00:00:00", "05:00:00", 73, "cool"), slot("05:00:00", "09:00:00", 85, "off"),
                   slot("09:00:00", "17:00:00", 73, "cool"), slot("17:00:00", "21:00:00", 85, "off"),
                   slot("21:00:00", "00:00:00", 73, "cool")]},
    {"schedule_id": "aa0002", "name": "Spring/Summer Weekend", "weekdays": ["weekend"], "start_date": None,
     "end_date": None, "repeat_type": "repeat", "enabled": False,
     "timeslots": [slot("00:00:00", "05:00:00", 73, "cool"), slot("05:00:00", "19:00:00", 74, "cool"),
                   slot("19:00:00", "00:00:00", 75, "cool")]},
    {"schedule_id": "aa0003", "name": "Summer Week Day", "weekdays": ["workday"], "start_date": "2026-05-01",
     "end_date": "2026-10-31", "repeat_type": "pause", "enabled": True,
     "timeslots": [slot("00:00:00", "10:00:00", 75, "cool"), slot("10:00:00", "14:00:00", 72, "cool"),
                   slot("14:00:00", "20:00:00", 85, "off"), slot("20:00:00", "00:00:00", 73, "cool")]},
], "tags": [], "time_shutdown": "2026-11-11T19:00:00+00:00"}

THERMOSTAT_ATTRS = {"hvac_modes": ["off", "heat", "cool"], "fan_modes": ["auto", "on"], "min_temp": 50,
                    "max_temp": 90, "temperature": 73, "current_temperature": 74,
                    "temperature_unit": "°F", "supported_features": 385}
SERVICES = ("climate.set_temperature", "climate.set_hvac_mode", "climate.set_fan_mode")


async def household(tmp_path, start: datetime | None = None, name: str = "hub.db") -> Env:
    kw: dict[str, Any] = {"start": start} if start else {}
    e = await make_env(str(tmp_path / name), entities=[A, B, "sensor.mains_power", "person.one", "person.two"],
                       scheduler=copy.deepcopy(HOUSEHOLD), **kw)
    for eid in (A, B):
        e.hub.states.set(eid, "cool", dict(THERMOSTAT_ATTRS, friendly_name=eid.split(".")[1].title()))
    for p in ("person.one", "person.two"):
        e.hub.states.set(p, "home", {})
    return e


@pytest.fixture
async def env(tmp_path):
    e = await household(tmp_path)
    yield e
    await automation_api.stop(e.engine)
    await e.engine.stop()


def cl(e: Env) -> Any:
    return e.engine.climate


def temps(calls: list[tuple[str, dict]]) -> list[tuple[str, str, Any]]:
    return [(d["entity_id"], name.split(".")[1], d.get("temperature", d.get("hvac_mode"))) for name, d in calls]


# -- migration -----------------------------------------------------------------------------------------

def test_household_schedules_read_as_programs_and_compile_back_identically() -> None:
    for conf in HOUSEHOLD["schedules"]:
        prog, why = program_from_schedule(conf)
        assert why is None and prog["entity_ids"] == [A, B]
        assert schedule_from_program(prog)["timeslots"] == conf["timeslots"]
    assert program_from_schedule(HOUSEHOLD["schedules"][0])[0]["slots"][1] == {
        "start": "05:00:00", "stop": "09:00:00", "temperature": 85, "hvac_mode": "off"}
    mixed = copy.deepcopy(HOUSEHOLD["schedules"][0])
    mixed["timeslots"][0]["actions"].append({"service": "light.turn_on", "entity_id": "light.porch"})
    assert program_from_schedule(mixed)[0] is None


async def test_household_programs_are_listed_and_saving_them_changes_nothing(env: Env) -> None:
    progs = {p["name"]: p for p in cl(env).programs()}
    assert set(progs) == {"Winter/Spring Week Day", "Spring/Summer Weekend", "Summer Week Day"}
    assert progs["Spring/Summer Weekend"]["enabled"] is False
    before = copy.deepcopy(env.store.get_kv("scheduler")["schedules"])
    for p in progs.values():
        await cl(env).save_program(p["id"], p)
    after = {c["schedule_id"]: c for c in env.store.get_kv("scheduler")["schedules"]}
    for conf in before:
        assert after[conf["schedule_id"]]["timeslots"] == conf["timeslots"]
        for k in ("weekdays", "start_date", "end_date", "repeat_type", "enabled", "name"):
            assert after[conf["schedule_id"]][k] == conf[k]
    assert {s.entity_id for s in env.engine.schedules.values()} == {
        "switch.schedule_winter_spring_week_day", "switch.schedule_spring_summer_weekend",
        "switch.schedule_summer_week_day"}


async def run_week(e: Env, days: int = 8) -> list[tuple[str, str, str, Any]]:
    calls = record(e.hub, *SERVICES)
    out = []
    for _ in range(days * 24):
        await advance(e.engine, 3600)
        while calls:
            name, d = calls.pop(0)
            out.append((e.engine.clock.now().astimezone(e.engine.tz).strftime("%a %m-%d %H"), d["entity_id"],
                        name, d.get("temperature")))
    return out


async def test_behaviour_is_unchanged_after_migration(tmp_path) -> None:
    # Wednesday 2026-10-28 00:30 Phoenix: across the summer program's last day and the winter one's first.
    start = datetime(2026, 10, 28, 7, 30, tzinfo=UTC)
    raw = await household(tmp_path, start, "raw.db")
    raw_calls = await run_week(raw)
    await raw.engine.stop()
    migrated = await household(tmp_path, start, "migrated.db")
    for p in cl(migrated).programs():
        await cl(migrated).save_program(p["id"], p)
    await settle()
    migrated_calls = await run_week(migrated)
    await migrated.engine.stop()
    assert raw_calls == migrated_calls
    hours = {(c[0], c[3]) for c in raw_calls if c[1] == A}
    assert ("Wed 10-28 14", 85) in hours and ("Fri 10-30 20", 73) in hours  # summer
    assert not any(c[0].startswith(("Sat 10-31", "Sun 11-01")) for c in raw_calls)  # weekend: nothing runs
    assert ("Mon 11-02 05", 85) in hours and ("Mon 11-02 09", 73) in hours  # winter starts


# -- what a thermostat shows ------------------------------------------------------------------------------------

async def test_current_next_and_the_published_sensor(env: Env) -> None:
    t = cl(env).thermostat(A)
    assert t["current"]["program_name"] == "Winter/Spring Week Day"
    assert t["current"]["setpoint"] == {"temperature": 73, "hvac_mode": "cool"}
    assert t["next"]["at"] == "2026-11-12T00:00:00+00:00"  # 17:00 Phoenix
    assert t["effective"]["source"] == "schedule"
    st = env.hub.states.get("sensor.hall_schedule")
    assert st.state == "schedule"
    assert st.attributes["next_change_at"] == "2026-11-11T17:00:00-07:00"
    assert st.attributes["next_setpoint"] == {"temperature": 85, "hvac_mode": "off"}
    assert st.attributes["summary"] == "Following Winter/Spring Week Day: 73 cool, goes to off at 5:00 PM"
    await advance(env.engine, 5 * 3600 + 2)
    assert env.hub.states.get("sensor.hall_schedule").attributes["next_change_at"] == "2026-11-11T21:00:00-07:00"


# -- holds -------------------------------------------------------------------------------------------------

async def test_permanent_hold_skips_slots_until_resumed(env: Env) -> None:
    calls = record(env.hub, *SERVICES)
    t = await cl(env).set_hold(A, {"hvac_mode": "cool", "temperature": 70}, "permanent", by="Someone")
    assert t["hold"]["kind"] == "permanent" and t["effective"]["source"] == "hold"
    assert temps(calls) == [(A, "set_temperature", 70)]
    calls.clear()
    await advance(env.engine, 5 * 3600)  # 17:00: the loft follows the schedule, the hall stays held
    assert temps(calls) == [(B, "set_temperature", 85)]
    assert env.hub.states.get("sensor.hall_schedule").attributes["summary"] == "Held at 70 cool until resumed"
    calls.clear()
    t = await cl(env).resume(A)
    assert temps(calls) == [(A, "set_temperature", 85)] and t["hold"] is None
    assert env.store.get_kv("climate_holds") == {}


async def test_hold_until_the_next_slot_ends_with_that_slot(env: Env) -> None:
    calls = record(env.hub, *SERVICES)
    t = await cl(env).set_hold(A, {"hvac_mode": "cool", "temperature": 71}, "next_slot")
    assert t["hold"]["until"] == "2026-11-12T00:00:00+00:00"
    calls.clear()
    await advance(env.engine, 5 * 3600 + 5)
    assert sorted(temps(calls)) == [(A, "set_temperature", 85), (B, "set_temperature", 85)]
    assert cl(env).holds == {}


async def test_hold_until_a_time_resumes_the_schedule_then(env: Env) -> None:
    calls = record(env.hub, *SERVICES)
    await cl(env).set_hold(A, {"hvac_mode": "cool", "temperature": 76}, "13:30")
    calls.clear()
    await advance(env.engine, 3600 + 1800 + 1)
    assert temps(calls) == [(A, "set_temperature", 73)]
    with pytest.raises(EditorError):
        await cl(env).set_hold(A, {"temperature": 70, "target_temp_low": 60, "target_temp_high": 70}, "permanent")
    with pytest.raises(EditorError):
        await cl(env).set_hold("climate.nowhere", {"temperature": 70}, "permanent")


async def test_an_expired_hold_is_dropped_at_startup(tmp_path) -> None:
    e = await household(tmp_path)
    await cl(e).set_hold(A, {"temperature": 70}, "13:00")
    await automation_api.stop(e.engine)
    await e.engine.stop()
    e.store.close()
    later = datetime(2026, 11, 11, 21, 0, tzinfo=UTC)  # 14:00 Phoenix
    e2 = await household(tmp_path, later)
    try:
        assert cl(e2).holds == {} and cl(e2).thermostat(A)["effective"]["source"] == "schedule"
    finally:
        await e2.engine.stop()


# -- away -------------------------------------------------------------------------------------------------

async def test_vacation_replaces_the_schedule_and_ends_on_time(env: Env) -> None:
    calls = record(env.hub, *SERVICES)
    await cl(env).save_settings({"away": {"enabled": True, "start": "2026-11-11T13:00:00-07:00",
                                          "end": "2026-11-11T18:00:00-07:00",
                                          "setpoint": {"hvac_mode": "cool", "temperature": 82}}})
    assert calls == []
    await advance(env.engine, 3600 + 1)  # 13:00: away begins
    assert sorted(temps(calls)) == [(A, "set_temperature", 82), (B, "set_temperature", 82)]
    calls.clear()
    await advance(env.engine, 4 * 3600)  # 17:00: the slot is replaced by the away setpoint
    assert sorted(temps(calls)) == [(A, "set_temperature", 82), (B, "set_temperature", 82)]
    calls.clear()
    await advance(env.engine, 3600)  # 18:00: back to the slot in progress
    assert sorted(temps(calls)) == [(A, "set_temperature", 85), (B, "set_temperature", 85)]


async def test_auto_away_when_nobody_is_home(env: Env) -> None:
    calls = record(env.hub, *SERVICES)
    await cl(env).save_settings({"away": {"auto": True, "thermostats": [A],
                                          "setpoint": {"hvac_mode": "off"}}})
    env.hub.states.set("person.one", "not_home", {})
    await settle()
    assert calls == []
    env.hub.states.set("person.two", "Work", {})
    await settle()
    assert temps(calls) == [(A, "set_hvac_mode", "off")]
    calls.clear()
    env.hub.states.set("person.one", "home", {})
    await settle()
    assert temps(calls) == [(A, "set_temperature", 73)]


# -- the energy rule ----------------------------------------------------------------------------------------

async def test_energy_rule_sheds_over_the_limit_and_restores_a_hot_room(env: Env) -> None:
    calls = record(env.hub, *SERVICES)
    env.hub.states.set("sensor.mains_power", "2.0", {"unit_of_measurement": "kW"})
    await cl(env).save_settings({"energy": {
        "enabled": True, "power_sensor": "sensor.mains_power", "shed_after_min": 5, "resume_below_kw": 5,
        "comfort_max_temp": 83, "comfort_setpoint": {"hvac_mode": "cool", "temperature": 80},
        "windows": [{"name": "On-peak", "months": [11], "weekdays": ["wed"], "start": "12:30", "stop": "16:00",
                     "limit_kw": 9}]}})
    with pytest.raises(EditorError):
        cl(env).validate_settings({"energy": {"windows": [{"start": "nope", "stop": "10:00"}]}})
    await advance(env.engine, 1800)  # 12:30 window opens
    env.hub.states.set("sensor.mains_power", "9500", {"unit_of_measurement": "W"})
    await advance(env.engine, 4 * 60)
    assert calls == []
    await advance(env.engine, 2 * 60)
    assert sorted(temps(calls)) == [(A, "set_hvac_mode", "off"), (B, "set_hvac_mode", "off")]
    assert cl(env).overview()["energy"]["shed"] == [A, B]
    calls.clear()
    env.hub.states.set(B, "off", dict(THERMOSTAT_ATTRS, current_temperature=86))
    env.hub.states.set("sensor.mains_power", "3.0", {"unit_of_measurement": "kW"})
    await settle()
    assert temps(calls) == [(B, "set_temperature", 80)]  # only the hot room, one at a time
    calls.clear()
    await advance(env.engine, 3 * 3600 + 25 * 60)  # 16:01, the window has ended: back to the schedule (73 cool)
    assert sorted(temps(calls)) == [(A, "set_temperature", 73), (B, "set_temperature", 73)]
    assert cl(env).shed == []


# -- programs ---------------------------------------------------------------------------------------------

async def test_program_editing_fills_stops_and_validates(env: Env) -> None:
    calls = record(env.hub, *SERVICES)
    prog = await cl(env).save_program(None, {
        "name": "Loft weekends", "entity_ids": [B], "weekdays": ["sat", "sun"],
        "slots": [{"start": "22:00", "hvac_mode": "cool", "temperature": 76, "fan_mode": "auto"},
                  {"start": "07:00", "hvac_mode": "heat_cool", "target_temp_low": 68, "target_temp_high": 77}]})
    assert [(s["start"], s["stop"]) for s in prog["slots"]] == [("07:00:00", "22:00:00"), ("22:00:00", "07:00:00")]
    assert prog["entity_id"] == "switch.schedule_loft_weekends" and calls == []  # not a weekend: nothing ran
    s = env.engine.schedules[prog["id"]]
    assert s.timeslots[1]["actions"][1] == {"service": "climate.set_fan_mode", "entity_id": B,
                                            "service_data": {"fan_mode": "auto"}}
    for bad in ({"name": "x", "entity_ids": [], "slots": [{"start": "07:00", "temperature": 70}]},
                {"name": "x", "entity_ids": ["light.porch"], "slots": [{"start": "07:00", "temperature": 70}]},
                {"name": "x", "entity_ids": [B], "slots": [{"start": "07:00", "temperature": 70},
                                                          {"start": "07:00", "temperature": 72}]},
                {"name": "x", "entity_ids": [B], "slots": [{"start": "07:00", "target_temp_low": 70}]},
                {"name": "x", "entity_ids": ["climate.gone"], "slots": [{"start": "07:00", "temperature": 70}]}):
        with pytest.raises(EditorError):
            await cl(env).save_program(None, bad)
    await cl(env).delete_program(prog["id"])
    assert prog["id"] not in env.engine.schedules
