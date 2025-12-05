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

# Ensure we import smart_resolve_entity for device targeting
from .media_ops import get_last_entity, smart_resolve_entity

# Constants
DEFAULT_TIMER_DURATION = 600  # 10 mins

WORD_TO_NUM = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
    'fifteen': 15, 'twenty': 20, 'thirty': 30, 'forty': 40,
    'fifty': 50, 'sixty': 60
}

def convert_words_to_numbers(text: str) -> str:
    """
    Converts number words (e.g., 'two', 'thirty-five') to digits in the string.
    """
    text = text.lower()
    # Replace hyphenated words first (e.g. twenty-five -> 25)
    for w1, v1 in WORD_TO_NUM.items():
        for w2, v2 in WORD_TO_NUM.items():
            if v1 >= 20 and v2 < 10:
                pattern = f"\\b{w1}-{w2}\\b"
                text = re.sub(pattern, str(v1 + v2), text, flags=re.IGNORECASE)
                pattern_space = f"\\b{w1} {w2}\\b"
                text = re.sub(pattern_space, str(v1 + v2), text, flags=re.IGNORECASE)

    # Replace single words
    for word, value in WORD_TO_NUM.items():
        text = re.sub(f"\\b{word}\\b", str(value), text, flags=re.IGNORECASE)

    return text


async def trigger_alarm(timer: Dict):
    """
    Called by scheduler when a timer expires.
    CRITICAL FIX: Updates DB state BEFORE playing audio to prevent scheduler race conditions (spam).
    """
    timer_id = timer["id"]
    log.info(f"ALARM TRIGGERED: {timer['title']} (ID: {timer_id})")

    recurrence = timer.get("recurrence")
    is_one_time = recurrence is None

    # --- 1. Immediate Persistence Update ---
    # We acknowledge the alarm immediately so the next scheduler tick doesn't pick it up.
    try:
        if is_one_time:
            await storage.delete_timer(timer_id)
            log.info(f"Timer {timer_id} removed from DB (processing started).")

        elif recurrence:
            if "daily" in recurrence.lower() or "every day" in recurrence.lower():
                try:
                    # Parse current expiry safely
                    current_exp = datetime.fromisoformat(timer["expires_at"])
                    # Ensure naive for calculation
                    if current_exp.tzinfo: current_exp = current_exp.replace(tzinfo=None)

                    new_expiry = current_exp + timedelta(days=1)
                    await storage.update_timer(timer_id, {
                        "expires_at": new_expiry.isoformat(),
                        "active": True
                    })
                    log.info(f"Rescheduled recurring alarm '{timer['title']}' to {new_expiry}")
                except Exception as e:
                    log.error(f"Failed to calculate new expiry for {timer_id}: {e}")
    except Exception as e:
        log.error(f"Failed to update timer state {timer_id}: {e}")
        # We continue to play audio, but log the DB error.

    # --- 2. Play Audio Sequence ---
    try:
        creds = get_user_creds() 
        # Use the global client for the background task
        await audio_manager.play_alarm_sequence(timer, creds, GlobalResources.redis_client)

    except Exception as e:
        log.critical(f"Critical unhandled exception during alarm audio sequence for {timer_id}: {e}")


async def tool_timer_add(query: str, user_creds: Dict[str, str], model: str, redis_client, ha_collection=None) -> Dict[str, Union[str, bool]]:
    """
    Adds a timer or alarm based on natural language query.
    Supports target device extraction ("on Office TV").
    """
    now = datetime.now() # Naive local time
    query_lower = query.lower()

    # --- 0. Pre-process: Word-to-Digit Conversion ---
    # Fixes "set a one minute timer" -> "set a 1 minute timer"
    query_lower = convert_words_to_numbers(query_lower)
    log.info(f"TimerAdd: Normalized query: '{query_lower}'")

    # --- 1. Extract Target Device ("on Office TV") ---
    target_device = None
    target_device_name = None

    # Look for "on [Device]" pattern at the end of the string
    device_match = re.search(r'\b(?:on|in)\s+(the\s+)?(.+?)$', query_lower)

    if device_match:
        potential_name = device_match.group(2).strip()
        # Safety check: verify it's not a time word
        time_keywords = ['minute', 'second', 'hour', 'tomorrow', 'tonight', 'morning', 'evening', 'afternoon', 'day', 'week']
        if not any(w in potential_name for w in time_keywords):
            target_device_name = potential_name
            # Remove the device part from the query so it doesn't confuse time parsing
            query_lower = query_lower.replace(device_match.group(0), "")

    if target_device_name and ha_collection:
        # Resolve to entity ID using media logic
        tid, _ = await smart_resolve_entity(target_device_name, "play_media", ha_collection)
        if tid:
            target_device = tid
            log.info(f"TimerAdd: Resolved target device '{target_device_name}' -> {target_device}")
        else:
            log.warning(f"TimerAdd: Could not resolve target device '{target_device_name}'")

    # --- 2. Clean introductory words/filler ---
    # Added 'set up', 'make a' to the cleanup list
    clean_parse_input = re.sub(
        r'^\s*[\d\.\s]*(?:can you|please|i want to|start|set|set up|make|create|add|a|an|\s+)+', '', query_lower, flags=re.IGNORECASE
    ).strip()

    expires_at = None
    is_alarm = False

    # --- 3. Robust Duration Parsing (Regex First) ---
    # We use separate searches to handle mixed order ("1 minute 30 seconds")
    hours = 0
    minutes = 0
    seconds = 0
    found_duration = False

    # Hours
    h_match = re.search(r'(\d+)\s*(?:hours?|hrs?)', clean_parse_input)
    if h_match:
        hours = int(h_match.group(1))
        found_duration = True

    # Minutes
    m_match = re.search(r'(\d+)\s*(?:minutes?|mins?)', clean_parse_input)
    if m_match:
        minutes = int(m_match.group(1))
        found_duration = True

    # Seconds
    s_match = re.search(r'(\d+)\s*-?\s*(?:seconds?|secs?)', clean_parse_input)
    if s_match:
        seconds = int(s_match.group(1))
        found_duration = True

    if found_duration:
        expires_at = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)
        log.info(f"TimerAdd: Regex found duration: {hours}h {minutes}m {seconds}s")

    # --- 4. Fallback to Dateparser (Absolute Time) ---
    if not found_duration:
        # Aggressive cleaning for dateparser
        dp_input = re.sub(r'\b(timer|alarm|wake me|remind me|set|start)\b', '', clean_parse_input, flags=re.IGNORECASE)

        dt = dateparser.parse(
            dp_input,
            settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now}
        )
        if dt:
            expires_at = dt
            log.info(f"TimerAdd: Dateparser matched absolute time: {dt}")

    if not expires_at:
        return {"status": "FAILURE", "message": "Could not understand the time or duration.", "service": "timer_add"}

    # --- FIX: Enforce Naive Timezone ---
    # This prevents crashes when subtracting from datetime.now() later
    if expires_at.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=None)

    # --- 5. Determine Title ---
    title_temp = query_lower
    title_temp = re.sub(r'\d+\s*-?\s*(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)', '', title_temp)
    title_temp = re.sub(r'\b(at|am|pm|tomorrow|tonight|o\'clock)\b', '', title_temp, flags=re.IGNORECASE)
    title_temp = re.sub(r'\b(set|start|create|add|wake|me|up|please|can|you|timer|alarm|for|in)\b', '', title_temp, flags=re.IGNORECASE)

    title = re.sub(r'[\d]+', '', title_temp)
    title = re.sub(r'[^\w\s]', '', title).strip()
    title = re.sub(r'\s+', ' ', title).strip()

    if not title or len(title) < 2:
        title = "Timer"

    # --- 6. Determine Alarm vs Timer ---
    time_difference = (expires_at - now).total_seconds()
    is_absolute_time_syntax = any(word in query.lower() for word in ['am', 'pm', 'tonight', 'tomorrow', 'clock'])

    if time_difference > 3600 or is_absolute_time_syntax:
        is_alarm = True
        # Logic to ensure alarms set for "6am" when it's 10am are set for tomorrow
        if expires_at < now and is_absolute_time_syntax:
            expires_at += timedelta(days=1)

    # --- 7. Determine Origin Device ---
    origin = get_last_entity(redis_client, user_creds.get("user"))

    # --- 8. Create Timer Object ---
    timer_obj = {
        "id": str(uuid.uuid4()),
        "type": "alarm" if is_alarm else "timer",
        "title": title,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "origin_device": origin,
        "target_device": target_device, # Stores the resolved Entity ID
        "active": True,
        "recurrence": "daily" if "every day" in query.lower() else None
    }

    # --- 9. Save & Verify (CRITICAL FIX) ---
    # Pass the active redis_client to ensure it's saved in this context
    saved_id = await storage.add_timer(timer_obj, redis_client)

    if not saved_id:
        return {"status": "FAILURE", "message": "Database Error: Could not save timer. Check Redis connection.", "service": "timer_add"}

    time_str = expires_at.strftime("%I:%M %p")
    msg = f"Set {timer_obj['type']} '{title}' for {time_str}."

    if target_device:
        msg += f" on {target_device_name}."

    return {"status": "SUCCESS", "message": msg, "service": "timer_add", "timer_id": timer_obj["id"]}


async def tool_timer_list(user_creds: Dict[str, str], redis_client=None) -> Dict[str, Union[str, bool]]:
    # Pass redis_client to storage
    timers = await storage.list_timers(redis_client)

    if not timers:
        return {"status": "SUCCESS", "message": "No active timers or alarms.", "service": "timer_list"}

    lines = []
    now = datetime.now()

    for t in timers:
        try:
            exp = datetime.fromisoformat(t["expires_at"])
            # Safety: Ensure naive timezone for comparison
            if exp.tzinfo: exp = exp.replace(tzinfo=None)

            remaining = exp - now
            if remaining.total_seconds() < 0:
                continue # Skip expired ones that haven't been cleaned yet

            rem_str = str(remaining).split('.')[0]
            lines.append(f"- {t['title']} ({t['type']}): expires in {rem_str} at {exp.strftime('%I:%M %p')}")
        except Exception as e:
            log.error(f"Error parsing timer for list: {e}")

    if not lines:
        return {"status": "SUCCESS", "message": "No active timers or alarms.", "service": "timer_list"}

    return {"status": "SUCCESS", "message": "Active Timers:\n" + "\n".join(lines), "service": "timer_list"}


async def tool_timer_delete(query: str, user_creds: Dict[str, str], redis_client=None) -> Dict[str, Union[str, bool]]:
    timers = await storage.list_timers(redis_client)
    target_id = None
    query_low = query.lower()

    for t in timers:
        if t["title"].lower() in query_low or query_low in t["title"].lower():
            target_id = t["id"]
            break

    if not target_id and len(timers) == 1:
        target_id = timers[0]["id"]

    if target_id:
        await storage.delete_timer(target_id, redis_client)
        return {"status": "SUCCESS", "message": "Timer deleted.", "service": "timer_delete"}

    return {"status": "FAILURE", "message": "Could not find a matching timer to delete.", "service": "timer_delete"}


async def tool_timer_pause(query: str) -> Dict[str, Union[str, bool]]:
    return {"status": "FAILURE", "message": "Pause functionality not yet fully implemented.", "service": "timer_pause"}


async def tool_timer_resume(query: str) -> Dict[str, Union[str, bool]]:
    return {"status": "FAILURE", "message": "Resume functionality not yet fully implemented.", "service": "timer_resume"}
