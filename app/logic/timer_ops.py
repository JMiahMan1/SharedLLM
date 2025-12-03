# app/logic/timer_ops.py
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
    """
    timer_id = timer["id"]
    log.info(f"ALARM TRIGGERED: {timer['title']} (ID: {timer_id})")

    recurrence = timer.get("recurrence")
    is_one_time = recurrence is None

    try:
        # 1. Play Audio
        creds = get_user_creds() 
        await audio_manager.play_alarm_sequence(timer, creds, GlobalResources.redis_client)

    except Exception as e:
        log.critical(f"Critical unhandled exception during alarm audio sequence for {timer_id}: {e}")

    finally:
        # 2. Cleanup (GUARANTEED)
        if is_one_time:
            await storage.delete_timer(timer_id)
            log.info(f"Cleaned up one-time timer: {timer_id}")

        elif recurrence:
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
    Uses robust search to find time units anywhere in the string.
    """
    now = datetime.now()
    query_lower = query.lower()

    expires_at = None
    is_alarm = False
    
    # --- 1. Robust Duration Extraction (Scan Anywhere) ---
    # We look for units independently to handle "1 minute 30 seconds" or "30-second" or "timer for 5 minutes"
    
    hours = 0
    minutes = 0
    seconds = 0
    found_duration = False
    
    # Extract Hours
    h_match = re.search(r'(\d+)\s*(?:hours?|hrs?)', query_lower)
    if h_match:
        hours = int(h_match.group(1))
        found_duration = True
        
    # Extract Minutes
    m_match = re.search(r'(\d+)\s*(?:minutes?|mins?)', query_lower)
    if m_match:
        minutes = int(m_match.group(1))
        found_duration = True
        
    # Extract Seconds (handles "30-second" via -?)
    s_match = re.search(r'(\d+)\s*-?\s*(?:seconds?|secs?)', query_lower)
    if s_match:
        seconds = int(s_match.group(1))
        found_duration = True

    if found_duration:
        expires_at = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)
        log.info(f"TimerAdd: Regex found duration: {hours}h {minutes}m {seconds}s")

    # --- 2. Fallback to Dateparser (Absolute Time) ---
    if not expires_at:
        # Remove confusing command words to help dateparser focus on the date
        dp_input = re.sub(r'\b(timer|alarm|wake me|remind me|set|start|create|add)\b', '', query_lower, flags=re.IGNORECASE)
        
        dt = dateparser.parse(
            dp_input,
            settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now}
        )
        if dt:
            expires_at = dt
            log.info(f"TimerAdd: Dateparser matched absolute time: {dt}")

    if not expires_at:
        return {"status": "FAILURE", "message": "Could not understand the time or duration.", "service": "timer_add"}

    # --- 3. Determine Title ---
    # Remove the specific time units we found from the string to leave the "subject"
    title_temp = query_lower
    
    # Remove duration text patterns (e.g. "30 seconds")
    title_temp = re.sub(r'\d+\s*-?\s*(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)', '', title_temp)
    # Remove absolute time words
    title_temp = re.sub(r'\b(at|am|pm|tomorrow|tonight|o\'clock)\b', '', title_temp)
    # Remove command fillers
    title_temp = re.sub(r'\b(set|start|create|add|timer|alarm|for|in|a|an|the|wake|me|up|please|can|you)\b', '', title_temp)
    
    # Final clean
    title = re.sub(r'[^\w\s]', '', title_temp).strip()
    title = re.sub(r'\s+', ' ', title).strip()
    
    if not title or len(title) < 2:
        title = "Timer"

    # --- 4. Determine Alarm vs Timer ---
    time_difference = (expires_at - now).total_seconds()
    is_absolute_time_syntax = any(word in query_lower for word in ['am', 'pm', 'tonight', 'tomorrow', 'clock'])
    
    if time_difference > 3600 or is_absolute_time_syntax:
        is_alarm = True
        # If absolute time is in the past (e.g. 6am today when it's 10am), move to tomorrow
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
    
    # Format readable time
    if is_alarm:
        time_str = expires_at.strftime("%I:%M %p")
    else:
        # Relative time string for duration timers
        total_seconds = int((expires_at - now).total_seconds())
        m, s = divmod(total_seconds, 60)
        h, m = divmod(m, 60)
        time_str = ""
        if h > 0: time_str += f"{h} hours "
        if m > 0: time_str += f"{m} minutes "
        if s > 0 or not time_str: time_str += f"{s} seconds"
        time_str = time_str.strip()

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
            continue
        rem_str = str(remaining).split('.')[0]
        lines.append(f"- {t['title']} ({t['type']}): expires in {rem_str} at {exp.strftime('%I:%M %p')}")

    return {"status": "SUCCESS", "message": "Active Timers:\n" + "\n".join(lines), "service": "timer_list"}


async def tool_timer_delete(query: str, user_creds: Dict[str, str]) -> Dict[str, Union[str, bool]]:
    timers = await storage.list_timers()
    target_id = None
    query_low = query.lower()

    for t in timers:
        if t["title"].lower() in query_low or query_low in t["title"].lower():
            target_id = t["id"]
            break

    if not target_id and len(timers) == 1:
        target_id = timers[0]["id"]

    if target_id:
        await storage.delete_timer(target_id)
        return {"status": "SUCCESS", "message": "Timer deleted.", "service": "timer_delete"}

    return {"status": "FAILURE", "message": "Could not find a matching timer to delete.", "service": "timer_delete"}


async def tool_timer_pause(query: str) -> Dict[str, Union[str, bool]]:
    return {"status": "FAILURE", "message": "Pause functionality not yet fully implemented.", "service": "timer_pause"}


async def tool_timer_resume(query: str) -> Dict[str, Union[str, bool]]:
    return {"status": "FAILURE", "message": "Resume functionality not yet fully implemented.", "service": "timer_resume"}
