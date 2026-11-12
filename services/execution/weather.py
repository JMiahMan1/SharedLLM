"""Household weather: the forecast, weather reminders and weather-vs-energy.

The forecast comes from Open-Meteo (``services/common/weather.py``); this
module decides *where* (the ``weather_location`` setting, else Home
Assistant's ``zone.home``), caches it in Redis, and runs one loop every
15 minutes that does three things:

1. **Refreshes the home forecast** (``weather:forecast:*``, fresh for 15 min,
   kept 6 h so the dashboard still has something while Open-Meteo is away).
2. **Weather reminders.** The ``weather_reminders`` setting (Admin > Users >
   Weather reminders) is a JSON list of rules such as

       {"id": "hay", "kind": "rain", "title": "Cover the hay",
        "note": "Tarp is in the barn.", "people": ["alex"],
        "hours": 12, "threshold": 60,
        "location": {"name": "Barn", "latitude": 36.0, "longitude": -86.8}}

   ``kind`` is rain, snow, freeze, heat or wind (see ``KINDS``); ``people``
   empty means everyone; ``hours`` is how far ahead to look; ``threshold`` is
   in the household's units; ``location`` empty means home. A rule sends one
   ``weather`` notice per *episode*: rain that keeps showing up in the window,
   or comes back within ``gap_h`` hours of the last wet hour, is the same rain
   and is not announced twice (state in Redis ``weather:rule_state``).
   A rule may also run ``actions`` (a Home Assistant service, or a call to
   another service) and may be action-only (``"notify": false``).
3. **Weather against everything else.** Each pass records the observed
   weather and every tracked metric (energy, heating and cooling hours, any
   Home Assistant sensor in ``weather_metrics``, and whatever other services
   push) in the day's store, so any metric can be analysed against the
   weather (``weather_metrics.py``).
4. **Events.** ``forecast.updated``, ``condition.changed``, ``alert.started``
   and ``day.completed`` go to the ``weather:events`` stream and to the
   ``weather_hooks`` that ask for them (``weather_bus.py``).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.common import weather as wx
from services.execution import weather_metrics as metrics
from services.execution.weather_bus import parse_actions

log = logging.getLogger("execution.weather")

INTERVAL_S = 15 * 60
#: First pass this long after start, so a restart loop does not hammer Open-Meteo.
FIRST_PASS_S = 60
#: A cached forecast younger than this is used as is.
FRESH_S = 15 * 60
#: A cached forecast is kept this long as a fallback while Open-Meteo is away.
STALE_KEEP_S = 6 * 3600
#: Settings are re-read this often, so an admin's edit applies without a restart.
SETTINGS_TTL_S = 60

SETTING_RULES = "weather_reminders"
SETTING_LOCATION = "weather_location"
SETTING_UNITS = "weather_units"
SETTING_API_URL = "weather_api_url"
SETTING_METRICS = "weather_metrics"
SETTING_HOOKS = "weather_hooks"

_RULE_STATE = "weather:rule_state"
_LAST_FORECAST = "weather:published:forecast"
_LAST_CONDITION = "weather:published:condition"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

#: Each kind: how far ahead it looks by default, its threshold in each unit
#: system, what the threshold means, and how many dry hours separate two
#: episodes.
KINDS: dict[str, dict[str, Any]] = {
    "rain": {"title": "Rain on the way", "hours": 12, "gap_h": 6,
             "threshold": {"imperial": 60, "metric": 60}, "unit": "%"},
    "snow": {"title": "Snow on the way", "hours": 24, "gap_h": 12,
             "threshold": {"imperial": 0.5, "metric": 1.0}, "unit": "snow"},
    "freeze": {"title": "Freeze coming", "hours": 24, "gap_h": 12,
               "threshold": {"imperial": 32, "metric": 0}, "unit": "temp"},
    "heat": {"title": "Heat coming", "hours": 24, "gap_h": 12,
             "threshold": {"imperial": 95, "metric": 35}, "unit": "temp"},
    "wind": {"title": "High winds coming", "hours": 12, "gap_h": 6,
             "threshold": {"imperial": 40, "metric": 65}, "unit": "wind"},
}
#: Measurable rain in an hour counts as wet even when the probability is low.
_WET_AMOUNT = {"imperial": 0.04, "metric": 1.0}
MAX_HOURS = wx.HOURLY_HOURS


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

_settings_cache: tuple[float, dict] = (0.0, {})


async def _read_setting(key: str) -> str | None:
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    async with get_client().get(
        f"{IDENTITY_SVC_URL.rstrip('/')}/api/settings/{key}", headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=aiohttp.ClientTimeout(total=5),
    ) as resp:
        if resp.status == 404:
            return None
        resp.raise_for_status()
        return (await resp.json(content_type=None) or {}).get("value")


async def settings() -> dict:
    """The weather settings, re-read at most once a minute."""
    global _settings_cache
    if time.time() - _settings_cache[0] < SETTINGS_TTL_S:
        return _settings_cache[1]
    out = dict(_settings_cache[1])
    keys = (SETTING_RULES, SETTING_LOCATION, SETTING_UNITS, SETTING_API_URL, SETTING_METRICS, SETTING_HOOKS)
    # Side by side, so a slow identity costs one timeout, not six. (Not the
    # bulk /api/settings: it carries every telemetry history row too.)
    got = await asyncio.gather(*(_read_setting(k) for k in keys), return_exceptions=True)
    for key, value in zip(keys, got, strict=True):
        if isinstance(value, BaseException):  # identity briefly away: keep what we had
            log.warning(f"[weather] could not read {key}: {type(value).__name__}: {value}")
        else:
            out[key] = value
    _settings_cache = (time.time(), out)
    return out


def units_of(cfg: dict) -> str:
    return wx.units_for(str(cfg.get(SETTING_UNITS) or "").strip().lower())


def parse_location(raw: Any) -> dict | None:
    """``{"name", "latitude", "longitude"}`` from JSON text or a dict, else None."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        lat, lon = float(raw.get("latitude")), float(raw.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    name = str(raw.get("name") or "").strip()[:60] or None
    return {"name": name, "latitude": lat, "longitude": lon}


def _rule_id(item: dict) -> str:
    raw = str(item.get("id") or "").strip().lower()
    if _ID.match(raw):
        return raw
    seed = f"{item.get('kind')}:{item.get('title')}:{item.get('location')}"
    return hashlib.sha1(seed.encode()).hexdigest()[:12]


def parse_rules(raw: str | None, units: str = "imperial") -> list[dict]:
    """The setting as clean rules; malformed ones are dropped and logged."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        log.warning(f"[weather] {SETTING_RULES} is not valid JSON; no weather reminders")
        return []
    out, seen = [], set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            log.warning(f"[weather] ignoring reminder {item!r}")
            continue
        kind = KINDS[item["kind"]]
        rid = _rule_id(item)
        if rid in seen:
            continue
        seen.add(rid)
        try:
            hours = int(item.get("hours") or kind["hours"])
        except (TypeError, ValueError):
            hours = kind["hours"]
        try:
            threshold = float(item["threshold"]) if item.get("threshold") not in (None, "") else None
        except (TypeError, ValueError):
            threshold = None
        if threshold is not None and item.get("units") in wx.UNITS:
            # Set in other units than the household's now: compare like with like.
            threshold = wx.convert(kind["unit"], threshold, item["units"], units)
        out.append({
            "id": rid,
            "kind": item["kind"],
            "title": str(item.get("title") or "").strip()[:80] or kind["title"],
            "note": str(item.get("note") or "").strip()[:200],
            "people": sorted({str(p).strip().lower() for p in (item.get("people") or []) if str(p).strip()}),
            "hours": max(1, min(hours, MAX_HOURS)),
            "threshold": threshold if threshold is not None else kind["threshold"][wx.units_for(units)],
            "location": parse_location(item.get("location")),
            "enabled": item.get("enabled", True) is not False,
            "notify": item.get("notify", True) is not False,
            "actions": parse_actions(item.get("actions")),
        })
    return out


# --------------------------------------------------------------------------
# Location and forecast
# --------------------------------------------------------------------------

_home_cache: tuple[float, dict | None] = (0.0, None)
HOME_TTL_S = 3600


async def home_location(cfg: dict | None = None) -> dict | None:
    """The ``weather_location`` setting, else Home Assistant's ``zone.home``."""
    global _home_cache
    cfg = cfg if cfg is not None else await settings()
    chosen = parse_location(cfg.get(SETTING_LOCATION))
    if chosen:
        return {**chosen, "name": chosen["name"] or "Home"}
    if time.time() - _home_cache[0] < HOME_TTL_S and _home_cache[1]:
        return _home_cache[1]
    from services.execution import ha_client
    from services.execution.main import resolve_first_user

    creds = await resolve_first_user() or {}
    if not creds.get("ha_url") or not creds.get("ha_token"):
        return _home_cache[1]
    state = await ha_client.get_state(creds["ha_url"], creds["ha_token"], "zone.home", account="system account")
    attrs = (state or {}).get("attributes") or {}
    loc = parse_location({"name": "Home", "latitude": attrs.get("latitude"), "longitude": attrs.get("longitude")})
    if loc:
        _home_cache = (time.time(), loc)
    return loc or _home_cache[1]


_redis = None


async def _r():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis

        from services.config import REDIS_URL

        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def _forecast_key(loc: dict, units: str) -> str:
    return f"weather:forecast:{loc['latitude']:.3f},{loc['longitude']:.3f}:{units}"


async def forecast_for(loc: dict, cfg: dict | None = None, *, max_age_s: float = FRESH_S,
                       units: str | None = None) -> dict | None:
    """The forecast at ``loc``: from the cache when fresh, else from Open-Meteo.

    When Open-Meteo fails the last cached forecast (up to 6 h old) comes back
    marked ``stale``; None only when there has never been one.
    """
    cfg = cfg if cfg is not None else await settings()
    units = wx.units_for(units) if units else units_of(cfg)
    key = _forecast_key(loc, units)
    r = await _r()
    cached = None
    try:
        text = await r.get(key)
        cached = json.loads(text) if text else None
    except Exception as e:
        log.warning(f"[weather] cache read failed: {type(e).__name__}: {e}")
    if cached and time.time() - float(cached.get("fetched_at") or 0) < max_age_s:
        cached["location"]["name"] = loc.get("name") or cached["location"].get("name")
        return cached
    try:
        fresh = await wx.fetch_forecast(loc["latitude"], loc["longitude"], units=units,
                                        base_url=(cfg.get(SETTING_API_URL) or "").strip() or None,
                                        name=loc.get("name"))
    except Exception as e:
        log.warning(f"[weather] Open-Meteo failed for {loc.get('name') or 'a location'}: {type(e).__name__}: {e}")
        if cached:
            cached["stale"] = True
            return cached
        return None
    try:
        await r.set(key, json.dumps(fresh), ex=STALE_KEEP_S)
    except Exception as e:
        log.warning(f"[weather] cache write failed: {type(e).__name__}: {e}")
    return fresh


async def home_forecast(*, max_age_s: float = FRESH_S) -> dict | None:
    cfg = await settings()
    loc = await home_location(cfg)
    if not loc:
        return None
    return await forecast_for(loc, cfg, max_age_s=max_age_s)


async def person_prefs(user: str) -> dict:
    """A person's own weather place and units (Settings > Weather); {} on any failure."""
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    try:
        async with get_client().get(
            f"{IDENTITY_SVC_URL.rstrip('/')}/api/internal/weather/prefs", params={"username": user},
            headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None) or {}
    except Exception as e:
        log.info(f"[weather] no weather prefs for {user}: {type(e).__name__}: {e}")
    return {}


async def own_forecast(user: str, cfg: dict | None = None, *,
                       max_age_s: float = FRESH_S) -> tuple[dict | None, bool]:
    """(forecast, is_their_own_place) for ``user``'s own place and units.

    (None, False) when they keep the household default or their place cannot
    be forecast; the caller then uses the household's. The one rule for "a
    person's forecast": the app (``/weather/forecast?user=``) and calendar
    reminder notes both come here, so they always agree.
    """
    prefs = await person_prefs(user)
    loc = parse_location(prefs.get("location"))
    units = prefs.get("units") if prefs.get("units") in wx.UNITS else None
    if not (loc or units):
        return None, False
    cfg = cfg if cfg is not None else await settings()
    target = loc or await home_location(cfg)
    if not target:
        return None, False
    fc = await forecast_for(target, cfg, max_age_s=max_age_s, units=units)
    return fc, bool(fc and loc)



# --------------------------------------------------------------------------
# Reminder rules
# --------------------------------------------------------------------------


def _clock(ts: float, offset_s: int, now: float) -> str:
    tz = timezone(timedelta(seconds=offset_s))
    t, today = datetime.fromtimestamp(ts, tz), datetime.fromtimestamp(now, tz).date()
    hour = t.strftime("%I %p").lstrip("0")
    if t.date() == today:
        return hour
    if t.date() == today + timedelta(days=1):
        return f"{hour} tomorrow"
    return f"{hour} {t.strftime('%a')}"


def _hits(rule: dict, forecast: dict, now: float) -> list[dict]:
    """Forecast hours inside the rule's window that meet its condition."""
    units = (forecast.get("units") or {}).get("system", "imperial")
    thr = rule["threshold"]
    kind = rule["kind"]
    hour_start = now - (now % 3600)
    window = [h for h in forecast.get("hourly") or [] if hour_start <= h["ts"] < now + rule["hours"] * 3600]
    if kind == "rain":
        wet = _WET_AMOUNT[units]
        return [h for h in window
                if (h.get("precip_prob") or 0) >= thr or ((h.get("precip") or 0) >= wet and h.get("icon") != "snow")]
    if kind == "snow":
        total = sum(h.get("snow") or 0 for h in window)
        return [h for h in window if (h.get("snow") or 0) > 0] if total >= thr else []
    if kind == "freeze":
        return [h for h in window if h.get("temp") is not None and h["temp"] <= thr]
    if kind == "heat":
        return [h for h in window if h.get("temp") is not None and h["temp"] >= thr]
    if kind == "wind":
        return [h for h in window if (h.get("gust") or 0) >= thr]
    return []


def _body(rule: dict, hits: list[dict], forecast: dict, now: float) -> str:
    u = forecast.get("units") or {}
    off = forecast.get("utc_offset_seconds") or 0
    first, kind = hits[0], rule["kind"]
    when = "now" if first["ts"] <= now else f"from {_clock(first['ts'], off, now)}"
    if kind == "rain":
        amount = sum(h.get("precip") or 0 for h in hits)
        peak = max(hits, key=lambda h: h.get("precip_prob") or 0)
        raining = (first.get("precip") or 0) >= _WET_AMOUNT[wx.units_for(str(u.get("system") or ""))]
        start = ("Raining now" if raining else "Rain likely now") if when == "now" else f"Rain likely {when}"
        text = f"{start} ({round(peak.get('precip_prob') or 0)}% chance"
        text += f", about {amount:.2f} {u.get('precip', '')})." if amount >= 0.01 else ")."
    elif kind == "snow":
        amount = sum(h.get("snow") or 0 for h in hits)
        text = f"Snow {when}, about {amount:.1f} {u.get('snow') or u.get('precip', '')} in the next {rule['hours']} h."
    elif kind == "freeze":
        low = min(hits, key=lambda h: h["temp"])
        text = f"Down to {round(low['temp'])}{u.get('temp', '°')} around {_clock(low['ts'], off, now)}."
    elif kind == "heat":
        high = max(hits, key=lambda h: h["temp"])
        text = f"Up to {round(high['temp'])}{u.get('temp', '°')} around {_clock(high['ts'], off, now)}."
    else:
        peak = max(hits, key=lambda h: h.get("gust") or 0)
        text = f"Gusts to {round(peak.get('gust') or 0)} {u.get('wind', '')} around {_clock(peak['ts'], off, now)}."
    where = (rule.get("location") or {}).get("name")
    if where:
        text = f"{where}: {text}"
    return f"{text} {rule['note']}".strip() if rule.get("note") else text


def _first_spell(hits: list[dict], gap_s: float) -> list[dict]:
    """The leading run of ``hits`` with no break longer than ``gap_s``."""
    out = hits[:1]
    for h in hits[1:]:
        if h["ts"] - out[-1]["ts"] > gap_s:
            break
        out.append(h)
    return out


def evaluate(rule: dict, forecast: dict, now: float, state: dict | None = None) -> tuple[dict | None, dict]:
    """(notice or None, new state) for one rule against one forecast.

    ``state`` is ``{"last_hit_ts": epoch}`` from the last pass. Only the
    first spell in the window counts (hits more than ``gap_h`` apart are
    separate spells), so tonight's freeze and tomorrow night's each get a
    notice. A spell starting within ``gap_h`` of the last hit seen is the same
    episode and sends nothing; the state still follows that spell's end.
    """
    state = dict(state or {})
    if not rule.get("enabled", True) or not forecast:
        return None, state
    hits = _hits(rule, forecast, now)
    if not hits:
        return None, state
    gap = KINDS[rule["kind"]]["gap_h"] * 3600
    hits = _first_spell(hits, gap)
    first, last = hits[0]["ts"], hits[-1]["ts"]
    prev = state.get("last_hit_ts")
    if prev is not None and first - float(prev) <= gap:
        state["last_hit_ts"] = max(float(prev), last)
        return None, state
    note = {
        "category": "weather",
        "kind": f"weather_{rule['kind']}",
        "title": rule["title"][:120],
        "body": _body(rule, hits, forecast, now)[:400],
        "priority": 3 if rule["kind"] == "rain" else 4,
        "tag": f"weather:{rule['id']}",
        "once": f"weather:{rule['id']}:{int(first)}",
        "data": {"url": "/", "rule": rule["id"], "starts_at": first},
    }
    return note, {"last_hit_ts": last, "notified_for": first}


def heads_up(rules: list[dict], forecasts: dict[str, dict], user: str | None, now: float) -> list[dict]:
    """What each rule that concerns ``user`` sees in the forecast right now.

    The dashboard's "heads up" list: no episode state, nothing sent.
    """
    out = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if user and rule["people"] and user.lower() not in rule["people"]:
            continue
        forecast = forecasts.get(_loc_key(rule.get("location")))
        if not forecast:
            continue
        hits = _hits(rule, forecast, now)
        if hits:
            out.append({"id": rule["id"], "kind": rule["kind"], "title": rule["title"],
                        "body": _body(rule, hits, forecast, now), "starts_at": hits[0]["ts"]})
    return sorted(out, key=lambda x: x["starts_at"])


def _loc_key(loc: dict | None) -> str:
    return "home" if not loc else f"{loc['latitude']:.3f},{loc['longitude']:.3f}"


async def _forecasts_for(rules: list[dict], cfg: dict) -> dict[str, dict]:
    """One forecast per distinct rule location (home included)."""
    out: dict[str, dict] = {}
    home = await home_location(cfg)
    for rule in rules:
        key = _loc_key(rule.get("location"))
        if key in out:
            continue
        loc = rule.get("location") or home
        if not loc:
            continue
        fc = await forecast_for(loc, cfg)
        if fc:
            out[key] = fc
    return out


async def check_reminders(now: float | None = None) -> int:
    """One round of weather reminders; returns how many notices were sent."""
    from services.common import notify
    from services.execution import weather_bus as bus

    now = now or time.time()
    cfg = await settings()
    rules = [r for r in parse_rules(cfg.get(SETTING_RULES), units_of(cfg)) if r["enabled"]]
    if not rules:
        return 0
    forecasts = await _forecasts_for(rules, cfg)
    r = await _r()
    try:
        raw_state = await r.hgetall(_RULE_STATE) or {}
    except Exception as e:
        log.warning(f"[weather] rule state unreadable; skipping this round: {type(e).__name__}: {e}")
        return 0
    sent = 0
    everyone: list[str] | None = None
    for rule in rules:
        forecast = forecasts.get(_loc_key(rule.get("location")))
        if not forecast or forecast.get("stale"):
            continue
        try:
            prev = json.loads(raw_state.get(rule["id"]) or "{}")
        except ValueError:
            prev = {}
        note, state = evaluate(rule, forecast, now, prev)
        if note:
            people: list[str] = []
            if rule["notify"]:
                if not rule["people"] and everyone is None:
                    everyone = await notify.family()
                people = rule["people"] or everyone or []
                took = await notify.send_many(people, lambda user, note=note: {"user": user, **note})
                sent += took
                log.info(f"[weather] {rule['id']} ({rule['kind']}) to {len(people)}: {note['body']}")
                if people and not took:
                    continue  # telemetry took none: leave the state so the next round retries
            event = {"rule": rule["id"], "kind": rule["kind"], "title": rule["title"], "body": note["body"],
                     "starts_at": note["data"]["starts_at"], "people": people,
                     "location": rule.get("location") or {"name": "Home"}}
            if rule["actions"]:
                await bus.run_actions(rule["actions"], event)
            await bus.publish("alert.started", event)
        if state != prev:
            await r.hset(_RULE_STATE, rule["id"], json.dumps(state))
    return sent


# --------------------------------------------------------------------------
# Sampling: the weather and every tracked metric
# --------------------------------------------------------------------------


async def _household_power(states: dict[str, dict]) -> float | None:
    """Total watts of the devices enrolled for power tracking, or None if none report."""
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET
    from services.execution.handlers.telemetry import power_watts

    async with get_client().get(f"{IDENTITY_SVC_URL.rstrip('/')}/api/telemetry/enroll",
                                headers={"X-Internal-Secret": INTERNAL_SECRET},
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
        if resp.status != 200:
            return None
        data = await resp.json(content_type=None)
    rows = data.get("enrollments", []) if isinstance(data, dict) else data or []
    total, any_reading = 0.0, False
    for e in rows:
        if not e.get("power_tracking"):
            continue
        watts = power_watts(states.get(e.get("entity_id")), e.get("power_attribute"))
        if watts is not None:
            total += watts
            any_reading = True
    return total if any_reading else None


async def sample(now: float | None = None, forecast: dict | None = None) -> bool:
    """Record one pass in the day store; publishes ``day.completed`` when a day ends."""
    from services.execution import ha_client
    from services.execution import weather_bus as bus
    from services.execution.main import resolve_first_user

    now = now or time.time()
    forecast = forecast or await home_forecast()
    if not forecast:
        return False
    cfg = await settings()
    states: dict[str, dict] = {}
    power: float | None = None
    creds = await resolve_first_user() or {}
    if creds.get("ha_url") and creds.get("ha_token"):
        all_states = await ha_client.get_all_states(creds["ha_url"], creds["ha_token"], account="system account") or []
        states = {s.get("entity_id"): s for s in all_states if isinstance(s, dict)}
        try:
            power = await _household_power(states)
        except Exception as e:
            log.info(f"[weather] household power unavailable: {type(e).__name__}: {e}")
    r = await _r()
    closed = await metrics.sample(r, now=now, forecast=forecast, states=states, power_w=power,
                                  cfg=cfg, interval_s=INTERVAL_S)
    if closed:
        rep = await metrics.report(r, cfg, forecast, units_of(cfg), days=3, now=now)
        row = next((d for d in rep["days"] if d["date"] == closed), None)
        if row:
            await bus.publish("day.completed", row)
    return True


async def energy_report(days: int = 30, now: float | None = None) -> dict:
    """The dashboard's weather-and-energy card (``weather_metrics.energy_view``)."""
    cfg = await settings()
    forecast = await home_forecast(max_age_s=STALE_KEEP_S)
    rep = await metrics.report(await _r(), cfg, forecast, units_of(cfg), days=days, now=now)
    return metrics.energy_view(rep)


# --------------------------------------------------------------------------
# Loop
# --------------------------------------------------------------------------


async def publish_forecast(forecast: dict) -> None:
    """``forecast.updated`` once per fresh fetch, ``condition.changed`` when it changes."""
    from services.execution import weather_bus as bus

    r = await _r()
    fetched = str(forecast.get("fetched_at"))
    if forecast.get("stale") or await r.get(_LAST_FORECAST) == fetched:
        return
    await r.set(_LAST_FORECAST, fetched)
    cur = forecast.get("current") or {}
    today = (forecast.get("daily") or [{}])[0]
    await bus.publish("forecast.updated", {
        "location": forecast.get("location"), "units": forecast.get("units"),
        "temp": cur.get("temp"), "label": cur.get("label"), "icon": cur.get("icon"),
        "high": today.get("high"), "low": today.get("low"), "precip_prob": today.get("precip_prob"),
        "summary": wx.summary_line(forecast),
    })
    previous = await r.get(_LAST_CONDITION)
    if cur.get("icon") and cur["icon"] != previous:
        await r.set(_LAST_CONDITION, cur["icon"])
        if previous:
            await bus.publish("condition.changed", {"from": previous, "to": cur["icon"], "label": cur.get("label"),
                                                    "temp": cur.get("temp"), "kind": cur["icon"]})


async def run_once(now: float | None = None) -> dict:
    forecast = await home_forecast()
    result = {"forecast": bool(forecast), "reminders": 0, "sampled": False}
    if forecast:
        try:
            await publish_forecast(forecast)
        except Exception as e:
            log.warning(f"[weather] forecast events failed: {type(e).__name__}: {e}")
    try:
        result["reminders"] = await check_reminders(now)
    except Exception as e:
        log.warning(f"[weather] reminders failed: {type(e).__name__}: {e}")
    try:
        result["sampled"] = await sample(now, forecast)
    except Exception as e:
        log.warning(f"[weather] sample failed: {type(e).__name__}: {e}")
    return result


async def weather_loop() -> None:
    await asyncio.sleep(FIRST_PASS_S)
    while True:
        try:
            await run_once()
        except Exception as e:  # keep looping, but never fail silently
            log.warning(f"[weather] pass failed: {type(e).__name__}: {e}")
        await asyncio.sleep(INTERVAL_S)


# --------------------------------------------------------------------------
# HTTP (internal; the app's X-Internal-Secret dependency covers these)
# --------------------------------------------------------------------------

router = APIRouter(prefix="/weather")


@router.get("/forecast")
async def forecast_route(latitude: float | None = None, longitude: float | None = None, name: str | None = None,
                         units: str | None = None, user: str | None = None):
    """The household forecast, one at ``latitude``/``longitude``, or ``user``'s own.

    ``personal`` says whether it is for the person's own place; their place
    failing falls back to the household's.
    """
    cfg = await settings()
    if user and latitude is None:
        fc, personal = await own_forecast(user, cfg)
        if fc:
            return {**fc, "personal": personal}
    if latitude is not None and longitude is not None:
        loc = parse_location({"name": name, "latitude": latitude, "longitude": longitude})
        if not loc:
            raise HTTPException(status_code=422, detail="latitude/longitude out of range")
    else:
        loc = await home_location(cfg)
        if not loc:
            # 404, not 503: nothing to retry until an admin sets a location (the app
            # retries 503s), and the widget explains this case by its status.
            raise HTTPException(status_code=404, detail="No home location: set one in Admin > Weather reminders or add zone.home in Home Assistant")
    fc = await forecast_for(loc, cfg, units=units if units in wx.UNITS else None)
    if not fc:
        raise HTTPException(status_code=502, detail="The weather service did not answer")
    return {**fc, "personal": False}


@router.get("/heads-up")
async def heads_up_route(user: str | None = None):
    cfg = await settings()
    rules = parse_rules(cfg.get(SETTING_RULES), units_of(cfg))
    if not rules:
        return {"items": []}
    forecasts = await _forecasts_for(rules, cfg)
    return {"items": heads_up(rules, forecasts, user, time.time())}


@router.get("/context")
async def context_route():
    fc = await home_forecast()
    if not fc:
        return {"text": "", "summary": ""}
    return {"text": wx.context_text(fc), "summary": wx.summary_line(fc), "stale": bool(fc.get("stale"))}


@router.get("/energy")
async def energy_route(days: int = 30):
    return await energy_report(days)


@router.get("/correlations")
async def correlations_route(days: int = 60, metric: str | None = None):
    """Every metric (or one) against the weather: daily rows, fits, insights, projections."""
    cfg = await settings()
    forecast = await home_forecast(max_age_s=STALE_KEEP_S)
    try:
        return await metrics.report(await _r(), cfg, forecast, units_of(cfg), days=days, metric=metric)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No metric named {metric!r}") from None


@router.get("/day/{date}")
async def day_route(date: str):
    """One day's observed weather and every metric recorded that day."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    cfg = await settings()
    r = await _r()
    h = await r.hgetall(f"{metrics.DAY_PREFIX}{date}")
    if not h:
        raise HTTPException(status_code=404, detail="Nothing recorded that day")
    return metrics.day_row(date, h, units_of(cfg), await metrics.definitions(cfg, r))


class MetricPush(BaseModel):
    name: str
    value: float
    ts: float | None = None
    agg: str = "sum"
    label: str | None = None
    unit: str | None = None
    span_s: float | None = None


@router.post("/metrics")
async def push_metric_route(body: MetricPush):
    """Any service records a value to be analysed against the weather."""
    try:
        day = await metrics.push(await _r(), body.name, body.value, ts=body.ts, agg=body.agg,
                                 label=body.label, unit=body.unit, span_s=body.span_s)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return {"ok": True, "day": day}


@router.get("/metrics")
async def metrics_route():
    return {"metrics": list((await metrics.definitions(await settings(), await _r())).values())}


@router.get("/events")
async def events_route(after: str = "-", count: int = 100):
    """Events after a stream id, oldest first (for services without Redis access)."""
    from services.execution import weather_bus as bus

    return {"events": await bus.read(after, count)}


@router.get("/integrations")
async def integrations_route():
    """What a hook, an action or a metric may name, for the admin screen."""
    from services.execution import weather_bus as bus

    return {
        "events": list(bus.EVENTS),
        "services": sorted(bus.known_services()),
        "metric_modes": list(metrics.MODES),
        "metric_aggs": list(metrics.AGGS),
        "kinds": list(KINDS),
        "stream": bus.STREAM,
    }


class TestReminder(BaseModel):
    rule: dict
    user: str


@router.post("/reminders/test")
async def test_reminder(body: TestReminder):
    """Send one rule's notice to one person now, as if it had triggered.

    When the forecast has nothing for the rule, the notice says so, so the
    admin still sees how it arrives.
    """
    from services.common import notify

    cfg = await settings()
    rules = parse_rules(json.dumps([body.rule]), units_of(cfg))
    if not rules:
        raise HTTPException(status_code=422, detail="Not a valid weather reminder")
    rule = {**rules[0], "enabled": True}
    loc = rule.get("location") or await home_location(cfg)
    forecast = await forecast_for(loc, cfg) if loc else None
    if not forecast:
        raise HTTPException(status_code=502, detail="The weather service did not answer")
    now = time.time()
    hits = _hits(rule, forecast, now)
    body_text = _body(rule, hits, forecast, now) if hits else f"Nothing in the next {rule['hours']} h would send this right now."
    ok = await notify.send({
        "user": body.user.strip().lower(), "category": "weather", "kind": f"weather_{rule['kind']}",
        "title": f"Test: {rule['title']}"[:120], "body": body_text[:400], "priority": 3,
        "tag": f"weather:test:{rule['id']}", "data": {"url": "/"},
    })
    return {"sent": ok, "would_trigger": bool(hits), "body": body_text}
