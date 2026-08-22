# services/automation/main.py
import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta

import aiohttp
import redis.asyncio as redis

from services.config import EXECUTION_SVC_URL, INTERNAL_SECRET, REDIS_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
log = logging.getLogger("automation")

EXECUTION_SVC = EXECUTION_SVC_URL

# Capped backoff for the scheduler poll: sleep until the next due timer, but
# never longer than SCHEDULER_INTERVAL_MAX (so timers added while asleep are
# still discovered promptly) and never shorter than SCHEDULER_INTERVAL_MIN
# (avoids a busy-loop when a past-due recurring timer is never advanced).
SCHEDULER_INTERVAL_MIN = 1   # seconds
SCHEDULER_INTERVAL_MAX = 60  # seconds

redis_client = None
_http_client: "aiohttp.ClientSession | None" = None


def _get_client() -> aiohttp.ClientSession:
    """Return a shared, pooled HTTP client (no per-trigger session churn)."""
    global _http_client
    if _http_client is None or _http_client.closed:
        _http_client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))
    return _http_client


def _parse_expires(raw: str) -> datetime:
    """Parse an expires_at ISO string into an aware datetime.

    Timestamp convention is UTC-aware everywhere. Legacy entries stored as
    naive container-local time are interpreted as local and normalized.
    """
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.astimezone()  # attach container-local zone for legacy rows
    return dt.astimezone(UTC)


_EVERY_RE = re.compile(r"every\s+(\d+)\s*(minute|min|hour|hr|day|week)s?", re.IGNORECASE)


def _next_recurrence(prev: datetime, recurrence: str) -> datetime:
    """Next occurrence after ``prev`` (UTC-aware) for a free-form rule string.

    Falls back to a daily advance when the rule text isn't recognized — a
    wrong-but-bounded interval beats an unbounded refire storm.
    """
    rule = (recurrence or "").strip().lower()
    step: timedelta | None = None

    every = _EVERY_RE.search(rule)
    if every:
        n = max(1, int(every.group(1)))
        unit = every.group(2)
        step = {
            "minute": timedelta(minutes=n), "min": timedelta(minutes=n),
            "hour": timedelta(hours=n), "hr": timedelta(hours=n),
            "day": timedelta(days=n), "week": timedelta(weeks=n),
        }[unit]
    elif any(w in rule for w in ("hourly", "hour")):
        step = timedelta(hours=1)
    elif any(w in rule for w in ("weekly", "week", "biweek")):
        # biweekly approximated weekly here; keeps the timer bounded
        step = timedelta(days=7)
    elif any(w in rule for w in ("monthly", "month")):
        nxt = prev
        while nxt <= prev:
            y, m = (nxt.year + 1, 1) if nxt.month == 12 else (nxt.year, nxt.month + 1)
            try:
                nxt = nxt.replace(year=y, month=m)
            except ValueError:  # e.g. Jan 31 -> Feb 31
                nxt = nxt.replace(year=y, month=m, day=28)
        return nxt
    elif any(w in rule for w in ("yearly", "annual", "year")):
        try:
            step = None  # handled below via year increment loop
            nxt = prev
            while nxt <= prev:
                try:
                    nxt = nxt.replace(year=nxt.year + 1)
                except ValueError:  # Feb 29
                    nxt = nxt.replace(year=nxt.year + 1, day=28)
            return nxt
        except Exception:
            step = timedelta(days=1)
    else:
        # daily / every day / weekdays / unknown — advance one day.
        # (weekday-only refinement intentionally not attempted on unknown text.)
        step = timedelta(days=1)

    nxt = prev
    guard = 0
    while nxt <= prev and guard < 10000:
        nxt += step
        guard += 1
    return nxt


async def _advance_recurring(rc, key: str, t: dict, fired_at: datetime) -> None:
    """Push a fired recurring timer's expiry past ``fired_at`` so it refires
    at its interval instead of matching due on every poll (~1 Hz forever)."""
    try:
        prev = _parse_expires(t["expires_at"])
        base = max(prev, fired_at)
        nxt = _next_recurrence(base, str(t.get("recurrence") or ""))
        t["expires_at"] = nxt.isoformat()
        await rc.set(key, json.dumps(t))
        log.info(f"Recurring timer {t.get('id')} rescheduled for {t['expires_at']}")
    except Exception as e:
        log.error(f"Failed to advance recurring timer {t.get('id')}: {e}")


async def _fire_timer(rc, key: str, t: dict) -> None:
    """Dispatch a due timer to the Execution Service and clean up if one-shot."""
    tid = t.get("id")
    log.info(f"Triggering Timer: {t.get('title')} ({tid})")
    try:
        resp = await _get_client().post(
            f"{EXECUTION_SVC}/execute/trigger",
            json={"timer": t},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
        if resp.status == 200:
            if t.get("recurrence"):
                await _advance_recurring(rc, key, t, datetime.now(UTC))
            else:
                await rc.delete(key)
                log.info(f"One-time timer {tid} deleted.")
        else:
            text = await resp.text()
            log.error(f"Failed to trigger timer {tid}: {resp.status} {text}")
    except Exception as e:
        log.error(f"Failed to trigger timer {tid}: {e}")


async def scheduler_loop():
    global redis_client
    log.info(f"Automation Scheduler Started. Connecting to Redis: {REDIS_URL}")

    from services.config import resolve_runtime_config
    await resolve_runtime_config()

    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    while True:
        try:
            # 1. List Timers from Redis
            keys = await redis_client.keys("timer:*")
            now = datetime.now(UTC)
            next_due: datetime | None = None

            for key in keys:
                timer_data = await redis_client.get(key)
                if not timer_data:
                    continue

                t = json.loads(timer_data)
                if not t.get("active", True):
                    continue

                # UTC-aware everywhere; legacy naive-local rows are handled
                # inside _parse_expires.
                expires = _parse_expires(t["expires_at"])

                if now >= expires:
                    await _fire_timer(redis_client, key, t)
                elif next_due is None or expires < next_due:
                    next_due = expires
        except Exception as e:
            log.error(f"Scheduler Error: {e}")
            await asyncio.sleep(SCHEDULER_INTERVAL_MIN)
            continue

        # 2. Capped backoff: wake when the next timer is due, bounded so newly
        # added timers are still discovered and past-due recurring timers don't
        # spin the loop.
        if next_due is not None:
            delta = (next_due - datetime.now(UTC)).total_seconds()
            sleep_s = max(SCHEDULER_INTERVAL_MIN, min(delta, SCHEDULER_INTERVAL_MAX))
        else:
            sleep_s = SCHEDULER_INTERVAL_MAX
        await asyncio.sleep(sleep_s)

if __name__ == "__main__":
    asyncio.run(scheduler_loop())
