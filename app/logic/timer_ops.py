import time
import uuid
import re
import asyncio
from datetime import datetime, timedelta
import dateparser
from typing import Dict, List, Optional, Union

from settings import log, GlobalResources, get_user_creds
from .timer_storage import storage
from .alarm_audio import audio_manager
from .media_ops import get_last_entity

# Constants
DEFAULT_TIMER_DURATION = 600  # 10 mins


async def trigger_alarm(timer: Dict):
    """
    Called by scheduler when a timer expires.
    (FIXED: Guaranteed cleanup via try/finally).
    """
    timer_id = timer["id"]
    log.info(f"ALARM TRIGGERED: {timer['title']} (ID: {timer_id})")

    # Check recurrence first to decide if we keep it or not.
    recurrence = timer.get("recurrence")
    is_one_time = recurrence is None

    try:
        # 1. Play Audio (This block is what often fails due to HA issues)
        creds = get_user_creds()  # Uses admin/default creds for system alarms
        await audio_manager.play_alarm_sequence(timer, creds, GlobalResources.redis_client)

    except Exception as e:
        log.critical(f"Critical unhandled exception during alarm audio sequence for {timer_id}: {e}")

    finally:
        # 2. Handle Recurrence or Clean up (GUARANTEED EXECUTION)
        if is_one_time:
            # Delete one-time timers regardless of playback success
            await storage.delete_timer(timer_id)
            log.info(f"Cleaned up one-time timer: {timer_id}")

        elif recurrence:
            # TODO: Implement robust RRULE parsing.
            # For now, simplistic 'daily' logic if keyword present, else manual edit required
            if "daily" in recurrence.lower() or "every day" in recurrence.lower():
                try:
                    new_expiry = datetime.fromisoformat(timer["expires_at"]) + timedelta(days=1)
                    await storage.update_timer(timer_id, {
                        "expires_at": new_expiry.isoformat(),
                        "active": True
                    })
                    log.info(f"Rescheduled recurring alarm '{timer['title']}' to {new_expiry}")
                except Exception as e:
                    log.error(f"Failed to reschedule recurring alarm {timer_id}: {e}")


async def tool_timer_add(query: str, user_creds: Dict[str, str], model: str, redis_client) -> Dict[str, Union[str, bool]]:
    """
    Adds a timer or alarm based on natural language query.
    Fully robust: handles durations (seconds, minutes, hours), absolute times (6am, 8:30 pm), and recurrence.
    Preserves alarm vs timer logic.
    """
    now = datetime.now()
    query_lower = query.lower()

    # Clean introductory words
    clean_parse_input = re.sub(
        r'^\s*[\d\.\s]*\s*(can you|please|i want to|start|set|a|an)\s*', '', query_lower, flags=re.IGNORECASE
    ).strip()

    expires_at = None
    is_alarm = False

    # --- 1. Try duration parsing ---
    duration_match = re.search(r'(\d+)\s*(second|minute|hour)s?', clean_parse_input)
    if duration_match:
        num = int(duration_match.group(1))
        unit = duration_match.group(2)
        if unit == "second":
            expires_at = now + timedelta(seconds=num)
        elif unit == "minute":
            expires_at = now + timedelta(minutes=num)
        elif unit == "hour":
            expires_at = now + timedelta(hours=num)
    else:
        # --- 2. Try absolute time parsing ---
        dt = dateparser.parse(
            clean_parse_input,
            settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now}
        )
        if dt:
            expires_at = dt

    if not expires_at:
        return {"status": "FAILURE", "message": "Could not understand the time or duration.", "service": "timer_add"}

    # --- 3. Determine Title ---
    title_temp = re.sub(
        r'\b(timer|alarm|for|in|at|am|pm|tomorrow|tonight|o\'clock|second[s]?|minute[s]?|hour[s]?)\b',
        '', clean_parse_input, flags=re.IGNORECASE
    )
    title = re.sub(r'[\s\d\-\.]+', ' ', title_temp).strip()
    if not title:
        title = "Timer"

    # --- 4. Determine Alarm vs Timer ---
    time_difference = (expires_at - now).total_seconds()
    is_absolute_time_syntax = any(word in query_lower for word in ['am', 'pm', 'tonight', 'tomorrow'])
    if time_difference > 3600 or is_absolute_time_syntax:
        is_alarm = True
        if expires_at < now and is_absolute_time_syntax:
            expires_at += timedelta(days=1)

    # --- 5. Determine Origin Device ---
    origin = get_last_entity(redis_client, user_creds.get("user"))

    # --- 6. Create Timer Object ---
    timer_obj = {
        "id": str(uuid.uuid4()),
        "type": "alarm" if is_alarm else "timer",
        "title": title,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "origin_device": origin,
        "active": True,
        "recurrence": "daily" if "every day" in query_lower else None
    }

    await storage.add_timer(timer_obj)
    time_str = expires_at.strftime("%I:%M %p")
    msg = f"Set {timer_obj['type']} '{title}' for {time_str}."
    return {"status": "SUCCESS", "message": msg, "service": "timer_add", "timer_id": timer_obj["id"]}


async def tool_timer_list(user_creds: Dict[str, str]) -> Dict[str, Union[str, bool]]:
    timers = await storage.list_timers()
    if not timers:
        return {"status": "SUCCESS", "message": "No active timers or alarms.", "service": "timer_list"}

    lines = []
    now = datetime.now()
    for t in timers:
        exp = datetime.fromisoformat(t["expires_at"])
        remaining = exp - now
        if remaining.total_seconds() < 0:
            continue  # Skip stale/processing
        rem_str = str(remaining).split('.')[0]
        lines.append(f"- {t['title']} ({t['type']}): expires in {rem_str} at {exp.strftime('%I:%M %p')}")

    return {"status": "SUCCESS", "message": "Active Timers:\n" + "\n".join(lines), "service": "timer_list"}


async def tool_timer_delete(query: str, user_creds: Dict[str, str]) -> Dict[str, Union[str, bool]]:
    timers = await storage.list_timers()
    target_id = None
    query_low = query.lower()

    # Find best match by title
    for t in timers:
        if t["title"].lower() in query_low or query_low in t["title"].lower():
            target_id = t["id"]
            break

    # If no title match, delete the only timer if there's just one
    if not target_id and len(timers) == 1:
        target_id = timers[0]["id"]

    if target_id:
        await storage.delete_timer(target_id)
        return {"status": "SUCCESS", "message": "Timer deleted.", "service": "timer_delete"}

    return {"status": "FAILURE", "message": "Could not find a matching timer to delete.", "service": "timer_delete"}


async def tool_timer_pause(query: str) -> Dict[str, Union[str, bool]]:
    # Simple pause: just deactivate timer
    return {"status": "FAILURE", "message": "Pause functionality not yet fully implemented.", "service": "timer_pause"}


async def tool_timer_resume(query: str) -> Dict[str, Union[str, bool]]:
    return {"status": "FAILURE", "message": "Resume functionality not yet fully implemented.", "service": "timer_resume"}

