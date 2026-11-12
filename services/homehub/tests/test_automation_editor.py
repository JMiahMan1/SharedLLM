"""The automation editor: CRUD through the running engine, traces, the remap, scheduler CRUD, the API."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from services.homehub import automation_api
from services.homehub.auth import Principal
from services.homehub.automation import AutomationEngine, FakeClock
from services.homehub.automation_editor import EditorError, apply_remap, normalise_remap, suggest_remap
from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.store import Store
from services.homehub.tests.test_automation import LOCATION, START, advance, names, record, settle


class Env(SimpleNamespace):
    hub: Hub
    store: Store
    engine: AutomationEngine
    editor: Any


async def make_env(path: str, *, entities: list[str] = (), automations: list[dict] | None = None,
                   scheduler: Any = None, start: datetime = START, tz: str = "Etc/GMT+7") -> Env:
    store = Store(path)
    hub = Hub(time_zone=tz, location=dict(LOCATION))
    hub.areas["office"] = Area("office", "Office")
    for eid in entities:
        hub.entities[eid] = EntityEntry(entity_id=eid, platform="test", unique_id=eid)
        hub.states.set(eid, "off", {})
    for i, a in enumerate(automations or []):
        store.save_automation(str(a.get("id") or i), "automation", a)
    if scheduler is not None:
        store.set_kv("scheduler", scheduler)
    engine = AutomationEngine(hub, store, clock=FakeClock(start))
    editor = automation_api.prepare(engine, store)
    scripts = {s["id"]: {k: v for k, v in s.items() if k != "id"} for s in store.load_automations("script")}
    await engine.load(store.load_automations("automation"), scripts, store.load_automations("scene"),
                      store.get_kv("scheduler"))
    await engine.start()
    await automation_api.start(SimpleNamespace(automations=engine))
    await settle()
    return Env(hub=hub, store=store, engine=engine, editor=editor)


@pytest.fixture
async def env(tmp_path):
    e = await make_env(str(tmp_path / "hub.db"), entities=["light.porch", "light.hall", "binary_sensor.door"])
    yield e
    await automation_api.stop(e.engine)
    await e.engine.stop()


def porch_on(alias: str = "Porch on when door opens", **extra: Any) -> dict:
    return {"alias": alias, "triggers": [{"trigger": "state", "entity_id": "binary_sensor.door", "to": "on"}],
            "actions": [{"action": "test.note", "data": {"msg": "porch"}}], **extra}


# -- CRUD ------------------------------------------------------------------------------------------

async def test_create_persists_registers_and_runs(env: Env) -> None:
    calls = record(env.hub, "test.note")
    res = await env.editor.save("automation", None, porch_on(), create=True)
    item = res["item"]
    assert item["entity_id"] == "automation.porch_on_when_door_opens"
    assert item["enabled"] and item["runnable"]
    assert env.hub.entities[item["entity_id"]].unique_id == item["id"]
    assert [d["alias"] for d in env.store.load_automations("automation")] == ["Porch on when door opens"]
    env.hub.states.set("binary_sensor.door", "on", {})
    await settle()
    assert names(calls) == ["test.note"]


async def test_rename_keeps_the_entity_id_and_reloads_only_that_item(env: Env) -> None:
    calls = record(env.hub, "test.note")
    other = {"alias": "Hall after five minutes", "triggers": [
        {"trigger": "state", "entity_id": "light.hall", "to": "on", "for": "00:05:00"}],
        "actions": [{"action": "test.note", "data": {"msg": "hall"}}]}
    a = (await env.editor.save("automation", None, porch_on(), create=True))["item"]
    await env.editor.save("automation", None, other, create=True)
    env.hub.states.set("light.hall", "on", {})
    await advance(env.engine, 120)
    # Editing (and renaming) one automation must not restart the other's running 'for:' timer.
    res = await env.editor.save("automation", a["id"], porch_on(alias="Front porch"))
    assert res["item"]["entity_id"] == a["entity_id"] and res["item"]["alias"] == "Front porch"
    await advance(env.engine, 181)
    assert [c[1]["msg"] for c in calls] == ["hall"]


async def test_validation_refuses_bad_shapes_and_saves_unsupported_disabled(env: Env) -> None:
    with pytest.raises(EditorError) as exc:
        await env.editor.save("automation", None, {"alias": "x", "actions": [{"delay": 1}]}, create=True)
    assert "trigger" in str(exc.value) and exc.value.status == 400
    bad = porch_on(alias="Gone")
    bad["actions"] = [{"action": "light.turn_on", "target": {"entity_id": "light.nowhere"}}]
    env.engine._check_refs = True
    check = env.editor.validate("automation", bad)
    assert check["valid"] and "light.nowhere" in check["unsupported_reason"]
    res = await env.editor.save("automation", None, bad, create=True)
    assert res["item"]["runnable"] is False


async def test_delete_removes_state_store_and_registry(env: Env) -> None:
    item = (await env.editor.save("automation", None, porch_on(), create=True))["item"]
    await env.editor.delete("automation", item["id"])
    assert env.hub.states.get(item["entity_id"]) is None
    assert item["entity_id"] not in env.hub.entities
    assert env.store.load_automations("automation") == []
    with pytest.raises(EditorError):
        await env.editor.delete("automation", item["id"])


async def test_saved_items_load_again_after_a_restart(tmp_path) -> None:
    path = str(tmp_path / "hub.db")
    e = await make_env(path, entities=["binary_sensor.door"])
    item = (await e.editor.save("automation", None, porch_on(), create=True))["item"]
    await e.editor.save("script", None, {"alias": "Bedtime", "sequence": [{"delay": "00:00:01"}]}, create=True)
    await e.editor.set_enabled(item["id"], False)
    await automation_api.stop(e.engine)
    await e.engine.stop()
    e.store.close()
    e2 = await make_env(path, entities=["binary_sensor.door"])
    try:
        again = e2.editor.get("automation", item["id"])
        assert again["item"]["alias"] == "Porch on when door opens" and again["item"]["enabled"] is False
        assert "script.bedtime" in {i["entity_id"] for i in e2.editor.items()}
    finally:
        await e2.engine.stop()


async def test_fixing_a_script_revives_its_callers(env: Env) -> None:
    env.engine._check_refs = True
    await env.editor.save("script", "greet", {"alias": "Greet", "sequence": [
        {"action": "light.turn_on", "target": {"entity_id": "light.nowhere"}}]}, create=True)
    caller = (await env.editor.save("automation", None, {**porch_on(), "actions": [{"action": "script.greet"}]},
                                    create=True))["item"]
    assert caller["runnable"] is False
    await env.editor.save("script", "greet", {"alias": "Greet", "sequence": [{"delay": 0}]})
    assert env.editor.get("automation", caller["id"])["item"]["runnable"] is True


async def test_scenes_and_scripts_run_now(env: Env) -> None:
    calls = record(env.hub, "light.turn_on", "test.note")
    scene = (await env.editor.save("scene", None, {"name": "Evening", "entities": {"light.porch": "on"}},
                                   create=True))["item"]
    await env.editor.run("scene", scene["id"])
    await env.editor.save("script", "hello", {"alias": "Hello", "sequence": [{"action": "test.note"}]}, create=True)
    await env.editor.run("script", "hello")
    await settle()
    assert names(calls) == ["light.turn_on", "test.note"]


async def test_export_and_import_round_trip(env: Env) -> None:
    await env.editor.save("automation", "1700000000000", porch_on(), create=True)
    out = env.editor.export("automation")
    assert out["count"] == 1 and "Porch on when door opens" in out["yaml"]
    report = await env.editor.import_yaml("automation", out["yaml"])
    assert report["skipped"] == [{"id": "1700000000000", "reason": "already exists"}]
    report = await env.editor.import_yaml("automation", out["yaml"].replace("Porch on", "Lamp on"), overwrite=True)
    assert report["updated"] == ["1700000000000"]
    assert env.editor.get("automation", "1700000000000")["item"]["alias"] == "Lamp on when door opens"
    report = await env.editor.import_yaml("script", "wake:\n  alias: Wake\n  sequence:\n    - delay: 1\n")
    assert report["created"] == ["wake"]
    item_yaml = env.editor.get("script", "wake")["yaml"].replace("Wake", "Wake up")
    assert (await env.editor.import_yaml("script", item_yaml, overwrite=True))["updated"] == ["wake"]
    single = env.editor.get("automation", "1700000000000")["yaml"].replace("Lamp on", "Light on")
    assert (await env.editor.import_yaml("automation", single, overwrite=True))["updated"] == ["1700000000000"]


# -- traces -------------------------------------------------------------------------------------------

async def test_traces_record_trigger_conditions_steps_and_errors(env: Env) -> None:
    record(env.hub, "test.note")
    record(env.hub, "test.fail", fail=True)
    conf = porch_on(conditions=[{"condition": "state", "entity_id": "light.hall", "state": "on"}])
    conf["actions"] = [{"action": "test.note"}, {"choose": [{"conditions": [], "sequence": [{"action": "test.fail"}]}]}]
    item = (await env.editor.save("automation", None, conf, create=True))["item"]
    env.hub.states.set("binary_sensor.door", "on", {})
    await settle()
    traces = env.editor.tracer.list("automation", item["id"])
    assert traces[0]["result"] == "condition_failed" and traces[0]["conditions"]["passed"] is False
    env.hub.states.set("light.hall", "on", {})
    env.hub.states.set("binary_sensor.door", "off", {})
    env.hub.states.set("binary_sensor.door", "on", {})
    await settle()
    t = env.editor.tracer.list("automation", item["id"])[0]
    assert t["result"] == "error" and "test.fail failed" in t["error"]
    assert t["trigger"]["entity_id"] == "binary_sensor.door" and t["trigger"]["to_state"] == {"state": "on"}
    assert [(s["kind"], s["depth"]) for s in t["steps"]] == [("service", 0), ("choose", 0), ("service", 1)]
    assert t["steps"][-1]["error"]
    # Kept in the store, so the history survives a restart.
    assert env.store.get_kv(f"trace:automation:{item['id']}")[0]["run_id"] == t["run_id"]


# -- the remap -------------------------------------------------------------------------------------------

LEGACY = {
    "id": "legacy1", "alias": "Old thermostat rule",
    "triggers": [{"trigger": "numeric_state", "entity_id": ["sensor.old_stat_indoor_temperature"], "above": 80}],
    "conditions": [{"condition": "device", "device_id": "a" * 32, "domain": "climate", "entity_id": "b" * 32,
                    "type": "is_hvac_mode", "hvac_mode": "off"}],
    "actions": [
        {"action": "climate.set_temperature", "data": {"temperature": 78},
         "target": {"entity_id": "climate.old_stat", "device_id": "a" * 32, "area_id": "upstairs"}},
        {"device_id": "a" * 32, "domain": "climate", "entity_id": "b" * 32, "type": "set_preset_mode",
         "preset_mode": "LegacyHold"},
        {"action": "test.note", "data": {"msg": "{{ states('sensor.old_stat_indoor_temperature') }}"}},
    ],
}
REMAP = {"entities": {"climate.old_stat": "climate.new_stat",
                      "sensor.old_stat_indoor_temperature": "sensor.new_stat_temperature"},
         "registry_ids": {"b" * 32: "climate.new_stat"}, "devices": {"a" * 32: "c" * 32},
         "drop_actions": [{"match": {"type": "set_preset_mode", "preset_mode": "LegacyHold"},
                           "reason": "no such preset"}]}


def test_apply_remap_rewrites_ids_templates_and_drops_actions() -> None:
    new, changes, dropped = apply_remap(normalise_remap(REMAP), "automation", "legacy1", LEGACY)
    assert new["triggers"][0]["entity_id"] == ["sensor.new_stat_temperature"]
    assert new["conditions"][0]["entity_id"] == "climate.new_stat" and new["conditions"][0]["device_id"] == "c" * 32
    assert new["actions"][0]["target"] == {"entity_id": "climate.new_stat", "device_id": "c" * 32,
                                           "area_id": "upstairs"}
    assert len(new["actions"]) == 2 and "sensor.new_stat_temperature" in new["actions"][1]["data"]["msg"]
    assert dropped == [{"path": "actions[1]", "reason": "no such preset"}]
    assert {c["new"] for c in changes} >= {"climate.new_stat", "c" * 32}
    assert LEGACY["actions"][0]["target"]["entity_id"] == "climate.old_stat"  # the input is untouched
    assert apply_remap(normalise_remap({**REMAP, "only": ["other"]}), "automation", "legacy1", LEGACY)[1] == []
    with pytest.raises(EditorError):
        normalise_remap({"entities": {"not an id": "climate.x"}})


async def test_set_remap_repairs_the_automation_but_leaves_it_off(tmp_path) -> None:
    e = await make_env(str(tmp_path / "hub.db"),
                       entities=["climate.new_stat", "sensor.new_stat_temperature", "light.porch"],
                       automations=[LEGACY])
    try:
        e.hub.devices["c" * 32] = DeviceEntry(id="c" * 32, name="New thermostat", area_id="upstairs")
        before = e.editor.get("automation", "legacy1")["item"]
        assert before["runnable"] is False and "old_stat" in before["unsupported_reason"]
        report = await e.editor.set_remap(REMAP)
        row = report[0]
        assert row["id"] == "legacy1" and row["reason_before"] and row["reason_after"] is None
        after = e.editor.get("automation", "legacy1")
        assert after["item"]["runnable"] is True and after["item"]["enabled"] is False
        # The stored config is the original; the remap is applied as it loads.
        assert after["config"]["actions"][0]["target"]["entity_id"] == "climate.old_stat"
        assert e.store.get_kv("automation_remap")["entities"]["climate.old_stat"] == "climate.new_stat"
    finally:
        await e.engine.stop()


async def test_suggest_remap_from_the_recorder(tmp_path) -> None:
    db = tmp_path / "ha.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE statistics_meta (id INTEGER PRIMARY KEY, statistic_id TEXT, unit_of_measurement TEXT);"
                      "CREATE TABLE statistics (metadata_id INT, start_ts REAL, mean REAL, min REAL, max REAL);")
    handover = datetime(2026, 5, 18, tzinfo=UTC)

    def series(mid: int, sid: str, start: datetime, hours: int, varying: bool = True) -> None:
        con.execute("INSERT INTO statistics_meta VALUES (?, ?, ?)", (mid, sid, "°F"))
        for h in range(hours):
            v = 70 + (h % 5 if varying else 0)
            ts = (start + timedelta(hours=h)).timestamp()
            con.execute("INSERT INTO statistics VALUES (?, ?, ?, ?, ?)", (mid, ts, v, v - 0.5, v + 0.5))

    series(1, "sensor.old_stat_indoor_temperature", handover - timedelta(days=3), 72)
    series(2, "sensor.new_stat_temperature", handover + timedelta(days=1), 48)
    series(3, "sensor.other_temperature", handover - timedelta(days=200), 48)
    con.commit()
    con.close()
    e = await make_env(str(tmp_path / "hub.db"), entities=[], automations=[LEGACY])
    try:
        hub = e.hub
        hub.areas["upstairs"] = Area("upstairs", "Upstairs")
        hub.devices["c" * 32] = DeviceEntry(id="c" * 32, name="New thermostat", area_id="upstairs")
        hub.devices["d" * 32] = DeviceEntry(id="d" * 32, name="Other", area_id="kitchen")
        for eid, dev in (("climate.new_stat", "c"), ("sensor.new_stat_temperature", "c"),
                         ("sensor.new_stat_outdoor_temperature", "c"), ("climate.other", "d")):
            hub.entities[eid] = EntityEntry(entity_id=eid, platform="test", unique_id=eid, device_id=dev * 32)
            hub.states.set(eid, "cool", {})
        out = suggest_remap(e.editor, str(db))
        remap = out["remap"]
        assert remap["entities"] == {"climate.old_stat": "climate.new_stat",
                                     "sensor.old_stat_indoor_temperature": "sensor.new_stat_temperature"}
        assert remap["devices"] == {"a" * 32: "c" * 32}
        assert remap["registry_ids"] == {"b" * 32: "climate.new_stat"}
        assert remap["drop_actions"][0]["match"] == {"type": "set_preset_mode", "preset_mode": "LegacyHold"}
        assert any("history starts" in ev["reason"] for ev in out["evidence"])
    finally:
        await e.engine.stop()


# -- the scheduler -------------------------------------------------------------------------------------------

def lights_schedule(**over: Any) -> dict:
    s = {"name": "Porch at night", "weekdays": ["daily"], "timeslots": [
        {"start": "sunset+00:15", "stop": "23:00:00",
         "actions": [{"service": "light.turn_on", "entity_id": "light.porch"}]}]}
    s.update(over)
    return s


async def test_schedule_crud_keeps_switch_entities(env: Env) -> None:
    calls = record(env.hub, "light.turn_on", "light.turn_off")
    s = await env.editor.save_schedule(None, lights_schedule())
    assert s.entity_id == "switch.schedule_porch_at_night"
    assert env.hub.states.get(s.entity_id).state == "on"
    assert env.hub.entities[s.entity_id].platform == "scheduler"
    nxt = s.next_trigger.astimezone(env.engine.tz)
    assert nxt.date().isoformat() == "2026-11-11" and 17 <= nxt.hour <= 18  # sunset at the hub location + 15 min
    # Turning the switch off works through the scheduler's own config entry.
    await env.hub.call_service("switch", "turn_off", {"entity_id": s.entity_id})
    assert env.hub.states.get(s.entity_id).state == "off"
    await env.hub.call_service("switch", "turn_on", {"entity_id": s.entity_id})
    s2 = await env.editor.save_schedule(s.id, lights_schedule(name="Porch renamed"))
    assert s2.entity_id == s.entity_id and s2.name == "Porch renamed"
    await advance(env.engine, 6 * 3600)
    assert names(calls) == ["light.turn_on"]
    await env.editor.delete_schedule(s.id)
    assert env.hub.states.get(s.entity_id) is None and env.store.get_kv("scheduler")["schedules"] == []


async def test_schedule_validation(env: Env) -> None:
    for bad, word in ((lights_schedule(name=""), "name"), (lights_schedule(weekdays=["funday"]), "weekdays"),
                      (lights_schedule(timeslots=[{"start": "25:00", "actions": [{"service": "light.turn_on"}]}]),
                       "start"),
                      (lights_schedule(timeslots=[{"start": "10:00"}]), "no actions"),
                      (lights_schedule(start_date="2026-12-01", end_date="2026-11-01"), "after")):
        with pytest.raises(EditorError) as exc:
            await env.editor.save_schedule(None, bad)
        assert word in str(exc.value)


async def test_schedule_weekday_edges_and_dst(tmp_path) -> None:
    # America/Denver has DST: a 07:00 slot stays at 07:00 local across the March change.
    start = datetime(2027, 3, 13, 18, 0, tzinfo=UTC)  # Saturday 11:00 MST, DST starts Sunday 02:00
    e = await make_env(str(tmp_path / "hub.db"), entities=["light.porch"], start=start, tz="America/Denver")
    try:
        record(e.hub, "light.turn_on")
        s = await e.editor.save_schedule(None, lights_schedule(weekdays=["sun", "mon"], timeslots=[
            {"start": "07:00:00", "actions": [{"service": "light.turn_on", "entity_id": "light.porch"}]}]))
        first = s.next_trigger
        assert first == datetime(2027, 3, 14, 13, 0, tzinfo=UTC)  # Sunday 07:00 MDT (UTC-6)
        nxt = e.engine.schedule_next(s, first)
        assert nxt[0] == datetime(2027, 3, 15, 13, 0, tzinfo=UTC)  # Monday, the very next day
        after = e.engine.schedule_next(s, nxt[0])
        assert after[0].astimezone(e.engine.tz).strftime("%a %H:%M") == "Sun 07:00"  # skips Tue..Sat
        weekend = await e.editor.save_schedule(None, lights_schedule(name="Weekend", weekdays=["weekend"], timeslots=[
            {"start": "00:00:00", "actions": [{"service": "light.turn_on", "entity_id": "light.porch"}]}]))
        assert weekend.next_trigger.astimezone(e.engine.tz).strftime("%a %H:%M") == "Sun 00:00"
    finally:
        await e.engine.stop()


# -- the HTTP API ----------------------------------------------------------------------------------------------

class FakeAuth:
    def authenticate(self, token: str | None) -> Principal | None:
        return {"admin": Principal("a", "Admin", True), "kid": Principal("k", "Kid", False)}.get(token or "")


@pytest.fixture
async def api(env: Env):
    env.hub.states.set("climate.hall", "cool", {"temperature": 74, "hvac_modes": ["off", "cool"]})
    record(env.hub, "climate.set_temperature", "climate.set_hvac_mode")
    app = FastAPI()
    app.include_router(automation_api.build_router(SimpleNamespace(hub=env.hub, automations=env.engine), FakeAuth()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub") as c:
        yield c


ADMIN = {"Authorization": "Bearer admin"}
KID = {"Authorization": "Bearer kid"}


async def test_api_reads_for_everyone_writes_for_admins(api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/homehub/automations")).status_code == 401
    r = await api.get("/api/homehub/automations", headers=KID)
    assert r.status_code == 200 and r.json()["can_edit"] is False
    assert (await api.post("/api/homehub/automations/automation", json={"config": porch_on()},
                           headers=KID)).status_code == 401
    r = await api.post("/api/homehub/automations/automation", json={"config": porch_on()}, headers=ADMIN)
    assert r.status_code == 200, r.text
    aid = r.json()["item"]["id"]
    r = await api.post("/api/homehub/automations/automation", json={"config": {"alias": "x"}}, headers=ADMIN)
    assert r.status_code == 400 and r.json()["errors"]
    assert (await api.get(f"/api/homehub/automations/automation/{aid}", headers=KID)).json()["config"]["id"] == aid
    assert (await api.post(f"/api/homehub/automations/automation/{aid}/enabled", json={"enabled": False},
                           headers=ADMIN)).json()["item"]["enabled"] is False
    assert (await api.get(f"/api/homehub/automations/automation/{aid}/traces", headers=KID)).json() == {"traces": []}
    assert (await api.get("/api/homehub/automations/nope/x", headers=KID)).status_code == 404
    cat = (await api.get("/api/homehub/automations/catalog", headers=KID)).json()
    assert {"state", "time", "sun", "device_onoff"} <= {t["type"] for t in cat["triggers"]}
    assert "light.porch" in {e["entity_id"] for e in cat["entities"]}
    # HA's own config routes.
    assert (await api.get(f"/api/config/automation/config/{aid}", headers=KID)).json()["alias"]
    assert (await api.delete(f"/api/config/automation/config/{aid}", headers=KID)).status_code == 401
    assert (await api.delete(f"/api/config/automation/config/{aid}", headers=ADMIN)).json() == {"result": "ok"}
    # Schedules and thermostats: a member may hold, not edit.
    assert (await api.post("/api/homehub/schedules", json=lights_schedule(), headers=KID)).status_code == 401
    sched = (await api.post("/api/homehub/schedules", json=lights_schedule(), headers=ADMIN)).json()
    assert sched["entity_id"].startswith("switch.schedule_") and sched["climate"] is False
    assert (await api.put("/api/homehub/climate/settings", json={}, headers=KID)).status_code == 401
    r = await api.post("/api/homehub/climate/hold", json={"entity_id": "climate.hall", "until": "permanent",
                                                          "setpoint": {"hvac_mode": "cool", "temperature": 72}},
                       headers=KID)
    assert r.status_code == 200 and r.json()["hold"]["by"] == "Kid"
    r = await api.post("/api/homehub/climate/hold", json={"entity_id": "climate.hall", "setpoint": {}}, headers=KID)
    assert r.status_code == 400
    assert (await api.get("/api/homehub/climate", headers=KID)).json()["thermostats"][0]["hold"] is not None


async def test_remap_routes_are_admin_only(api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/homehub/automations/remap", headers=KID)).status_code == 401
    r = await api.post("/api/homehub/automations/remap/preview", json={"remap": {"entities": {"x": "y"}}},
                       headers=ADMIN)
    assert r.status_code == 400
    r = await api.put("/api/homehub/automations/remap", json={"remap": {}}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["report"] == []


async def test_scheduler_config_entry_is_created_when_missing(env: Env) -> None:
    assert not [e for e in env.hub.config_entries.values() if e.domain == "scheduler"]
    s = await env.editor.save_schedule(None, lights_schedule())
    entries = [e for e in env.hub.config_entries.values() if e.domain == "scheduler"]
    assert len(entries) == 1 and env.hub.entities[s.entity_id].config_entry_id == entries[0].entry_id
    assert isinstance(entries[0], ConfigEntry)
