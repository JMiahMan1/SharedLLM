# services/automation/main.py
import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta

import aiohttp
import redis.asyncio as redis

from services.config import (
    EXECUTION_SVC_URL,
    GEO_SVC_URL,
    IDENTITY_SVC_URL,
    INTERNAL_SECRET,
    RAG_SVC_URL,
    REDIS_URL,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
log = logging.getLogger("automation")

EXECUTION_SVC = EXECUTION_SVC_URL

# Capped backoff for the scheduler poll: sleep until the next due timer, but
# never longer than SCHEDULER_INTERVAL_MAX (so timers added while asleep are
# still discovered promptly) and never shorter than SCHEDULER_INTERVAL_MIN
# (avoids a busy-loop when a past-due recurring timer is never advanced).
SCHEDULER_INTERVAL_MIN = 1   # seconds
SCHEDULER_INTERVAL_MAX = 60  # seconds

# Failure handling for dispatched jobs. A dispatch that fails is retried with
# exponential backoff until the attempt budget is spent, then the timer is parked
# (inactive, kept briefly for inspection) instead of firing forever.
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 1800
DEFAULT_MAX_ATTEMPTS = 5
FAILED_TIMER_TTL_SECONDS = 7 * 24 * 3600
HISTORY_MAX_ENTRIES = 20
HISTORY_SCAN_BATCH = 200

ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# Services a timer may target. Keeps dispatch off arbitrary URLs (no SSRF) while
# letting scheduled jobs reach any internal service that exposes an endpoint.
DISPATCH_TARGETS: dict[str, str] = {
    "execution": (EXECUTION_SVC_URL or "").rstrip("/"),
    "identity": (IDENTITY_SVC_URL or "").rstrip("/"),
    "geo": (GEO_SVC_URL or "").rstrip("/"),
    "rag": (RAG_SVC_URL or "").rstrip("/"),
}
DISPATCH_TARGETS = {k: v for k, v in DISPATCH_TARGETS.items() if v}

redis_client = None
_http_client: "aiohttp.ClientSession | None" = None


def _get_client(timeout: float = 10.0) -> aiohttp.ClientSession:
    """Return a shared, pooled HTTP client (no per-trigger session churn)."""
    global _http_client
    if _http_client is None or _http_client.closed:
        _http_client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
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


def _history_key(key: str) -> str:
    return f"{key}:history"


async def _record_run(rc, key: str, entry: dict) -> None:
    """Append a run record to the timer's bounded history list."""
    hkey = _history_key(key)
    try:
        await rc.rpush(hkey, json.dumps(entry))
        await rc.ltrim(hkey, -HISTORY_MAX_ENTRIES, -1)
        await rc.expire(hkey, FAILED_TIMER_TTL_SECONDS)
    except Exception as e:  # history must never block dispatch
        log.warning(f"Failed to record run history for {key}: {e}")


def _retry_delay_seconds(attempts: int) -> int:
    """Exponential backoff for attempt N (1-based), capped at RETRY_MAX_SECONDS."""
    delay = RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1))
    return int(min(delay, RETRY_MAX_SECONDS))


def _resolve_dispatch(t: dict) -> tuple[str, str, dict]:
    """Resolve a timer to (method, url, payload).

    Backward compatible: a timer without an explicit ``target`` fires the
    Execution alarm trigger exactly as before. With a ``target`` the service must
    be allowlisted and the path must be a plain absolute path, so a timer record
    can never be used to reach an arbitrary host.
    """
    target = t.get("target")
    if not target:
        return "POST", f"{EXECUTION_SVC}/execute/trigger", {"timer": t}

    if not isinstance(target, dict):
        raise ValueError("target must be an object")

    service = str(target.get("service") or "").strip().lower()
    path = str(target.get("path") or "").strip()
    method = str(target.get("method") or "POST").strip().upper()

    if service not in DISPATCH_TARGETS:
        raise ValueError(f"target service not allowed: {service!r}")
    if not path.startswith("/") or "://" in path:
        raise ValueError("target path must be a relative absolute path")
    if method not in ALLOWED_METHODS:
        raise ValueError(f"target method not allowed: {method!r}")

    payload = target.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("target payload must be an object")
    body = dict(payload or {})
    body.setdefault("timer", t)
    body.setdefault("user_id", t.get("user_id"))
    return method, f"{DISPATCH_TARGETS[service]}{path}", body


async def _mark_failed(
    rc,
    key: str,
    t: dict,
    error: str,
    fired_at: datetime,
    entry: dict | None = None,
) -> None:
    """Park a timer whose attempt budget is exhausted; recurring ones still advance."""
    t["attempts"] = int(t.get("attempts") or 0)
    t["failed"] = True
    t["active"] = False
    t["failed_at"] = fired_at.isoformat()
    t["last_error"] = error[:500]
    if t.get("recurrence"):
        with contextlib.suppress(Exception):
            await _advance_recurring(rc, key, t, fired_at)
            t["active"] = True  # keep recurring timers running after a failed run
            t["failed"] = False
            t["attempts"] = 0
    with contextlib.suppress(Exception):
        await rc.set(key, json.dumps(t), ex=FAILED_TIMER_TTL_SECONDS)
    record = dict(entry or {
        "fired_at": fired_at.isoformat(),
        "error": error[:500],
        "attempts": t["attempts"],
    })
    record["status"] = "failed"
    await _record_run(rc, key, record)
    log.error(f"Timer {t.get('id')} exhausted attempts: {error}")


async def _handle_failure(
    rc,
    key: str,
    t: dict,
    error: str,
    fired_at: datetime,
    http_status: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Record a failed dispatch and schedule a backed-off retry if budget remains."""
    attempts = int(t.get("attempts") or 0) + 1
    max_attempts = int(t.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    t["attempts"] = attempts
    t["last_error"] = error[:500]
    t["last_status"] = "error"
    t["last_attempt_at"] = fired_at.isoformat()

    entry = {
        "fired_at": fired_at.isoformat(),
        "http_status": http_status,
        "error": error[:500],
        "attempts": attempts,
        "duration_ms": duration_ms,
    }

    if attempts >= max_attempts:
        await _mark_failed(rc, key, t, error, fired_at, entry)
        return

    delay = _retry_delay_seconds(attempts)
    t["expires_at"] = (fired_at + timedelta(seconds=delay)).isoformat()
    with contextlib.suppress(Exception):
        await rc.set(key, json.dumps(t))
    entry["status"] = "retry"
    entry["retry_in_seconds"] = delay
    await _record_run(rc, key, entry)
    log.warning(f"Timer {t.get('id')} dispatch failed ({error}); retry {attempts}/{max_attempts} in {delay}s")


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
    """Dispatch a due timer to its target service, with retry + run history."""
    tid = t.get("id")
    log.info(f"Triggering Timer: {t.get('title')} ({tid})")
    fired_at = datetime.now(UTC)
    started = time.monotonic()

    try:
        method, url, payload = _resolve_dispatch(t)
    except ValueError as e:
        # A bad target is a configuration error: retrying cannot fix it, so park
        # the timer immediately instead of burning the attempt budget.
        log.error(f"Timer {tid} has an invalid dispatch target: {e}")
        t["attempts"] = int(t.get("attempts") or 0) + 1
        t["failed"] = True
        t["active"] = False
        t["failed_at"] = fired_at.isoformat()
        t["last_error"] = f"invalid target: {e}"[:500]
        t["last_status"] = "invalid_target"
        with contextlib.suppress(Exception):
            await rc.set(key, json.dumps(t), ex=FAILED_TIMER_TTL_SECONDS)
        await _record_run(rc, key, {
            "fired_at": fired_at.isoformat(),
            "status": "invalid_target",
            "error": str(e),
            "attempts": t["attempts"],
        })
        return

    try:
        client = _get_client(timeout=float(t.get("timeout_seconds") or 30.0))
        resp = await client.request(
            method,
            url,
            json=payload if method != "GET" else None,
            params=payload if method == "GET" else None,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        if 200 <= resp.status < 300:
            t["attempts"] = 0
            t["last_status"] = "success"
            t["last_error"] = None
            t["last_run_at"] = fired_at.isoformat()
            if t.get("recurrence"):
                await _advance_recurring(rc, key, t, fired_at)
            else:
                await rc.delete(key)
                log.info(f"One-time timer {tid} deleted.")
            await _record_run(rc, key, {
                "fired_at": fired_at.isoformat(),
                "status": "success",
                "http_status": resp.status,
                "duration_ms": duration_ms,
            })
        else:
            text = await resp.text()
            await _handle_failure(
                rc,
                key,
                t,
                f"HTTP {resp.status}: {text[:200]}",
                fired_at,
                http_status=resp.status,
                duration_ms=duration_ms,
            )
    except Exception as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        await _handle_failure(
            rc,
            key,
            t,
            f"{type(e).__name__}: {e}",
            fired_at,
            duration_ms=duration_ms,
        )


async def _iter_timer_keys(rc, pattern: str = "timer:*"):
    """Iterate timer keys with SCAN so a large keyspace never blocks the loop."""
    batch: list[str] = []
    async for key in rc.scan_iter(match=pattern, count=HISTORY_SCAN_BATCH):
        if key.endswith(":history"):
            continue
        batch.append(key)
        if len(batch) >= HISTORY_SCAN_BATCH:
            for item in batch:
                yield item
            batch = []
    for item in batch:
        yield item


async def _collect_status(rc) -> dict:
    now = datetime.now(UTC)
    active = 0
    due = 0
    failed = 0
    next_due: str | None = None
    for key in [k async for k in _iter_timer_keys(rc)]:
        raw = await rc.get(key)
        if not raw:
            continue
        try:
            t = json.loads(raw)
        except Exception:
            continue
        if t.get("failed"):
            failed += 1
        if not t.get("active", True):
            continue
        active += 1
        try:
            expires = _parse_expires(t["expires_at"])
        except Exception:
            continue
        if now >= expires:
            due += 1
        elif next_due is None or expires.isoformat() < next_due:
            next_due = expires.isoformat()
    return {
        "active_timers": active,
        "due_now": due,
        "failed_timers": failed,
        "next_due_at": next_due,
        "dispatch_targets": sorted(DISPATCH_TARGETS),
    }


async def _list_timers(rc, user_id: str | None = None) -> list[dict]:
    pattern = f"timer:{user_id}:*" if user_id else "timer:*"
    now = datetime.now(UTC)
    out: list[dict] = []
    for key in [k async for k in _iter_timer_keys(rc, pattern)]:
        raw = await rc.get(key)
        if not raw:
            continue
        try:
            t = json.loads(raw)
        except Exception:
            continue
        try:
            expires = _parse_expires(t["expires_at"])
            t["due"] = bool(t.get("active", True) and now >= expires)
        except Exception:
            t["due"] = False
        out.append(t)
    return out


async def scheduler_loop():
    global redis_client
    log.info(f"Automation Scheduler Started. Connecting to Redis: {REDIS_URL}")

    from services.config import resolve_runtime_config
    await resolve_runtime_config()

    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    while True:
        try:
            # 1. List Timers from Redis (SCAN, not KEYS: never block the loop)
            now = datetime.now(UTC)
            next_due: datetime | None = None

            for key in _iter_timer_keys(redis_client):
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


# ── HTTP surface ────────────────────────────────────────────────────────────
# The scheduler used to be a bare script, so nothing could observe it. These
# endpoints make the schedule inspectable and give scheduled jobs a status
# channel without touching the dispatch hot path.

def _internal_ok(x_internal_secret: str | None) -> bool:
    expected = INTERNAL_SECRET or ""
    provided = x_internal_secret or ""
    if not expected:
        return False
    return hmac.compare_digest(provided, expected)


def _create_app():
    from fastapi import Depends, FastAPI, Header, HTTPException, Query

    async def require_internal(x_internal_secret: str | None = Header(None)):
        if not _internal_ok(x_internal_secret):
            raise HTTPException(status_code=401, detail="Invalid internal secret")
        return True

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(scheduler_loop())
        app.state.scheduler_task = task
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    application = FastAPI(title="Automation Scheduler", lifespan=lifespan)

    @application.get("/health")
    async def health():
        return {"status": "ok", "service": "automation"}

    @application.get("/api/automation/status")
    async def status(_: bool = Depends(require_internal)):
        rc = redis.from_url(REDIS_URL, decode_responses=True)
        try:
            return await _collect_status(rc)
        finally:
            await rc.aclose()

    @application.get("/api/automation/timers")
    async def timers(
        user_id: str | None = Query(default=None),
        _: bool = Depends(require_internal),
    ):
        rc = redis.from_url(REDIS_URL, decode_responses=True)
        try:
            return {"timers": await _list_timers(rc, user_id)}
        finally:
            await rc.aclose()

    @application.get("/api/automation/timers/{timer_id}/history")
    async def timer_history(timer_id: str, _: bool = Depends(require_internal)):
        rc = redis.from_url(REDIS_URL, decode_responses=True)
        try:
            keys = [k async for k in _iter_timer_keys(rc) if k.endswith(f":{timer_id}")]
            if not keys:
                raise HTTPException(status_code=404, detail="Timer not found")
            raw = await rc.lrange(_history_key(keys[0]), 0, -1)
            return {"history": [json.loads(r) for r in raw if r]}
        finally:
            await rc.aclose()

    return application


app = _create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("AUTOMATION_HOST", "0.0.0.0"),
        port=int(os.getenv("AUTOMATION_PORT", "11437")),
    )
