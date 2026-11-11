"""The last states Home Assistant reported, kept for when it stops answering.

With HA down, "is the garage door open?" had no answer at all. HA's states
are read all the time (FastPath entity sync, discovery, searches), so every
successful full read is kept here - at most once a minute per HA host - and
the routes that answer a person can say what the house looked like *and when*
("closed, as of 4 minutes ago") instead of nothing.

This is never used silently: callers that fall back to it must say the answer
is a last-known one and how old it is. Acting on it (e.g. toggling based on a
stale state) is not allowed; it is for answering questions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from services.execution.http_client import host_of

log = logging.getLogger("execution.ha_snapshot")

SAVE_INTERVAL_S = 60.0
#: A snapshot older than this is not offered at all: a week-old "the door is
#: closed" is not an answer.
MAX_AGE_S = 7 * 24 * 3600

#: Attributes worth keeping for answering questions; the rest (entity_picture,
#: source lists, forecasts...) is what makes a full dump large.
_KEEP_ATTRS = {
    "friendly_name", "device_class", "unit_of_measurement", "area_id", "brightness", "color_temp",
    "temperature", "current_temperature", "hvac_action", "hvac_modes", "preset_mode", "humidity",
    "current_humidity", "current_position", "percent_open", "volume_level", "is_volume_muted",
    "media_title", "media_artist", "app_id", "source", "supported_features", "battery_level",
}

_last_saved: dict[str, float] = {}
_tasks: set[asyncio.Task] = set()


def _key(ha_url: str) -> str:
    return f"ha:last_states:{host_of(ha_url)}"


def slim(states: list[dict]) -> list[dict]:
    out = []
    for s in states:
        if not isinstance(s, dict) or not s.get("entity_id"):
            continue
        attrs = s.get("attributes") or {}
        out.append({
            "entity_id": s["entity_id"],
            "state": s.get("state"),
            "attributes": {k: v for k, v in attrs.items() if k in _KEEP_ATTRS},
            "last_changed": s.get("last_changed"),
            "last_updated": s.get("last_updated"),
        })
    return out


async def _redis():
    from redis import asyncio as aioredis

    from services import config

    return aioredis.from_url(config.REDIS_URL, decode_responses=True)


async def save(ha_url: str, states: list[dict]) -> None:
    r = None
    try:
        r = await _redis()
        await r.set(_key(ha_url), json.dumps({"at": time.time(), "states": slim(states)}), ex=MAX_AGE_S)
    except Exception as e:
        log.debug(f"[ha_snapshot] save failed: {e}")
    finally:
        if r is not None:
            with contextlib.suppress(Exception):
                await r.aclose()


def remember(ha_url: str, states: list[dict]) -> None:
    """Keep this read in the background, at most once per SAVE_INTERVAL_S per host."""
    if not ha_url or not states:
        return
    now = time.monotonic()
    host = host_of(ha_url)
    if now - _last_saved.get(host, -SAVE_INTERVAL_S) < SAVE_INTERVAL_S:
        return
    _last_saved[host] = now
    try:
        task = asyncio.get_running_loop().create_task(save(ha_url, states))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def recall(ha_url: str) -> tuple[list[dict], float] | None:
    """(states, unix time they were read) or None when there is nothing usable."""
    r = None
    try:
        r = await _redis()
        raw = await r.get(_key(ha_url))
        if not raw:
            return None
        data = json.loads(raw)
        at = float(data.get("at") or 0)
        if time.time() - at > MAX_AGE_S:
            return None
        return list(data.get("states") or []), at
    except Exception as e:
        log.debug(f"[ha_snapshot] recall failed: {e}")
        return None
    finally:
        if r is not None:
            with contextlib.suppress(Exception):
                await r.aclose()


def age_phrase(at: float) -> str:
    """'just now' / '4 minutes ago' / '2 hours ago' / '3 days ago'."""
    seconds = max(0, int(time.time() - at))
    if seconds < 90:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def reset() -> None:
    _last_saved.clear()
