"""The energy dashboard's configuration and the numbers it shows.

Configuration (``energy_prefs`` in the hub's store) is HA's own energy
preferences, ``.storage/energy``, which :func:`import_ha_prefs` copies:

- ``energy_sources``: grid (``stat_energy_from``/``stat_energy_to``, a cost
  statistic or a fixed ``number_energy_price``), solar, battery, gas. Both of
  HA's grid shapes are read: the flat one and the older ``flow_from`` list.
- ``device_consumption``: ``{stat_consumption, name?, stat_rate?, included_in_stat?}``.

Plus two things HA kept elsewhere or not at all:

- ``utility_sources``: a utility's own meter readings, as external statistics
  (``<source>:<id>_energy_consumption`` with ``..._energy_cost`` and one
  ``..._energy_consumption_<period>`` per time-of-use period, the convention
  the srp_energy driver writes). :func:`utility_candidates` finds them.
- ``tariff``: the rate plan, as data: a currency, an optional flat price and
  time-of-use ``periods`` (``{name, price?, months[], weekdays[], start, end}``;
  months 1-12, weekdays 0 = Monday, empty means all). Nothing about a plan is
  built in; :func:`suggest_tou` proposes periods from a utility's own on-peak
  readings, which the admin can accept or edit.
"""
from __future__ import annotations

import copy
import json
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from services.homehub.statistics import Statistics, parse_dt

PREFS_KEY = "energy_prefs"
ENERGY_UNIT = "kWh"
CONSUMPTION_SUFFIX = "_energy_consumption"
COST_SUFFIX = "_energy_cost"
DEFAULT_TARIFF: dict[str, Any] = {"currency": None, "flat_price": None, "periods": [],
                                  "default_period": {"name": "Off-peak", "price": None}}
DEFAULT_PREFS: dict[str, Any] = {"energy_sources": [], "device_consumption": [], "device_consumption_water": [],
                                 "utility_sources": [], "tariff": DEFAULT_TARIFF}


class PrefsError(ValueError):
    pass


# -- preferences ---------------------------------------------------------------
def load_prefs(store: Any) -> dict[str, Any]:
    prefs = copy.deepcopy(DEFAULT_PREFS)
    saved = store.get_kv(PREFS_KEY, None) if store is not None else None
    if isinstance(saved, dict):
        prefs.update({k: v for k, v in saved.items() if v is not None})
        prefs["tariff"] = {**copy.deepcopy(DEFAULT_TARIFF), **(saved.get("tariff") or {})}
    return prefs


def _str_or_none(v: Any) -> str | None:
    return str(v).strip() or None if v not in (None, "") else None


def _num_or_none(v: Any, what: str) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError) as exc:
        raise PrefsError(f"{what} must be a number") from exc


def _hhmm(v: Any, what: str) -> str:
    s = str(v or "").strip()
    try:
        h, m = (int(x) for x in s.split(":")[:2])
    except ValueError as exc:
        raise PrefsError(f"{what} must be HH:MM") from exc
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m):
        raise PrefsError(f"{what} must be between 00:00 and 24:00")
    return f"{h:02d}:{m:02d}"


def _int_list(v: Any, lo: int, hi: int, what: str) -> list[int]:
    if v in (None, ""):
        return []
    if not isinstance(v, list):
        raise PrefsError(f"{what} must be a list")
    out = sorted({int(x) for x in v})
    if any(x < lo or x > hi for x in out):
        raise PrefsError(f"{what} must be between {lo} and {hi}")
    return out


def validate_prefs(body: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """A complete, checked preferences object from a (possibly partial) update."""
    if not isinstance(body, dict):
        raise PrefsError("preferences must be an object")
    prefs = copy.deepcopy(current or DEFAULT_PREFS)
    for key in ("energy_sources", "device_consumption", "device_consumption_water", "utility_sources"):
        if key in body:
            items = body[key] or []
            if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
                raise PrefsError(f"{key} must be a list of objects")
            prefs[key] = items
    for src in prefs["energy_sources"]:
        if src.get("type") not in ("grid", "solar", "battery", "gas", "water"):
            raise PrefsError("every energy source needs a type: grid, solar, battery, gas or water")
    for dev in prefs["device_consumption"]:
        if not _str_or_none(dev.get("stat_consumption")):
            raise PrefsError("every device needs stat_consumption")
    for util in prefs["utility_sources"]:
        if not _str_or_none(util.get("stat_energy")):
            raise PrefsError("every utility source needs stat_energy")
        if util.get("stat_periods") is not None and not isinstance(util["stat_periods"], dict):
            raise PrefsError("stat_periods must map a period name to a statistic id")
    if "tariff" in body:
        t = body["tariff"] or {}
        if not isinstance(t, dict):
            raise PrefsError("tariff must be an object")
        periods = []
        for i, p in enumerate(t.get("periods") or []):
            if not isinstance(p, dict) or not _str_or_none(p.get("name")):
                raise PrefsError(f"tariff period {i + 1} needs a name")
            periods.append({"name": _str_or_none(p["name"]),
                            "price": _num_or_none(p.get("price"), f"the price of {p['name']}"),
                            "months": _int_list(p.get("months"), 1, 12, "months"),
                            "weekdays": _int_list(p.get("weekdays"), 0, 6, "weekdays"),
                            "start": _hhmm(p.get("start"), "start"), "end": _hhmm(p.get("end"), "end")})
        default = t.get("default_period") or {}
        prefs["tariff"] = {
            "currency": _str_or_none(t.get("currency")),
            "flat_price": _num_or_none(t.get("flat_price"), "flat_price"),
            "periods": periods,
            "default_period": {"name": _str_or_none(default.get("name")) or "Off-peak",
                               "price": _num_or_none(default.get("price"), "the default price")},
        }
    return prefs


def save_prefs(store: Any, body: dict[str, Any]) -> dict[str, Any]:
    prefs = validate_prefs(body, load_prefs(store))
    store.set_kv(PREFS_KEY, prefs)
    return prefs


def ha_shape(prefs: dict[str, Any]) -> dict[str, Any]:
    """What HA's ``energy/get_prefs`` returns (extra hub keys included; HA clients ignore them)."""
    return prefs


def import_ha_prefs(store: Any, config_dir: str, stats: Statistics | None = None) -> dict[str, Any]:
    """Copy HA's ``.storage/energy`` into the hub's energy preferences (repeatable)."""
    path = os.path.join(config_dir, ".storage", "energy")
    with open(path, encoding="utf-8") as fh:
        data = (json.load(fh) or {}).get("data") or {}
    current = load_prefs(store)
    body: dict[str, Any] = {k: data.get(k) or [] for k in ("energy_sources", "device_consumption",
                                                            "device_consumption_water")}
    tariff = dict(current["tariff"])
    if tariff.get("flat_price") is None:
        for flow in grid_flows(body["energy_sources"]):
            if flow.get("number_energy_price") is not None:
                tariff["flat_price"] = flow["number_energy_price"]
                break
    body["tariff"] = tariff
    if not current["utility_sources"] and stats is not None:
        body["utility_sources"] = [c for c in utility_candidates(stats) if c.get("kind") == "utility"]
    prefs = validate_prefs(body, current)
    store.set_kv(PREFS_KEY, prefs)
    return {"energy_sources": len(prefs["energy_sources"]), "device_consumption": len(prefs["device_consumption"]),
            "utility_sources": len(prefs["utility_sources"]), "flat_price": prefs["tariff"]["flat_price"]}


def grid_flows(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every grid import flow in either of HA's shapes: ``{stat_energy_from, stat_cost, number_energy_price}``."""
    out = []
    for src in sources or []:
        if src.get("type") != "grid":
            continue
        if src.get("flow_from"):
            out.extend(f for f in src["flow_from"] if isinstance(f, dict) and f.get("stat_energy_from"))
        elif src.get("stat_energy_from"):
            out.append(src)
    return out


def grid_returns(sources: list[dict[str, Any]]) -> list[str]:
    out = []
    for src in sources or []:
        if src.get("type") != "grid":
            continue
        for f in src.get("flow_to") or []:
            if isinstance(f, dict) and f.get("stat_energy_to"):
                out.append(f["stat_energy_to"])
        if src.get("stat_energy_to"):
            out.append(src["stat_energy_to"])
    return out


# -- candidates ------------------------------------------------------------------
def utility_candidates(stats: Statistics) -> list[dict[str, Any]]:
    """Energy statistics an admin can pick: utility meters (grouped) and every summed energy sensor."""
    metas = {m["statistic_id"]: m for m in stats.all_meta() if m["has_sum"]}
    out: list[dict[str, Any]] = []
    for sid, m in sorted(metas.items()):
        if ":" in sid and sid.endswith(CONSUMPTION_SUFFIX):
            base = sid[: -len(CONSUMPTION_SUFFIX)]
            cost = base + COST_SUFFIX
            periods = {p[len(sid) + 1:]: p for p in metas if p.startswith(sid + "_")}
            out.append({"kind": "utility", "name": m["name"] or base.split(":", 1)[1], "stat_energy": sid,
                        "stat_cost": cost if cost in metas else None, "stat_periods": periods or None,
                        "source": m["source"]})
    for sid, m in sorted(metas.items()):
        if m["unit_class"] == "energy" and ":" not in sid:
            out.append({"kind": "sensor", "name": m["name"], "stat_energy": sid, "source": m["source"]})
    return out


# -- tariff ---------------------------------------------------------------------------
def _minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def period_for(tariff: dict[str, Any], local: datetime) -> dict[str, Any]:
    """The tariff period an hour starting at ``local`` (the hub's time zone) falls in."""
    minute = local.hour * 60 + local.minute
    for p in tariff.get("periods") or []:
        if p.get("months") and local.month not in p["months"]:
            continue
        if p.get("weekdays") and local.weekday() not in p["weekdays"]:
            continue
        s, e = _minutes(p["start"]), _minutes(p["end"])
        inside = s <= minute < e if s <= e else (minute >= s or minute < e)
        if inside:
            return p
    return tariff.get("default_period") or DEFAULT_TARIFF["default_period"]


def _ranges(hours: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for h in sorted(hours):
        if out and out[-1][1] == h:
            out[-1] = (out[-1][0], h + 1)
        else:
            out.append((h, h + 1))
    return out


def suggest_tou(stats: Statistics, on_peak_stat: str, total_stat: str, *, days: int = 365,
                name: str = "On-peak", now: datetime | None = None) -> dict[str, Any]:
    """Time-of-use periods read off a utility's own hourly on-peak readings.

    For each month, weekdays and weekends apart, an hour is on-peak when the
    utility billed usage in it as on-peak on at least half the days that had
    usage then. Months with the same hours become one period. Holidays the
    utility bills off-peak are outvoted by the other days.
    """
    end = now or datetime.now(UTC)
    start = end - timedelta(days=days)
    data = stats.during_period(start, end, [on_peak_stat, total_stat], "hour", {"change"},
                               {"energy": ENERGY_UNIT})
    on = {r["start"]: r.get("change") or 0.0 for r in data.get(on_peak_stat, [])}
    tz = stats.tz
    votes: dict[tuple[int, bool, int], list[int]] = defaultdict(lambda: [0, 0])
    for r in data.get(total_stat, []):
        if not r.get("change"):
            continue
        local = datetime.fromtimestamp(r["start"] / 1000, tz)
        key = (local.month, local.weekday() >= 5, local.hour)
        votes[key][1] += 1
        if on.get(r["start"], 0.0) > 0:
            votes[key][0] += 1
    schedule: dict[int, dict[bool, tuple]] = {}
    for month in range(1, 13):
        for weekend in (False, True):
            hours = [h for h in range(24) if (v := votes.get((month, weekend, h))) and v[1] >= 3 and v[0] * 2 >= v[1]]
            schedule.setdefault(month, {})[weekend] = tuple(_ranges(hours))
    seen = sorted({m for (m, _w, _h) in votes})
    groups: dict[tuple, list[int]] = defaultdict(list)
    for month in seen:
        groups[(schedule[month][False], schedule[month][True])].append(month)
    periods = []
    for (weekday_ranges, weekend_ranges), months in sorted(groups.items(), key=lambda kv: kv[1][0]):
        for ranges, weekdays in ((weekday_ranges, [0, 1, 2, 3, 4]), (weekend_ranges, [5, 6])):
            for s, e in ranges:
                periods.append({"name": name, "price": None, "months": months, "weekdays": weekdays,
                                "start": f"{s:02d}:00", "end": f"{e:02d}:00"})
    return {"periods": periods, "months_seen": seen,
            "hours_compared": sum(v[1] for v in votes.values()), "from": start.isoformat(), "to": end.isoformat()}


# -- the dashboard's numbers -------------------------------------------------------------
def _changes(stats: Statistics, ids: list[str], start: datetime, end: datetime, period: str) -> dict[str, list]:
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    return stats.during_period(start, end, ids, period, {"change"}, {"energy": ENERGY_UNIT})


def _total(rows: list[dict[str, Any]]) -> float:
    return round(sum(r.get("change") or 0.0 for r in rows), 4)


def _series(rows_by_stat: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[float, float] = defaultdict(float)
    for rows in rows_by_stat:
        for r in rows:
            merged[r["start"]] += r.get("change") or 0.0
    return [{"start": datetime.fromtimestamp(k / 1000, UTC).isoformat(), "value": round(v, 4)}
            for k, v in sorted(merged.items())]


def summary(stats: Statistics, prefs: dict[str, Any], start: datetime, end: datetime, period: str = "day",
            *, related_power: dict[str, str | None] | None = None,
            power_now: dict[str, float | None] | None = None) -> dict[str, Any]:
    """Usage, breakdown, time-of-use split and cost for ``[start, end)``, bucketed by ``period``."""
    tariff = prefs.get("tariff") or DEFAULT_TARIFF
    flows = grid_flows(prefs.get("energy_sources") or [])
    grid_ids = [f["stat_energy_from"] for f in flows]
    return_ids = grid_returns(prefs.get("energy_sources") or [])
    solar_ids = [s["stat_energy_from"] for s in prefs.get("energy_sources") or []
                 if s.get("type") == "solar" and s.get("stat_energy_from")]
    devices = prefs.get("device_consumption") or []
    dev_ids = [d["stat_consumption"] for d in devices]

    bucketed = _changes(stats, grid_ids + return_ids + solar_ids + dev_ids, start, end, period)
    hourly = _changes(stats, grid_ids, start, end, "hour")

    grid_kwh = sum(_total(bucketed.get(i, [])) for i in grid_ids)
    out: dict[str, Any] = {
        "start": start.isoformat(), "end": end.isoformat(), "period": period,
        "currency": tariff.get("currency"),
        "grid": {"kwh": round(grid_kwh, 3), "series": _series([bucketed.get(i, []) for i in grid_ids]),
                 "returned_kwh": round(sum(_total(bucketed.get(i, [])) for i in return_ids), 3),
                 "stats": grid_ids},
        "solar": {"kwh": round(sum(_total(bucketed.get(i, [])) for i in solar_ids), 3),
                  "series": _series([bucketed.get(i, []) for i in solar_ids])} if solar_ids else None,
    }

    # Cost of grid energy: a cost statistic, else a fixed price, else the tariff.
    cost: float | None = None
    cost_source = None
    cost_ids = [f["stat_cost"] for f in flows if f.get("stat_cost")]
    if cost_ids:
        costs = stats.during_period(start, end, cost_ids, period, {"change"})
        cost, cost_source = sum(_total(costs.get(i, [])) for i in cost_ids), "statistic"
    elif any(f.get("number_energy_price") is not None for f in flows):
        cost = sum(_total(bucketed.get(f["stat_energy_from"], [])) * float(f["number_energy_price"])
                   for f in flows if f.get("number_energy_price") is not None)
        cost_source = "fixed_price"

    # Time of use: classify each hour of grid usage by the tariff.
    tou: dict[str, dict[str, float]] = {}
    tou_cost = 0.0
    tou_priced = bool(tariff.get("periods")) or tariff.get("default_period", {}).get("price") is not None
    if tariff.get("periods"):
        tz = stats.tz
        for sid in grid_ids:
            for r in hourly.get(sid, []):
                kwh = r.get("change") or 0.0
                p = period_for(tariff, datetime.fromtimestamp(r["start"] / 1000, tz))
                slot = tou.setdefault(p["name"], {"kwh": 0.0, "cost": 0.0})
                slot["kwh"] += kwh
                price = p.get("price") if p.get("price") is not None else tariff.get("flat_price")
                if price is not None:
                    slot["cost"] += kwh * float(price)
                    tou_cost += kwh * float(price)
                else:
                    tou_priced = False
    if cost is None and tariff.get("periods") and tou_priced:
        cost, cost_source = tou_cost, "tariff"
    if cost is None and tariff.get("flat_price") is not None:
        cost, cost_source = grid_kwh * float(tariff["flat_price"]), "flat_price"
    out["grid"]["cost"] = round(cost, 2) if cost is not None else None
    out["grid"]["cost_source"] = cost_source
    out["tou"] = {"source": "tariff" if tariff.get("periods") else None,
                  "periods": [{"name": k, "kwh": round(v["kwh"], 3), "cost": round(v["cost"], 2) if tou_priced else None}
                              for k, v in tou.items()]}

    # Devices.
    rows = []
    tracked = 0.0
    for d in devices:
        sid = d["stat_consumption"]
        kwh = _total(bucketed.get(sid, []))
        if not d.get("included_in_stat"):
            tracked += kwh
        rate = d.get("stat_rate") or (related_power or {}).get(sid)
        rows.append({"name": d.get("name") or (stats.get_meta(sid) or {}).get("name") or sid, "stat": sid,
                     "kwh": round(kwh, 3), "series": _series([bucketed.get(sid, [])]),
                     "power_entity": rate, "power_w": (power_now or {}).get(rate) if rate else None,
                     "included_in_stat": d.get("included_in_stat")})
    rows.sort(key=lambda r: r["kwh"], reverse=True)
    out["devices"] = rows
    out["untracked_kwh"] = round(grid_kwh - tracked, 3) if grid_ids else None

    # Utility meters.
    utils = []
    for u in prefs.get("utility_sources") or []:
        sid = u["stat_energy"]
        ids = [sid, *list((u.get("stat_periods") or {}).values())]
        ch = _changes(stats, ids, start, end, period)
        item: dict[str, Any] = {"name": u.get("name") or sid, "stat": sid, "kwh": round(_total(ch.get(sid, [])), 3),
                                "series": _series([ch.get(sid, [])]), "cost": None, "periods": []}
        last = stats.last_start(sid)
        item["data_until"] = (last + timedelta(hours=1)).isoformat() if last else None
        for pname, pid in (u.get("stat_periods") or {}).items():
            item["periods"].append({"name": pname, "stat": pid, "kwh": round(_total(ch.get(pid, [])), 3)})
        if u.get("stat_cost"):
            c = stats.during_period(start, end, [u["stat_cost"]], period, {"change"})
            item["cost"] = round(_total(c.get(u["stat_cost"], [])), 2)
            item["cost_series"] = _series([c.get(u["stat_cost"], [])])
        item["rate"] = effective_rate(stats, u, end)
        utils.append(item)
    out["utility"] = utils
    return out


def effective_rate(stats: Statistics, util: dict[str, Any], end: datetime, days: int = 30) -> float | None:
    """What the utility charged per kWh over the last ``days`` of its data (cost / energy)."""
    if not util.get("stat_cost"):
        return None
    last = stats.last_start(util["stat_energy"])
    until = min(end, last + timedelta(hours=1)) if last else end
    since = until - timedelta(days=days)
    e = _changes(stats, [util["stat_energy"]], since, until, "month")
    c = stats.during_period(since, until, [util["stat_cost"]], "month", {"change"})
    kwh = _total(e.get(util["stat_energy"], []))
    cost = _total(c.get(util["stat_cost"], []))
    return round(cost / kwh, 4) if kwh > 0 else None


def range_for(span: str, when: datetime | None, tz: Any) -> tuple[datetime, datetime, str]:
    """``day|week|month|year`` containing ``when`` (the hub's zone) -> (start, end, bucket period)."""
    local = (when or datetime.now(UTC)).astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if span == "day":
        start, nxt, period = day, (day + timedelta(days=1, hours=2)).replace(hour=0), "hour"
    elif span == "week":
        start = day - timedelta(days=day.weekday())
        nxt, period = (start + timedelta(days=7, hours=2)).replace(hour=0), "day"
    elif span == "month":
        start = day.replace(day=1)
        nxt, period = (start.replace(day=28) + timedelta(days=5)).replace(day=1), "day"
    elif span == "year":
        start = day.replace(month=1, day=1)
        nxt, period = start.replace(year=start.year + 1), "month"
    else:
        raise PrefsError("range must be day, week, month or year")
    return start, nxt, period


def parse_when(value: str | None) -> datetime | None:
    return parse_dt(value) if value else None
