"""Anything measured against the weather: one daily store, any number of metrics.

Each local day has one Redis hash, ``weather:day:<date>`` (kept 400 days):
the observed weather (time-weighted outdoor temperature, min, max, rain that
fell, hours of rain or snow, humidity) and every metric recorded that day as
``m:<name>:<field>``. A metric comes from one of three places, and all three
are treated alike from then on:

- **Built in** (``BUILTIN``): tracked energy (the devices enrolled for power
  tracking), heating and cooling hours (one per climate zone running) and the
  indoor temperature, sampled from Home Assistant every pass.
- **Home Assistant sensors** (the ``weather_metrics`` setting), sampled every
  pass, no code needed:

      {"name": "solar_kwh", "label": "Solar", "entity_id": "sensor.solar_power",
       "mode": "power"}

  ``mode`` (``MODES``) is ``power`` (watts, summed to kWh), ``counter`` (a
  meter that only goes up, such as water or gas; the day's increase), ``mean``
  (any reading, such as barn humidity), or ``on_time`` (hours the entity is on,
  heating or running). ``attribute`` reads an attribute instead of the state.
- **Pushed by any service** with ``POST /weather/metrics`` (or
  ``services.common.weather_client.push_metric``): steps walked, water used,
  eggs collected. The first push registers the metric (``weather:metric_meta``).

``correlations`` then answers, for every metric the same way: the daily
values, a least-squares fit against heating plus cooling degree days, the
correlation with temperature and with rain, wet-day versus dry-day averages,
plain-sentence insights, and the coming week projected from the forecast.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from services.common import weather as wx

log = logging.getLogger("execution.weather_metrics")

KEEP_DAYS = 400
DAY_PREFIX = "weather:day:"
META = "weather:metric_meta"
LAST_SAMPLE = "weather:sample:last_ts"
LAST_DAY = "weather:sample:last_day"
SETTING_METRICS = "weather_metrics"
#: A finished day counts in a fit only with this much of it covered, for
#: metrics that report coverage (sampled ones do).
FIT_MIN_COVERAGE = 0.8
FIT_MIN_DAYS = 5
#: Rain that makes a day "wet" for the wet/dry comparison.
WET_DAY = {"imperial": 0.04, "metric": 1.0}

AGGS = ("sum", "mean", "max", "min", "delta")
MODES = ("power", "counter", "mean", "on_time")
_MODE_AGG = {"power": "sum", "counter": "delta", "mean": "mean", "on_time": "sum"}
_MODE_UNIT = {"power": "kWh", "on_time": "h"}
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_ON = {"on", "heating", "cooling", "running", "open", "home", "playing", "active", "true"}

BUILTIN: dict[str, dict] = {
    "energy_kwh": {"label": "Tracked energy", "unit": "kWh", "agg": "sum", "source": "builtin"},
    "heat_hours": {"label": "Heating", "unit": "h", "agg": "sum", "source": "builtin"},
    "cool_hours": {"label": "Cooling", "unit": "h", "agg": "sum", "source": "builtin"},
    "indoor_temp": {"label": "Indoor temperature", "unit": "°", "agg": "mean", "source": "builtin"},
}


# --------------------------------------------------------------------------
# Definitions
# --------------------------------------------------------------------------


def valid_name(name: Any) -> bool:
    return isinstance(name, str) and bool(_NAME.match(name))


def parse_ha_metrics(raw: str | None) -> list[dict]:
    """The ``weather_metrics`` setting as clean Home Assistant metrics."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        log.warning(f"[weather] {SETTING_METRICS} is not valid JSON; no sensor metrics")
        return []
    out, seen = [], set(BUILTIN)
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        entity = str(item.get("entity_id") or "").strip()
        mode = item.get("mode")
        if not valid_name(name) or name in seen or "." not in entity or mode not in MODES:
            log.warning(f"[weather] ignoring metric {item!r}")
            continue
        seen.add(name)
        out.append({
            "name": name,
            "label": str(item.get("label") or "").strip()[:60] or name.replace("_", " ").capitalize(),
            "entity_id": entity,
            "attribute": str(item.get("attribute") or "").strip() or None,
            "mode": mode,
            "agg": _MODE_AGG[mode],
            "unit": str(item.get("unit") or "").strip()[:12] or _MODE_UNIT.get(mode, ""),
            "source": "ha",
            "enabled": item.get("enabled", True) is not False,
        })
    return out


async def definitions(cfg: dict, redis) -> dict[str, dict]:
    """Every metric this deployment knows: built in, sensors, then pushed."""
    out = {name: {"name": name, **meta} for name, meta in BUILTIN.items()}
    for m in parse_ha_metrics(cfg.get(SETTING_METRICS)):
        out[m["name"]] = m
    try:
        pushed = await redis.hgetall(META) or {}
    except Exception:
        pushed = {}
    for name, text in pushed.items():
        if name in out:
            continue
        with suppress(ValueError):
            out[name] = {"name": name, **json.loads(text), "source": "pushed"}
    return out


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def local_day(ts: float, offset_s: int) -> str:
    return datetime.fromtimestamp(ts, timezone(timedelta(seconds=offset_s))).date().isoformat()


async def add(redis, day: str, name: str, value: float, *, span_s: float | None = None) -> None:
    """Record one value of metric ``name`` on ``day`` (every aggregate at once)."""
    key = f"{DAY_PREFIX}{day}"
    p = f"m:{name}:"
    pipe = redis.pipeline()
    pipe.hincrbyfloat(key, p + "sum", value)
    pipe.hincrbyfloat(key, p + "n", 1)
    if span_s:
        pipe.hincrbyfloat(key, p + "span", span_s)
    pipe.hget(key, p + "min")
    pipe.hget(key, p + "max")
    pipe.hget(key, p + "first")
    res = await pipe.execute()
    lo, hi, first = res[-3], res[-2], res[-1]
    updates = {p + "last": value}
    if lo is None or value < float(lo):
        updates[p + "min"] = value
    if hi is None or value > float(hi):
        updates[p + "max"] = value
    if first is None:
        updates[p + "first"] = value
    await redis.hset(key, mapping=updates)
    await redis.expire(key, KEEP_DAYS * 86400)


def weather_fields(current: dict, dt: float) -> dict[str, float]:
    """The observed-weather increments for one sample spanning ``dt`` seconds."""
    f: dict[str, float] = {}
    temp = current.get("temp")
    if temp is not None:
        f["temp_x_s"] = temp * dt
        f["temp_span_s"] = dt
    if current.get("humidity") is not None:
        f["hum_x_s"] = current["humidity"] * dt
        f["hum_span_s"] = dt
    # Open-Meteo's current precipitation is the sum over its 15-minute step.
    if current.get("precip"):
        f["precip"] = float(current["precip"]) * dt / 900
    if current.get("code") in wx.WET_CODES:
        f["wet_s"] = dt
    return f


def builtin_values(climates: list[dict], power_w: float | None, dt: float) -> dict[str, tuple[float, float | None]]:
    """Built-in metric values for one sample: name -> (value, span or None)."""
    out: dict[str, tuple[float, float | None]] = {}
    if power_w is not None:
        out["energy_kwh"] = (power_w * dt / 3_600_000, dt)
    heat = sum(dt for c in climates if str((c.get("attributes") or {}).get("hvac_action") or "").lower() == "heating")
    cool = sum(dt for c in climates if str((c.get("attributes") or {}).get("hvac_action") or "").lower() == "cooling")
    if climates:
        out["heat_hours"] = (heat / 3600, dt)
        out["cool_hours"] = (cool / 3600, dt)
    indoor = []
    for c in climates:
        with suppress(TypeError, ValueError):
            indoor.append(float((c.get("attributes") or {}).get("current_temperature")))
    if indoor:
        out["indoor_temp"] = (sum(indoor) / len(indoor), dt)
    return out


def sensor_value(metric: dict, state: dict | None, dt: float) -> tuple[float, float | None] | None:
    """One Home Assistant metric's value for this sample, or None (unreadable)."""
    if not state:
        return None
    raw = (state.get("attributes") or {}).get(metric["attribute"]) if metric.get("attribute") else state.get("state")
    mode = metric["mode"]
    if mode == "on_time":
        return ((dt / 3600) if str(raw).lower() in _ON else 0.0, dt)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if mode == "power":
        return (number * dt / 3_600_000, dt)
    return (number, dt if mode == "mean" else None)


async def sample(redis, *, now: float, forecast: dict, states: dict[str, dict], power_w: float | None,
                 cfg: dict, interval_s: float) -> str | None:
    """Record one pass: the weather and every sampled metric. Returns the day closed, if one was."""
    offset = forecast.get("utc_offset_seconds") or 0
    last = await redis.get(LAST_SAMPLE)
    dt = interval_s if not last else max(0.0, min(now - float(last), 2 * interval_s))
    await redis.set(LAST_SAMPLE, str(now))
    await redis.set("weather:utc_offset", str(offset))
    day = local_day(now, offset)
    closed = None
    prev_day = await redis.get(LAST_DAY)
    if prev_day and prev_day != day:
        closed = prev_day
    await redis.set(LAST_DAY, day)
    if dt <= 0:
        return closed
    key = f"{DAY_PREFIX}{day}"
    current = forecast.get("current") or {}
    fields = weather_fields(current, dt)
    if fields:
        pipe = redis.pipeline()
        for name, value in fields.items():
            pipe.hincrbyfloat(key, name, value)
        await pipe.execute()
        temp = current.get("temp")
        if temp is not None:
            lo, hi = await redis.hmget(key, "temp_min", "temp_max")
            if lo is None or temp < float(lo):
                await redis.hset(key, "temp_min", temp)
            if hi is None or temp > float(hi):
                await redis.hset(key, "temp_max", temp)
        await redis.expire(key, KEEP_DAYS * 86400)
    climates = [s for eid, s in states.items() if str(eid).startswith("climate.")]
    for name, (value, span) in builtin_values(climates, power_w, dt).items():
        await add(redis, day, name, value, span_s=span)
    for metric in parse_ha_metrics(cfg.get(SETTING_METRICS)):
        if not metric["enabled"]:
            continue
        got = sensor_value(metric, states.get(metric["entity_id"]), dt)
        if got is not None:
            await add(redis, day, metric["name"], got[0], span_s=got[1])
    return closed


async def push(redis, name: str, value: float, *, ts: float | None = None, agg: str = "sum",
               label: str | None = None, unit: str | None = None, span_s: float | None = None) -> str:
    """A value another service measured; registers the metric on first use. Returns the day."""
    if not valid_name(name) or name in BUILTIN:
        raise ValueError(f"metric name {name!r} is not allowed")
    if agg not in AGGS:
        raise ValueError(f"agg must be one of {', '.join(AGGS)}")
    if not math.isfinite(float(value)):
        raise ValueError("value must be a finite number")
    offset = int(float(await redis.get("weather:utc_offset") or 0))
    day = local_day(ts or time.time(), offset)
    meta = {"label": (label or name.replace("_", " ").capitalize())[:60], "unit": (unit or "")[:12], "agg": agg}
    await redis.hset(META, name, json.dumps(meta))
    await add(redis, day, name, float(value), span_s=span_s)
    return day


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _f(h: dict, name: str) -> float | None:
    try:
        return float(h[name]) if h.get(name) is not None else None
    except (TypeError, ValueError):
        return None


def metric_value(h: dict, name: str, agg: str) -> float | None:
    p = f"m:{name}:"
    n = _f(h, p + "n")
    if not n:
        return None
    if agg == "mean":
        return (_f(h, p + "sum") or 0) / n
    if agg == "delta":
        first, last = _f(h, p + "first"), _f(h, p + "last")
        return None if first is None or last is None else max(0.0, last - first)
    return _f(h, p + {"sum": "sum", "max": "max", "min": "min"}[agg])


def day_row(date: str, h: dict, units: str, defs: dict[str, dict]) -> dict:
    """One day: the observed weather and every metric recorded that day."""
    span = _f(h, "temp_span_s")
    mean = (_f(h, "temp_x_s") or 0) / span if span else None
    hdd, cdd = wx.degree_days(mean, units)
    hum_span = _f(h, "hum_span_s")
    metrics = {}
    for name, meta in defs.items():
        value = metric_value(h, name, meta.get("agg", "sum"))
        if value is None:
            continue
        cover = _f(h, f"m:{name}:span")
        metrics[name] = {"value": round(value, 3),
                         "coverage": round(min(1.0, cover / 86400), 2) if cover else None}
    return {
        "date": date,
        "weather": {
            "mean": round(mean, 1) if mean is not None else None,
            "min": _f(h, "temp_min"),
            "max": _f(h, "temp_max"),
            "precip": round(_f(h, "precip") or 0, 3),
            "wet_hours": round((_f(h, "wet_s") or 0) / 3600, 2),
            "humidity": round((_f(h, "hum_x_s") or 0) / hum_span, 1) if hum_span else None,
            "hdd": hdd,
            "cdd": cdd,
        },
        "metrics": metrics,
    }


def fit(points: list[tuple[float, float]]) -> dict | None:
    """y ≈ baseline + slope * x by least squares; None with too few points or no spread."""
    if len(points) < FIT_MIN_DAYS:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in points) / sxx
    base = my - slope * mx
    ss_tot = sum((y - my) ** 2 for _, y in points)
    ss_res = sum((y - (base + slope * x)) ** 2 for x, y in points)
    return {"baseline": round(base, 3), "per_degree_day": round(slope, 4),
            "r2": round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else None, "days": n}


def pearson(points: list[tuple[float, float]]) -> float | None:
    if len(points) < FIT_MIN_DAYS:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x, _ in points))
    sy = math.sqrt(sum((y - my) ** 2 for _, y in points))
    if not sx or not sy:
        return None
    return round(sum((x - mx) * (y - my) for x, y in points) / (sx * sy), 3)


def _usable(row: dict, name: str, agg: str) -> float | None:
    """A metric's value for fitting.

    A sampled sum (kWh, hours) on a partly covered day is scaled up to a full
    day; a mean is not. Days covered less than ``FIT_MIN_COVERAGE`` are left out.
    """
    m = row["metrics"].get(name)
    if not m:
        return None
    cover = m.get("coverage")
    if cover is None:
        return m["value"]
    if cover < FIT_MIN_COVERAGE:
        return None
    return m["value"] / cover if agg == "sum" else m["value"]


def analyse(name: str, meta: dict, finished: list[dict], forecast: dict | None, units: str) -> dict:
    """Everything the weather says about one metric over finished days."""
    usable = [(r, v) for r in finished if (v := _usable(r, name, meta.get("agg", "sum"))) is not None]
    dd = [((r["weather"]["hdd"] or 0) + (r["weather"]["cdd"] or 0), v) for r, v in usable]
    temp = [(r["weather"]["mean"], v) for r, v in usable if r["weather"]["mean"] is not None]
    rain = [(r["weather"]["precip"], v) for r, v in usable]
    wet_cut = WET_DAY[wx.units_for(units)]
    wet = [v for r, v in usable if r["weather"]["precip"] >= wet_cut]
    dry = [v for r, v in usable if r["weather"]["precip"] < wet_cut]
    model = fit(dd)
    out = {
        "name": name, "label": meta.get("label", name), "unit": meta.get("unit", ""),
        "agg": meta.get("agg", "sum"), "source": meta.get("source", "pushed"), "days": len(usable),
        "mean": round(sum(v for _, v in usable) / len(usable), 3) if usable else None,
        "fit": model, "r_temperature": pearson(temp), "r_precip": pearson(rain),
        "wet_mean": round(sum(wet) / len(wet), 3) if len(wet) >= 2 else None,
        "dry_mean": round(sum(dry) / len(dry), 3) if len(dry) >= 2 else None,
        "projection": [],
    }
    if model:
        for day in (forecast or {}).get("daily") or []:
            if day.get("high") is None or day.get("low") is None:
                continue
            hdd, cdd = wx.degree_days((day["high"] + day["low"]) / 2, units)
            out["projection"].append({"date": day["date"],
                                      "value": round(max(0.0, model["baseline"] + model["per_degree_day"] * (hdd + cdd)), 2)})
    out["insights"] = insights_for(out)
    return out


def _n(value: float) -> str:
    return f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"


def insights_for(a: dict) -> list[str]:
    """Plain sentences for one metric's analysis (only what the data supports)."""
    lines = []
    unit = f" {a['unit']}" if a["unit"] else ""
    m = a.get("fit")
    if m and m["r2"] is not None and m["r2"] >= 0.3 and m["per_degree_day"]:
        lines.append(f"{a['label']}: about {_n(m['per_degree_day'])}{unit} per degree-day of heating or cooling, "
                     f"on top of {_n(m['baseline'])}{unit} a day ({m['days']} days, r² {m['r2']}).")
    if a.get("wet_mean") is not None and a.get("dry_mean") and abs(a["wet_mean"] - a["dry_mean"]) / abs(a["dry_mean"]) >= 0.15:
        more = "more" if a["wet_mean"] > a["dry_mean"] else "less"
        lines.append(f"{a['label']}: {_n(a['wet_mean'])}{unit} on rainy days against {_n(a['dry_mean'])}{unit} on dry days "
                     f"({more} when it rains).")
    return lines


async def report(redis, cfg: dict, forecast: dict | None, units: str, *, days: int = 30,
                 metric: str | None = None, now: float | None = None) -> dict:
    """Daily rows and the analysis of every metric (or just ``metric``)."""
    days = max(1, min(int(days), KEEP_DAYS))
    now = now or time.time()
    offset = (forecast or {}).get("utc_offset_seconds") or int(float(await redis.get("weather:utc_offset") or 0))
    tz = timezone(timedelta(seconds=offset))
    today = datetime.fromtimestamp(now, tz).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    pipe = redis.pipeline()
    for d in dates:
        pipe.hgetall(f"{DAY_PREFIX}{d}")
    hashes = await pipe.execute()
    defs = await definitions(cfg, redis)
    if metric:
        if metric not in defs:
            raise KeyError(metric)
        defs = {metric: defs[metric]}
    rows = [day_row(d, h, units, defs) for d, h in zip(dates, hashes, strict=True) if h]
    finished = [r for r in rows if r["date"] != today.isoformat()]
    analyses = [analyse(name, meta, finished, forecast, units) for name, meta in defs.items()]
    return {
        "units": wx.UNITS[wx.units_for(units)]["temp"],
        "today": today.isoformat(),
        "days": rows,
        "metrics": [a for a in analyses if a["days"] or metric],
        "insights": [line for a in analyses for line in a["insights"]],
    }


def energy_view(rep: dict) -> dict:
    """The dashboard's weather-and-energy card, from the generic report."""
    by_name = {a["name"]: a for a in rep["metrics"]}
    energy = by_name.get("energy_kwh") or {}
    model = energy.get("fit")
    lines = []
    finished = [r for r in rep["days"] if r["date"] != rep["today"]]
    if finished:
        y = finished[-1]
        m, w = y["metrics"], y["weather"]
        bits = []
        if "energy_kwh" in m:
            bits.append(f"{m['energy_kwh']['value']:.1f} kWh tracked")
        if m.get("heat_hours", {}).get("value"):
            bits.append(f"heat ran {m['heat_hours']['value']:.1f} h")
        if m.get("cool_hours", {}).get("value"):
            bits.append(f"cooling ran {m['cool_hours']['value']:.1f} h")
        if w["mean"] is not None:
            bits.append(f"outside averaged {round(w['mean'])}{rep['units']}")
        if bits:
            lines.append(f"Yesterday: {', '.join(bits)}.")
    lines += [line for name in ("energy_kwh", "heat_hours", "cool_hours") for line in (by_name.get(name) or {}).get("insights", [])]
    return {
        "units": rep["units"],
        "days": rep["days"],
        "model": {"baseline_kwh": model["baseline"], "kwh_per_degree_day": model["per_degree_day"],
                  "r2": model["r2"], "days": model["days"]} if model else None,
        "projection": [{"date": p["date"], "kwh": p["value"]} for p in energy.get("projection", [])],
        "insights": lines,
    }
