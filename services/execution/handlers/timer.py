# services/execution/handlers/timer.py
import os
import sys
import logging
import json
import uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import REDIS_URL
from datetime import datetime, timedelta
import redis.asyncio as redis
from typing import Optional

try:
    from schemas import TimerRequest, ExecutionResult
except ImportError:
    from schemas import TimerRequest, ExecutionResult

log = logging.getLogger("execution.timer")

async def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)

async def handle_timer(req: TimerRequest) -> ExecutionResult:
    action = req.action
    log.info(f"[timer] Action: {action}")
    
    r = await get_redis()
    
    try:
        if action == "add":
            # 1. Resolve Time
            now = datetime.now()
            expires_at = now
            
            if req.duration_str:
                # Basic duration parsing (e.g. "10m", "5s")
                # This should ideally be handled by Gateway extraction
                # but we'll add a safety fallback here.
                import re
                m = re.search(r"(\d+)\s*(m|s|h)", req.duration_str)
                if m:
                    val, unit = int(m.group(1)), m.group(2)
                    if unit == "m": expires_at += timedelta(minutes=val)
                    elif unit == "s": expires_at += timedelta(seconds=val)
                    elif unit == "h": expires_at += timedelta(hours=val)
            elif req.time_str:
                import dateparser
                dt = dateparser.parse(req.time_str, settings={'PREFER_DATES_FROM': 'future'})
                if dt: expires_at = dt
            
            if expires_at == now:
                return ExecutionResult(status="FAILURE", message="Could not determine timer duration/time.", service="timer_add")

            # 2. Save to Redis
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
                "target_device": req.target_device
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
            # For simplicity, delete by title match
            user_id = req.user_context.user
            keys = await r.keys(f"timer:{user_id}:*")
            deleted_count = 0
            for k in keys:
                data = await r.get(k)
                if data:
                    t = json.loads(data)
                    if req.title and req.title.lower() in t['title'].lower():
                        await r.delete(k)
                        deleted_count += 1
            
            if deleted_count > 0:
                return ExecutionResult(status="SUCCESS", message=f"Deleted {deleted_count} timer(s).", service="timer_delete")
            return ExecutionResult(status="FAILURE", message="No matching timer found.", service="timer_delete")

        return ExecutionResult(status="FAILURE", message=f"Action {action} not yet implemented.", service="timer")

    except Exception as e:
        log.error(f"Timer error: {e}")
        return ExecutionResult(status="FAILURE", message=f"Timer error: {str(e)}", service="timer")

async def get_active_timers(user_id: Optional[str] = None):
    r = await get_redis()
    pattern = f"timer:{user_id}:*" if user_id else "timer:*:*"
    keys = await r.keys(pattern)
    timers = []
    for k in keys:
        data = await r.get(k)
        if data:
            timers.append(json.loads(data))
    return timers
