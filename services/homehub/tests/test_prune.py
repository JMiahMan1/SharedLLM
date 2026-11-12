"""Pruning stale imported HA data: every rule, the dry run, applying a reviewed plan, protections.

A synthetic store with made-up ids and names (Alex, Sam, Robin) and a fixed
clock; registry timestamps come from a fake HA config directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import prune
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, Hub, State
from services.homehub.recorder_statistics import Statistics
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC).timestamp()
OLD = NOW - 500 * 86400   # well past a 365-day cutoff
RECENT = NOW - 10 * 86400  # inside it
CUTOFF = NOW - 365 * 86400


def dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


class FakeDriver:
    async def async_unload(self):
        pass


class Env:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "hub"
        self.store = Store(str(self.dir / "hub.db"), Fernet.generate_key().decode())
        self.hub = Hub(time_zone="UTC")
        self.hub.store = self.store
        self.rt = FakeRuntime(self.hub, self.store)
        self.rt.statistics = Statistics(str(self.dir / "statistics.db"))
        self.ha = tmp_path / "ha"
        (self.ha / ".storage").mkdir(parents=True)
        self.reg: dict[str, list[dict]] = {"entities": [], "devices": [], "entries": []}

    # registry ----------------------------------------------------------------------
    def entry(self, entry_id, domain, *, disabled=False, source="user", modified=OLD, loaded=False):
        e = ConfigEntry(entry_id, domain, f"{domain} {entry_id}", {}, source=source,
                        disabled_by="user" if disabled else None)
        e.state = "loaded" if loaded else "not_loaded"
        self.hub.config_entries[entry_id] = e
        self.store.save_config_entry(e)
        if loaded:
            self.hub.drivers[entry_id] = FakeDriver()
        if modified is not None:
            self.reg["entries"].append({"entry_id": entry_id, "modified_at": dt(modified).isoformat(),
                                        "data": {"password": "never-read"}})
        return e

    def device(self, dev_id, entry_ids, *, disabled=False, modified=OLD, via=None, name=None):
        d = DeviceEntry(dev_id, name=name or f"Device {dev_id}", config_entries=list(entry_ids),
                        disabled_by="user" if disabled else None, via_device_id=via)
        self.hub.devices[dev_id] = d
        self.store.save_device(d)
        if modified is not None:
            self.reg["devices"].append({"id": dev_id, "modified_at": dt(modified).isoformat()})
        return d

    def entity(self, eid, *, entry=None, device=None, disabled=False, modified=OLD, state=None, uid=None,
               platform="test"):
        e = EntityEntry(eid, platform, uid or eid, entry, device, disabled_by="user" if disabled else None)
        self.hub.entities[eid] = e
        self.store.save_entity(e)
        if modified is not None:
            self.reg["entities"].append({"entity_id": eid, "modified_at": dt(modified).isoformat()})
        if state is not None:
            value, ts = state
            st = State(eid, value, {}, dt(ts), dt(ts))
            self.hub.states.set(eid, value, {}, restore=st)
            self.store.save_last_state(st)
        return e

    def history(self, eid, value, ts):
        self.store.import_history_rows([(eid, value, "{}", ts, ts)])

    def stat(self, sid, ts, origin):
        s = self.rt.statistics
        s._exec("INSERT OR IGNORE INTO statistics_meta(statistic_id, source, has_sum, mean_type) "
                "VALUES (?, 'recorder', 0, 1)", (sid,))
        mid = s._exec("SELECT id FROM statistics_meta WHERE statistic_id=?", (sid,)).fetchone()[0]
        s._exec("INSERT INTO statistics(metadata_id, start_ts, mean, origin) VALUES (?, ?, 1.0, ?)",
                (mid, ts, origin))

    def automation(self, item_id, conf, *, enabled, last=None, kind="automation"):
        doc = {"id": item_id, "alias": f"Auto {item_id}", **conf}
        self.store.save_automation(item_id, kind, doc)
        if kind == "automation":
            self.store.set_kv(f"automation:{item_id}", {"enabled": enabled,
                                                        "last_triggered": dt(last).isoformat() if last else None})
        return doc

    def write_ha(self):
        for name, key in (("core.entity_registry", "entities"), ("core.device_registry", "devices"),
                          ("core.config_entries", "entries")):
            (self.ha / ".storage" / name).write_text(json.dumps({"data": {key: self.reg[key]}}))
        return str(self.ha)

    def plan(self, **kw):
        return prune.build_plan(prune.snapshot_from_runtime(self.rt, self.write_ha()), CUTOFF, **kw)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.rt.statistics.close()
    e.store.close()


def ids(plan, kind=None, reason=None):
    return {p["id"] for p in plan["items"]
            if (kind is None or p["kind"] == kind) and (reason is None or p["reason"] == reason)}


# -- entities ---------------------------------------------------------------------------------

def test_disabled_entity_is_pruned_only_when_nothing_is_newer_than_the_cutoff(env):
    env.entry("e1", "wiz", loaded=True)
    env.device("d1", ["e1"], modified=RECENT)
    env.entity("light.alex_lamp", entry="e1", device="d1", state=("on", RECENT))
    env.entity("sensor.old_disabled", entry="e1", device="d1", disabled=True)
    env.entity("sensor.disabled_but_history", entry="e1", device="d1", disabled=True)
    env.history("sensor.disabled_but_history", "21.5", RECENT)
    env.entity("sensor.disabled_recently", entry="e1", device="d1", disabled=True, modified=RECENT)
    plan = env.plan()
    assert ids(plan, "entity", "entity_disabled") == {"sensor.old_disabled"}
    item = next(p for p in plan["items"] if p["id"] == "sensor.old_disabled")
    assert item["last_activity"] == dt(OLD).isoformat()


def test_orphaned_entity_without_recent_activity_is_pruned(env):
    env.entity("sensor.orphan_old")
    env.entity("sensor.orphan_alive", state=("3", RECENT))
    env.entity("sensor.orphan_bad_links", entry="gone", device="gone-too")
    plan = env.plan()
    assert ids(plan, "entity", "entity_orphaned") == {"sensor.orphan_old", "sensor.orphan_bad_links"}


def test_unavailable_and_unknown_are_not_activity(env):
    env.entity("sensor.orphan_flapping", state=("unavailable", RECENT))
    env.history("sensor.orphan_flapping", "unknown", RECENT)
    env.history("sensor.orphan_flapping", "12", OLD)
    assert "sensor.orphan_flapping" in ids(env.plan())


def test_imported_statistics_count_but_compiled_ones_do_not(env):
    env.entity("sensor.orphan_imported_stats")
    env.stat("sensor.orphan_imported_stats", RECENT, 1)
    env.entity("sensor.orphan_compiled_stats")
    env.stat("sensor.orphan_compiled_stats", RECENT, 0)
    plan = env.plan()
    assert "sensor.orphan_imported_stats" not in ids(plan)
    assert "sensor.orphan_compiled_stats" in ids(plan)


def test_registry_modified_at_counts_as_activity(env):
    env.entity("sensor.orphan_touched", modified=RECENT)
    env.entity("sensor.orphan_untouched")
    assert ids(env.plan(), "entity") == {"sensor.orphan_untouched"}


# -- devices ----------------------------------------------------------------------------------

def test_disabled_device_goes_with_its_entities(env):
    env.entry("e1", "wiz", modified=RECENT)
    env.device("d-off", ["e1"], disabled=True)
    env.entity("light.sam_old", entry="e1", device="d-off", disabled=True)
    plan = env.plan()
    assert ids(plan, "device", "device_disabled") == {"d-off"}
    assert ids(plan, "entity") == {"light.sam_old"}
    assert ids(plan, "config_entry") == set()


def test_silent_device_is_pruned_but_one_live_entity_keeps_it(env):
    env.entry("e1", "wiz", modified=RECENT)
    env.device("d-silent", ["e1"])
    env.entity("light.silent_a", entry="e1", device="d-silent", state=("unavailable", RECENT))
    env.entity("sensor.silent_b", entry="e1", device="d-silent")
    env.device("d-mixed", ["e1"])
    env.entity("light.mixed_a", entry="e1", device="d-mixed")
    env.entity("sensor.mixed_b", entry="e1", device="d-mixed", state=("4", RECENT))
    plan = env.plan()
    assert ids(plan, "device") == {"d-silent"}
    assert ids(plan, "entity", "with_device") == {"light.silent_a", "sensor.silent_b"}


def test_empty_device_needs_an_old_registry_change_and_no_loaded_entry(env):
    env.entry("e1", "wiz", modified=RECENT)
    env.entry("e2", "wiz", modified=RECENT, loaded=True)
    env.device("d-empty", ["e1"])
    env.device("d-empty-loaded", ["e2"])
    env.device("d-empty-no-times", ["e1"], modified=None)
    assert ids(env.plan(), "device") == {"d-empty"}


def test_a_parent_of_a_kept_device_is_kept(env):
    env.entry("e1", "wiz", modified=RECENT)
    env.device("d-bridge", ["e1"], disabled=True)
    env.device("d-child", ["e1"], via="d-bridge")
    env.entity("light.robin_child", entry="e1", device="d-child", state=("on", RECENT))
    plan = env.plan()
    assert "d-bridge" not in ids(plan)
    kept = {k["id"]: k["kept_because"] for k in plan["kept"]}
    assert "parent of a kept device" in kept["d-bridge"]


# -- config entries ---------------------------------------------------------------------------

def test_disabled_entry_goes_with_its_devices_and_entities(env):
    env.entry("e-off", "wiz", disabled=True)
    env.device("d1", ["e-off"])
    env.entity("light.porch", entry="e-off", device="d1")
    env.entity("sensor.no_device", entry="e-off")
    plan = env.plan()
    assert ids(plan, "config_entry", "entry_disabled") == {"e-off"}
    assert ids(plan, "device", "with_entry") == {"d1"}
    assert ids(plan, "entity") == {"light.porch", "sensor.no_device"}


def test_entry_without_a_driver_is_pruned_when_silent(env):
    env.entry("e-legacy", "legacy_cloud_example")
    env.entity("sensor.legacy", entry="e-legacy")
    env.entry("e-legacy-alive", "legacy_cloud_example")
    env.entity("sensor.legacy_alive", entry="e-legacy-alive")
    env.history("sensor.legacy_alive", "1", RECENT)
    env.entry("e-driver", "wiz")  # has a driver and is enabled: never an entry candidate
    plan = env.plan()
    assert ids(plan, "config_entry") == {"e-legacy"}
    assert ids(plan, "config_entry", "entry_no_driver") == {"e-legacy"}


def test_disabled_entry_with_recent_activity_is_kept(env):
    env.entry("e-off", "wiz", disabled=True, modified=RECENT)
    assert ids(env.plan()) == set()


def test_ignored_discoveries_and_the_hubs_own_entries_are_kept(env):
    env.entry("e-ign", "legacy_cloud_example", source="ignore")
    env.entry("e-sched", "scheduler")
    plan = env.plan()
    assert ids(plan) == set()
    assert {k["id"] for k in plan["kept"]} == {"e-ign", "e-sched"}


# -- protections ------------------------------------------------------------------------------

def test_enabled_automation_protects_what_it_names_and_their_device_and_entry(env):
    env.entry("e-off", "wiz", disabled=True)
    env.device("d1", ["e-off"])
    env.entity("light.alex_desk", entry="e-off", device="d1", disabled=True)
    env.automation("a1", {"trigger": [{"platform": "time", "at": "07:00"}],
                          "action": [{"service": "light.turn_on", "target": {"entity_id": "light.alex_desk"}}]},
                   enabled=True)
    plan = env.plan()
    assert ids(plan) == set()
    kept = {k["id"]: k["kept_because"] for k in plan["kept"]}
    assert "enabled automation" in kept["light.alex_desk"]
    assert "kept entity" in kept["d1"] and "kept" in kept["e-off"]


def test_templates_device_ids_and_registry_uuids_count_as_references(env):
    env.entity("sensor.in_template", disabled=True)
    env.device("d-named", [], disabled=True)
    env.entity("sensor.by_uuid", disabled=True)
    env.store.set_kv("entity_registry_ids", {"0123456789abcdef0123456789abcdef": "sensor.by_uuid"})
    env.hub.entity_registry_ids = {"0123456789abcdef0123456789abcdef": "sensor.by_uuid"}
    env.automation("a1", {"condition": [{"condition": "template",
                                         "value_template": "{{ states('sensor.in_template') | float > 3 }}"}],
                          "action": [{"device_id": "d-named", "domain": "light", "type": "turn_on",
                                      "entity_id": "0123456789abcdef0123456789abcdef"}]}, enabled=True)
    assert ids(env.plan()) == set()


def test_persons_zones_scenes_schedules_and_energy_protect_what_they_name(env):
    for eid in ("device_tracker.sam_phone", "sensor.zone_thing", "light.scene_lamp", "switch.scheduled",
                "sensor.grid_import"):
        env.entity(eid, disabled=True)
    env.store.set_kv("persons", [{"id": "sam", "name": "Sam", "device_trackers": ["device_tracker.sam_phone"]}])
    env.store.set_kv("zones", [{"id": "z", "name": "Work", "passive": False, "note": "sensor.zone_thing"}])
    env.store.save_automation("s1", "scene", {"id": "s1", "name": "Evening", "entities": {"light.scene_lamp": "on"}})
    env.store.set_kv("scheduler", {"schedules": [{"actions": [{"entity_id": "switch.scheduled"}]}]})
    env.store.set_kv("energy_prefs", {"energy_sources": [{"stat_energy_from": "sensor.grid_import"}]})
    assert ids(env.plan()) == set()


def test_person_and_zone_entities_are_never_pruned(env):
    env.entity("person.robin")
    env.entity("zone.office", disabled=True)
    assert ids(env.plan()) == set()


def test_entity_a_loaded_driver_publishes_is_kept(env):
    env.entry("e1", "legacy_cloud_example", loaded=True)
    env.entity("sensor.live_but_old_value", entry="e1", state=("7", OLD))
    env.entity("sensor.orphan_like", entry="e1", state=("unavailable", OLD))
    plan = env.plan()
    assert "sensor.live_but_old_value" not in ids(plan)
    assert "e1" not in ids(plan)


# -- automations and scripts ------------------------------------------------------------------

def test_disabled_automation_naming_only_removed_or_missing_entities_is_removed(env):
    env.entity("light.gone_soon", disabled=True)
    env.entity("automation.auto_a_old", platform="automation", uid="a-old")
    env.automation("a-old", {"action": [{"service": "light.turn_on", "entity_id": ["light.gone_soon",
                                                                                   "light.never_existed"]}]},
                   enabled=False, last=OLD)
    env.automation("a-recent", {"action": [{"service": "light.turn_on", "entity_id": "light.gone_soon"}]},
                   enabled=False, last=RECENT)
    plan = env.plan()
    assert ids(plan, "automation") == {"a-old"}
    assert "light.gone_soon" in ids(plan, "entity")
    assert "automation.auto_a_old" not in ids(plan, "entity")  # it goes with the automation


def test_disabled_automation_naming_kept_entities_too_is_listed_for_review(env):
    env.entity("light.gone_soon", disabled=True)
    env.entity("light.alive", state=("on", RECENT))
    env.automation("a-mixed", {"action": [{"service": "light.turn_on",
                                           "entity_id": ["light.gone_soon", "light.alive"]}]}, enabled=False, last=OLD)
    plan = env.plan()
    assert ids(plan, "automation") == set()
    review = plan["review"]
    assert [r["id"] for r in review] == ["a-mixed"] and review[0]["reason"] == "references_stale_entities"
    assert review[0]["removed_references"] == ["light.gone_soon"] and review[0]["kept_references"] == ["light.alive"]


def test_enabled_automations_are_never_removed(env):
    env.automation("a-on", {"action": [{"service": "light.turn_on", "entity_id": "light.never_existed"}]},
                   enabled=True, last=OLD)
    assert ids(env.plan()) == set()


def test_script_is_disabled_by_its_registry_entity(env):
    env.entity("script.old_routine", platform="script", uid="old_routine", disabled=True)
    env.entity("light.gone_soon", disabled=True)
    env.automation("old_routine", {"sequence": [{"service": "light.turn_on", "entity_id": "light.gone_soon"}]},
                   enabled=False, kind="script")
    env.automation("live_routine", {"sequence": [{"service": "light.turn_on", "entity_id": "light.never_existed"}]},
                   enabled=True, kind="script")
    assert ids(env.plan(), "script") == {"old_routine"}


# -- the plan id, the dry run and applying ------------------------------------------------------

def _dump(path: str) -> dict:
    db = sqlite3.connect(path)
    try:
        out = {}
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            out[table] = sorted(map(repr, db.execute(f"SELECT * FROM {table}").fetchall()))
        return out
    finally:
        db.close()


def _seed(env):
    env.entry("e-off", "wiz", disabled=True)
    env.device("d1", ["e-off"])
    env.entity("light.porch", entry="e-off", device="d1")
    env.history("light.porch", "on", OLD)
    env.stat("light.porch", OLD, 1)
    env.entry("e-live", "wiz", modified=RECENT, loaded=True)
    env.device("d-live", ["e-live"], modified=RECENT)
    env.entity("light.alex_lamp", entry="e-live", device="d-live", state=("on", RECENT))
    env.entity("sensor.lamp_old_disabled", entry="e-live", device="d-live", disabled=True)
    env.device("d-dead", ["e-live"])
    env.entity("light.dead", entry="e-live", device="d-dead", state=("unavailable", RECENT))
    env.entity("sensor.orphan_old")
    env.entity("automation.auto_a_old", platform="automation", uid="a-old")
    env.automation("a-old", {"action": [{"service": "light.turn_on", "entity_id": "light.porch"}]},
                   enabled=False, last=OLD)
    env.entity("person.robin")


def test_plan_id_is_stable_and_depends_on_cutoff_and_drop_history(env):
    _seed(env)
    a, b = env.plan(), env.plan()
    assert a["plan_id"] == b["plan_id"]
    assert env.plan(drop_history=True)["plan_id"] != a["plan_id"]
    other = prune.build_plan(prune.snapshot_from_runtime(env.rt, env.write_ha()), CUTOFF - 1)
    assert other["plan_id"] != a["plan_id"]


def test_dry_run_changes_nothing(env):
    _seed(env)
    env.store._exec("PRAGMA wal_checkpoint(FULL)")
    before = (_dump(env.store.path), _dump(env.rt.statistics.path), dict(env.hub.entities), dict(env.hub.devices),
              dict(env.hub.config_entries), {s.entity_id: s for s in env.hub.states.all()})
    plan = env.plan(drop_history=True)
    assert plan["items"]
    after = (_dump(env.store.path), _dump(env.rt.statistics.path), dict(env.hub.entities), dict(env.hub.devices),
             dict(env.hub.config_entries), {s.entity_id: s for s in env.hub.states.all()})
    assert before == after


async def test_apply_removes_exactly_the_reviewed_plan(env):
    _seed(env)
    plan = env.plan()
    planned = {(p["kind"], p["id"]) for p in plan["items"]}
    assert planned == {
        ("config_entry", "e-off"), ("device", "d1"), ("entity", "light.porch"),
        ("entity", "sensor.lamp_old_disabled"), ("device", "d-dead"), ("entity", "light.dead"),
        ("entity", "sensor.orphan_old"), ("automation", "a-old")}
    res = await prune.apply_plan(env.rt, plan_id=plan["plan_id"], cutoff=plan["cutoff"], import_dir=str(env.ha),
                                 now=NOW)
    assert res["status"] == "applied"
    assert set(env.hub.config_entries) == {"e-live"}
    assert set(env.hub.devices) == {"d-live"}
    assert set(env.hub.entities) == {"light.alex_lamp", "person.robin"}
    assert env.hub.states.get("light.dead") is None
    assert {e.entity_id for e in env.store.load_entities()} == {"light.alex_lamp", "person.robin"}
    assert {d.id for d in env.store.load_devices()} == {"d-live"}
    assert {e.entry_id for e in env.store.load_config_entries()} == {"e-live"}
    assert env.store.load_automations("automation") == []
    # History and statistics are kept by default.
    assert env.store.history(["light.porch"], dt(OLD - 1))
    assert env.rt.statistics._exec("SELECT COUNT(*) FROM statistics").fetchone()[0] == 1
    # The live entry lost a device and an entity: it was reloaded.
    assert "e-live" in env.rt.stopped and "e-live" in env.rt.started
    # Nothing left to prune.
    assert ids(env.plan()) == set()


async def test_apply_with_drop_history_deletes_their_rows(env):
    _seed(env)
    env.history("light.alex_lamp", "on", RECENT)
    plan = env.plan(drop_history=True)
    res = await prune.apply_plan(env.rt, plan_id=plan["plan_id"], cutoff=plan["cutoff"], drop_history=True,
                                 import_dir=str(env.ha), now=NOW)
    assert res["history_rows_deleted"] == 1 and res["statistics_rows_deleted"] == 1
    assert env.store.history(["light.porch"], dt(OLD - 1)) == []
    assert env.store.history(["light.alex_lamp"], dt(OLD - 1))


async def test_apply_refuses_a_plan_the_hub_has_moved_past(env):
    _seed(env)
    plan = env.plan()
    env.history("sensor.orphan_old", "5", RECENT)  # it woke up after the review
    before = _dump(env.store.path)
    with pytest.raises(prune.PruneError) as err:
        await prune.apply_plan(env.rt, plan_id=plan["plan_id"], cutoff=plan["cutoff"], import_dir=str(env.ha),
                               now=NOW)
    assert err.value.status == 409
    assert _dump(env.store.path) == before
    with pytest.raises(prune.PruneError, match="drop_history"):
        await prune.apply_plan(env.rt, plan_id=plan["plan_id"], cutoff=plan["cutoff"], drop_history=True,
                               import_dir=str(env.ha), now=NOW)


async def test_apply_validates_its_input(env):
    with pytest.raises(prune.PruneError, match="plan_id"):
        await prune.apply_plan(env.rt, plan_id="", cutoff=CUTOFF, now=NOW)
    with pytest.raises(prune.PruneError, match="cutoff"):
        await prune.apply_plan(env.rt, plan_id="x", cutoff=None, now=NOW)
    with pytest.raises(prune.PruneError, match="30 days"):
        await prune.apply_plan(env.rt, plan_id="x", cutoff=NOW - 86400, now=NOW)
    with pytest.raises(prune.PruneError, match="at least 30"):
        prune.cutoff_for(7)


def _tree(root) -> dict[str, tuple[str, int]]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


async def test_prune_only_touches_the_store_it_is_given(env, tmp_path):
    """The HA config it reads timestamps from, and anything else on disk, is never written."""
    _seed(env)
    ha_dir = env.write_ha()
    neighbour = tmp_path / "other"
    neighbour.mkdir()
    (neighbour / "unrelated.db").write_bytes(b"not the hub's store")
    ha_before, other_before = _tree(ha_dir), _tree(neighbour)
    store_files_before = set(os.listdir(env.dir))
    top_before = set(os.listdir(tmp_path))
    plan = prune.build_plan(prune.snapshot_from_runtime(env.rt, ha_dir), CUTOFF, drop_history=True)
    await prune.apply_plan(env.rt, plan_id=plan["plan_id"], cutoff=plan["cutoff"], drop_history=True,
                           import_dir=ha_dir, now=NOW)
    assert _tree(ha_dir) == ha_before
    assert _tree(neighbour) == other_before
    assert set(os.listdir(tmp_path)) == top_before
    assert set(os.listdir(env.dir)) <= store_files_before | {"hub.db-journal"}


def test_offline_snapshot_reads_a_copy_read_only_and_never_opens_credentials(env, tmp_path):
    _seed(env)
    env.store._exec("PRAGMA wal_checkpoint(TRUNCATE)")
    env.rt.statistics._exec("PRAGMA wal_checkpoint(TRUNCATE)")
    ha_dir = env.write_ha()
    live = prune.build_plan(prune.snapshot_from_runtime(env.rt, ha_dir), CUTOFF)
    hub_db, stats_db = env.store.path, env.rt.statistics.path
    before = _tree(env.dir)
    snap = prune.snapshot_from_files(hub_db, stats_db, ha_dir, loaded_entries=["e-live"])
    assert all(e.data == {} for e in snap.entries.values())
    offline = prune.build_plan(snap, CUTOFF)
    assert offline["plan_id"] == live["plan_id"]
    assert {k: v[0] for k, v in _tree(env.dir).items() if k.endswith(".db")} == \
        {k: v[0] for k, v in before.items() if k.endswith(".db")}
    with pytest.raises(prune.PruneError):
        prune.snapshot_from_files(str(tmp_path / "missing.db"), None, None, [])


def test_registry_times_reader_keeps_only_ids_and_timestamps(env):
    env.entry("e1", "wiz")
    times = prune.ha_registry_times(env.write_ha())
    assert times["entry"] == {"e1": OLD}
    assert "never-read" not in json.dumps(times)
    assert prune.ha_registry_times(str(env.ha / "nope")) is None
    assert env.plan()["registry_times"] == "ha_import"


# -- the routes -------------------------------------------------------------------------------

def test_routes_are_admin_only_and_apply_what_the_dry_run_returned(env, monkeypatch):
    _seed(env)
    monkeypatch.setattr("services.homehub.prune_api.IMPORT_DIR", str(env.ha.parent / "nowhere"))
    real_time = prune.time.time
    monkeypatch.setattr(prune.time, "time", lambda: NOW)
    try:
        c = TestClient(build_app(env.rt, Auth(env.store, SECRET)))
        assert c.get("/hub/prune/plan").status_code == 401
        assert c.post("/hub/prune/apply", json={}).status_code == 401
        assert c.get("/hub/prune/plan?days=7", headers=H).status_code == 400
        assert c.get("/hub/prune/plan?days=abc", headers=H).status_code == 400
        r = c.get("/hub/prune/plan?days=365", headers=H)
        assert r.status_code == 200
        plan = r.json()
        assert plan["days"] == 365 and plan["registry_times"] == "not available" and plan["items"]
        assert c.post("/hub/prune/apply", headers=H, json={"plan_id": "wrong", "cutoff": plan["cutoff"]}
                      ).status_code == 409
        assert c.post("/hub/prune/apply", headers=H, json=[1]).status_code == 400
        r = c.post("/hub/prune/apply", headers=H, json={"plan_id": plan["plan_id"], "cutoff": plan["cutoff"]})
        assert r.status_code == 200 and r.json()["status"] == "applied"
        assert c.get("/hub/prune/plan", headers=H).json()["items"] == []
    finally:
        monkeypatch.setattr(prune.time, "time", real_time)


def test_cutoff_is_days_before_now():
    assert prune.cutoff_for(365, now=NOW) == CUTOFF
    assert dt(prune.cutoff_for(400, now=NOW)) == dt(NOW) - timedelta(days=400)


async def test_live_engine_items_are_planned_and_removed_through_the_editor(env):
    from services.homehub import automation_api
    from services.homehub.automation import AutomationEngine

    env.entity("light.gone_soon", disabled=True)
    env.entity("light.kept", state=("on", RECENT))
    env.entity("automation.auto_a_old", platform="automation", uid="a-old")
    env.automation("a-old", {"trigger": [{"platform": "state", "entity_id": "light.gone_soon"}],
                             "action": [{"service": "light.turn_off", "entity_id": "light.gone_soon"}]},
                   enabled=False, last=OLD)
    env.automation("a-on", {"trigger": [{"platform": "state", "entity_id": "light.kept"}],
                            "action": [{"service": "light.turn_off", "entity_id": "light.kept"}]}, enabled=True)
    engine = AutomationEngine(env.hub, env.store)
    automation_api.prepare(engine, env.store)
    await engine.load(env.store.load_automations("automation"), {}, [], None)
    env.rt.automations = engine
    assert engine.automations["a-old"].enabled is False
    plan = env.plan()
    assert ids(plan, "automation") == {"a-old"} and "light.gone_soon" in ids(plan, "entity")
    await prune.apply_plan(env.rt, plan_id=plan["plan_id"], cutoff=plan["cutoff"], import_dir=str(env.ha), now=NOW)
    assert set(engine.automations) == {"a-on"}
    assert "automation.auto_a_old" not in env.hub.entities
    assert [d["id"] for d in env.store.load_automations("automation")] == ["a-on"]
