"""Weather-smart care for Jarvis Garden (Phase 3).

Uses ``services.common.weather_client`` so weather is *always* an optional
extra: every call returns ``None``/empty instead of raising, and the garden
service degrades gracefully with zero errors when weather is unavailable or
unconfigured.

Responsibilities:
  * Rain skip for uncovered outdoor beds only (observed rain last 24h +
    upcoming 12h forecast >= threshold) — logs ``rain_auto_skip`` care logs,
    postpones ``next_due_at`` by 24h, dedupes per rain episode.
  * Heatwave boost (forecast daily high >= 90°F / 32.2°C, dry) — shortens
    effective outdoor/container watering interval (``heat_factor = 0.75``).
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


def _daily_rain_in(day: dict | None) -> float:
    """Total rain (inches) reported for a day by the day store."""
    if not day:
        return 0.0
    rain = day.get("rain") or day.get("rain_in") or day.get("precip") or 0.0
    try:
        return float(rain)
    except (TypeError, ValueError):
        return 0.0


def _hourly_rain_in(hourly: list[dict] | None, hours: int) -> float:
    """Sum rain (inches) from the next ``hours`` hourly forecast entries."""
    if not hourly:
        return 0.0
    total = 0.0
    for entry in hourly[: max(1, hours)]:
        rain = entry.get("rain") or entry.get("rain_in") or entry.get("precip") or 0.0
        try:
            total += float(rain)
        except (TypeError, ValueError):
            continue
    return total


def _daily_high_f(daily: list[dict] | None) -> float | None:
    """Today's forecast daily high in °F (None when unavailable)."""
    if not daily:
        return None
    high = (daily[0] or {}).get("high") or (daily[0] or {}).get("temp_max")
    try:
        return float(high)
    except (TypeError, ValueError):
        return None


def _daily_low_f(daily: list[dict] | None, index: int = 1) -> float | None:
    """Forecast daily low in °F at ``index`` (0=today, 1=tomorrow)."""
    if not daily or index >= len(daily):
        return None
    low = (daily[index] or {}).get("low") or (daily[index] or {}).get("temp_min")
    try:
        return float(low)
    except (TypeError, ValueError):
        return None


def _max_gust_mph(hourly: list[dict] | None, hours: int) -> float | None:
    """Max gust (mph) over the next ``hours`` hourly entries."""
    if not hourly:
        return None
    best: float | None = None
    for entry in hourly[: max(1, hours)]:
        g = entry.get("gusts") or entry.get("wind_gust") or 0.0
        try:
            g = float(g)
        except (TypeError, ValueError):
            continue
        if best is None or g > best:
            best = g
    return best


# --------------------------------------------------------------------------- rain skip


async def _observed_and_forecast_rain_in() -> float:
    """Observed rain (today) + upcoming 12h forecast rain, in inches."""
    today = datetime.now(UTC).date().isoformat()
    day = await _fetch_day(today)
    observed = _daily_rain_in(day)
    fc = await _fetch_forecast()
    hourly = (fc or {}).get("hourly") or []
    upcoming = _hourly_rain_in(hourly, RAIN_LOOKAHEAD_H)
    return observed + upcoming


async def apply_rain_skip(session: Session, profile: GardenProfile | None) -> list[dict]:
    """Postpone watering for uncovered outdoor plants when rain meets the threshold.

    Returns a list of ``{"plant_id", "nickname", "rain_in", "next_due_at"}`` dicts
    for every plant that was skipped.  Deduplicates within a rain episode via
    ``_rain_skip_done`` and existing ``rain_auto_skip`` care logs.
    """
    skipped: list[dict] = []
    try:
        rain_in = await _observed_and_forecast_rain_in()
    except Exception as e:
        log.info("[Garden weather] rain lookup failed: %s", e)
        return skipped

    threshold = _rain_threshold_in(profile)
    if rain_in < threshold:
        return skipped

    today_iso = datetime.now(UTC).date().isoformat()
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
            )
        ).all()
        if any(
            (l.created_at or "").startswith(today_iso) for l in already
        ):
            _rain_skip_done[plant.id] = today_iso
            continue

        next_due = datetime.now(UTC) + timedelta(hours=24)
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
) -> list[dict]:
    """Cross-reference active outdoor plants vs forecast lows by frost tolerance.

    Returns a list of alert dicts: ``{"at_risk": [...], "safe": [...],
    "min_temp_f", "advice"}`` — empty when no outdoor plants are at risk.
    """
    daily = (forecast or {}).get("daily") or []
    low = _daily_low_f(daily, 1) or _daily_low_f(daily, 0)
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


async def check_heat_advisory(session: Session, forecast: dict | None) -> dict | None:
    """Heat advisory when forecast daily high >= 90°F / 32.2°C and dry.

    Returns an advisory dict or None.  Degrades to None when weather is
    unavailable.
    """
    daily = (forecast or {}).get("daily") or []
    high = _daily_high_f(daily)
    if high is None:
        return None
    if high < HEAT_HIGH_F:
        return None
    hourly = (forecast or {}).get("hourly") or []
    # "dry": no meaningful rain in the next 12h.
    rain_in = _hourly_rain_in(hourly, RAIN_LOOKAHEAD_H)
    dry = rain_in < 0.05
    if not dry:
        return None
    return {
        "high_f": round(high, 1),
        "heat_factor": heat_factor(high, dry=True),
        "advice": "Early-morning watering and mulch checks help outdoor plants through the heat.",
    }


async def check_wind_advisory(session: Session, forecast: dict | None, seed_get) -> dict | None:
    """High-wind warning for tall/trellis or potted outdoor plants.

    Gusts >= 35 mph / 56 km/h warns for plants with ``tall_trellis == 1`` or
    potted outdoor plants (pot_size_in set).  Returns an advisory dict or None.
    """
    hourly = (forecast or {}).get("hourly") or []
    gusts = _max_gust_mph(hourly, RAIN_LOOKAHEAD_H)
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


async def build_weather_summary(session: Session, seed_get) -> dict | None:
    """Build the ``weather`` object for ``GET /today``, or None when unavailable.

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

    # Current-condition one-liner.
    current = (forecast or {}).get("current") or {}
    label = current.get("label") or current.get("condition") or ""
    temp = current.get("temp") or current.get("temperature")
    summary = f"{label}, {temp}°" if (label or temp) else None

    profile = session.exec(select(GardenProfile).where(GardenProfile.user_id == "household")).first()

    rain_skipped = await apply_rain_skip(session, profile)
    frost_alerts = await check_frost_alerts(session, forecast, seed_get)
    heat = await check_heat_advisory(session, forecast)
    wind = await check_wind_advisory(session, forecast, seed_get)

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
    today = datetime.now(UTC).date().isoformat()

    for alert in weather_summary.get("frost_alerts") or []:
        key = f"{ONCE_PREFIX}:frost:{today}"
        names = ", ".join(alert.get("names") or [])
        for member in family:
            await _send_urgent_notice(
                member,
                "Frost alert — cover or bring inside",
                f"Forecast low {alert.get('min_temp_f')}°F: {names}. {alert.get('safe_names') and 'Safe: ' + ', '.join(alert['safe_names']) or ''}".strip(),
                key,
            )

    if weather_summary.get("wind_advisory"):
        wa = weather_summary["wind_advisory"]
        key = f"{ONCE_PREFIX}:wind:{today}"
        for member in family:
            await _send_urgent_notice(
                member,
                "High wind alert",
                f"{wa.get('advice')} ({', '.join(wa.get('names') or [])})",
                key,
            )

    if weather_summary.get("heat_advisory"):
        ha = weather_summary["heat_advisory"]
        key = f"{ONCE_PREFIX}:heat:{today}"
        for member in family:
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
                    summary = await build_weather_summary(session, seed_get)
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
        summary = await build_weather_summary(session, seed_get)
        await dispatch_weather_notices(session, family, summary)
    return {"evaluated": True, "weather": summary}
