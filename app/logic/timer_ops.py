# app/logic/timer_ops.py
import time
import uuid
import re
import asyncio
from datetime import datetime, timedelta
import dateparser
from typing import Dict, List, Optional, Union

from app.settings import log, GlobalResources, get_user_creds
from .timer_storage import storage
from .alarm_audio import audio_manager
# Ensure we import smart_resolve_entity for device targeting
from .media_ops import get_last_entity, smart_resolve_entity

# Constants
DEFAULT_TIMER_DURATION = 600  # 10 mins

# Word to Number Mapping for Robust Parsing
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
            try:
                # Parse current expiry safely
                current_exp = datetime.fromisoformat(timer["expires_at"])
                if current_exp.tzinfo: current_exp = current_exp.replace(tzinfo=None)
                
                new_expiry = None
                
                if "daily" in recurrence.lower() or "every day" in recurrence.lower():
                    new_expiry = current_exp + timedelta(days=1)
                elif "every" in recurrence.lower():
                    # Handle "every Monday", "every Tuesday", etc.
                    # 0=Monday, 6=Sunday
                    days_map = {
                        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                        'friday': 4, 'saturday': 5, 'sunday': 6
                    }
                    target_day_str = recurrence.lower().replace("every", "").strip()
                    target_day = days_map.get(target_day_str)
                    
                    if target_day is not None:
                        # Add 1 day first to avoid finding today if we are still on the same day
                        next_day = current_exp + timedelta(days=1)
                        while next_day.weekday() != target_day:
                            next_day += timedelta(days=1)
                        new_expiry = next_day
                
                if new_expiry:
                    await storage.update_timer(timer_id, {
                        "expires_at": new_expiry.isoformat(),
                        "active": True
                    })
                    log.info(f"Rescheduled recurring alarm '{timer['title']}' to {new_expiry}")
                else:
                    log.warning(f"Could not calculate next recurrence for '{recurrence}'. Alarm will not repeat.")
                    
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


async def _create_timer_entry(
    title: str,
    expires_at: datetime,
    is_alarm: bool,
    recurrence: Optional[str],
    origin_device: str,
    target_device: Optional[str],
    target_device_name: Optional[str],
    redis_client
) -> Dict[str, Union[str, bool]]:
    """Internal helper to save timer/alarm to DB."""
    
    # Enforce Naive Timezone
    if expires_at.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=None)

    # Resolve Metadata for UI Display
    sound_settings = audio_manager.get_sound_settings(title)
    sound_display = sound_settings.get("sound", "default_alarm.wav")
    
    # Resolve Target Display
    # Resolve Target Display
    target_display = "Follow Me"
    if target_device:
        # Explicit target (e.g. "Office TV")
        target_display = target_device_name or target_device.split(".")[-1].replace("_", " ").title()
    elif origin_device:
        # Implicit origin (Last active device or Source)
        # We want to show the user WHERE it will ring, even if implied
        friendly = origin_device.split(".")[-1].replace("_", " ").title()
        target_display = f"{friendly} (Follow Me)"
        
    timer_obj = {
        "id": str(uuid.uuid4()),
        "type": "alarm" if is_alarm else "timer",
        "title": title,
        "created_at": datetime.now().isoformat(),
        "expires_at": expires_at.isoformat(),
        "origin_device": origin_device,
        "target_device": target_device,
        "active": True,
        "recurrence": recurrence,
        # Metadata for UI
        "target_display": target_display,
        "sound_display": sound_display
    }

    saved_id = await storage.add_timer(timer_obj, redis_client)
    
    if not saved_id:
        return {"status": "FAILURE", "message": "Database Error: Could not save timer.", "service": "timer_add"}

    time_str = expires_at.strftime("%I:%M %p")
    msg = f"Set {timer_obj['type']} '{title}' for {time_str}"
    if recurrence:
        msg += f" (repeats {recurrence})"
    msg += "."
    
    if target_device:
        msg += f" on {target_device_name}."
        
    return {"status": "SUCCESS", "message": msg, "service": "timer_add", "timer_id": timer_obj["id"]}


async def tool_timer_add(query: str, user_creds: Dict[str, str], model: str, redis_client, ha_collection=None, params: Dict = None) -> Dict[str, Union[str, bool]]:
    """
    Strictly handles DURATION based timers (e.g. "10 minutes").
    """
    now = datetime.now()
    params = params or {}
    origin_device = params.get("origin_device")
    
    query_lower = convert_words_to_numbers(query.lower())
    log.info(f"TimerAdd: Normalized query: '{query_lower}'")

    # 1. Extract Target Device
    target_device, target_device_name = await _extract_target_device(query_lower, ha_collection)
    if target_device_name:
        # Remove device string to avoid parsing confusion
        query_lower = query_lower.replace(target_device_name, "").replace("on ", "").replace("in ", "")

    # 2. Parse Duration
    hours, minutes, seconds = 0, 0, 0
    found_duration = False
    
    h_match = re.search(r'(\d+)\s*(?:hours?|hrs?)', query_lower)
    if h_match: hours, found_duration = int(h_match.group(1)), True
        
    m_match = re.search(r'(\d+)\s*(?:minutes?|mins?)', query_lower)
    if m_match: minutes, found_duration = int(m_match.group(1)), True
        
    s_match = re.search(r'(\d+)\s*-?\s*(?:seconds?|secs?)', query_lower)
    if s_match: seconds, found_duration = int(s_match.group(1)), True

    if not found_duration:
        # Check if it looks like an alarm (absolute time) and forward if necessary
        # This handles intent misclassification (timer_add vs alarm_add)
        is_absolute_time_syntax = any(word in query_lower for word in ['am', 'pm', 'tonight', 'tomorrow', 'clock', 'at', 'wake'])
        if is_absolute_time_syntax:
            log.info("TimerAdd: Detected absolute time syntax, forwarding to tool_alarm_add.")
            return await tool_alarm_add(query, user_creds, model, redis_client, ha_collection)

        return {"status": "FAILURE", "message": "Please specify a duration (e.g., '5 minutes') for a timer.", "service": "timer_add"}

    expires_at = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)
    
    # 3. Determine Title
    title = _extract_title(query_lower, ["timer", "set", "for", "minutes", "seconds", "hours"])

    return await _create_timer_entry(
        title, expires_at, False, None, 
        origin_device or get_last_entity(redis_client, user_creds.get("user")), 
        target_device, target_device_name, redis_client
    )


async def tool_alarm_add(query: str, user_creds: Dict[str, str], model: str, redis_client, ha_collection=None) -> Dict[str, Union[str, bool]]:
    """
    Strictly handles ABSOLUTE TIME based alarms (e.g. "8am").
    Supports Recurrence (e.g. "every day").
    """
    now = datetime.now()
    query_lower = convert_words_to_numbers(query.lower())
    log.info(f"AlarmAdd: Normalized query: '{query_lower}'")

    # 1. Extract Target Device
    target_device, target_device_name = await _extract_target_device(query_lower, ha_collection)
    if target_device_name:
        query_lower = query_lower.replace(target_device_name, "").replace("on ", "").replace("in ", "")

    # 2. Parse Recurrence
    recurrence = None
    if "every day" in query_lower or "daily" in query_lower:
        recurrence = "daily"
    elif "every" in query_lower:
        # Simple extraction for "every Monday", etc.
        match = re.search(r'every\s+(\w+)', query_lower)
        if match:
            recurrence = f"every {match.group(1)}"

    # 3. Parse Absolute Time
    # Clean up common prefixes and recurrence phrases for better parsing
    # Added: 'an', 'a', 'the', 'every', 'day', 'daily'
    clean_input = re.sub(r'\b(set|alarm|wake|me|up|for|at|an|a|the|every|day|daily)\b', '', query_lower, flags=re.IGNORECASE)
    
    # Remove "called X" or "named X" to prevent dateparser confusion
    clean_input = re.sub(r'\b(called|named)\b.*', '', clean_input, flags=re.IGNORECASE)
    
    dt = dateparser.parse(
        clean_input,
        settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': now}
    )
    
    if not dt:
         return {"status": "FAILURE", "message": "Please specify a time (e.g., '8am') for the alarm.", "service": "alarm_add"}

    # If time is in the past (e.g. "8am" said at "10am"), dateparser might default to today.
    # We want tomorrow.
    if dt < now and "tomorrow" not in query_lower:
        dt += timedelta(days=1)

    # 4. Determine Title
    title = _extract_title(query_lower, ["alarm", "set", "wake", "up", "at", "for", "every", "daily"])

    return await _create_timer_entry(
        title, dt, True, recurrence, 
        get_last_entity(redis_client, user_creds.get("user")), 
        target_device, target_device_name, redis_client
    )


async def _extract_target_device(query: str, ha_collection):
    target_device = None
    target_device_name = None
    # Improved regex: Handle trailing punctuation
    device_match = re.search(r'\b(?:on|in)\s+(the\s+)?(.+?)[.?!]?$', query)
    
    if device_match:
        potential_name = device_match.group(2).strip().strip("!?.")
        time_keywords = ['minute', 'second', 'hour', 'tomorrow', 'tonight', 'morning', 'evening', 'afternoon', 'day', 'week', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        
        # Avoid capturing time phrases as devices
        if not any(w in potential_name for w in time_keywords):
            target_device_name = potential_name
            log.info(f"Timer Extraction: Identified potential device name: '{target_device_name}'")
            
    if target_device_name and ha_collection:
        # Try 'play_media' first (covers TVs/Speakers)
        resolved = await smart_resolve_entity(target_device_name, "play_media", ha_collection)
        if resolved and resolved[0]:
            target_device = resolved[0]
            log.info(f"Timer Extraction: Resolved '{target_device_name}' -> {target_device}")
        else:
            # Fallback: Try generic 'turn_on' if media resolve fails (e.g. for switches/lights used as alarms)
            log.info(f"Timer Extraction: No media device found for '{target_device_name}', trying generic resolution.")
            resolved = await smart_resolve_entity(target_device_name, "turn_on", ha_collection)
            if resolved and resolved[0]:
                target_device = resolved[0]
                log.info(f"Timer Extraction: Resolved generic '{target_device_name}' -> {target_device}")

    return target_device, target_device_name

def _extract_title(query: str, ignore_words: List[str]) -> str:
    title_temp = query
    # Remove digits and time units (duration)
    title_temp = re.sub(r'\d+\s*-?\s*(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)', '', title_temp)
    # Remove absolute times (e.g. 8am, 8:30pm, 8:00)
    title_temp = re.sub(r'\b\d+(?::\d+)?\s*(?:am|pm)?\b', '', title_temp, flags=re.IGNORECASE)
    
    # Remove common words
    for w in ignore_words + ['please', 'can', 'you', 'a', 'an', 'the', 'called', 'named']:
        title_temp = re.sub(f'\\b{w}\\b', '', title_temp, flags=re.IGNORECASE)
    
    title = re.sub(r'[^\w\s]', '', title_temp).strip()
    title = re.sub(r'\s+', ' ', title).strip()
    return title if len(title) >= 2 else ("Alarm" if "alarm" in ignore_words else "Timer")


async def tool_timer_list(user_creds: Dict[str, str], redis_client=None) -> Dict[str, Union[str, bool]]:
    # Pass redis_client to storage
    timers = await storage.list_timers(redis_client)
    
    if not timers:
        return {"status": "SUCCESS", "message": "No active timers or alarms.", "service": "timer_list"}

    timer_lines = []
    alarm_lines = []
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
            line = f"- {t['title']}: expires in {rem_str} at {exp.strftime('%I:%M %p')}"
            
            if t.get("type") == "alarm":
                alarm_lines.append(line)
            else:
                timer_lines.append(line)
        except Exception as e:
            log.error(f"Error parsing timer for list: {e}")

    if not timer_lines and not alarm_lines:
        return {"status": "SUCCESS", "message": "No active timers or alarms.", "service": "timer_list"}

    msg_parts = []
    if timer_lines:
        msg_parts.append("Active Timers:\n" + "\n".join(timer_lines))
    if alarm_lines:
        msg_parts.append("Active Alarms:\n" + "\n".join(alarm_lines))

    return {"status": "SUCCESS", "message": "\n\n".join(msg_parts), "service": "timer_list"}


async def tool_timer_delete(query: str, user_creds: Dict[str, str], redis_client=None) -> Dict[str, Union[str, bool]]:
    timers = await storage.list_timers(redis_client)
    target_id = None
    query_low = query.lower()

    for t in timers:
        log.info(f"Checking timer: '{t['title']}' against query: '{query_low}'")
        if t["title"].lower() in query_low or query_low in t["title"].lower():
            target_id = t["id"]
            log.info(f"Match found: {target_id}")
            break

    # DANGEROUS FALLBACK REMOVED: Do not automatically delete the only timer if no name match.
    # if not target_id and len(timers) == 1:
    #     target_id = timers[0]["id"]

    if target_id:
        await storage.delete_timer(target_id, redis_client)
        return {"status": "SUCCESS", "message": "Timer deleted.", "service": "timer_delete"}

    return {"status": "FAILURE", "message": "Could not find a matching timer to delete.", "service": "timer_delete"}


async def tool_timer_pause(query: str, user_creds: Dict[str, str], redis_client=None) -> Dict[str, Union[str, bool]]:
    query_low = query.lower()
    timers = await storage.list_timers(redis_client)
    
    # Filter for ACTIVE timers
    # Note: 'active' might be missing in older records, assume True if missing
    active_candidates = [t for t in timers if t.get("active", True)]
    
    if not active_candidates:
        return {"status": "FAILURE", "message": "No active timers found to pause.", "service": "timer_pause"}

    target_timer = None
    
    # 1. Try Name Match
    for t in active_candidates:
        if t["title"].lower() in query_low or query_low in t["title"].lower():
            target_timer = t
            break
            
    # 2. Single Candidate Fallback
    if not target_timer and len(active_candidates) == 1:
        target_timer = active_candidates[0]
        
    if not target_timer:
        return {"status": "FAILURE", "message": f"Which timer do you want to pause? I found {len(active_candidates)} active timers.", "service": "timer_pause"}

    # Execute Pause
    try:
        expires_at = datetime.fromisoformat(target_timer["expires_at"])
        if expires_at.tzinfo: expires_at = expires_at.replace(tzinfo=None)
        
        remaining = expires_at - datetime.now()
        
        if remaining.total_seconds() <= 0:
             return {"status": "FAILURE", "message": "That timer has already expired.", "service": "timer_pause"}

        await storage.update_timer(target_timer["id"], {
            "active": False,
            "remaining_seconds": remaining.total_seconds()
        }, redis_client)
        
        rem_str = str(remaining).split('.')[0]
        return {"status": "SUCCESS", "message": f"Paused '{target_timer['title']}'. Remaining time: {rem_str}.", "service": "timer_pause"}
        
    except Exception as e:
        log.error(f"Pause Error: {e}")
        return {"status": "FAILURE", "message": "Failed to pause timer due to a system error.", "service": "timer_pause"}


async def tool_timer_resume(query: str, user_creds: Dict[str, str], redis_client=None) -> Dict[str, Union[str, bool]]:
    query_low = query.lower()
    timers = await storage.list_timers(redis_client)
    
    # Filter for PAUSED timers (active=False)
    paused_candidates = [t for t in timers if t.get("active") is False]
    
    if not paused_candidates:
        return {"status": "FAILURE", "message": "No paused timers found to resume.", "service": "timer_resume"}

    target_timer = None
    
    # 1. Try Name Match
    for t in paused_candidates:
        if t["title"].lower() in query_low or query_low in t["title"].lower():
            target_timer = t
            break
            
    # 2. Single Candidate Fallback
    if not target_timer and len(paused_candidates) == 1:
        target_timer = paused_candidates[0]
        
    if not target_timer:
        return {"status": "FAILURE", "message": f"Which timer do you want to resume? I found {len(paused_candidates)} paused timers.", "service": "timer_resume"}

    # Execute Resume
    try:
        remaining_seconds = target_timer.get("remaining_seconds")
        if not remaining_seconds:
             return {"status": "FAILURE", "message": "Could not determine remaining time for this timer.", "service": "timer_resume"}

        new_expires_at = datetime.now() + timedelta(seconds=float(remaining_seconds))
        
        await storage.update_timer(target_timer["id"], {
            "active": True,
            "expires_at": new_expires_at.isoformat(),
            # storage.update_timer does a dictionary merge/update. 
            # We explicitly set remaining_seconds to None/0? 
            # Or just ignore it if active is True. The scheduler checks active flag.
            "remaining_seconds": 0 
        }, redis_client)
        
        return {"status": "SUCCESS", "message": f"Resumed '{target_timer['title']}'. New time: {new_expires_at.strftime('%I:%M %p')}.", "service": "timer_resume"}

    except Exception as e:
        log.error(f"Resume Error: {e}")
        return {"status": "FAILURE", "message": "Failed to resume timer due to a system error.", "service": "timer_resume"}
