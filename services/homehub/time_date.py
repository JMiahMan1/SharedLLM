"""``sensor.time``, ``sensor.date`` and the rest of Home Assistant's ``time_date`` sensors, built in.

HA made these one config entry per display option; automations and templates read them
(``states('sensor.time')``, a state trigger on ``sensor.date``). The hub publishes every option
itself on every hub, imported or fresh, in the household time zone, at the start of each minute,
with the state text HA's sensors had. The imported ``time_date`` entries load as replaced.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("homehub.time_date")

#: object id -> (friendly name, icon, state from (local now, utc now)), as HA's time_date names them.
SENSORS: dict[str, tuple[str, str, Callable[[datetime, datetime], str]]] = {
    "time": ("Time", "mdi:clock", lambda loc, _u: loc.strftime("%H:%M")),
    "date": ("Date", "mdi:calendar", lambda loc, _u: loc.strftime("%Y-%m-%d")),
    "date_time": ("Date & Time", "mdi:calendar-clock", lambda loc, _u: loc.strftime("%Y-%m-%d, %H:%M")),
    "date_time_utc": ("Date & Time (UTC)", "mdi:calendar-clock", lambda _l, utc: utc.strftime("%Y-%m-%d, %H:%M")),
    "date_time_iso": ("Date & Time (ISO)", "mdi:calendar-clock",
                      lambda loc, _u: loc.replace(tzinfo=None).isoformat(timespec="seconds")),
    "time_date": ("Time & Date", "mdi:calendar-clock", lambda loc, _u: loc.strftime("%H:%M, %Y-%m-%d")),
    "time_utc": ("Time (UTC)", "mdi:clock", lambda _l, utc: utc.strftime("%H:%M")),
}


def states_at(now: datetime, time_zone: str) -> dict[str, str]:
    """``entity_id -> state`` for every time and date sensor at ``now`` (an aware datetime)."""
    utc = now.astimezone(UTC).replace(second=0, microsecond=0)
    local = utc.astimezone(ZoneInfo(time_zone))
    return {f"sensor.{oid}": fn(local, utc) for oid, (_name, _icon, fn) in SENSORS.items()}


def publish(hub: Any, now: datetime | None = None) -> None:
    """Set every sensor's state; an entity the import brought keeps its registry name and icon (``Hub.publish``)."""
    values = states_at(now or datetime.now(UTC), hub.time_zone)
    for oid, (name, icon, _fn) in SENSORS.items():
        entity_id = f"sensor.{oid}"
        attrs: dict[str, Any] = {} if entity_id in hub.entities else {"friendly_name": name, "icon": icon}
        hub.publish(entity_id, values[entity_id], attrs)


def seconds_to_next_minute(now: datetime) -> float:
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return max(0.05, (nxt - now).total_seconds())


async def run(hub: Any, *, now: Callable[[], datetime] = lambda: datetime.now(UTC),
              sleep: Callable[[float], Any] = asyncio.sleep) -> None:
    """Publish now, then again at the start of every minute until cancelled."""
    while True:
        try:
            publish(hub, now())
        except Exception:  # one bad tick must not stop the clock
            log.exception("time and date sensors could not update")
        await sleep(seconds_to_next_minute(now()))
