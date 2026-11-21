"""Weather-smart care for Jarvis Garden (Phase 3).

Uses ``services.common.weather_client`` so weather is *always* an optional
extra: every call returns ``None``/empty instead of raising, and the garden
service degrades gracefully with zero errors when weather is unavailable or
unconfigured.

Responsibilities:
  * Rain skip for uncovered outdoor beds only (observed rain last 24h +
    upcoming 12h forecast >= threshold) — logs ``rain_auto_skip`` care logs,
    postpones ``next_due_at`` by 24h, dedupes per rain episode.
  * Heatwave boost (forecast daily high >= 90°F / 32.2°C, dry) — the poller
    brings outdoor watering forward to ``interval * heat_factor`` (0.75), once
    a day, logged as ``heat_boost`` (:func:`apply_heat_boost`).
  * Plant-specific frost & high-wind alerts keyed to each species'
    ``frost_tolerance``.
  * Internal webhook ``POST /hooks/weather`` (X-Internal-Secret) plus a
    lightweight background weather-event poller (``weather_client.events()``,
    disabled under pytest) that evaluates rain-skip and sends urgent ``garden``
    notices via Telemetry with ``once`` keys.
  * Weather summary object exposed on ``GET /today``.

Nothing here raises when weather is away — every function returns a safe
default and logs one line.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from services.common import notify
from services.common import weather_client as weather
from services import config
from services.garden import clock
from services.garden.cadence import heat_factor
from services.garden.models import (
    CareLog,
    CareRule,
    GardenProfile,
    Location,
    Plant,
    utcnow,
)

log = logging.getLogger("garden.weather_care")

# --------------------------------------------------------------------------- thresholds

#: Default rain-skip threshold: 0.25 in / 6.3 mm over the observed+forecast window.
RAIN_SKIP_THRESHOLD_IN = 0.25
RAIN_SKIP_THRESHOLD_MM = 6.3

#: Heatwave: forecast daily high at or above this (°F / °C).
HEAT_HIGH_F = 90.0
HEAT_HIGH_C = 32.2

#: Frost/freeze thresholds (°F / °C).
FROST_TENDER_F = 36.0      # tender*: alert when low <= 36°F (2°C)
FROST_TENDER_C = 2.0
FROST_HALF_HARDY_F = 28.0  # half_hardy*: alert when low <= 28°F (-2°C)
FROST_HALF_HARDY_C = -2.0
FROST_HARDY_EXTREME_F = 15.0  # hardy*: unsafe only at extreme freeze <= 15°F (-9°C)
FROST_HARDY_EXTREME_C = -9.0

#: High wind: gusts at or above this (mph / km/h).
WIND_HIGH_MPH = 35.0
WIND_HIGH_KMH = 56.0

#: Outdoor kinds that are exposed to rain (never indoor_room, greenhouse).
OUTDOOR_UNCOVERED_KINDS = ("raised_bed", "in_ground", "container", "orchard")

#: Once-key date prefix for urgent weather notices.
ONCE_PREFIX = "garden:weather"

#: How far the poller looks ahead for rain (hours) and frost/wind (hours).
RAIN_LOOKAHEAD_H = 12
FROST_LOOKAHEAD_H = 24

#: In-memory dedup: plant_id -> date string of last rain-skip episode.
_rain_skip_done: dict[str, str] = {}


# --------------------------------------------------------------------------- unit conversion


def _forecast_units(forecast: dict | None) -> str:
    """Return ``"imperial"`` or ``"metric"`` based on the forecast payload.

    ``weather.forecast()`` reports units as a mapping
    (``{"system": "imperial", "temp": "°F", "wind": "mph", "precip": "in"}`` --
    ``services/common/weather.py``), which is what execution reads too. A bare
    string is still accepted, for a payload from elsewhere or an older shape;
    anything else falls back to imperial rather than raising, because this runs
    inside the weather poller and on every ``/today``: an AttributeError here
    silently stopped rain-skip and every frost/heat/wind alert.
    """
    if not forecast:
        return "imperial"
    units = forecast.get("units")
    if isinstance(units, dict):
        system = str(units.get("system") or "")
    elif isinstance(units, str):
        system = units
    else:
        system = ""
    system = system.strip().lower()
    return system if system in ("imperial", "metric") else "imperial"


def _c_to_f(c: float | None) -> float | None:
    """Celsius -> Fahrenheit; None stays None."""
    if c is None:
        return None
    return c * 9.0 / 5.0 + 32.0


def _mm_to_in(mm: float | None) -> float | None:
    """Millimeters -> inches; None stays None."""
    if mm is None:
        return None
    return mm / 25.4


def _kmh_to_mph(kmh: float | None) -> float | None:
    """km/h -> mph; None stays None."""
    if kmh is None:
        return None
    return kmh * 0.621371


# --------------------------------------------------------------------------- helpers


def _units(profile: GardenProfile | None) -> str:
    return (profile.units if profile and profile.units else "imperial").lower()


def _rain_threshold_in(profile: GardenProfile | None) -> float:
    """Rain-skip threshold in inches for the household's units.

    Default 0.25 in / 6.3 mm.  When the household uses metric units we compare
    against 6.3 mm converted to inches (weather values are normalized to inches
    internally).
    """
    if _units(profile) == "metric":
        return RAIN_SKIP_THRESHOLD_MM / 25.4  # 6.3 mm -> inches
    return RAIN_SKIP_THRESHOLD_IN


def _is_outdoor_uncovered(location: Location | None) -> bool:
    """True only for uncovered outdoor spaces that rain can reach."""
    if location is None:
        return False
    if location.kind not in OUTDOOR_UNCOVERED_KINDS:
        return False
    return not bool(location.covered)


def _frost_tolerance_key(species: dict | None) -> str:
    return (species or {}).get("frost_tolerance") or "hardy"


def _frost_limit_f(tolerance: str) -> float:
    """Forecast-low threshold (°F) below which a plant of this tolerance needs protection."""
    t = (tolerance or "hardy").lower()
    if t.startswith("tender"):
        return FROST_TENDER_F
    if t.startswith("half"):
        return FROST_HALF_HARDY_F
    if t.startswith("hardy"):
        return FROST_HARDY_EXTREME_F
    return FROST_HARDY_EXTREME_F


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- weather data access


async def _fetch_forecast() -> dict | None:
    """Best-effort forecast; None when weather is unavailable."""
    try:
        return await weather.forecast()
    except Exception as e:
        log.info("[Garden weather] forecast unavailable: %s", e)
        return None


async def _fetch_day(date_iso: str) -> dict | None:
    """Best-effort day store for an observed-rain lookback."""
    try:
        return await weather.day(date_iso)
    except Exception as e:
        log.info("[Garden weather] day(%s) unavailable: %s", date_iso, e)
        return None


def _hourly_rain_in(hourly: list[dict] | None, hours: int, units: str = "imperial") -> float:
    """Sum rain (inches) from the next ``hours`` hourly forecast entries.

    Converts mm -> inches when ``units == "metric"``.
    """
    if not hourly:
        return 0.0
    total = 0.0
    for entry in hourly[: max(1, hours)]:
        rain = entry.get("rain") or entry.get("rain_in") or entry.get("precip") or 0.0
        try:
            rain = float(rain)
        except (TypeError, ValueError):
            continue
        if units == "metric":
            rain = _mm_to_in(rain) or 0.0
        total += rain
    return total


def _daily_rain_in(day: dict | None, units: str = "imperial") -> float:
    """Total rain (inches) reported for a day by the day store.

    Converts mm -> inches when ``units == "metric"``.
    """
    if not day:
        return 0.0
    rain = day.get("rain") or day.get("rain_in") or day.get("precip") or 0.0
    try:
        rain = float(rain)
    except (TypeError, ValueError):
        return 0.0
    if units == "metric":
        rain = _mm_to_in(rain) or 0.0
    return rain


def _daily_high_f(daily: list[dict] | None, units: str = "imperial") -> float | None:
    """Today's forecast daily high in °F (None when unavailable).

    Uses explicit ``is not None`` checks so a 0.0°F reading is never
    dropped as falsy.  Converts °C -> °F when ``units == "metric"``.
    """
    if not daily:
        return None
    entry = daily[0] or {}
    high = entry.get("high")
    if high is None:
        high = entry.get("temp_max")
    try:
        high = float(high)
    except (TypeError, ValueError):
        return None
    if units == "metric":
        high = _c_to_f(high)
    return high


def _daily_low_f(daily: list[dict] | None, index: int = 1, units: str = "imperial") -> float | None:
    """Forecast daily low in °F at ``index`` (0=today, 1=tomorrow).

    Uses explicit ``is not None`` checks so a 0.0°F reading is never
    dropped as falsy.  Converts °C -> °F when ``units == "metric"``.
    """
    if not daily or index >= len(daily):
        return None
    entry = daily[index] or {}
    low = entry.get("low")
    if low is None:
        low = entry.get("temp_min")
    try:
        low = float(low)
    except (TypeError, ValueError):
        return None
    if units == "metric":
        low = _c_to_f(low)
    return low


def _max_gust_mph(hourly: list[dict] | None, hours: int, units: str = "imperial") -> float | None:
    """Max gust (mph) over the next ``hours`` hourly entries.

    weather.py emits ``"gust"`` (singular); accept ``"gusts"``/``"wind_gust"``
    as fallbacks.  Converts km/h -> mph when ``units == "metric"``.
    """
    if not hourly:
        return None
    best: float | None = None
    for entry in hourly[: max(1, hours)]:
        g = entry.get("gust") or entry.get("gusts") or entry.get("wind_gust") or 0.0
        try:
            g = float(g)
        except (TypeError, ValueError):
            continue
        if units == "metric":
            g = _kmh_to_mph(g) or 0.0
        if best is None or g > best:
            best = g
    return best


# --------------------------------------------------------------------------- rain skip


class WeatherUnavailable(RuntimeError):
    """The forecast could not be read, so nobody can say whether it rained."""


async def _observed_and_forecast_rain_in() -> float:
    """Observed rain (today, at home) + upcoming 12h forecast rain, in inches.

    Both amounts are converted by the units the *weather data* is in (the
    forecast's ``units``), never by the household's display preference: the
    valve interlock and ``/today`` used to convert the same millimetres
    differently. No forecast is :class:`WeatherUnavailable`, not "no rain".
    """
    fc = await _fetch_forecast()
    if fc is None:
        raise WeatherUnavailable("the weather forecast is unavailable")
    data_units = _forecast_units(fc)
    day = await _fetch_day(clock.today_iso())
    observed = _daily_rain_in(day, data_units)
    hourly = fc.get("hourly") or []
    upcoming = _hourly_rain_in(hourly, RAIN_LOOKAHEAD_H, data_units)
    return observed + upcoming


def is_outdoor_uncovered(location: Location | None) -> bool:
    """Is this place open to the sky? (The irrigation interlock asks this.)"""
    return _is_outdoor_uncovered(location)


async def rain_state(profile: GardenProfile | None = None) -> dict:
    """Enough rain to skip watering, right now -- for the irrigation interlock.

    Same source and same threshold as the rain-skip the day's plan already uses,
    so a valve and a schedule can never disagree about whether it rained.
    """
    threshold = _rain_threshold_in(profile)
    try:
        rain_in = await _observed_and_forecast_rain_in()
    except Exception as e:
        log.warning("[Garden weather] rain state unavailable: %s", e)
        return {"active": False, "known": False, "rain_in": None, "threshold_in": threshold,
                "reason": str(e)}
    return {
        "active": rain_in >= threshold,
        "known": True,
        "rain_in": round(rain_in, 3),
        "threshold_in": threshold,
    }


async def apply_rain_skip(session: Session, profile: GardenProfile | None) -> list[dict]:
    """Postpone watering for uncovered outdoor plants when rain meets the threshold.

    Returns a list of ``{"plant_id", "nickname", "rain_in", "next_due_at"}`` dicts
    for every plant that was skipped.  Deduplicates within a rain episode via
    ``_rain_skip_done`` and existing ``rain_auto_skip`` care logs. This writes,
    so only the weather poller and its webhook call it; ``GET /today`` reads
    what was skipped (:func:`rain_skipped_today`).
    """
    skipped: list[dict] = []
    try:
        rain_in = await _observed_and_forecast_rain_in()
    except Exception as e:
        log.warning("[Garden weather] rain lookup failed, nothing skipped: %s", e)
        return skipped

    threshold = _rain_threshold_in(profile)
    if rain_in < threshold:
        return skipped

    today_iso = clock.today_iso()
    day_start, day_end = clock.day_bounds_utc()
    rules = session.exec(select(CareRule).where(CareRule.paused == False)).all()  # noqa: E712
    for rule in rules:
        plant = session.get(Plant, rule.plant_id)
        if plant is None or plant.status != "active":
            continue
        if rule.action != "water":
            continue
        if not rule.rain_skip_enabled:
            continue
        location = session.get(Location, plant.location_id) if plant.location_id else None
        if not _is_outdoor_uncovered(location):
            continue

        # Dedup: same rain episode already applied to this plant today.
        if _rain_skip_done.get(plant.id) == today_iso:
            continue
        already = session.exec(
            select(CareLog).where(
                CareLog.plant_id == plant.id,
                CareLog.action == "rain_skip",
                CareLog.source == "rain_auto_skip",
                CareLog.created_at >= day_start,
                CareLog.created_at < day_end,
            )
        ).first()
        if already is not None:
            _rain_skip_done[plant.id] = today_iso
            continue

        # Never pull a later due date earlier: a plant due in three days stays
        # due in three days; only one due sooner waits a day for the rain.
        next_due = datetime.now(UTC) + timedelta(hours=24)
        current = _parse_due(rule.next_due_at)
        if current is None or current < next_due:
            rule.next_due_at = next_due.isoformat()
        rule.updated_at = utcnow()
        session.add(rule)

        session.add(
            CareLog(
                plant_id=plant.id,
                location_id=plant.location_id,
                action="rain_skip",
                performed_by=None,
                source="rain_auto_skip",
                notes=f"Skipped — {rain_in:.2f} in rain forecast/observed",
                created_at=_now_iso(),
            )
        )
        plant.updated_at = utcnow()
        _rain_skip_done[plant.id] = today_iso

        skipped.append(
            {
                "plant_id": plant.id,
                "nickname": plant.nickname or plant.species_ref,
                "rain_in": round(rain_in, 2),
                "next_due_at": rule.next_due_at,
            }
        )

    if skipped:
        session.commit()
        log.info("[Garden weather] rain-skip applied to %d plant(s) (%.2f in)", len(skipped), rain_in)
    return skipped


def _parse_due(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def rain_skipped_today(session: Session) -> list[dict]:
    """The rain skips already applied today (household day), read-only, for ``/today``."""
    day_start, day_end = clock.day_bounds_utc()
    rows = session.exec(
        select(CareLog).where(
            CareLog.action == "rain_skip",
            CareLog.source == "rain_auto_skip",
            CareLog.created_at >= day_start,
            CareLog.created_at < day_end,
        )
    ).all()
    out: list[dict] = []
    for row in rows:
        plant = session.get(Plant, row.plant_id)
        if plant is None:
            continue
        rule = session.exec(
            select(CareRule).where(CareRule.plant_id == plant.id, CareRule.action == "water")
        ).first()
        out.append({
            "plant_id": plant.id,
            "nickname": plant.nickname or plant.species_ref,
            "rain_in": None,
            "notes": row.notes,
            "next_due_at": rule.next_due_at if rule else None,
        })
    return out


# --------------------------------------------------------------------------- frost / heat / wind alerts


def _plant_names(plants: list[Plant], seed_get) -> list[str]:
    names = []
    for p in plants:
        sp = seed_get(p.species_ref) if seed_get else None
        names.append(p.nickname or (sp or {}).get("common_name") or p.species_ref)
    return names


async def check_frost_alerts(
    session: Session,
    forecast: dict | None,
    seed_get,
    units: str = "imperial",
) -> list[dict]:
    """Cross-reference active outdoor plants vs forecast lows by frost tolerance.

    Returns a list of alert dicts: ``{"at_risk": [...], "safe": [...],
    "min_temp_f", "advice"}`` — empty when no outdoor plants are at risk.
    """
    daily = (forecast or {}).get("daily") or []
    low = _daily_low_f(daily, 1, units)
    if low is None:
        low = _daily_low_f(daily, 0, units)
    if low is None:
        return []

    rules = session.exec(select(CareRule).where(CareRule.paused == False)).all()  # noqa: E712
    at_risk: list[Plant] = []
    safe: list[str] = []
    seen_plant_ids: set[str] = set()

    for rule in rules:
        plant = session.get(Plant, rule.plant_id)
        if plant is None or plant.status != "active":
            continue
        if plant.id in seen_plant_ids:
            continue
        seen_plant_ids.add(plant.id)
        location = session.get(Location, plant.location_id) if plant.location_id else None
        if not _is_outdoor_uncovered(location):
            continue
        species = seed_get(plant.species_ref) if seed_get else None
        tolerance = _frost_tolerance_key(species)
        limit = _frost_limit_f(tolerance)
        name = plant.nickname or (species or {}).get("common_name") or plant.species_ref
        if low <= limit:
            at_risk.append(plant)
        else:
            safe.append(name)

    if not at_risk:
        return []

    at_risk_names = _plant_names(at_risk, seed_get)
    advice = (
        f"Cover or bring inside: {', '.join(at_risk_names)}; "
        f"Safe as-is: {', '.join(safe)}" if safe else f"Cover or bring inside: {', '.join(at_risk_names)}"
    )
    return [
        {
            "plant_ids": [p.id for p in at_risk],
            "names": at_risk_names,
            "min_temp_f": round(low, 1),
            "safe_names": safe,
            "advice": advice,
        }
    ]


async def check_heat_advisory(session: Session, forecast: dict | None,
                              units: str = "imperial") -> dict | None:
    """Heat advisory when forecast daily high >= 90°F / 32.2°C and dry.

    Returns an advisory dict or None.  Degrades to None when weather is
    unavailable.
    """
    daily = (forecast or {}).get("daily") or []
    high = _daily_high_f(daily, units)
    if high is None:
        return None
    if high < HEAT_HIGH_F:
        return None
    hourly = (forecast or {}).get("hourly") or []
    # "dry": no meaningful rain in the next 12h.
    rain_in = _hourly_rain_in(hourly, RAIN_LOOKAHEAD_H, units)
    dry = rain_in < 0.05
    if not dry:
        return None
    return {
        "high_f": round(high, 1),
        "heat_factor": heat_factor(high, dry=True),
        "advice": "Early-morning watering and mulch checks help outdoor plants through the heat.",
    }


def apply_heat_boost(session: Session, factor: float) -> list[dict]:
    """Bring outdoor watering forward in a heatwave: the interval times ``factor``.

    A plant due ``interval`` days after its last watering becomes due after
    ``interval * factor`` days (never earlier than now), once per household day,
    logged as a ``heat_boost`` care log so ``/today`` can say what moved. Only
    plants in outdoor places (beds, in-ground, containers, orchards; covered or
    not, since a roof does not cool the soil) are boosted. Called by the weather
    poller, never by a page read.
    """
    if factor >= 1.0:
        return []
    now = datetime.now(UTC)
    day_start, day_end = clock.day_bounds_utc()
    boosted: list[dict] = []
    rules = session.exec(
        select(CareRule).where(CareRule.action == "water", CareRule.paused == False)  # noqa: E712
    ).all()
    for rule in rules:
        plant = session.get(Plant, rule.plant_id)
        if plant is None or plant.status != "active" or not rule.interval_days:
            continue
        location = session.get(Location, plant.location_id) if plant.location_id else None
        if location is None or location.kind not in OUTDOOR_UNCOVERED_KINDS:
            continue
        done = session.exec(
            select(CareLog).where(
                CareLog.plant_id == plant.id,
                CareLog.action == "heat_boost",
                CareLog.created_at >= day_start,
                CareLog.created_at < day_end,
            )
        ).first()
        due = _parse_due(rule.next_due_at)
        if done is not None or due is None:
            continue
        sooner = max(now, due - timedelta(days=float(rule.interval_days) * (1.0 - factor)))
        if sooner >= due:
            continue
        rule.next_due_at = sooner.isoformat()
        rule.updated_at = utcnow()
        session.add(rule)
        session.add(CareLog(
            plant_id=plant.id,
            location_id=plant.location_id,
            action="heat_boost",
            performed_by=None,
            source="weather_heat_boost",
            notes=f"Heatwave: watering brought forward (interval x {factor:g})",
            created_at=_now_iso(),
        ))
        boosted.append({"plant_id": plant.id, "nickname": plant.nickname or plant.species_ref,
                        "next_due_at": rule.next_due_at})
    if boosted:
        session.commit()
        log.info("[Garden weather] heat boost brought %d watering(s) forward", len(boosted))
    return boosted


def heat_boosted_today(session: Session) -> list[dict]:
    """The heat boosts already applied today, read-only, for ``/today``."""
    day_start, day_end = clock.day_bounds_utc()
    rows = session.exec(
        select(CareLog).where(
            CareLog.action == "heat_boost",
            CareLog.created_at >= day_start,
            CareLog.created_at < day_end,
        )
    ).all()
    out = []
    for row in rows:
        plant = session.get(Plant, row.plant_id)
        if plant is not None:
            out.append({"plant_id": plant.id, "nickname": plant.nickname or plant.species_ref})
    return out


async def check_wind_advisory(session: Session, forecast: dict | None, seed_get,
                              units: str = "imperial") -> dict | None:
    """High-wind warning for tall/trellis or potted outdoor plants.

    Gusts >= 35 mph / 56 km/h warns for plants with ``tall_trellis == 1`` or
    potted outdoor plants (pot_size_in set).  Returns an advisory dict or None.
    """
    hourly = (forecast or {}).get("hourly") or []
    gusts = _max_gust_mph(hourly, RAIN_LOOKAHEAD_H, units)
    if gusts is None or gusts < WIND_HIGH_MPH:
        return None

    rules = session.exec(select(CareRule).where(CareRule.paused == False)).all()  # noqa: E712
    warned: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        plant = session.get(Plant, rule.plant_id)
        if plant is None or plant.status != "active":
            continue
        if plant.id in seen:
            continue
        seen.add(plant.id)
        location = session.get(Location, plant.location_id) if plant.location_id else None
        if not _is_outdoor_uncovered(location):
            continue
        species = seed_get(plant.species_ref) if seed_get else None
        tall = int((species or {}).get("tall_trellis") or 0) == 1
        potted = bool(plant.pot_size_in)
        if tall or potted:
            name = plant.nickname or (species or {}).get("common_name") or plant.species_ref
            warned.append(name)

    if not warned:
        return None
    return {
        "gusts_mph": round(gusts, 1),
        "names": warned,
        "advice": f"High winds ({gusts:.0f} mph) — secure tall/trellis and potted outdoor plants.",
    }


# --------------------------------------------------------------------------- weather summary for GET /today


async def build_weather_summary(session: Session, seed_get, *, apply_skips: bool = False) -> dict | None:
    """Build the ``weather`` object for ``GET /today``, or None when unavailable.

    ``apply_skips`` is for the weather poller and webhook only: they postpone
    watering when it rained. ``GET /today`` passes False and reports the skips
    already applied, so reading the page never changes a schedule.

    Shape::
        {
          "summary": str | None,
          "rain_skipped": [{"plant_id", "nickname", "rain_in", "next_due_at"}],
          "frost_alerts": [{"plant_ids", "names", "min_temp_f", "safe_names", "advice"}],
          "heat_advisory": {"high_f", "heat_factor", "advice"} | None,
          "wind_advisory": {"gusts_mph", "names", "advice"} | None,
        }
    """
    forecast = await _fetch_forecast()
    if forecast is None:
        return None

    # Detect units from the forecast payload so metric (°C, mm, km/h) values
    # are converted to imperial (°F, inches, mph) before threshold checks.
    units = _forecast_units(forecast)

    # Current-condition one-liner.
    current = (forecast or {}).get("current") or {}
    label = current.get("label") or current.get("condition") or ""
    # ``or`` dropped a real 0°C/0°F reading as falsy; take the first that is
    # actually present.
    temp = current.get("temp")
    if temp is None:
        temp = current.get("temperature")
    if units == "metric" and temp is not None:
        try:
            temp = round(_c_to_f(float(temp)), 1)
        except (TypeError, ValueError):
            pass
    summary = f"{label}, {temp}°" if (label or temp) else None

    profile = session.exec(select(GardenProfile).where(GardenProfile.user_id == "household")).first()

    rain_skipped = await apply_rain_skip(session, profile) if apply_skips else rain_skipped_today(session)
    frost_alerts = await check_frost_alerts(session, forecast, seed_get, units)
    heat = await check_heat_advisory(session, forecast, units)
    if heat is not None:
        heat["boosted"] = apply_heat_boost(session, heat["heat_factor"]) if apply_skips else heat_boosted_today(session)
    wind = await check_wind_advisory(session, forecast, seed_get, units)

    relevant = (
        len(rain_skipped) > 0
        or len(frost_alerts) > 0
        or heat is not None
        or wind is not None
    )
    if not relevant:
        return None

    return {
        "summary": summary,
        "rain_skipped": rain_skipped,
        "frost_alerts": frost_alerts,
        "heat_advisory": heat,
        "wind_advisory": wind,
    }


# --------------------------------------------------------------------------- urgent notices via Telemetry


async def _send_urgent_notice(user: str, title: str, body: str, once_key: str) -> None:
    """Send one urgent garden notice via Telemetry with a ``once`` key."""
    payload = notify.notice(
        user=user,
        category="garden",
        title=title,
        body=body,
        priority=4,
        tag=f"{ONCE_PREFIX}:{once_key}",
        once=once_key,
        kind="garden_weather_alert",
        audience="all",
    )
    try:
        await notify.send(payload)
    except Exception as e:
        log.info("[Garden weather] urgent notice to %s failed: %s", user, e)


async def dispatch_weather_notices(
    session: Session,
    family: list[str],
    weather_summary: dict | None,
) -> None:
    """Send urgent Telemetry notices for frost/wind/heat when plants are at risk.

    Uses ``once`` keys like ``garden:weather:frost:2026-11-20`` so the same
    episode is never repeated.
    """
    if not weather_summary or not family:
        return
    today = clock.today_iso()

    for alert in weather_summary.get("frost_alerts") or []:
        names = ", ".join(alert.get("names") or [])
        for member in family:
            key = f"{ONCE_PREFIX}:frost:{member}:{today}"
            await _send_urgent_notice(
                member,
                "Frost alert — cover or bring inside",
                f"Forecast low {alert.get('min_temp_f')}°F: {names}. {alert.get('safe_names') and 'Safe: ' + ', '.join(alert['safe_names']) or ''}".strip(),
                key,
            )

    if weather_summary.get("wind_advisory"):
        wa = weather_summary["wind_advisory"]
        names = ", ".join(wa.get("names") or [])
        for member in family:
            key = f"{ONCE_PREFIX}:wind:{member}:{today}"
            await _send_urgent_notice(
                member,
                "High wind alert",
                f"{wa.get('advice')} ({names})",
                key,
            )

    if weather_summary.get("heat_advisory"):
        ha = weather_summary["heat_advisory"]
        for member in family:
            key = f"{ONCE_PREFIX}:heat:{member}:{today}"
            await _send_urgent_notice(
                member,
                "Heat advisory",
                f"High {ha.get('high_f')}°F forecast — {ha.get('advice')}",
                key,
            )


# --------------------------------------------------------------------------- background poller


async def _family_list() -> list[str]:
    from services.common.notify import family

    try:
        return await family()
    except Exception as e:
        log.warning("[Garden weather] could not list family: %s", e)
        return []


async def weather_poller(session_factory, get_family, seed_get, poll_s: int = 300) -> None:
    """Background loop: poll weather:events, evaluate rain-skip + alerts.

    Disabled under pytest.  ``session_factory`` returns a SQLAlchemy engine
    (like ``services.garden.main._db``), not a Session.
    """
    import os

    if "PYTEST_CURRENT_TEST" in os.environ:
        log.info("[Garden weather] poller disabled under pytest")
        return

    last_id = "-"
    while True:
        try:
            events, last_id = await weather.events(after=last_id, count=50)
            for event in events or []:
                etype = event.get("type")
                if etype not in ("forecast.updated", "condition.changed", "alert.started"):
                    continue
                from services.garden.main import _db as _garden_db

                engine = _garden_db()
                with Session(engine) as session:
                    family = await _family_list()
                    summary = await build_weather_summary(session, seed_get, apply_skips=True)
                    await dispatch_weather_notices(session, family, summary)
        except Exception as e:
            log.warning("[Garden weather] poller error: %s", e)
        await asyncio.sleep(poll_s)


# --------------------------------------------------------------------------- internal webhook


async def handle_weather_webhook(session_factory, get_family, seed_get) -> dict[str, Any]:
    """Handle ``POST /hooks/weather``: evaluate rain-skip + alerts on demand.

    Called by the weather bus hook (or directly) with ``X-Internal-Secret`` auth.
    Returns a summary dict with what was evaluated.
    """
    from services.garden.main import _db as _garden_db

    engine = _garden_db()
    with Session(engine) as session:
        family = await _family_list()
        summary = await build_weather_summary(session, seed_get, apply_skips=True)
        await dispatch_weather_notices(session, family, summary)
    return {"evaluated": True, "weather": summary}
