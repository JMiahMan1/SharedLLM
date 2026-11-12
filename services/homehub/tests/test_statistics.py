"""Long-term statistics: compiling (HA's semantics), reading, importing HA's recorder, the WS/REST API."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import energy
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Area, DeviceEntry, EntityEntry, Hub
from services.homehub.runtime import Runtime
from services.homehub.statistics import Statistics, Target, convert, targets_from_states
from services.homehub.store import Store

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
T0 = datetime(2026, 3, 2, 7, 0, tzinfo=UTC).timestamp()  # midnight in a UTC-7 zone, on the hour


def attrs(**kw) -> str:
    return json.dumps(kw)


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    stats = Statistics(str(tmp_path / "statistics.db"), store.path, time_zone="America/Phoenix")
    yield store, stats
    stats.close()
    store.close()


def history(store: Store, entity_id: str, points: list[tuple[float, object, dict]]) -> None:
    store.import_history_rows([(entity_id, str(v), json.dumps(a), ts, ts) for ts, v, a in points])


def rows(stats: Statistics, table: str, sid: str) -> list[dict]:
    meta = stats.get_meta(sid)
    return stats._rows(table, meta["id"], 0, None) if meta else []


# -- compiling ----------------------------------------------------------------------
def test_measurement_is_time_weighted_and_includes_the_state_at_the_start(env):
    store, stats = env
    a = {"state_class": "measurement", "unit_of_measurement": "W"}
    history(store, "sensor.power", [(T0 - 100, 1000, a), (T0 + 60, 2000, a), (T0 + 240, 500, a)])
    stats.compile(T0, T0 + 300, [Target("sensor.power", "measurement", "W")])
    (r,) = rows(stats, "statistics_short_term", "sensor.power")
    assert r["mean"] == pytest.approx((1000 * 60 + 2000 * 180 + 500 * 60) / 300)
    assert (r["min"], r["max"]) == (500, 2000)
    assert r["sum"] is None


def test_measurement_carries_an_unchanged_value_through_quiet_periods(env):
    store, stats = env
    a = {"state_class": "measurement", "unit_of_measurement": "°F"}
    history(store, "sensor.temp", [(T0 - 3000, 70, a)])
    stats.compile(T0, T0 + 3600, [Target("sensor.temp", "measurement", "°F")])
    short = rows(stats, "statistics_short_term", "sensor.temp")
    assert len(short) == 12 and all(r["mean"] == 70 for r in short)
    (hour,) = rows(stats, "statistics", "sensor.temp")
    assert (hour["mean"], hour["min"], hour["max"]) == (70, 70, 70)


def test_unavailable_states_are_skipped(env):
    store, stats = env
    a = {"state_class": "measurement", "unit_of_measurement": "W"}
    history(store, "sensor.p", [(T0, 100, a), (T0 + 150, "unavailable", a)])
    stats.compile(T0, T0 + 300, [Target("sensor.p", "measurement", "W")])
    (r,) = rows(stats, "statistics_short_term", "sensor.p")
    assert r["mean"] == 100


def test_kw_states_are_stored_in_the_statistics_unit(env):
    store, stats = env
    stats.upsert_meta("sensor.p", source="recorder", unit="W", has_sum=False, mean_type=1)
    history(store, "sensor.p", [(T0, 1.5, {"state_class": "measurement", "unit_of_measurement": "kW"})])
    stats.compile(T0, T0 + 300, [Target("sensor.p", "measurement", "kW")])
    assert rows(stats, "statistics_short_term", "sensor.p")[0]["mean"] == 1500


def test_total_with_daily_last_reset_sums_across_cycles(env):
    store, stats = env

    def a(day):
        return {"state_class": "total", "unit_of_measurement": "kWh",
                "last_reset": datetime.fromtimestamp(T0 + day * 86400, UTC).isoformat()}

    history(store, "sensor.day", [
        (T0 + 10, 1.0, a(0)), (T0 + 400, 3.0, a(0)), (T0 + 700, 5.0, a(0)),
        # midnight: the meter starts again from 0 with a new last_reset
        (T0 + 86400 + 10, 0.5, a(1)), (T0 + 86400 + 400, 2.0, a(1)),
    ])
    t = [Target("sensor.day", "total", "kWh")]
    stats.compile(T0, T0 + 900, t)
    stats.compile(T0 + 86400, T0 + 86400 + 600, t)
    short = rows(stats, "statistics_short_term", "sensor.day")
    sums = [r["sum"] for r in short]
    assert sums[:3] == [0.0, 2.0, 4.0]  # first sample is the baseline, then growth
    # 4.0 so far + 0.5 at the new cycle's first reading + 1.5 more
    assert sums[-2:] == [4.5, 6.0]
    assert short[-1]["last_reset"] == pytest.approx(T0 + 86400)


def test_total_increasing_meter_reset_keeps_counting(env):
    store, stats = env
    a = {"state_class": "total_increasing", "unit_of_measurement": "kWh"}
    history(store, "sensor.meter", [(T0, 100.0, a), (T0 + 310, 110.0, a), (T0 + 620, 2.0, a), (T0 + 920, 5.0, a)])
    stats.compile(T0, T0 + 1200, [Target("sensor.meter", "total_increasing", "kWh")])
    sums = [r["sum"] for r in rows(stats, "statistics_short_term", "sensor.meter")]
    assert sums == [0.0, 10.0, 12.0, 15.0]


def test_a_small_dip_is_not_a_reset(env):
    store, stats = env
    a = {"state_class": "total_increasing", "unit_of_measurement": "kWh"}
    history(store, "sensor.meter", [(T0, 100.0, a), (T0 + 310, 95.0, a), (T0 + 620, 101.0, a)])
    stats.compile(T0, T0 + 900, [Target("sensor.meter", "total_increasing", "kWh")])
    sums = [r["sum"] for r in rows(stats, "statistics_short_term", "sensor.meter")]
    assert sums == [0.0, -5.0, 1.0]


def test_leaving_a_future_last_reset_is_not_a_new_cycle(env):
    store, stats = env
    meta = stats.upsert_meta("sensor.month", source="recorder", unit="kWh", has_sum=True, mean_type=0)
    future = T0 + 20 * 86400
    stats._exec("INSERT INTO statistics_short_term VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, 1)",
                (meta["id"], T0 - 300, future, 2000.0, 50000.0))
    past = datetime.fromtimestamp(T0 - 10 * 86400, UTC).isoformat()
    history(store, "sensor.month", [(T0 + 5, 2003.0, {"state_class": "total", "unit_of_measurement": "kWh",
                                                      "last_reset": past})])
    stats.compile(T0, T0 + 300, [Target("sensor.month", "total", "kWh")])
    assert rows(stats, "statistics_short_term", "sensor.month")[-1]["sum"] == pytest.approx(50003.0)


def test_compiling_twice_writes_nothing_new(env):
    store, stats = env
    a = {"state_class": "measurement", "unit_of_measurement": "W"}
    history(store, "sensor.p", [(T0, 1, a)])
    t = [Target("sensor.p", "measurement", "W")]
    assert stats.compile(T0, T0 + 600, t)["short_term"] == 2
    assert stats.compile(T0, T0 + 600, t)["short_term"] == 0


def test_hourly_rows_come_from_the_short_term_rows(env):
    store, stats = env
    a = {"state_class": "total_increasing", "unit_of_measurement": "kWh"}
    history(store, "sensor.m", [(T0 + i * 300 + 1, float(i), a) for i in range(24)])
    stats.compile(T0, T0 + 7200, [Target("sensor.m", "total_increasing", "kWh")])
    hours = rows(stats, "statistics", "sensor.m")
    assert [h["start"] for h in hours] == [T0, T0 + 3600]
    assert [h["state"] for h in hours] == [11.0, 23.0]
    assert [h["sum"] for h in hours] == [11.0, 23.0]


def test_targets_are_the_states_with_a_state_class():
    hub = Hub()
    hub.states.set("sensor.a", "1", {"state_class": "measurement", "unit_of_measurement": "W"})
    hub.states.set("sensor.b", "1", {})
    hub.states.set("sensor.c", "1", {"state_class": "total"})
    assert [t.entity_id for t in targets_from_states(hub.states.all())] == ["sensor.a", "sensor.c"]
    hub.entities["sensor.b"] = EntityEntry(entity_id="sensor.b", platform="x",
                                           capabilities={"state_class": "measurement"})
    assert [t.entity_id for t in targets_from_states(hub.states.all(), hub.entities)] == \
        ["sensor.a", "sensor.b", "sensor.c"]


# -- reading ------------------------------------------------------------------------------
def _hourly_sums(stats: Statistics, sid: str, start: float, amounts: list[float], unit: str = "kWh") -> None:
    stats.add_hourly_amounts(sid, source="test", unit=unit, name=None,
                             amounts=[(datetime.fromtimestamp(start + i * 3600, UTC), a) for i, a in enumerate(amounts)])


def test_during_period_days_in_the_local_zone_with_change(env):
    _store, stats = env
    _hourly_sums(stats, "test:usage", T0 - 3600, [1.0] * 49)  # one hour before local midnight, then 2 days
    out = stats.during_period(datetime.fromtimestamp(T0, UTC), datetime.fromtimestamp(T0 + 2 * 86400, UTC),
                              ["test:usage"], "day", {"change", "sum"})
    days = out["test:usage"]
    assert [d["change"] for d in days] == [24.0, 24.0]
    assert days[0]["start"] == T0 * 1000 and days[0]["end"] == (T0 + 86400) * 1000
    assert days[1]["sum"] == 49.0


def test_during_period_converts_units_and_omits_types_not_asked(env):
    _store, stats = env
    _hourly_sums(stats, "test:wh", T0, [500.0, 1500.0], unit="Wh")
    out = stats.during_period(datetime.fromtimestamp(T0, UTC), None, ["test:wh"], "hour", ["change"],
                              {"energy": "kWh"})
    assert [r["change"] for r in out["test:wh"]] == [0.5, 1.5]
    assert set(out["test:wh"][0]) == {"start", "end", "change"}


def test_external_amounts_are_idempotent(env):
    _store, stats = env
    _hourly_sums(stats, "test:x", T0, [1.0, 2.0])
    _hourly_sums(stats, "test:x", T0, [1.0, 2.0, 3.0])
    assert [r["sum"] for r in rows(stats, "statistics", "test:x")] == [1.0, 3.0, 6.0]
    with pytest.raises(ValueError):
        stats.add_hourly_amounts("no_colon", source="x", unit="kWh", name=None, amounts=[])


def test_temperature_conversion():
    assert convert(212.0, "°F", "°C") == pytest.approx(100.0)
    assert convert(9.0, "°F", "°C", delta=True) == pytest.approx(5.0)
    assert convert(2.0, "kW", "W") == 2000.0


# -- importing HA's recorder ------------------------------------------------------------------
def make_ha_db(path, *, legacy: bool = False) -> None:
    db = sqlite3.connect(path)
    mean_col = "has_mean BOOLEAN" if legacy else "has_mean BOOLEAN, mean_type INTEGER NOT NULL DEFAULT 0"
    db.executescript(f"""
        CREATE TABLE statistics_meta (id INTEGER PRIMARY KEY, statistic_id VARCHAR(255), source VARCHAR(32),
            unit_of_measurement VARCHAR(255), {mean_col}, has_sum BOOLEAN, name VARCHAR(255));
        CREATE TABLE statistics (id INTEGER PRIMARY KEY, created_ts FLOAT, metadata_id INTEGER, start_ts FLOAT,
            mean FLOAT, min FLOAT, max FLOAT, last_reset_ts FLOAT, state FLOAT, sum FLOAT, mean_weight FLOAT);
        CREATE TABLE statistics_short_term (id INTEGER PRIMARY KEY, created_ts FLOAT, metadata_id INTEGER,
            start_ts FLOAT, mean FLOAT, min FLOAT, max FLOAT, last_reset_ts FLOAT, state FLOAT, sum FLOAT,
            mean_weight FLOAT);
    """)
    if legacy:
        db.execute("INSERT INTO statistics_meta VALUES (1, 'sensor.house_energy', 'recorder', 'kWh', 0, 1, NULL)")
        db.execute("INSERT INTO statistics_meta VALUES (2, 'sensor.house_power', 'recorder', 'W', 1, 0, NULL)")
    else:
        db.execute("INSERT INTO statistics_meta VALUES (1, 'sensor.house_energy', 'recorder', 'kWh', NULL, 0, 1, NULL)")
        db.execute("INSERT INTO statistics_meta VALUES (2, 'sensor.house_power', 'recorder', 'W', NULL, 1, 0, NULL)")
        db.execute("INSERT INTO statistics_meta VALUES (3, 'utility:acct_energy_consumption', 'utility', 'kWh', "
                   "NULL, 0, 1, 'Utility')")
    for i in range(3):
        db.execute("INSERT INTO statistics(metadata_id, start_ts, state, sum) VALUES (1, ?, ?, ?)",
                   (T0 + i * 3600, 10.0 + i, 100.0 + i))
        db.execute("INSERT INTO statistics(metadata_id, start_ts, mean, min, max) VALUES (2, ?, 500, 100, 900)",
                   (T0 + i * 3600,))
    db.execute("INSERT INTO statistics_short_term(metadata_id, start_ts, state, sum) VALUES (1, ?, 12.0, 102.0)",
               (T0 + 2 * 3600 + 3300,))
    db.commit()
    db.close()


def test_import_copies_ha_rows_and_is_repeatable(env, tmp_path):
    _store, stats = env
    make_ha_db(tmp_path / "ha.db")
    report = stats.import_ha(str(tmp_path / "ha.db"))
    assert report["metadata"] == 3
    assert report["statistics"]["statistics"]["rows"] == 6
    again = stats.import_ha(str(tmp_path / "ha.db"))
    assert again["statistics"]["statistics"]["rows"] == 6
    assert len(rows(stats, "statistics", "sensor.house_energy")) == 3
    meta = {m["statistic_id"]: m for m in stats.list_statistic_ids()}
    assert meta["sensor.house_power"]["has_mean"] and not meta["sensor.house_power"]["has_sum"]
    assert meta["sensor.house_energy"]["has_sum"] and meta["sensor.house_energy"]["unit_class"] == "energy"
    assert meta["utility:acct_energy_consumption"]["source"] == "utility"


def test_import_reads_a_legacy_has_mean_schema(env, tmp_path):
    _store, stats = env
    make_ha_db(tmp_path / "old.db", legacy=True)
    stats.import_ha(str(tmp_path / "old.db"))
    assert stats.get_meta("sensor.house_power")["mean_type"] == 1


def test_compiling_continues_ha_sums_after_an_import(env, tmp_path):
    store, stats = env
    make_ha_db(tmp_path / "ha.db")
    stats.import_ha(str(tmp_path / "ha.db"))
    a = {"state_class": "total_increasing", "unit_of_measurement": "kWh"}
    start = T0 + 3 * 3600
    history(store, "sensor.house_energy", [(start + 10, 12.5, a), (start + 310, 13.0, a)])
    stats.compile(start, start + 600, [Target("sensor.house_energy", "total_increasing", "kWh")])
    short = rows(stats, "statistics_short_term", "sensor.house_energy")
    assert [r["sum"] for r in short[-2:]] == [102.5, 103.0]


def test_an_import_after_compiling_rebases_the_hubs_sums(env, tmp_path):
    store, stats = env
    a = {"state_class": "total_increasing", "unit_of_measurement": "kWh"}
    history(store, "sensor.house_energy", [(T0 + 3 * 3600 + 10, 12.5, a), (T0 + 3 * 3600 + 310, 13.0, a)])
    t = [Target("sensor.house_energy", "total_increasing", "kWh")]
    stats.compile(T0 + 3 * 3600, T0 + 3 * 3600 + 600, t)
    assert [r["sum"] for r in rows(stats, "statistics_short_term", "sensor.house_energy")] == [0.0, 0.5]
    make_ha_db(tmp_path / "ha.db")
    stats.import_ha(str(tmp_path / "ha.db"))
    short = rows(stats, "statistics_short_term", "sensor.house_energy")
    assert [r["sum"] for r in short] == [102.0, 102.5, 103.0]
    stats.import_ha(str(tmp_path / "ha.db"))  # repeatable: no second shift
    assert [r["sum"] for r in rows(stats, "statistics_short_term", "sensor.house_energy")] == [102.0, 102.5, 103.0]


# -- API ----------------------------------------------------------------------------------------
@pytest.fixture
def api(env, tmp_path, monkeypatch):
    store, stats = env
    hub = Hub(time_zone="America/Phoenix")
    hub.store = store
    runtime = Runtime(hub, store)
    runtime.statistics = stats
    hub.areas["den"] = Area("den", "Den")
    hub.devices["meter"] = DeviceEntry(id="meter", name="Main panel", area_id="den")
    for eid, dc in (("sensor.mains_energy", "energy"), ("sensor.mains_power", "power")):
        hub.entities[eid] = EntityEntry(entity_id=eid, platform="test", device_id="meter", original_device_class=dc)
    hub.states.set("sensor.mains_power", "2.5", {"unit_of_measurement": "kW", "device_class": "power"})
    import services.homehub.statistics_api as sapi

    monkeypatch.setattr(sapi, "IMPORT_DIR", str(tmp_path))
    app = build_app(runtime, Auth(store, SECRET))
    return TestClient(app), runtime, stats


def test_ws_recorder_commands(api):
    client, _runtime, stats = api
    _hourly_sums(stats, "test:usage", T0, [1.0, 2.0])
    with client.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": SECRET})
        ws.receive_json()
        ws.send_json({"id": 1, "type": "recorder/statistics_during_period", "statistic_ids": ["test:usage"],
                      "start_time": datetime.fromtimestamp(T0, UTC).isoformat(), "period": "hour",
                      "types": ["change", "sum"]})
        res = ws.receive_json()
        assert res["success"], res
        assert [r["change"] for r in res["result"]["test:usage"]] == [1.0, 2.0]
        ws.send_json({"id": 2, "type": "recorder/list_statistic_ids", "statistic_type": "sum"})
        res = ws.receive_json()
        assert res["result"][0]["statistic_id"] == "test:usage" and res["result"][0]["has_sum"]
        ws.send_json({"id": 3, "type": "recorder/get_statistics_metadata", "statistic_ids": ["test:usage"]})
        assert ws.receive_json()["result"][0]["statistics_unit_of_measurement"] == "kWh"
        ws.send_json({"id": 4, "type": "recorder/statistics_during_period", "statistic_ids": ["test:usage"],
                      "start_time": "2026-01-01T00:00:00Z", "period": "fortnight"})
        assert not ws.receive_json()["success"]
        ws.send_json({"id": 5, "type": "energy/get_prefs"})
        assert ws.receive_json()["result"]["energy_sources"] == []


def test_rest_statistics_and_import(api, tmp_path):
    client, _runtime, _stats = api
    ha = tmp_path / "homeassistant"
    (ha / ".storage").mkdir(parents=True)
    make_ha_db(ha / "home-assistant_v2.db")
    (ha / ".storage" / "energy").write_text(json.dumps({"data": {
        "energy_sources": [{"type": "grid", "stat_energy_from": "sensor.house_energy", "number_energy_price": 0.1}],
        "device_consumption": [{"stat_consumption": "sensor.mains_energy", "name": "Panel"}],
        "device_consumption_water": []}}))
    assert client.post("/hub/statistics/import", json={}).status_code == 401
    r = client.post("/hub/statistics/import", headers=H, json={})
    assert r.status_code == 200, r.text
    assert r.json()["energy"]["energy_sources"] == 1 and r.json()["energy"]["utility_sources"] == 1
    prefs = client.get("/api/homehub/energy/prefs", headers=H).json()
    assert prefs["tariff"]["flat_price"] == 0.1
    assert prefs["utility_sources"][0]["stat_energy"] == "utility:acct_energy_consumption"
    r = client.get("/api/homehub/statistics", headers=H, params={
        "statistic_ids": "sensor.house_energy", "start": datetime.fromtimestamp(T0, UTC).isoformat(),
        "period": "hour", "types": "change"})
    assert [x["change"] for x in r.json()["sensor.house_energy"]] == [100.0, 1.0, 1.0]
    assert client.post("/hub/statistics/import", headers=H, json={"path": "/etc"}).status_code == 400
    status = client.get("/hub/statistics/status", headers=H).json()
    assert status["statistics"]["rows"] == 6


def test_energy_summary_and_prefs_validation(api):
    client, _runtime, stats = api
    _hourly_sums(stats, "grid:meter_energy_consumption", T0, [1.0] * 24)
    _hourly_sums(stats, "grid:meter_energy_consumption_on_peak", T0, [0.0] * 14 + [1.0] * 6 + [0.0] * 4)
    body = {"energy_sources": [{"type": "grid", "stat_energy_from": "grid:meter_energy_consumption"}],
            "tariff": {"currency": "USD", "periods": [{"name": "On-peak", "price": 0.2, "start": "14:00",
                                                        "end": "20:00", "weekdays": [0, 1, 2, 3, 4]}],
                       "default_period": {"name": "Off-peak", "price": 0.1}}}
    r = client.put("/hub/energy/prefs", headers=H, json=body)
    assert r.status_code == 200, r.text
    bad = client.put("/hub/energy/prefs", headers=H, json={"tariff": {"periods": [{"name": "x", "start": "25:00"}]}})
    assert bad.status_code == 400
    s = client.get("/api/homehub/energy/summary", headers=H,
                   params={"range": "day", "date": datetime.fromtimestamp(T0 + 3600, UTC).isoformat()}).json()
    assert s["grid"]["kwh"] == 24.0
    tou = {p["name"]: p for p in s["tou"]["periods"]}
    assert tou["On-peak"]["kwh"] == 6.0 and tou["Off-peak"]["kwh"] == 18.0  # 2026-03-02 is a Monday
    assert s["grid"]["cost"] == pytest.approx(6 * 0.2 + 18 * 0.1) and s["grid"]["cost_source"] == "tariff"
    cands = client.get("/api/homehub/energy/candidates", headers=H).json()
    util = next(c for c in cands if c["kind"] == "utility")
    assert util["stat_periods"] == {"on_peak": "grid:meter_energy_consumption_on_peak"}


def test_device_rows_show_the_live_power_of_the_same_device(api):
    client, _runtime, stats = api
    meta = stats.upsert_meta("sensor.mains_energy", source="recorder", unit="kWh", has_sum=True, mean_type=0)
    for i, total in enumerate((2.0, 4.0)):
        stats._exec("INSERT INTO statistics VALUES (?, ?, NULL, NULL, NULL, NULL, ?, ?, 0)",
                    (meta["id"], T0 + i * 3600, total, total))
    client.put("/hub/energy/prefs", headers=H, json={"device_consumption": [{"stat_consumption": "sensor.mains_energy"}]})
    s = client.get("/api/homehub/energy/summary", headers=H, params={
        "start": datetime.fromtimestamp(T0, UTC).isoformat(),
        "end": datetime.fromtimestamp(T0 + 7200, UTC).isoformat(), "period": "hour"}).json()
    (dev,) = s["devices"]
    assert dev["kwh"] == 4.0 and dev["power_entity"] == "sensor.mains_power" and dev["power_w"] == 2500.0


def test_suggest_tou_reads_the_utilitys_on_peak_hours(env):
    _store, stats = env
    start = datetime(2026, 7, 6, 7, tzinfo=UTC).timestamp()  # Monday midnight, UTC-7
    total, on = [], []
    for day in range(14):
        weekday = day % 7 < 5
        for hour in range(24):
            total.append(1.0)
            on.append(1.0 if weekday and 14 <= hour < 20 else 0.0)
    _hourly_sums(stats, "u:a_energy_consumption", start, total)
    _hourly_sums(stats, "u:a_energy_consumption_on_peak", start, on)
    out = energy.suggest_tou(stats, "u:a_energy_consumption_on_peak", "u:a_energy_consumption",
                             now=datetime.fromtimestamp(start + 15 * 86400, UTC))
    assert out["periods"] == [{"name": "On-peak", "price": None, "months": [7], "weekdays": [0, 1, 2, 3, 4],
                               "start": "14:00", "end": "20:00"}]


def test_period_for_wraps_midnight():
    tariff = {"periods": [{"name": "Night", "months": [], "weekdays": [], "start": "22:00", "end": "06:00"}],
              "default_period": {"name": "Day"}}
    assert energy.period_for(tariff, datetime(2026, 1, 1, 23)).get("name") == "Night"
    assert energy.period_for(tariff, datetime(2026, 1, 1, 3)).get("name") == "Night"
    assert energy.period_for(tariff, datetime(2026, 1, 1, 12)).get("name") == "Day"


def test_range_for_month_and_year():
    tz = Statistics.__new__(Statistics)
    tz._tz = "America/Phoenix"
    start, end, period = energy.range_for("month", datetime(2026, 2, 10, tzinfo=UTC), tz.tz)
    assert (start.day, end.month, period) == (1, 3, "day")
    start, end, period = energy.range_for("year", datetime(2026, 2, 10, tzinfo=UTC), tz.tz)
    assert (start.month, end.year, period) == (1, 2027, "month")
    assert end - start == timedelta(days=365)


async def test_the_compiler_loop_compiles_and_remembers_where_it_got_to(env, monkeypatch):
    import asyncio
    import time as time_mod

    from services.homehub import statistics as st_mod

    store, stats = env
    now = T0 + 3600 + 30
    monkeypatch.setattr(st_mod.time, "time", lambda: now)
    monkeypatch.setattr(st_mod, "CATCHUP_DAYS", 1)
    hub = Hub()
    hub.states.set("sensor.p", "5", {"state_class": "measurement", "unit_of_measurement": "W"})
    history(store, "sensor.p", [(T0 - 10, 5, {"state_class": "measurement", "unit_of_measurement": "W"})])

    async def stop(_s):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await st_mod.run_compiler(stats, hub, sleep=stop)
    assert stats.get_kv("compiled_until") == T0 + 3600
    assert len(rows(stats, "statistics_short_term", "sensor.p")) == 13  # from the first reading
    assert time_mod  # the real module stays importable
