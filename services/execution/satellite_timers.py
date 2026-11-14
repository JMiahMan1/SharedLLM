"""A timer set by voice shows and rings on the voice satellite that heard it.

Home Assistant tells an ESPHome voice satellite about the timers set through it
(``VoiceAssistantTimerEvent``: started, updated, cancelled, finished), so the
node's LED ring counts down and the node rings when the timer is done. Jarvis
timers live here in execution, so the same events are sent from here.

The contract:

* The hub's voice pipeline names its satellite in ``voice_context.satellite``
  (an ``assist_satellite.*`` entity id). The gateway trusts it only from an
  internal caller and carries it on the person's credentials as
  ``voice_satellite``, which reaches every tool as ``user_context.voice_satellite``.
* ``TimerRequest`` ``add`` stores it on the timer as ``satellite``. A timer
  without one (set from chat or the app) sends nothing.
* Every change to that timer calls the hub's ``assist_satellite.timer_event``
  on that entity with ``event`` (started, updated, cancelled, finished),
  ``timer_id``, ``name``, ``total_seconds``, ``seconds_left`` and ``is_active``.
  The hub's ESPHome driver sends the node's ``VoiceAssistantTimerEvent``.

The timer itself never fails because a satellite could not be told: the result
says so (``detail["satellite"]``) and a warning names the reason.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from services.execution import ha_client

log = logging.getLogger("execution.satellite_timers")

EVENTS = ("started", "updated", "cancelled", "finished")
_SATELLITE = re.compile(r"^assist_satellite\.[a-z0-9_]+$")


def valid_satellite(value: Any) -> str | None:
    """An ``assist_satellite.*`` entity id, or None for anything else."""
    text = str(value or "").strip()
    return text if _SATELLITE.match(text) else None


def _when(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def seconds_left(timer: dict[str, Any], now: datetime | None = None) -> int:
    """Seconds until the timer rings; a paused timer counts from when it was paused."""
    expires = _when(timer.get("expires_at"))
    if expires is None:
        return 0
    paused = _when(timer.get("paused_at")) if not timer.get("active", True) else None
    ref = paused or now or datetime.now(UTC)
    return max(0, round((expires - ref).total_seconds()))


def event_data(timer: dict[str, Any], event: str, now: datetime | None = None) -> dict[str, Any]:
    """The hub's ``assist_satellite.timer_event`` data for one timer."""
    if event not in EVENTS:
        raise ValueError(f"unknown satellite timer event {event!r}; expected one of {', '.join(EVENTS)}")
    total = max(1, int(timer.get("duration_sec") or 0) or seconds_left(timer, now) or 1)
    left = 0 if event in ("finished", "cancelled") else seconds_left(timer, now)
    return {
        "event": event,
        "timer_id": str(timer.get("id") or ""),
        "name": str(timer.get("title") or ""),
        "total_seconds": total,
        "seconds_left": left,
        "is_active": event in ("started", "updated") and bool(timer.get("active", True)),
    }


async def send(timer: dict[str, Any], event: str, ha_url: str | None, ha_token: str | None,
               now: datetime | None = None) -> dict[str, Any] | None:
    """Tell the timer's satellite about ``event``. None when the timer has no satellite.

    Returns ``{"satellite", "event", "ok", "error"?}`` so the caller can report it.
    """
    satellite = valid_satellite(timer.get("satellite"))
    if satellite is None:
        return None
    if not ha_url:
        error = "the hub URL is not configured for this user"
        log.warning("[satellite timer] %s %s for %s not sent: %s", event, timer.get("id"), satellite, error)
        return {"satellite": satellite, "event": event, "ok": False, "error": error}
    result = await ha_client.call_service(ha_url, ha_token or "", "assist_satellite", "timer_event",
                                          entity_id=satellite, service_data=event_data(timer, event, now))
    if result.get("ok"):
        return {"satellite": satellite, "event": event, "ok": True}
    error = str(result.get("error") or "the hub refused it")
    log.warning("[satellite timer] %s %s for %s failed: %s", event, timer.get("id"), satellite, error)
    return {"satellite": satellite, "event": event, "ok": False, "error": error}
