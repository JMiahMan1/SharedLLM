# services/execution/handlers/timer.py
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

import dateparser
import redis.asyncio as redis

from services.config import REDIS_URL
from services.execution.schemas import ExecutionResult, TimerRequest

log = logging.getLogger("execution.timer")

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)(?![a-z])", re.I)


def _parse_duration(text: str | None) -> timedelta | None:
    """"10m", "1h30m", "1 hour 20 minutes", "90 sec" -> a timedelta; else None.

    Every part counts: reading only the first turned "1h30m" into one hour.
    A bare number is minutes, which is what people mean by "timer 10".
    """
    if not text or not text.strip():
        return None
    stripped = text.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
        return timedelta(minutes=float(stripped))
    parts = _DURATION_PART.findall(stripped)
    if not parts or re.search(r"\d\s*(am|pm)\b|:\d", stripped, re.I):
        return None
    total = timedelta()
    for value, unit in parts:
        n = float(value)
        u = unit[0].lower()
        total += timedelta(hours=n) if u == "h" else timedelta(minutes=n) if u == "m" else timedelta(seconds=n)
    return total if total > timedelta() else None


async def notify_timer_done(user: str, timer_data: dict) -> bool:
    """Put "<title> is done" on the person's phone (telemetry outbox).

    A timer set from the app has no speaker, and firing one used to do
    nothing at all: this notice is what makes it ring somewhere.
    """
    import aiohttp

    from services.config import INTERNAL_SECRET, TELEMETRY_SVC_URL

    title = str(timer_data.get("title") or "Timer").strip() or "Timer"
    is_alarm = timer_data.get("type") == "alarm"
    payload = {
        "user": user,
        "kind": "alarm" if is_alarm else "timer",
        "title": f"{'Alarm' if is_alarm else 'Timer'}: {title}",
        "body": f"{title} is done" if not is_alarm else f"{title}",
        "data": {"url": "/communication"},
        # One notice per firing, however often the scheduler retries it.
        "once": f"timer:{timer_data.get('id')}:{timer_data.get('expires_at')}",
    }
    try:
        async with aiohttp.ClientSession() as session, session.post(
            f"{TELEMETRY_SVC_URL.rstrip('/')}/api/telemetry/notify",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return resp.status == 200
    except Exception as e:  # the speaker alert still runs
        log.warning(f"[timer] phone notice for {user} failed: {e}")
        return False


async def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)

async def handle_timer(req: TimerRequest) -> ExecutionResult:
    action = req.action
    log.info(f"[timer] Action: {action}")

    r = await get_redis()

    try:
        if action == "add":
            # 1. Resolve Time
            # Single timestamp convention: UTC-aware everywhere. Legacy Redis
            # entries written as naive-local are handled by the scheduler.
            now = datetime.now(UTC)
            expires_at = now

            duration = _parse_duration(req.duration_str)
            # The Communication page has one box for "10m" *or* "3pm": a value
            # that is not a duration is a clock time.
            time_str = req.time_str or (req.duration_str if req.duration_str and duration is None else None)
            if duration is not None:
                expires_at += duration
            elif time_str:
                dt = dateparser.parse(time_str, settings={"PREFER_DATES_FROM": "future"})
                if dt:
                    # Naive wall-clock input ("3pm") means the container's
                    # local zone; normalize everything to UTC for storage.
                    if dt.tzinfo is None:
                        dt = dt.astimezone()
                    expires_at = dt.astimezone(UTC)

            if expires_at == now:
                return ExecutionResult(status="FAILURE", message="Could not determine timer duration/time.", service="timer_add")

            # 2. Calculate duration in seconds for progress display
            duration_sec = int((expires_at - now).total_seconds())

            # 3. Save to Redis
            user_id = req.user_context.user
            timer_id = str(uuid.uuid4())
            timer_obj = {
                "id": timer_id,
                "user_id": user_id,
                "type": req.type,
                "title": req.title or f"{req.type.title()} {timer_id[:4]}",
                "expires_at": expires_at.isoformat(),
                "active": True,
                "recurrence": req.recurrence,
                "target_device": req.target_device,
                "duration_sec": duration_sec,
            }

            await r.set(f"timer:{user_id}:{timer_id}", json.dumps(timer_obj))

            msg = f"Set {req.type} '{timer_obj['title']}' for {expires_at.strftime('%I:%M %p')}."
            return ExecutionResult(status="SUCCESS", message=msg, service="timer_add", detail={"timer_id": timer_id})

        elif action == "list":
            user_id = req.user_context.user
            keys = await r.keys(f"timer:{user_id}:*")
            timers = []
            for k in keys:
                data = await r.get(k)
                if data: timers.append(json.loads(data))

            if not timers:
                return ExecutionResult(status="SUCCESS", message="No active timers.", service="timer_list")

            lines = [f"- {t['title']}: {t['expires_at']}" for t in timers]
            return ExecutionResult(status="SUCCESS", message="Active Timers:\n" + "\n".join(lines), service="timer_list")

        elif action == "delete":
            # Prefer exact id match; fall back to title substring match.
            user_id = req.user_context.user
            keys = await r.keys(f"timer:{user_id}:*")
            deleted_count = 0
            for k in keys:
                data = await r.get(k)
                if data:
                    t = json.loads(data)
                    if req.id:
                        if t.get("id") == req.id:
                            await r.delete(k)
                            deleted_count += 1
                    elif req.title and req.title.lower() in t['title'].lower():
                        await r.delete(k)
                        deleted_count += 1

            if deleted_count > 0:
                return ExecutionResult(status="SUCCESS", message=f"Deleted {deleted_count} timer(s).", service="timer_delete")
            return ExecutionResult(status="FAILURE", message="No matching timer found.", service="timer_delete")

        elif action == "pause":
            user_id = req.user_context.user
            keys = await r.keys(f"timer:{user_id}:*")
            for k in keys:
                data = await r.get(k)
                if data:
                    t = json.loads(data)
                    if t.get("active") and _targets(t, req):
                        t["active"] = False
                        t["paused_at"] = datetime.now(UTC).isoformat()
                        await r.set(k, json.dumps(t))
                        return ExecutionResult(status="SUCCESS", message=f"Paused timer '{t['title']}'.", service="timer_pause", detail={"timer_id": t["id"]})
            return ExecutionResult(status="FAILURE", message="No active matching timer found to pause.", service="timer_pause")

        elif action == "resume":
            user_id = req.user_context.user
            keys = await r.keys(f"timer:{user_id}:*")
            for k in keys:
                data = await r.get(k)
                if data:
                    t = json.loads(data)
                    if not t.get("active") and t.get("paused_at") and _targets(t, req):
                        try:
                            paused_at = datetime.fromisoformat(t["paused_at"])
                            expires_at = datetime.fromisoformat(t["expires_at"])
                            now = datetime.now(UTC)
                            if not paused_at.tzinfo:
                                paused_at = paused_at.replace(tzinfo=UTC)
                            if not expires_at.tzinfo:
                                expires_at = expires_at.replace(tzinfo=UTC)
                            if not now.tzinfo:
                                now = now.replace(tzinfo=UTC)

                            elapsed_paused = now - paused_at
                            new_expires = expires_at + elapsed_paused
                            t["expires_at"] = new_expires.isoformat()
                        except Exception as err:
                            log.error(f"Error recalculating timer expiry on resume: {err}")

                        t["active"] = True
                        t.pop("paused_at", None)
                        await r.set(k, json.dumps(t))
                        return ExecutionResult(status="SUCCESS", message=f"Resumed timer '{t['title']}'.", service="timer_resume", detail={"timer_id": t["id"]})
            return ExecutionResult(status="FAILURE", message="No paused matching timer found to resume.", service="timer_resume")

        return ExecutionResult(status="FAILURE", message=f"Unknown timer action: {action}", service="timer")

    except Exception as e:
        log.error(f"Timer error: {e}")
        return ExecutionResult(status="FAILURE", message=f"Timer error: {e!s}", service="timer")

def _targets(timer: dict, req) -> bool:
    """Whether a pause/resume request names this timer.

    An id is exact (the dashboard always sends one); otherwise the title is a
    case-insensitive substring, and no title at all means "any". Pause used to
    ignore the id, so pausing "Timer 1" could pause "Timer 10", and resume
    ignored the title too and woke whichever paused timer came first.
    """
    if req.id:
        return timer.get("id") == req.id
    return not req.title or req.title.lower() in str(timer.get("title", "")).lower()


async def get_active_timers(user_id: str | None = None):
    r = await get_redis()
    pattern = f"timer:{user_id}:*" if user_id else "timer:*:*"
    keys = await r.keys(pattern)
    timers = []
    for k in keys:
        data = await r.get(k)
        if data:
            timers.append(json.loads(data))
    return timers
