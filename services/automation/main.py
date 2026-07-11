# services/automation/main.py
import asyncio
import json
import logging
from datetime import datetime

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
            if not t.get("recurrence"):
                await rc.delete(key)
                log.info(f"One-time timer {tid} deleted.")
            # Recurring timers: expires_at advancement is deferred (left as-is).
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
            now = datetime.now()
            next_due: datetime | None = None

            for key in keys:
                timer_data = await redis_client.get(key)
                if not timer_data:
                    continue

                t = json.loads(timer_data)
                if not t.get("active", True):
                    continue

                expires = datetime.fromisoformat(t["expires_at"])
                # Ensure naive for comparison
                if expires.tzinfo:
                    expires = expires.replace(tzinfo=None)

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
            delta = (next_due - datetime.now()).total_seconds()
            sleep_s = max(SCHEDULER_INTERVAL_MIN, min(delta, SCHEDULER_INTERVAL_MAX))
        else:
            sleep_s = SCHEDULER_INTERVAL_MAX
        await asyncio.sleep(sleep_s)

if __name__ == "__main__":
    asyncio.run(scheduler_loop())
