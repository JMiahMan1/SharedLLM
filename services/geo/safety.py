"""Family safety state kept by geo: important places and long stops.

Important places are Home Assistant zones someone marked in Wander's place
list; arriving at or leaving one sends a ``family_safety`` notice to the
family members chosen for that place (everyone who may see the person when
none are chosen). A long stop is a person staying put for LONG_STOP_S
somewhere that is not a zone, which the family hears about once per stop,
during the day unless LONG_STOP_ACTIVE_HOURS says otherwise.

The functions here only read and write Redis; geo's main module wires them to
fixes, zones, sharing consent and notices.
"""
from __future__ import annotations

import json
import os

#: Hash zone entity id -> {"important": bool, "notify": [usernames]}.
PLACE_SETTINGS_KEY = "geo:place_settings"
#: Per person: where they have been since when, and whether it was announced.
STOP_KEY = "geo:stop:{user}"

#: Fixes within this distance of where a stop began are the same stop.
STOP_RADIUS_M = float(os.environ.get("LONG_STOP_RADIUS_M", "150"))
#: How long someone stays put somewhere unknown before the family hears.
LONG_STOP_S = float(os.environ.get("LONG_STOP_HOURS", "2")) * 3600
#: Stops older than this with no fix since are a phone that went quiet, not
#: a person waiting somewhere; they are left alone.
STOP_MAX_SILENCE_S = 6 * 3600


def _active_hours(spec: str | None = None) -> tuple[int, int]:
    """LONG_STOP_ACTIVE_HOURS "8-21" as (8, 21); "0-24" means any hour."""
    raw = spec if spec is not None else os.environ.get("LONG_STOP_ACTIVE_HOURS", "8-21")
    try:
        start, end = (int(part) for part in raw.split("-", 1))
    except ValueError:
        return 8, 21
    return max(0, min(24, start)), max(0, min(24, end))


def in_active_hours(hour: int, spec: str | None = None) -> bool:
    start, end = _active_hours(spec)
    return start <= hour < end


def _decode(value):
    return value.decode() if isinstance(value, bytes) else value


async def place_settings(r) -> dict[str, dict]:
    """Every zone's settings; a zone never marked is absent."""
    raw = await r.hgetall(PLACE_SETTINGS_KEY) if r else {}
    out = {}
    for key, value in (raw or {}).items():
        try:
            entry = json.loads(_decode(value))
        except (TypeError, ValueError):
            continue
        if isinstance(entry, dict):
            out[_decode(key)] = {
                "important": bool(entry.get("important")),
                "notify": [str(u).lower() for u in entry.get("notify") or [] if str(u).strip()],
            }
    return out


async def save_place_setting(r, zone_id: str, important: bool, notify: list[str] | None) -> dict:
    entry = {
        "important": bool(important),
        "notify": sorted({str(u).strip().lower() for u in notify or [] if str(u).strip()}),
    }
    await r.hset(PLACE_SETTINGS_KEY, zone_id, json.dumps(entry))
    return entry


def annotate_zone_features(features: list[dict], settings: dict[str, dict]) -> list[dict]:
    """Add ``important`` and ``notify`` to each zone feature's properties."""
    for feature in features:
        props = feature.setdefault("properties", {})
        entry = settings.get(props.get("entity_id") or "", {})
        props["important"] = bool(entry.get("important"))
        props["notify"] = list(entry.get("notify") or [])
    return features


async def note_fix(r, user: str, lat: float, lon: float, ts: float, distance) -> dict:
    """Move the person's stop along with a new fix; returns the stop.

    A fix within STOP_RADIUS_M of where the stop began extends it; anything
    further starts a new one. ``distance(lat1, lon1, lat2, lon2)`` in metres.
    """
    key = STOP_KEY.format(user=user)
    raw = await r.get(key)
    stop = json.loads(raw) if raw else None
    if stop and ts < float(stop.get("t", 0)):
        return stop  # a late fix
    if stop and distance(stop["lat"], stop["lon"], lat, lon) <= STOP_RADIUS_M:
        stop["t"] = ts
    else:
        stop = {"lat": lat, "lon": lon, "since": ts, "t": ts, "notified": False}
    await r.set(key, json.dumps(stop), ex=int(STOP_MAX_SILENCE_S * 2))
    return stop


async def stops(r) -> dict[str, dict]:
    """Every person's current stop."""
    out = {}
    async for key in r.scan_iter(match=STOP_KEY.format(user="*")):
        name = _decode(key).split(":", 2)[-1]
        raw = await r.get(key)
        if raw:
            out[name] = json.loads(raw)
    return out


def stop_is_due(stop: dict, now: float) -> bool:
    """A stop long enough to mention that has not been, with a fix recent
    enough to say the person is still there."""
    return (
        not stop.get("notified")
        and now - float(stop.get("since", now)) >= LONG_STOP_S
        and now - float(stop.get("t", 0)) <= STOP_MAX_SILENCE_S
    )


async def mark_stop_notified(r, user: str, stop: dict) -> None:
    stop["notified"] = True
    await r.set(STOP_KEY.format(user=user), json.dumps(stop), ex=int(STOP_MAX_SILENCE_S * 2))
