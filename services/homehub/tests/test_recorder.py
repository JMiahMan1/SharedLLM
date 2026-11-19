"""The recorder: unrecorded attributes, skipped rows, shared attributes, exclusions, purges, the size report and the
writer thread. Entity names and values are made up."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from services.homehub import recorder as rec_mod
from services.homehub.core import ConfigEntry, Driver, EntityEntry, Hub
from services.homehub.recorder import Recorder, validate_settings
from services.homehub.recorder_statistics import Statistics
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.test_core_api import SECRET, H, client, setup  # noqa: F401 (fixtures)

T0 = datetime(2020, 1, 1, tzinfo=UTC)


def history_rows(store: Store, eid: str) -> list[dict]:
    return store.history([eid], T0)


def raw_count(store: Store, table: str = "history") -> int:
    return store._exec(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
def hub_store(tmp_path):
    store = Store(str(tmp_path / "hub.db"))
    hub = Hub()
    hub.store = store
    yield hub, store
    store.close()


def recorder_for(hub: Hub, store: Store, **kw) -> Recorder:
    rec = Recorder(hub, store, **kw)
    rec.start()
    return rec


# -- unrecorded attributes and skipped rows -------------------------------------------------------------------------

def test_a_camera_token_change_alone_writes_no_row_and_the_live_state_keeps_it(hub_store):
    hub, store = hub_store
    rec = recorder_for(hub, store, threaded=False)
    hub.states.set("camera.porch", "idle", {"access_token": "t1", "entity_picture": "/p?t1", "friendly_name": "Porch"})
    for tok in ("t2", "t3", "t4"):
        hub.states.set("camera.porch", "idle", {"access_token": tok, "entity_picture": f"/p?{tok}",
                                                 "friendly_name": "Porch"})
    rows = history_rows(store, "camera.porch")
    assert len(rows) == 1 and rows[0]["attributes"] == {"friendly_name": "Porch"}
    assert rec.skipped == 3
    # Live and restore states are never trimmed.
    assert hub.states.get("camera.porch").attributes["access_token"] == "t4"
    restored = {s.entity_id: s for s in store.load_last_states()}
    assert restored["camera.porch"].attributes["access_token"] == "t4"
    # A real change is still a row.
    hub.states.set("camera.porch", "recording", {"access_token": "t5", "friendly_name": "Porch"})
    assert [r["state"] for r in history_rows(store, "camera.porch")] == ["idle", "recording"]


def test_sun_position_and_player_position_are_not_history(hub_store):
    hub, store = hub_store
    recorder_for(hub, store, threaded=False)
    for i in range(5):
        hub.states.set("sun.sun", "above_horizon", {"azimuth": 100 + i, "elevation": 20 + i, "rising": True,
                                                    "next_rising": f"t{i}", "next_setting": f"s{i}",
                                                    "friendly_name": "Sun"})
        hub.states.set("media_player.den", "playing", {"media_position": i * 10, "media_position_updated_at": str(i),
                                                       "source_list": ["A", "B"], "volume_level": 0.3})
    assert len(history_rows(store, "sun.sun")) == 1
    assert history_rows(store, "sun.sun")[0]["attributes"] == {"friendly_name": "Sun"}
    assert len(history_rows(store, "media_player.den")) == 1
    hub.states.set("media_player.den", "playing", {"media_position": 99, "volume_level": 0.5})
    vols = [r["attributes"].get("volume_level") for r in history_rows(store, "media_player.den")]
    assert vols == [0.3, 0.5]  # a recorded attribute changing is still a row


def test_supported_features_is_never_recorded_like_ha(hub_store):
    hub, store = hub_store
    recorder_for(hub, store, threaded=False)
    hub.states.set("light.lamp", "on", {"supported_features": 44, "brightness": 10})
    assert history_rows(store, "light.lamp")[0]["attributes"] == {"brightness": 10}


def test_a_driver_adds_its_own_unrecorded_attributes(hub_store):
    hub, store = hub_store

    class Chatty(Driver):
        platform = "chatty"
        unrecorded_attributes = frozenset({"rssi"})

    hub.config_entries["c1"] = entry = ConfigEntry("c1", "chatty", "Chatty")
    hub.entities["sensor.chatty_temp"] = EntityEntry(entity_id="sensor.chatty_temp", platform="chatty",
                                                     unique_id="t", config_entry_id="c1")
    hub.drivers["c1"] = Chatty(hub, entry)
    recorder_for(hub, store, threaded=False)
    for rssi in (-50, -51, -52):
        hub.states.set("sensor.chatty_temp", "21.5", {"rssi": rssi, "unit_of_measurement": "°C"})
    rows = history_rows(store, "sensor.chatty_temp")
    assert len(rows) == 1 and rows[0]["attributes"] == {"unit_of_measurement": "°C"}
    # Another integration's sensor keeps the attribute.
    hub.states.set("sensor.other", "1", {"rssi": -40})
    assert history_rows(store, "sensor.other")[0]["attributes"] == {"rssi": -40}


# -- shared attributes ------------------------------------------------------------------------------------------------

def test_equal_attributes_are_stored_once(hub_store):
    hub, store = hub_store
    recorder_for(hub, store, threaded=False)
    attrs = {"unit_of_measurement": "W", "friendly_name": "Draw"}
    for i in range(20):
        hub.states.set("sensor.draw", str(i), dict(reversed(list(attrs.items()))) if i % 2 else attrs)
    assert raw_count(store) == 20
    assert raw_count(store, "state_attributes") == 1
    assert store._exec("SELECT COUNT(*) FROM history WHERE attributes != ''").fetchone()[0] == 0


def _old_schema_db(path: str) -> None:
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL, state TEXT NOT NULL,
        attributes TEXT NOT NULL, last_changed REAL NOT NULL, last_updated REAL NOT NULL,
        state_changed INTEGER NOT NULL DEFAULT 1);
    CREATE INDEX history_entity_time ON history(entity_id, last_updated);
    """)
    ts = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    db.execute("INSERT INTO history(entity_id, state, attributes, last_changed, last_updated) VALUES (?,?,?,?,?)",
               ("sensor.draw", "5", json.dumps({"unit_of_measurement": "W"}), ts, ts))
    db.commit()
    db.close()


def test_old_rows_stay_readable_and_read_like_new_ones(tmp_path):
    path = str(tmp_path / "old.db")
    _old_schema_db(path)
    store = Store(path)
    cols = {r[1] for r in store._exec("PRAGMA table_info(history)").fetchall()}
    assert "attributes_id" in cols
    hub = Hub()
    recorder_for(hub, store, threaded=False)
    hub.states.set("sensor.draw", "6", {"unit_of_measurement": "W"})
    old, new = store.history(["sensor.draw"], T0)
    assert set(old) == set(new) and old["attributes"] == new["attributes"] == {"unit_of_measurement": "W"}
    assert [old["state"], new["state"]] == ["5", "6"]
    at = store.states_at(["sensor.draw"], datetime.now(UTC) + timedelta(seconds=1))
    assert at["sensor.draw"]["attributes"] == {"unit_of_measurement": "W"}
    # The statistics compiler reads both through its own connection.
    stats = Statistics(str(tmp_path / "stats.db"), path)
    prior, rows = stats._history_rows("sensor.draw", 0, time.time() + 10)
    assert prior is None and [json.loads(r[2]) for r in rows] == [{"unit_of_measurement": "W"}] * 2
    store.close()


def test_rest_and_logbook_read_new_rows_like_before(client, setup):  # noqa: F811
    hub, _store, runtime, _d, _a = setup
    runtime.start_recording()
    hub.states.set("sensor.chatter", "1", {"friendly_name": "Chatter"})
    hub.states.set("sensor.chatter", "2", {"friendly_name": "Chatter"})
    hist = client.get("/api/history/period/2020-01-01T00:00:00Z?filter_entity_id=sensor.chatter", headers=H).json()
    assert [(r["state"], r["attributes"]) for r in hist[0]] == [("1", {"friendly_name": "Chatter"}),
                                                                ("2", {"friendly_name": "Chatter"})]
    log = client.get("/api/logbook/2020-01-01T00:00:00Z?entity=sensor.chatter", headers=H).json()
    assert [e["name"] for e in log] == ["Chatter", "Chatter"]


# -- exclusions and settings ------------------------------------------------------------------------------------------

def test_settings_are_validated():
    s = validate_settings({"keep_days": 10, "exclude": {"domains": ["Weather "], "entity_globs": ["sensor.*_rssi"],
                                                       "entities": ["sensor.noisy"]}})
    assert s == {"keep_days": 10, "exclude": {"domains": ["weather"], "entity_globs": ["sensor.*_rssi"],
                                              "entities": ["sensor.noisy"]}}
    for bad in ({"keep_days": 0}, {"keep_days": "7"}, {"keep_days": True}, {"nope": 1},
                {"exclude": {"domains": ["not a domain"]}}, {"exclude": {"entities": ["noperiod"]}},
                {"exclude": {"other": []}}, {"exclude": {"domains": "sensor"}}, []):
        with pytest.raises(ValueError):
            validate_settings(bad)
    # A partial change keeps the rest.
    assert validate_settings({"keep_days": 5}, s)["exclude"]["entities"] == ["sensor.noisy"]


def test_excluded_entities_keep_their_restore_state_but_no_history(hub_store):
    hub, store = hub_store
    store.set_kv(rec_mod.SETTINGS_KEY, {"keep_days": 30, "exclude": {
        "domains": ["weather"], "entity_globs": ["sensor.*_signal"], "entities": ["switch.noisy"]}})
    rec = recorder_for(hub, store, threaded=False)
    for eid in ("weather.home", "sensor.plug_signal", "switch.noisy", "sensor.plug_power"):
        hub.states.set(eid, "1", {"a": 1})
    assert {r["entity_id"] for r in store.history(None, T0)} == {"sensor.plug_power"}
    assert {s.entity_id for s in store.load_last_states()} >= {"weather.home", "sensor.plug_signal", "switch.noisy"}
    assert rec.excluded == 3


def _user_token(store: Store) -> str:
    store.set_kv("users", [{"id": "u2", "name": "Kid", "is_active": True, "group_ids": ["system-users"]}])
    store.save_refresh_token("rt2", {"user_id": "u2"}, "j" * 64)
    now = int(time.time())
    return jwt.encode({"iss": "rt2", "iat": now, "exp": now + 600}, "j" * 64, algorithm="HS256")


def test_the_settings_route_is_for_admins_and_applies_at_once(client, setup):  # noqa: F811
    hub, store, runtime, _d, _a = setup
    runtime.start_recording()
    user = {"Authorization": f"Bearer {_user_token(store)}"}
    assert client.get("/hub/recorder", headers=user).status_code == 401
    assert client.put("/hub/recorder", headers=user, json={"keep_days": 3}).status_code == 401
    got = client.get("/hub/recorder", headers=H).json()
    assert got["settings"]["keep_days"] == 30 and "access_token" in got["unrecorded_attributes"]["camera"]
    assert client.put("/hub/recorder", headers=H, json={"keep_days": 0}).status_code == 400
    r = client.put("/hub/recorder", headers=H, json={"keep_days": 7, "exclude": {"domains": ["sensor"]}})
    assert r.status_code == 200 and r.json()["settings"]["keep_days"] == 7
    assert store.get_kv(rec_mod.SETTINGS_KEY)["exclude"]["domains"] == ["sensor"]
    hub.states.set("sensor.after", "1")
    assert history_rows(store, "sensor.after") == []
    assert runtime.recorder.keep_days == 7


# -- purges -----------------------------------------------------------------------------------------------------------

def _aged(store: Store, eid: str, days_ago: float, attrs: dict | None = None) -> None:
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()
    from services.homehub.store import attrs_hash, canonical_attrs

    c = canonical_attrs(attrs or {"k": eid})
    with store._lock:
        store.write_history(store._db, [(eid, "x", c, attrs_hash(c), ts, ts, 1)])


def test_purge_keeps_the_retention_and_drops_orphaned_attributes(hub_store):
    hub, store = hub_store
    rec = recorder_for(hub, store)
    _aged(store, "sensor.old", 40)
    _aged(store, "sensor.new", 1)
    assert raw_count(store, "state_attributes") == 2
    n = asyncio.run(rec.purge(None, repack=True))
    assert n == 1
    assert [r["entity_id"] for r in store.history(None, T0)] == ["sensor.new"]
    assert raw_count(store, "state_attributes") == 1
    rec.stop()


def test_purge_entities_and_apply_filter(hub_store):
    hub, store = hub_store
    rec = recorder_for(hub, store)
    for eid in ("sensor.a_signal", "sensor.b_signal", "light.porch", "switch.x"):
        _aged(store, eid, 1)
    n = asyncio.run(rec.purge_entities(rec_mod.entity_matcher(["switch.x"], [], ["sensor.*_signal"])))
    assert n == 3 and [r["entity_id"] for r in store.history(None, T0)] == ["light.porch"]
    rec.apply_settings(validate_settings({"exclude": {"domains": ["light"]}}))
    assert asyncio.run(rec.purge(apply_filter=True)) == 1
    assert store.history(None, T0) == []
    rec.stop()


def test_purge_services_are_admin_only_over_the_api(client, setup):  # noqa: F811
    _hub, store, runtime, _d, _a = setup
    runtime.start_recording()
    _aged(store, "sensor.gone", 1)
    user = {"Authorization": f"Bearer {_user_token(store)}"}
    r = client.post("/api/services/recorder/purge_entities", headers=user, json={"entity_id": "sensor.gone"})
    assert r.status_code == 403
    assert len(history_rows(store, "sensor.gone")) == 1
    r = client.post("/api/services/recorder/purge_entities?return_response", headers=H,
                    json={"entity_id": "sensor.gone"})
    assert r.status_code == 200 and r.json()["service_response"] == {"purged_rows": 1}
    assert history_rows(store, "sensor.gone") == []
    r = client.post("/api/services/recorder/purge?return_response", headers=H, json={"keep_days": 5, "repack": True})
    assert r.status_code == 200 and r.json()["service_response"] == {"purged_rows": 0}
    assert client.post("/api/services/recorder/purge_entities", headers=H, json={}).status_code == 400


# -- size report ------------------------------------------------------------------------------------------------------

def test_size_report_and_health(client, setup):  # noqa: F811
    hub, store, runtime, _d, _a = setup
    runtime.start_recording()
    for i in range(5):
        hub.states.set("sensor.busy", str(i))
    hub.states.set("sensor.quiet", "1")
    _aged(store, "sensor.busy", 3)
    user = {"Authorization": f"Bearer {_user_token(store)}"}
    assert client.get("/hub/recorder/size", headers=user).status_code == 401
    rep = client.get("/hub/recorder/size", headers=H).json()
    assert rep["db_bytes"] > 0 and rep["history_rows"] >= 7 and rep["keep_days"] == 30
    top = {e["entity_id"]: e["rows"] for e in rep["top_entities_24h"]}
    assert top["sensor.busy"] == 5 and top["sensor.quiet"] == 1
    assert rep["top_entities_24h"][0]["entity_id"] == "sensor.busy"
    assert sum(d["rows"] for d in rep["rows_per_day"]) == rep["history_rows"]
    assert rep["state_attributes_rows"] >= 1 and rep["writer"]["threaded"] is True
    health = client.get("/health").json()
    assert health["recorder"]["db_bytes"] > 0 and health["recorder"]["dropped"] == 0


# -- the writer thread ------------------------------------------------------------------------------------------------

def test_the_writer_commits_in_batches_off_the_caller_and_flushes_at_stop(tmp_path):
    path = str(tmp_path / "w.db")
    store = Store(path)
    hub = Hub()
    rec = recorder_for(hub, store, commit_interval=60.0)  # nothing commits on its own during the test
    for i in range(10):
        hub.states.set("sensor.fast", str(i))
    other = sqlite3.connect(path)
    assert other.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0  # still queued
    assert len(store.history(["sensor.fast"], T0)) == 10  # a reader waits for what was queued before it
    for i in range(10, 15):
        hub.states.set("sensor.fast", str(i))
    rec.stop()  # shutdown commits the rest
    assert other.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 15
    assert other.execute("SELECT COUNT(*) FROM last_states").fetchone()[0] == 1
    assert rec.writer.batches == 2 and not rec.writer.alive
    other.close()
    store.close()


def test_a_full_queue_drops_and_counts_instead_of_blocking(hub_store):
    hub, store = hub_store
    rec = Recorder(hub, store, max_queue=3)  # never started: nothing drains the queue
    rec._unsub = hub.bus.subscribe(rec._on_change, "state_changed")
    started = time.monotonic()
    for i in range(10):
        hub.states.set("sensor.flood", str(i))
    assert time.monotonic() - started < 1.0
    assert rec.writer.dropped > 0 and rec.stats()["dropped"] == rec.writer.dropped


async def test_the_runtime_records_through_the_writer_and_shutdown_flushes(tmp_path):
    path = str(tmp_path / "rt.db")
    store = Store(path)
    hub = Hub()
    rt = Runtime(hub, store)
    rt.start_recording()
    assert rt.recorder.writer is not None and store.writer is rt.recorder.writer
    hub.states.set("input_boolean.flag", "on", {"editable": True, "friendly_name": "Flag"})
    await rt.shutdown()
    assert store.writer is None and rt.recorder is None
    db = sqlite3.connect(path)
    attrs = db.execute("SELECT shared_attrs FROM history h JOIN state_attributes a USING (attributes_id)").fetchall()
    assert [json.loads(a[0]) for a in attrs] == [{"friendly_name": "Flag"}]
    db.close()
    store.close()
