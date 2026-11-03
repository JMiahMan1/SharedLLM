"""Weather against any metric, and weather events for other services (docs/WEATHER.md)."""
import json

import pytest

from services.execution import weather_bus as bus
from services.execution import weather_metrics as m

DAY = 86400
OFFSET = -6 * 3600
# Local midnight (UTC-6) of 2026-11-02, plus ten past noon.
MIDNIGHT = 1793599200
NOON = MIDNIGHT + 12 * 3600 + 600


class FakeRedis:
    """Just enough of redis.asyncio for the day store and the stream."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.h: dict[str, dict] = {}
        self.stream: list[tuple[str, dict]] = []

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, **kw):
        self.kv[k] = str(v)

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def hmget(self, k, *fields):
        return [self.h.get(k, {}).get(f) for f in fields]

    async def hset(self, k, field=None, value=None, mapping=None):
        d = self.h.setdefault(k, {})
        if mapping:
            d.update({f: str(v) for f, v in mapping.items()})
        if field is not None:
            d[field] = str(value)

    async def hincrbyfloat(self, k, f, v):
        d = self.h.setdefault(k, {})
        d[f] = str(float(d.get(f, 0)) + v)
        return float(d[f])

    async def expire(self, k, s):
        return True

    async def xadd(self, name, fields, **kw):
        sid = f"{len(self.stream) + 1}-0"
        self.stream.append((sid, fields))
        return sid

    async def xrange(self, name, min="-", max="+", count=100):
        after = int(min[1:].split("-")[0]) if min.startswith("(") else 0
        return [(sid, f) for sid, f in self.stream if int(sid.split("-")[0]) > after][:count]

    def pipeline(self):
        return FakePipe(self)


class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def __getattr__(self, name):
        def op(*a, **kw):
            self.ops.append((name, a, kw))
            return self
        return op

    async def execute(self):
        return [await getattr(self.r, name)(*a, **kw) for name, a, kw in self.ops]


def fc(temp=40.0, precip=0.0, code=0, humidity=80):
    return {"utc_offset_seconds": OFFSET, "current": {"temp": temp, "precip": precip, "code": code, "humidity": humidity},
            "daily": [{"date": "2026-11-03", "high": 50, "low": 30}]}


# --- definitions -------------------------------------------------------------


def test_sensor_metrics_from_the_setting():
    defs = m.parse_ha_metrics(json.dumps([
        {"name": "solar_kwh", "label": "Solar", "entity_id": "sensor.solar_power", "mode": "power"},
        {"name": "water", "entity_id": "sensor.water_meter", "mode": "counter", "unit": "gal"},
        {"name": "pump", "entity_id": "switch.well_pump", "mode": "on_time"},
        {"name": "energy_kwh", "entity_id": "sensor.x", "mode": "power"},  # a built-in name
        {"name": "Bad Name", "entity_id": "sensor.x", "mode": "mean"},
        {"name": "barn", "entity_id": "nope", "mode": "mean"},
        {"name": "fog", "entity_id": "sensor.x", "mode": "vibes"},
    ]))
    assert [(d["name"], d["agg"], d["unit"]) for d in defs] == [
        ("solar_kwh", "sum", "kWh"), ("water", "delta", "gal"), ("pump", "sum", "h")]
    assert m.parse_ha_metrics("nope") == []


def test_sensor_values_by_mode():
    power = {"mode": "power"}
    assert m.sensor_value(power, {"state": "2000"}, 900) == (0.5, 900)
    assert m.sensor_value(power, {"state": "unavailable"}, 900) is None
    assert m.sensor_value({"mode": "on_time"}, {"state": "on"}, 1800) == (0.5, 1800)
    assert m.sensor_value({"mode": "on_time"}, {"state": "off"}, 1800) == (0.0, 1800)
    assert m.sensor_value({"mode": "counter"}, {"state": "1234.5"}, 900) == (1234.5, None)
    attr = {"mode": "mean", "attribute": "humidity"}
    assert m.sensor_value(attr, {"state": "x", "attributes": {"humidity": 61}}, 900) == (61.0, 900)
    assert m.sensor_value(power, None, 900) is None


def test_builtin_values_count_each_zone():
    climates = [
        {"attributes": {"hvac_action": "heating", "current_temperature": 68}},
        {"attributes": {"hvac_action": "Heating", "current_temperature": "70"}},
        {"attributes": {"hvac_action": "cooling"}},
    ]
    v = m.builtin_values(climates, 1200.0, 900)
    assert v["energy_kwh"] == (0.3, 900)
    assert v["heat_hours"] == (0.5, 900) and v["cool_hours"] == (0.25, 900)
    assert v["indoor_temp"] == (69.0, 900)
    assert m.builtin_values([], None, 900) == {}


# --- the day store ---------------------------------------------------------------


async def test_sampling_records_weather_builtins_and_sensors_then_closes_the_day():
    r = FakeRedis()
    cfg = {m.SETTING_METRICS: json.dumps([
        {"name": "water", "entity_id": "sensor.water_meter", "mode": "counter", "unit": "gal"},
        {"name": "solar_kwh", "entity_id": "sensor.solar", "mode": "power"},
    ])}
    states = {"climate.hall": {"attributes": {"hvac_action": "heating", "current_temperature": 68}},
              "sensor.water_meter": {"state": "100"}, "sensor.solar": {"state": "4000"}}
    assert await m.sample(r, now=NOON, forecast=fc(40, precip=0.02, code=61), states=states, power_w=1000,
                          cfg=cfg, interval_s=900) is None
    states["sensor.water_meter"] = {"state": "130"}
    assert await m.sample(r, now=NOON + 900, forecast=fc(44), states=states, power_w=3000, cfg=cfg,
                          interval_s=900) is None
    defs = await m.definitions(cfg, r)
    row = m.day_row("2026-11-02", r.h["weather:day:2026-11-02"], "imperial", defs)
    assert row["weather"]["mean"] == 42.0 and row["weather"]["min"] == 40.0 and row["weather"]["max"] == 44.0
    assert row["weather"]["precip"] == 0.02 and row["weather"]["wet_hours"] == 0.25
    assert row["weather"]["hdd"] == 23.0
    got = {k: v["value"] for k, v in row["metrics"].items()}
    assert got == {"energy_kwh": 1.0, "heat_hours": 0.5, "cool_hours": 0.0, "indoor_temp": 68.0,
                   "water": 30.0, "solar_kwh": 2.0}
    assert row["metrics"]["energy_kwh"]["coverage"] == round(1800 / DAY, 2)
    # The first pass on the next local day closes this one.
    assert await m.sample(r, now=MIDNIGHT + DAY + 60, forecast=fc(), states={}, power_w=None, cfg={},
                          interval_s=900) == "2026-11-02"


async def test_any_service_can_push_a_metric():
    r = FakeRedis()
    await r.set("weather:utc_offset", OFFSET)
    assert await m.push(r, "eggs", 11, ts=NOON, label="Eggs", unit="eggs") == "2026-11-02"
    await m.push(r, "eggs", 1, ts=NOON + 60)
    defs = await m.definitions({}, r)
    assert defs["eggs"]["source"] == "pushed" and defs["eggs"]["agg"] == "sum"
    row = m.day_row("2026-11-02", r.h["weather:day:2026-11-02"], "imperial", defs)
    assert row["metrics"]["eggs"] == {"value": 12.0, "coverage": None}
    for bad in (("Eggs!", 1, "sum"), ("energy_kwh", 1, "sum"), ("eggs", 1, "median"), ("eggs", float("nan"), "sum")):
        with pytest.raises(ValueError):
            await m.push(r, bad[0], bad[1], agg=bad[2])


# --- analysis ----------------------------------------------------------------


def row(date, mean, value, precip=0.0, coverage=1.0, name="energy_kwh"):
    hdd, cdd = max(0.0, 65 - mean), max(0.0, mean - 65)
    return {"date": date, "weather": {"mean": mean, "precip": precip, "hdd": hdd, "cdd": cdd},
            "metrics": {name: {"value": value, "coverage": coverage}}}


def test_analyse_fits_projects_and_compares_wet_and_dry_days():
    meta = {"label": "Tracked energy", "unit": "kWh", "agg": "sum"}
    days = [row(f"d{i}", 65 - dd, 10 + 0.8 * dd) for i, dd in enumerate([5, 10, 15, 20, 25, 30])]
    a = m.analyse("energy_kwh", meta, days, {"daily": [{"date": "2026-11-03", "high": 50, "low": 30}]}, "imperial")
    assert a["fit"] == {"baseline": 10.0, "per_degree_day": 0.8, "r2": 1.0, "days": 6}
    assert a["r_temperature"] == -1.0
    assert a["projection"] == [{"date": "2026-11-03", "value": 30.0}]
    assert a["insights"][0].startswith("Tracked energy: about 0.80 kWh per degree-day")

    # A partly covered sum is scaled to a full day; too little coverage is left out.
    partial = [row("p", 45, 15.0, coverage=0.9)]
    assert m._usable(partial[0], "energy_kwh", "sum") == pytest.approx(16.667, abs=0.01)
    assert m._usable(row("q", 45, 15.0, coverage=0.5), "energy_kwh", "sum") is None
    assert m._usable(row("r", 45, 68.0, coverage=0.9), "energy_kwh", "mean") == 68.0

    steps = [row(f"s{i}", 60, 4000 if wet else 9000, precip=0.3 if wet else 0, name="steps", coverage=None)
             for i, wet in enumerate([1, 0, 1, 0, 0, 1])]
    s = m.analyse("steps", {"label": "Steps", "unit": "", "agg": "sum"}, steps, None, "imperial")
    assert s["wet_mean"] == 4000 and s["dry_mean"] == 9000 and s["fit"] is None
    assert s["insights"] == ["Steps: 4000.0 on rainy days against 9000.0 on dry days (less when it rains)."]


def test_fit_needs_enough_varied_days():
    assert m.fit([(1, 1)] * 4) is None
    assert m.fit([(5, 1), (5, 2), (5, 3), (5, 4), (5, 5)]) is None
    assert m.pearson([(1, 1)] * 3) is None


async def test_report_and_energy_view():
    r = FakeRedis()
    for i, dd in enumerate([5, 10, 15, 20, 25, 30]):
        key = f"weather:day:2026-10-{20 + i:02d}"
        r.h[key] = {"temp_x_s": str((65 - dd) * DAY), "temp_span_s": str(DAY), "precip": "0",
                    "m:energy_kwh:sum": str(10 + 0.8 * dd), "m:energy_kwh:n": "96", "m:energy_kwh:span": str(DAY),
                    "m:heat_hours:sum": "6", "m:heat_hours:n": "96", "m:heat_hours:span": str(DAY)}
    rep = await m.report(r, {}, fc(), "imperial", days=30, now=MIDNIGHT + 6 * 3600 - 10 * DAY + 9 * DAY)
    assert rep["today"] == "2026-11-01"
    names = [a["name"] for a in rep["metrics"]]
    assert names == ["energy_kwh", "heat_hours"]
    view = m.energy_view(rep)
    assert view["model"] == {"baseline_kwh": 10.0, "kwh_per_degree_day": 0.8, "r2": 1.0, "days": 6}
    assert view["insights"][0] == "Yesterday: 34.0 kWh tracked, heat ran 6.0 h, outside averaged 35°F."
    assert view["projection"] == [{"date": "2026-11-03", "kwh": 30.0}]
    with pytest.raises(KeyError):
        await m.report(r, {}, fc(), "imperial", metric="nope")


# --- events and hooks ----------------------------------------------------------


def test_hooks_and_actions_only_reach_known_paths():
    hooks = bus.parse_hooks(json.dumps([
        {"id": "barn", "events": ["alert.started", "nope"], "service": "Geo", "path": "/api/hooks/weather",
         "filter": {"kinds": ["rain"]}},
        {"events": ["day.completed"], "service": "health", "path": "/x/../admin"},
        {"events": ["day.completed"], "service": "health", "path": "https://evil.example/"},
        {"events": [], "service": "health", "path": "/x"},
        {"events": ["forecast.updated"], "service": "health", "path": "/x", "method": "DELETE"},
    ]))
    assert hooks == [{"id": "barn", "events": ["alert.started"], "service": "geo", "path": "/api/hooks/weather",
                      "method": "POST", "filter": {"kinds": ["rain"], "rules": []}, "enabled": True}]
    assert bus.matches(hooks[0], "alert.started", {"kind": "rain"})
    assert not bus.matches(hooks[0], "alert.started", {"kind": "freeze"})
    assert not bus.matches(hooks[0], "day.completed", {"kind": "rain"})


def test_known_services_follow_the_config(monkeypatch):
    from services import config

    monkeypatch.setattr(config, "FUTURE_SVC_URL", "http://future:9000/", raising=False)
    known = bus.known_services()
    assert known["future"] == "http://future:9000"
    assert {"identity", "geo", "telemetry", "health"} <= set(known)


async def test_publish_writes_the_stream_and_calls_matching_hooks(monkeypatch):
    r = FakeRedis()
    called = []

    async def call(target, body, services=None):
        called.append((target["service"], body["event"], body["data"]["rule"]))
        return True

    monkeypatch.setattr(bus, "call_service", call)
    hooks = bus.parse_hooks(json.dumps([{"events": ["alert.started"], "service": "geo", "path": "/h",
                                         "filter": {"rules": ["hay"]}}]))
    sid = await bus.publish("alert.started", {"rule": "hay", "kind": "rain"}, redis=r, hooks=hooks)
    await bus.publish("alert.started", {"rule": "pipes", "kind": "freeze"}, redis=r, hooks=hooks)
    assert sid == "1-0" and called == [("geo", "alert.started", "hay")]
    events = await bus.read("-", redis=r)
    assert [e["data"]["rule"] for e in events] == ["hay", "pipes"]
    assert [e["data"]["rule"] for e in await bus.read(sid, redis=r)] == ["pipes"]


async def test_call_service_refuses_unknown_services():
    assert await bus.call_service({"service": "nowhere", "path": "/x", "method": "POST"}, {}, services={}) is False
