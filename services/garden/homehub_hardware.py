"""Garden's view of Home Hub hardware: readings, guarded valves, grow lights, tags.

Home Hub owns the devices. This module only ever holds **entity ids**: it asks the
hub what exists (`/api/states`, `/hub/tags`), reads live states, and calls
services. No friendly name, area or state is copied into Garden, so renaming a
sensor in Home Hub cannot leave a stale label here.

Everything degrades: with no ``HOMEHUB_URL``, a hub that is away, or a hub with
no garden hardware, discovery reports ``has_any_hardware: False`` and the UI
shows nothing at all -- that boolean is what keeps the Garden page clean in a
house with no sensors.

The irrigation valve is the one place where a bug could flood a bed, so it is
guarded three times over: caps are validated on write, re-checked on run, and a
watchdog closes the valve if it is still open past its timer -- and shouts if it
cannot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import aiohttp
from sqlmodel import Session, select

from services import config
from services.common import notify
from services.garden import clock
from services.garden.models import (
    MAX_DAILY_RUNS_LIMIT,
    MAX_RUN_MINUTES_LIMIT,
    CareLog,
    CareRule,
    GardenProfile,
    HomeHubBinding,
    Location,
    Plant,
    ValveRun,
    utcnow,
)

log = logging.getLogger("garden.homehub_hardware")

#: The hub answers in milliseconds on the LAN; a slow one must not stall a page.
HUB_TIMEOUT_S = 5.0

#: How long discovery is reused. A hub entity appearing shows up within seconds,
#: and a page of cards does not ask the hub once per card.
_CACHE_TTL_S = 15.0

#: "Clearly moist": this far above the dry threshold, watering is held off.
HOLD_BAND_PCT = 15.0

#: How often the watchdog looks for a valve that should already be closed.
WATCHDOG_S = 60

#: How often grow-light schedules are reconciled.
GROW_LIGHT_S = 300

#: ``(read at, states)`` / ``(read at, tags)``; ``None`` until the first read.
_states_cache: tuple[float, list[dict[str, Any]]] | None = None
_tags_cache: tuple[float, list[dict[str, Any]]] | None = None


#: Telemetry's address for every admin (``services/telemetry/notices.py``).
ADMINS = "@admins"


class HardwareBlocked(Exception):
    """A valve (or a tag) request that must not proceed, with the reason to show."""

    def __init__(self, reason: str, *, status: int = 409, detail: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.detail = detail or {}


def enabled() -> bool:
    """Is there a hub to ask? (``HOMEHUB_URL``, resolved once at startup.)"""
    return bool(config.HOMEHUB_URL)


def invalidate() -> None:
    """Forget the cached discovery: the next read asks the hub again."""
    global _states_cache, _tags_cache
    _states_cache = None
    _tags_cache = None


async def _call(method: str, path: str, *, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """One request to the hub. ``(0, None)`` when it is off, unreachable or slow."""
    if not enabled():
        return (0, None)
    url = f"{config.HOMEHUB_URL.rstrip('/')}{path}"
    headers = {"X-Internal-Secret": config.INTERNAL_SECRET}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=HUB_TIMEOUT_S)
        ) as client, client.request(method, url, json=body, headers=headers) as resp:
            try:
                data = await resp.json(content_type=None)
            except ValueError:
                data = None
            return (resp.status, data)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        log.warning("[hardware] Home Hub did not answer %s %s (%s)", method, path, exc)
        return (0, None)


async def states(*, fresh: bool = False) -> list[dict[str, Any]] | None:
    """Every entity state from the hub, cached briefly. ``None`` when unavailable."""
    global _states_cache
    if not enabled():
        return None
    now = time.monotonic()
    if not fresh and _states_cache is not None and now - _states_cache[0] < _CACHE_TTL_S:
        return _states_cache[1]
    status, data = await _call("GET", "/api/states")
    if status != 200 or not isinstance(data, list):
        return None
    rows = [row for row in data if isinstance(row, dict) and row.get("entity_id")]
    _states_cache = (time.monotonic(), rows)
    return rows


async def tags(*, fresh: bool = False) -> list[dict[str, Any]]:
    """The hub's imported NFC/QR tags, cached briefly. ``[]`` when unavailable."""
    global _tags_cache
    if not enabled():
        return []
    now = time.monotonic()
    if not fresh and _tags_cache is not None and now - _tags_cache[0] < _CACHE_TTL_S:
        return _tags_cache[1]
    status, data = await _call("GET", "/hub/tags")
    if status != 200:
        return []
    rows = data.get("tags") if isinstance(data, dict) else data
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    _tags_cache = (time.monotonic(), rows)
    return rows


def _attr(state: dict[str, Any], key: str) -> Any:
    attrs = state.get("attributes") or {}
    return attrs.get(key) if isinstance(attrs, dict) else None


def _reading(state: dict[str, Any] | None) -> float | None:
    """A numeric state, or ``None`` for unknown/unavailable/garbage."""
    if not state:
        return None
    raw = state.get("state")
    if raw in (None, "", "unknown", "unavailable", "None"):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _entity_row(row: dict[str, Any]) -> dict[str, Any]:
    """One entity as the pickers need it: id, name, device class, unit."""
    return {
        "entity_id": row.get("entity_id"),
        "friendly_name": _attr(row, "friendly_name") or row.get("entity_id"),
        "device_class": _attr(row, "device_class"),
        "unit": _attr(row, "unit_of_measurement"),
        "state": row.get("state"),
    }


# --------------------------------------------------------------------------- discovery

_MOISTURE_WORDS = ("moisture", "soil")
_VALVE_WORDS = ("irrigation", "valve", "water", "drip", "sprinkler", "soaker")
_GROW_LIGHT_WORDS = ("grow", "plant light", "plant_lamp", "growlight")


def _named(row: dict[str, Any]) -> str:
    """Everything Home Hub names an entity by, lowercased (id + friendly name)."""
    name = _attr(row, "friendly_name") or ""
    return f"{row.get('entity_id') or ''} {name}".lower()


def classify(states_rows: list[dict[str, Any]], tag_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Sort the hub's entities into the garden's buckets.

    A sensor is matched by its device class first (Home Hub's own vocabulary),
    and by name only where Home Hub offers no class -- a ``switch`` named
    "drip line" is a valve, and a ``light`` named "grow light" is a grow light.
    """
    moisture: list[dict] = []
    temperature: list[dict] = []
    humidity: list[dict] = []
    lux: list[dict] = []
    valves: list[dict] = []
    grow_lights: list[dict] = []

    for row in states_rows:
        domain = (row.get("entity_id") or "").split(".", 1)[0]
        device_class = _attr(row, "device_class")
        named = _named(row)
        unit = str(_attr(row, "unit_of_measurement") or "").lower()
        if domain == "sensor":
            if device_class == "moisture" or any(word in named for word in _MOISTURE_WORDS):
                moisture.append(_entity_row(row))
            if device_class == "temperature":
                temperature.append(_entity_row(row))
            if device_class == "humidity":
                humidity.append(_entity_row(row))
            if device_class == "illuminance" or unit in ("lx", "lux"):
                lux.append(_entity_row(row))
        elif domain == "valve":
            valves.append(_entity_row(row))
        elif domain == "switch":
            if any(word in named for word in _VALVE_WORDS):
                valves.append(_entity_row(row))
            elif any(word in named for word in _GROW_LIGHT_WORDS):
                grow_lights.append(_entity_row(row))
        elif domain == "light":
            if any(word in named for word in _GROW_LIGHT_WORDS):
                grow_lights.append(_entity_row(row))

    tags_out = [
        {
            "tag_id": str(row.get("tag_id") or row.get("id") or ""),
            "name": row.get("name"),
            "last_scanned": row.get("last_scanned"),
        }
        for row in tag_rows
        if row.get("tag_id") or row.get("id")
    ]
    return {
        "moisture_sensors": moisture,
        "temperature_sensors": temperature,
        "humidity_sensors": humidity,
        "lux_sensors": lux,
        "valves": valves,
        "grow_lights": grow_lights,
        "tags": tags_out,
    }


async def discover_hardware(*, fresh: bool = False) -> dict[str, Any]:
    """What garden hardware the hub has. ``has_any_hardware`` drives the UI."""
    empty = {
        "available": False,
        "has_any_hardware": False,
        "moisture_sensors": [],
        "temperature_sensors": [],
        "humidity_sensors": [],
        "lux_sensors": [],
        "valves": [],
        "grow_lights": [],
        "tags": [],
    }
    if not enabled():
        return empty
    states_rows = await states(fresh=fresh)
    if states_rows is None:
        return empty
    buckets = classify(states_rows, await tags(fresh=fresh))
    has_any = any(buckets[key] for key in buckets)
    return {"available": True, "has_any_hardware": has_any, **buckets}


# --------------------------------------------------------------------------- live readings


async def state_map() -> dict[str, dict[str, Any]]:
    """Entity id -> state row, for reading a binding's sensors in one pass."""
    return {row["entity_id"]: row for row in await states() or []}


def _bound_entities(binding: HomeHubBinding | None) -> list[str]:
    if binding is None:
        return []
    return [
        entity
        for entity in (
            binding.moisture_entity_id,
            binding.temperature_entity_id,
            binding.humidity_entity_id,
            binding.lux_entity_id,
        )
        if entity
    ]


def snapshot_from(binding: HomeHubBinding | None, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A binding's live numbers, from an already-fetched state map.

    Each reading is independently ``None``: one unavailable sensor must not hide
    the three that are reporting.
    """
    if binding is None:
        return {"moisture_pct": None, "temperature": None, "temperature_unit": None,
                "humidity_pct": None, "lux": None, "unavailable": [], "bound": False}

    def pick(entity: str | None) -> tuple[float | None, dict[str, Any] | None]:
        if not entity:
            return (None, None)
        row = rows.get(entity)
        return (_reading(row), row)

    moisture, _ = pick(binding.moisture_entity_id)
    temperature, temp_row = pick(binding.temperature_entity_id)
    humidity, _ = pick(binding.humidity_entity_id)
    lux, _ = pick(binding.lux_entity_id)
    unavailable = [
        entity for entity in _bound_entities(binding)
        if _reading(rows.get(entity)) is None
    ]
    return {
        "moisture_pct": moisture,
        "temperature": temperature,
        "temperature_unit": _attr(temp_row, "unit_of_measurement") if temp_row else None,
        "humidity_pct": humidity,
        "lux": lux,
        "unavailable": unavailable,
        "bound": bool(_bound_entities(binding)),
    }


async def read_binding_snapshot(binding: HomeHubBinding | None) -> dict[str, Any]:
    """A binding's live numbers, asking the hub. Never raises."""
    if binding is None or not _bound_entities(binding):
        return snapshot_from(None, {})
    try:
        rows = await state_map()
    except Exception as exc:  # a hub that is away is not an error, just no numbers
        log.info("[hardware] readings unavailable: %s", exc)
        rows = {}
    return snapshot_from(binding, rows)


def moisture_verdict(binding: HomeHubBinding | None, snapshot: dict[str, Any]) -> dict[str, Any]:
    """What a live moisture reading means for this plant's schedule.

    Only an opt-in binding overrides the cadence, and only with a real number:
    a sensor that is offline changes nothing and says so, so a dead battery can
    never silently stop (or trigger) watering.
    """
    if binding is None or not binding.moisture_override_enabled or not binding.moisture_entity_id:
        return {"applies": False}
    pct = snapshot.get("moisture_pct")
    if pct is None:
        return {
            "applies": False,
            "offline": True,
            "note": "The soil moisture sensor is not reporting; the normal schedule applies.",
        }
    threshold = float(binding.moisture_threshold_pct)
    if pct < threshold:
        return {
            "applies": True,
            "due": True,
            "moisture_pct": pct,
            "reason": f"Soil moisture is {pct:g}% (below {threshold:g}%)",
        }
    if pct >= threshold + HOLD_BAND_PCT:
        return {
            "applies": True,
            "due": False,
            "hold": True,
            "moisture_pct": pct,
            "reason": f"Soil moisture is {pct:g}% — holding off",
        }
    return {"applies": True, "due": False, "moisture_pct": pct, "reason": None}


# --------------------------------------------------------------------------- guarded valves

#: ``on`` states a valve can report (Home Hub uses different words per domain).
_OPEN_STATES = ("on", "open", "opening", "closing")


def valve_domain(entity_id: str | None) -> tuple[str, str, str] | None:
    """``(domain, open_service, close_service)`` for a valve entity."""
    if not entity_id:
        return None
    domain = entity_id.split(".", 1)[0]
    if domain == "valve":
        return ("valve", "open_valve", "close_valve")
    if domain == "switch":
        return ("switch", "turn_on", "turn_off")
    return None


def runs_today(session: Session, binding: HomeHubBinding, *, now: datetime | None = None) -> int:
    """How many guarded runs this binding has had today (the household's day).

    Counted from ``garden_valve_runs`` -- one row per run -- because a bed's run
    writes one care log per plant in it. Counting those would make a five-plant
    bed burn its whole daily allowance in one watering, and an empty bed never
    count at all.
    """
    day_start, day_end = clock.day_bounds_utc(clock.today(now))
    rows = session.exec(
        select(ValveRun).where(
            ValveRun.binding_id == binding.id,
            ValveRun.started_at >= day_start,
            ValveRun.started_at < day_end,
        )
    ).all()
    return len(rows)


def _close_open_runs(session: Session, binding_id: str, *, when: str | None = None) -> None:
    """Stamp ``closed_at`` on every run of this binding that has not been closed."""
    rows = session.exec(
        select(ValveRun).where(ValveRun.binding_id == binding_id, ValveRun.closed_at.is_(None))
    ).all()
    for row in rows:
        row.closed_at = when or utcnow()
        session.add(row)


def active_run(binding: HomeHubBinding) -> bool:
    """Is a run still open (or overdue and not yet confirmed closed)?"""
    return bool(binding.active_run_started_at)


async def _service(entity_id: str, service: str) -> tuple[bool, Any]:
    """Call one Home Hub service on one entity."""
    domain = valve_domain(entity_id)
    if domain is None:
        return (False, "not a valve or switch")
    hub_domain = domain[0]
    status, data = await _call(
        "POST", f"/api/services/{hub_domain}/{service}", body={"entity_id": entity_id}
    )
    return (status in (200, 201), data if status not in (200, 201) else None)


async def valve_is_open(binding: HomeHubBinding) -> bool | None:
    """Is the bound valve reporting itself open? ``None`` when unknown."""
    if not binding.valve_entity_id:
        return None
    rows = await state_map()
    row = rows.get(binding.valve_entity_id)
    if not row:
        return None
    return str(row.get("state") or "").lower() in _OPEN_STATES


async def open_valve(binding: HomeHubBinding) -> None:
    """Open the bound valve. Raises ``HardwareBlocked`` if the hub refuses."""
    domain = valve_domain(binding.valve_entity_id)
    if domain is None:
        raise HardwareBlocked("No irrigation valve is bound to this plant or bed.", status=400)
    ok, detail = await _service(binding.valve_entity_id, domain[1])
    if not ok:
        raise HardwareBlocked(
            "Home Hub did not open the valve.",
            status=502,
            detail={"hub_detail": detail},
        )


#: Seconds to wait before each re-read of a valve after closing it. A valve may
#: report "closing" for a moment; three reads over ~3 s settle it.
CLOSE_CONFIRM_DELAYS_S: tuple[float, ...] = (0.0, 1.0, 2.0)


async def close_valve(binding: HomeHubBinding) -> bool:
    """Close the bound valve and confirm it. ``False`` unless it no longer reads open.

    The hub accepting the close is not enough: the valve's own state is read
    back (fresh, not from the cache). A valve still reporting open after the
    confirmation reads counts as not closed, so the watchdog keeps it and warns.
    A valve with no state row at all is taken at the hub's word, with a warning.
    """
    domain = valve_domain(binding.valve_entity_id)
    if domain is None:
        return False
    try:
        ok, detail = await _service(binding.valve_entity_id, domain[2])
    except Exception as exc:  # a timeout must not leave the caller thinking it closed
        log.error("[hardware] close of %s raised %s: %s", binding.valve_entity_id, type(exc).__name__, exc)
        return False
    if not ok:
        log.warning("[hardware] close failed for %s: %s", binding.valve_entity_id, detail)
        return False
    state = None
    for delay in CLOSE_CONFIRM_DELAYS_S:
        if delay:
            await asyncio.sleep(delay)
        rows = await states(fresh=True)
        row = {r["entity_id"]: r for r in rows or []}.get(binding.valve_entity_id)
        if row is None:
            log.warning(
                "[hardware] %s closed by the hub, but its state could not be read back",
                binding.valve_entity_id,
            )
            return True
        state = str(row.get("state") or "").lower()
        if state not in _OPEN_STATES:
            return True
    log.error("[hardware] %s still reports %r after the hub accepted a close", binding.valve_entity_id, state)
    return False


async def run_valve(
    session: Session,
    binding: HomeHubBinding,
    *,
    performed_by: str,
    minutes: int | None = None,
    override_rain: bool = False,
    profile: Any = None,
    location: Any = None,
    trigger: str = "manual",
) -> dict[str, Any]:
    """Open the bound valve, guarded. Every refusal names its reason.

    The order is deliberate: the caps are checked before the hub is touched, the
    daily count is read from the care log rather than a cached number, and the
    rain interlock is only consulted for a place open to the sky.
    """
    if not binding.valve_entity_id:
        raise HardwareBlocked("No irrigation valve is bound to this plant or bed.", status=400)
    if not 1 <= int(binding.max_run_minutes) <= MAX_RUN_MINUTES_LIMIT:
        raise HardwareBlocked(
            f"max_run_minutes must be between 1 and {MAX_RUN_MINUTES_LIMIT}.", status=400
        )
    if not 1 <= int(binding.daily_max_runs) <= MAX_DAILY_RUNS_LIMIT:
        raise HardwareBlocked(
            f"daily_max_runs must be between 1 and {MAX_DAILY_RUNS_LIMIT}.", status=400
        )
    if active_run(binding):
        raise HardwareBlocked(
            "A valve run is already in progress for this plant or bed.",
            status=409,
            detail={"active_run_started_at": binding.active_run_started_at,
                    "active_run_ends_at": binding.active_run_ends_at},
        )
    requested = int(minutes) if minutes is not None else int(binding.max_run_minutes)
    if requested < 1:
        raise HardwareBlocked("A valve run needs at least one minute.", status=400)
    if requested > int(binding.max_run_minutes):
        raise HardwareBlocked(
            f"This valve is capped at {binding.max_run_minutes} minutes per run.",
            status=400,
            detail={"max_run_minutes": binding.max_run_minutes},
        )

    runs = runs_today(session, binding)
    if runs >= int(binding.daily_max_runs):
        raise HardwareBlocked(
            f"Daily max runs reached ({runs} of {binding.daily_max_runs} today).",
            status=409,
            detail={"runs_today": runs, "daily_max_runs": binding.daily_max_runs},
        )

    from services.garden import weather_care  # local: weather pulls in the forecast client

    if binding.rain_interlock and location is not None and weather_care.is_outdoor_uncovered(location):
        rain = await weather_care.rain_state(profile)
        if not rain.get("known") and not override_rain:
            # No forecast means nobody knows whether it rained: say so, and let a
            # person decide (override_rain) rather than water blind.
            raise HardwareBlocked(
                "The rain interlock cannot tell whether it rained (weather is unavailable). "
                "Run it again with the rain override to water anyway.",
                status=409,
                detail=rain,
            )
        if rain.get("active") and not override_rain:
            raise HardwareBlocked(
                f"Blocked by rain interlock: {rain.get('rain_in')} in of rain in the last/next hours.",
                status=409,
                detail=rain,
            )

    snapshot = await read_binding_snapshot(binding)

    # The run is recorded *before* the hub is asked to open. An open that times
    # out may still have opened the valve, and a commit that fails after a
    # successful open would leave a running valve nobody knows about; recorded
    # first, the watchdog always knows to close it.
    now = datetime.now(UTC)
    binding.active_run_started_at = now.isoformat()
    binding.active_run_ends_at = (now + timedelta(minutes=requested)).isoformat()
    binding.updated_at = utcnow()
    session.add(binding)
    # One run, one row: this is what ``daily_max_runs`` counts, so a bed with
    # forty plants still gets two runs and an empty bed still gets two.
    run = ValveRun(
        binding_id=binding.id,
        valve_entity_id=binding.valve_entity_id,
        performed_by=performed_by,
        minutes=requested,
        trigger=trigger,
        started_at=binding.active_run_started_at,
        ends_at=binding.active_run_ends_at,
    )
    session.add(run)
    session.commit()
    session.refresh(binding)

    try:
        await open_valve(binding)
    except asyncio.CancelledError:
        # No awaiting here: make the run due now so the watchdog closes it next tick.
        binding.active_run_ends_at = datetime.now(UTC).isoformat()
        session.add(binding)
        session.commit()
        raise
    except Exception as exc:
        closed = await _after_failed_open(session, binding, exc)
        first = exc.reason if isinstance(exc, HardwareBlocked) else "Home Hub did not answer when asked to open the valve."
        raise HardwareBlocked(
            f"{first} Garden sent a close"
            + (" and the valve reports closed." if closed else ", which was not confirmed: check the valve."),
            status=exc.status if isinstance(exc, HardwareBlocked) else 502,
            detail={"error": type(exc).__name__, "close_confirmed": closed},
        ) from exc
    log.info(
        "[hardware] valve %s opened for %s min by %s (%s)",
        binding.valve_entity_id, requested, performed_by, binding.target_id,
    )
    return {
        "binding_id": binding.id,
        "valve_entity_id": binding.valve_entity_id,
        "minutes": requested,
        "opened_at": binding.active_run_started_at,
        "closes_at": binding.active_run_ends_at,
        "runs_today": runs + 1,
        "daily_max_runs": binding.daily_max_runs,
        "readings": snapshot,
    }


async def _after_failed_open(session: Session, binding: HomeHubBinding, exc: Exception) -> bool:
    """An open that failed may still have opened the valve: close it, keep the run.

    The ``ValveRun`` row stays (it happened, as far as anyone can tell, and it
    counts toward the day's cap). With the close confirmed the run is stamped
    closed; otherwise it stays open and due now, so the watchdog retries the
    close on its next tick and warns the household if it still will not close.
    Returns whether the close was confirmed.
    """
    log.error(
        "[hardware] opening %s failed (%s: %s); sending a close",
        binding.valve_entity_id, type(exc).__name__, getattr(exc, "reason", exc),
    )
    closed = await close_valve(binding)
    if closed:
        _close_open_runs(session, binding.id)
        binding.active_run_started_at = None
        binding.active_run_ends_at = None
    else:
        binding.active_run_ends_at = datetime.now(UTC).isoformat()
        log.error("[hardware] %s may be open after a failed open; the watchdog keeps it", binding.valve_entity_id)
    binding.updated_at = utcnow()
    session.add(binding)
    session.commit()
    session.refresh(binding)
    return closed


async def stop_valve(session: Session, binding: HomeHubBinding) -> dict[str, Any]:
    """Close the valve now. Idempotent: a closed valve is already the goal."""
    if not binding.valve_entity_id:
        raise HardwareBlocked("No irrigation valve is bound to this plant or bed.", status=400)
    ok = await close_valve(binding)
    if not ok:
        raise HardwareBlocked("Home Hub did not confirm the valve closed.", status=502)
    _close_open_runs(session, binding.id)
    binding.active_run_started_at = None
    binding.active_run_ends_at = None
    binding.updated_at = utcnow()
    session.add(binding)
    session.commit()
    session.refresh(binding)
    return {"binding_id": binding.id, "stopped": True}


# --------------------------------------------------------------------------- grow lights


def _hhmm(value: str | None) -> int | None:
    """``'HH:MM'`` as minutes past midnight, or ``None`` when it is not a time."""
    if not value or ":" not in value:
        return None
    try:
        hours, minutes = (int(part) for part in value.split(":", 1))
    except (TypeError, ValueError):
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def grow_light_window(binding: HomeHubBinding | None, local_now: datetime) -> bool | None:
    """Should the light be on at ``local_now``? ``None`` when not scheduled.

    A window may cross midnight (18:00-06:00 for seedlings in winter), which is
    why the comparison is written out rather than a simple ``on <= now < off``.
    """
    if binding is None or not binding.grow_light_enabled or not binding.grow_light_entity_id:
        return None
    on = _hhmm(binding.grow_light_on_time)
    off = _hhmm(binding.grow_light_off_time)
    if on is None or off is None or on == off:
        return None
    minutes = local_now.hour * 60 + local_now.minute
    if on < off:
        return on <= minutes < off
    return minutes >= on or minutes < off


def local_now() -> datetime:
    """The household's wall clock, so 06:00 means 06:00 at home.

    The household zone (``clock``); an unset or invalid one raises
    ``HouseholdTimeZoneError`` instead of switching lights on UTC's clock.
    """
    return clock.now()


async def _toggle_light(entity_id: str, on: bool) -> bool:
    domain = entity_id.split(".", 1)[0]
    if domain not in ("light", "switch"):
        return False
    service = "turn_on" if on else "turn_off"
    status, _ = await _call("POST", f"/api/services/{domain}/{service}", body={"entity_id": entity_id})
    return status in (200, 201)


#: The schedule's last answer per binding (and window), to tell a transition
#: from a light someone switched by hand.
_last_desired: dict[str, bool] = {}


async def sync_grow_lights(
    session_factory: Callable[[], Any], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Reconcile every scheduled grow light with its photoperiod window.

    Only a *transition* of the schedule is sent: a light someone switched by
    hand stays as they left it until the window next opens or closes. The first
    look at a binding (after a start, or a new schedule) has no transition to
    wait for, so it brings the light to the schedule once.
    """
    actions: list[dict[str, Any]] = []
    if not enabled():
        return actions
    moment = now or local_now()
    rows = await state_map()
    with Session(session_factory()) as session:
        scheduled = session.exec(
            select(HomeHubBinding).where(HomeHubBinding.grow_light_enabled == True)  # noqa: E712
        ).all()
        for binding in scheduled:
            desired = grow_light_window(binding, moment)
            if desired is None:
                continue
            key = f"{binding.id}:{binding.grow_light_on_time}-{binding.grow_light_off_time}"
            previous = _last_desired.get(key)
            _last_desired[key] = desired
            if previous is not None and previous == desired:
                continue  # no transition: leave a hand-switched light alone
            row = rows.get(binding.grow_light_entity_id or "")
            if row is None:
                continue
            current = str(row.get("state") or "").lower() == "on"
            if current == desired:
                continue
            if await _toggle_light(binding.grow_light_entity_id or "", desired):
                actions.append(
                    {
                        "binding_id": binding.id,
                        "entity_id": binding.grow_light_entity_id,
                        "turned_on": desired,
                    }
                )
    return actions


async def grow_light_loop(
    session_factory: Callable[[], Any], poll_s: int = GROW_LIGHT_S
) -> None:
    """Background loop: keep grow lights inside their window (off under pytest)."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        log.info("[hardware] grow-light loop disabled under pytest")
        return
    while True:
        try:
            await sync_grow_lights(session_factory)
        except Exception as exc:
            log.warning("[hardware] grow-light tick failed: %s", exc)
        await asyncio.sleep(poll_s)


# --------------------------------------------------------------------------- failsafe watchdog


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _send_open_valve_alert(user: str, title: str, body: str, once_key: str) -> None:
    """Tell a parent, urgently, that a valve may still be open."""
    payload = notify.notice(
        user=user,
        category="garden",
        title=title,
        body=body,
        priority=4,
        tag=f"garden_valve:{once_key}",
        once=once_key,
        kind="garden_valve_failsafe",
        audience="parents",
        link=notify.link("garden"),
    )
    try:
        await notify.send(payload)
    except Exception as exc:
        log.warning("[hardware] valve alert to %s failed: %s", user, exc)


async def _family() -> list[str]:
    """Who to warn, via telemetry's notify helper (degrades to nobody)."""
    try:
        return await notify.family()
    except Exception as exc:
        log.warning("[hardware] could not list family members: %s", exc)
        return []


async def check_valve_failsafe(
    session_factory: Callable[[], Any],
    get_family: Callable[[], Any] | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Close every valve whose timer has run out -- and shout if one will not close.

    This is the last line of defence. It runs from a plain background loop, so it
    keeps working even if the request that opened the valve died, and because the
    open run is stored on the binding it also survives a restart.
    """
    results: list[dict[str, Any]] = []
    if not enabled():
        return results
    moment = now or datetime.now(UTC)
    with Session(session_factory()) as session:
        open_runs = session.exec(
            select(HomeHubBinding).where(HomeHubBinding.active_run_started_at.is_not(None))
        ).all()
        for binding in open_runs:
            ends = _parse_iso(binding.active_run_ends_at)
            if ends is not None and moment < ends:
                continue
            if await close_valve(binding):
                _close_open_runs(session, binding.id)
                binding.active_run_started_at = None
                binding.active_run_ends_at = None
                binding.updated_at = utcnow()
                session.add(binding)
                session.commit()
                results.append({"binding_id": binding.id, "closed": True})
                continue
            # It will not close. Keep the flag (so the next tick retries) and ask a
            # parent to go and look at the valve.
            results.append({"binding_id": binding.id, "closed": False})
            once = f"open-valve:{binding.id}:{binding.active_run_started_at or moment.isoformat()}"
            await alert_valve_may_be_open(
                "Irrigation valve may still be open",
                (
                    f"Home Hub could not confirm that {binding.valve_entity_id} closed after its "
                    f"{binding.max_run_minutes}-minute run. Check the valve now."
                ),
                once,
                get_family or _family,
            )
    return results


async def alert_valve_may_be_open(
    title: str, body: str, once: str, get_family: Callable[[], Any]
) -> None:
    """Warn the household that a valve may be open; never silently nobody.

    The family list comes from Identity. If it cannot be read, or is empty, that
    is logged as an error and the alert goes to ``@admins`` instead: telemetry
    resolves that address itself, so the warning cannot vanish because one
    lookup failed.
    """
    members: list[str] = []
    try:
        members = list(await get_family() or [])
    except Exception as exc:
        log.error("[hardware] listing the family for an open-valve alert failed: %s", exc)
    if members:
        for member in members:
            await _send_open_valve_alert(member, title, body, once)
        return
    log.error("[hardware] no family list for an open-valve alert (%s); alerting admins instead", once)
    payload = notify.notice(
        user=ADMINS,
        category="admin",
        title=title,
        body=body,
        priority=5,
        tag=f"garden_valve:{once}",
        once=once,
        kind="garden_valve_failsafe",
        link=notify.link("garden"),
    )
    if not await notify.send(payload):
        log.error("[hardware] the admin open-valve alert was not delivered either: %s", body)



async def valve_watchdog(
    session_factory: Callable[[], Any],
    get_family: Callable[[], Any] | None = None,
    poll_s: int = WATCHDOG_S,
) -> None:
    """Background loop: close valves past their timer (disabled under pytest)."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        log.info("[hardware] valve watchdog disabled under pytest")
        return
    while True:
        try:
            await check_valve_failsafe(session_factory, get_family)
        except Exception as exc:  # a watchdog that dies is worse than a noisy one
            log.warning("[hardware] valve watchdog tick failed: %s", exc)
        await asyncio.sleep(poll_s)


# --------------------------------------------------------------------------- NFC / QR tags


def binding_for_tag(session: Session, tag_id: str) -> HomeHubBinding | None:
    """The binding a scanned tag points at, if any."""
    if not tag_id:
        return None
    return session.exec(
        select(HomeHubBinding).where(HomeHubBinding.nfc_tag_id == tag_id)
    ).first()


def _next_due_at(session: Session, plant_id: str) -> str | None:
    """The plant's soonest care, so a scan can say what is owed."""
    rows = session.exec(
        select(CareRule).where(CareRule.plant_id == plant_id, CareRule.paused == False)  # noqa: E712
    ).all()
    stamps = [row.next_due_at for row in rows if row.next_due_at]
    return min(stamps) if stamps else None


async def resolve_tag(session: Session, tag_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """A scanned tag as the card that opens it: what it is and what is owed.

    Every role may scan -- putting a phone on a pot is not an edit. This never
    changes anything: the actions it lists are what the person taps next.
    """
    binding = binding_for_tag(session, tag_id)
    if binding is None:
        raise HardwareBlocked(
            "This tag is not linked to a plant or bed yet.",
            status=404,
            detail={"tag_id": tag_id},
        )
    moment = now or datetime.now(UTC)
    readings = await read_binding_snapshot(binding)
    verdict = moisture_verdict(binding, readings)
    out: dict[str, Any] = {
        "matched": True,
        "tag_id": tag_id,
        "target_kind": binding.target_kind,
        "target_id": binding.target_id,
        "target_name": None,
        "plant": None,
        "location": None,
        "readings": readings,
        "moisture": verdict,
        "next_due_at": None,
        "due_today": False,
        "actions": ["water", "photo"],
    }
    if binding.target_kind == "plant":
        plant = session.get(Plant, binding.target_id)
        if plant is None:
            raise HardwareBlocked("The plant this tag pointed at is gone.", status=404)
        next_due = _next_due_at(session, plant.id)
        due = False
        if next_due:
            parsed = _parse_iso(next_due)
            due = bool(parsed and parsed <= moment)
        out.update(
            {
                "target_name": plant.nickname or plant.species_ref,
                "next_due_at": next_due,
                "due_today": due or bool(verdict.get("due")),
                "plant": {
                    "id": plant.id,
                    "nickname": plant.nickname,
                    "species_ref": plant.species_ref,
                    "category": plant.category,
                    "location_id": plant.location_id,
                    "status": plant.status,
                },
            }
        )
    else:
        location = session.get(Location, binding.target_id)
        if location is None:
            raise HardwareBlocked("The bed this tag pointed at is gone.", status=404)
        plants = session.exec(
            select(Plant).where(Plant.location_id == location.id, Plant.status == "active")
        ).all()
        out.update(
            {
                "target_name": location.name,
                "location": {
                    "id": location.id,
                    "name": location.name,
                    "kind": location.kind,
                    "plant_count": len(plants),
                },
            }
        )
    return out


# --------------------------------------------------------------------------- opt-in auto watering


def target_place(session: Session, binding: HomeHubBinding) -> Any:
    """The garden's own row for a binding's target (``None`` for a hub room).

    Only used to ask the rain question: a bed Garden owns is the only place that
    can be open to the sky.
    """
    if binding.target_kind == "location":
        return session.get(Location, binding.target_id)
    plant = session.get(Plant, binding.target_id)
    return session.get(Location, plant.location_id) if plant and plant.location_id else None


def target_name(session: Session, binding: HomeHubBinding) -> str | None:
    """How a person would name this target (for a notice, never for storage)."""
    if binding.target_kind == "location":
        location = session.get(Location, binding.target_id)
        return location.name if location else None
    plant = session.get(Plant, binding.target_id)
    return (plant.nickname or plant.species_ref) if plant else None


async def _notify_auto_watered(
    get_family: Callable[[], Any] | None,
    *,
    name: str | None,
    minutes: int,
    moisture_pct: float | None,
) -> None:
    """Tell a parent what Jarvis watered on its own, and why."""
    who = name or "a garden bed"
    soil = f" (soil {moisture_pct:g}%)" if moisture_pct is not None else ""
    payload = notify.notice(
        user="",
        category="garden",
        title=f"Auto-watered {who}",
        body=f"Jarvis opened the valve for {minutes} min{soil} because the soil was dry.",
        priority=2,
        kind="garden_auto_water",
        audience="parents",
        link=notify.link("garden"),
    )
    for member in await (get_family or _family)():
        try:
            await notify.send({**payload, "user": member})
        except Exception as exc:
            log.warning("[hardware] auto-water notice to %s failed: %s", member, exc)


async def evaluate_auto_valves(
    session_factory: Callable[[], Any],
    get_family: Callable[[], Any] | None = None,
    on_water: Callable[..., Any] | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Opt-in dry-soil watering: the only path that opens a valve unattended.

    It goes through exactly the same ``run_valve`` as a person tapping Run, so
    the caps, the daily count, the rain interlock and the failsafe all apply
    unchanged. A binding that is ``notify_only`` (the default), has no live
    reading, or is blocked for any reason is simply left alone.
    """
    results: list[dict[str, Any]] = []
    if not enabled():
        return results
    with Session(session_factory()) as session:
        candidates = session.exec(
            select(HomeHubBinding).where(
                HomeHubBinding.valve_mode == "auto",
                HomeHubBinding.moisture_override_enabled == True,  # noqa: E712
                HomeHubBinding.valve_entity_id.is_not(None),
                HomeHubBinding.moisture_entity_id.is_not(None),
            )
        ).all()
        if not candidates:
            return results
        rows = await state_map()
        profile = session.get(GardenProfile, "household")
        for binding in candidates:
            snapshot = snapshot_from(binding, rows)
            verdict = moisture_verdict(binding, snapshot)
            if not verdict.get("due"):
                continue
            name = target_name(session, binding)
            try:
                result = await run_valve(
                    session,
                    binding,
                    performed_by="jarvis-auto",
                    minutes=binding.max_run_minutes,
                    override_rain=False,
                    profile=profile,
                    location=target_place(session, binding),
                    trigger="auto",
                )
            except HardwareBlocked as exc:
                log.info("[hardware] auto-water skipped for %s: %s", name, exc.reason)
                results.append({"binding_id": binding.id, "ran": False, "reason": exc.reason})
                continue
            if on_water is not None:
                payload = on_water(session, binding, result)
                if asyncio.iscoroutine(payload):
                    await payload
            await _notify_auto_watered(
                get_family,
                name=name,
                minutes=int(result["minutes"]),
                moisture_pct=snapshot.get("moisture_pct"),
            )
            results.append({
                "binding_id": binding.id,
                "ran": True,
                "minutes": result["minutes"],
                "moisture_pct": snapshot.get("moisture_pct"),
            })
    return results


async def auto_valve_loop(
    session_factory: Callable[[], Any],
    get_family: Callable[[], Any] | None = None,
    on_water: Callable[..., Any] | None = None,
    poll_s: int = 300,
) -> None:
    """Background loop: the opt-in dry-soil trigger (off under pytest)."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        log.info("[hardware] auto-water loop disabled under pytest")
        return
    while True:
        try:
            await evaluate_auto_valves(session_factory, get_family, on_water)
        except Exception as exc:
            log.warning("[hardware] auto-water tick failed: %s", exc)
        await asyncio.sleep(poll_s)
