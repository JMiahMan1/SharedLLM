# app/logic/calendar_ops.py
import json
import time
import re
import caldav
import dateparser
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional, Union # FIX: Added Union for structured return

# Import settings and utils
from settings import (
    log, run_blocking, NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS,
    CALENDAR_EXTRACT_PROMPT # IMPORTED PROMPT
)
from .utils import clean_llm_output, call_ollama_generate

# Constants
CAL_LIST_TTL = 300  # 5 minutes for calendar lists
CAL_WRITE_TTL = 3600  # 1 hour for writability check

# --- Helper Functions ---

def _get_cal_client(creds: Dict[str, str]):
    """Creates a CalDAV client using user credentials."""
    url = f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav"
    return caldav.DAVClient(
        url=url, 
        username=creds.get('user', NEXTCLOUD_USER), 
        password=creds.get('nc_pass', NEXTCLOUD_PASS), 
        timeout=20
    )

def _get_default_cal_key(user: str) -> str:
    return f"rag:cal_default:{user}"

def _get_writable_cache_key(url: str) -> str:
    return f"rag:cal_writable:{url.lower().rstrip('/')}"

def _get_cal_list_cache_key(user: str) -> str:
    return f"rag:cal_list:{user}"

def _is_cal_writable(cal, user: str, redis_client) -> bool:
    """
    Checks if a calendar is writable using a test write operation. 
    Caches result in Redis for 1 hour to improve performance.
    """
    url = str(cal.url)

    # 1. Redis Cache Check
    if redis_client:
        cache_key = _get_writable_cache_key(url)
        cached = redis_client.get(cache_key)
        if cached is not None: return cached == "1"

    # 2. Active Write Check
    try:
        test_uid = f"RAG_WRITE_{int(time.time())}"
        # Creates a temp event to verify permissions
        ev = cal.save_event(dtstart=datetime.now() - timedelta(hours=1), summary=test_uid)
        ev.delete() # Clean up immediately
        
        if redis_client: 
            # 1 Hour TTL
            redis_client.setex(cache_key, CAL_WRITE_TTL, "1") 
        return True
    except requests.exceptions.ReadTimeout:
        log.warning(f"Calendar write check timed out for {cal.name}")
        if redis_client: 
            redis_client.setex(cache_key, CAL_WRITE_TTL, "0") 
        return False
    except Exception:
        if redis_client: 
            redis_client.setex(cache_key, CAL_WRITE_TTL, "0")
        return False

def _set_user_default_cal(user: str, cal_name: str, redis_client):
    if redis_client:
        redis_client.set(_get_default_cal_key(user), cal_name)

def _get_user_default_cal(user: str, redis_client) -> Optional[str]:
    if redis_client:
        return redis_client.get(_get_default_cal_key(user))
    return None

async def extract_event_data(query: str, model: str) -> Dict[str, str]:
    """
    Extracts event details (Summary, Time, Target) from natural language.
    Uses Regex Fallback for simple queries if LLM fails.
    """
    # 1. Regex Fallback (Fast & Reliable for simple format)
    # Matches: "Schedule [Title] at [Time]" or "Add event [Title] on [Date]"
    match = re.search(r"(?:schedule|add|remind) (?:me to )?(.+?) (?:at|on|for) (.+)", query, re.IGNORECASE)
    if match:
        summary, time_str = match.groups()
        # Basic cleanup
        return {
            "summary": summary.strip(), 
            "start_time": time_str.strip(), 
            "calendar_target": None
        }

    # 2. LLM Extraction (Smart) - Using Prompt from Settings
    prompt = CALENDAR_EXTRACT_PROMPT.format(query=query)
    
    r = await call_ollama_generate(prompt, model=model)
    # Use is_voice=False to preserve JSON formatting (quotes, brackets)
    text = clean_llm_output(r.get("text", "{}"), is_voice=False).strip()
    
    try:
        # Try to find JSON block if wrapped in text
        match_json = re.search(r"\{.*\}", text, re.DOTALL)
        if match_json: 
            return json.loads(match_json.group(0))
        return json.loads(text)
    except: 
        return {}

# --- Tool Functions (MODIFIED TO RETURN STRUCTURED DICT) ---

async def tool_calendar_list(user_creds: Dict[str, str], redis_client) -> Dict[str, Union[str, bool]]:
    """Lists available writable calendars."""
    if not NEXTCLOUD_URL: 
        return {"status": "FAILURE", "message": "Nextcloud configuration missing."}

    user = user_creds.get("user")
    
    # Check Cache
    if redis_client and user:
        ck = _get_cal_list_cache_key(user)
        cached = redis_client.get(ck)
        if cached: 
            return {"status": "SUCCESS", "message": cached}

    try:
        def _fetch():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            # Filter output to avoid showing system calendars by default
            valid = [f"- {c.name}" for c in calendars if "birthday" not in (c.name or "").lower() and "contact" not in (c.name or "").lower()]
            result = "Available Calendars:\n" + "\n".join(valid) if valid else "No writable calendars."
            return result
        
        final_res = await run_blocking(_fetch)
        
        # Cache Result
        if redis_client and user:
            redis_client.setex(_get_cal_list_cache_key(user), CAL_LIST_TTL, final_res)
            
        return {"status": "SUCCESS", "message": final_res}
    except Exception as e: 
        return {"status": "FAILURE", "message": f"Error listing calendars: {e}"}

async def tool_calendar_read(user_creds: Dict[str, str], redis_client) -> str:
    """Reads upcoming events for context injection (RAG). Returns raw string context."""
    if not NEXTCLOUD_URL: return ""
    try:
        def _fetch():
            client = _get_cal_client(user_creds)
            found_events = []
            calendars = client.principal().calendars()
            now = datetime.now()
            end = now + timedelta(days=7)
            
            for cal in calendars:
                # Optimization: Check writability cache if available to skip known read-only
                if redis_client:
                    ck = _get_writable_cache_key(str(cal.url))
                    if redis_client.get(ck) == "0": continue

                try:
                    events = cal.search(start=now, end=end, event=True, expand=True)
                    for ev in events:
                        if hasattr(ev.vobject_instance, 'vevent'):
                            ve = ev.vobject_instance.vevent
                            # Format date nicely
                            start_dt = ve.dtstart.value
                            if isinstance(start_dt, datetime):
                                t = start_dt.strftime("%Y-%m-%d %H:%M")
                            else:
                                t = str(start_dt)
                                
                            found_events.append(f"- [{t}] {ve.summary.value} ({cal.name})")
                except: pass
            return "Upcoming Events:\n" + "\n".join(found_events) if found_events else "No events found."
        
        return await run_blocking(_fetch)
    except: return ""

async def tool_calendar_add(query: str, user_creds: Dict[str, str], model: str, redis_client) -> Dict[str, Union[str, bool]]:
    """Adds an event to a calendar."""
    if not NEXTCLOUD_URL: 
        return {"status": "FAILURE", "message": "Error: Nextcloud not configured."}
    
    data = await extract_event_data(query, model)
    summary = data.get("summary")
    start = data.get("start_time")
    target = data.get("calendar_target")
    
    if not summary or not start: 
        return {"status": "FAILURE", "message": "Missing event details."}
    
    # Parse Date
    dt = dateparser.parse(start, languages=['en'], settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now()})
    if not dt: 
        return {"status": "FAILURE", "message": f"Invalid date format for: {start}"}

    try:
        def _add():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            if not calendars: raise Exception("No calendars found.")
            
            # Smart Sort: Personal > Username > Others
            calendars.sort(key=lambda c: 0 if "personal" in (c.name or "").lower() else (1 if user_creds['user'].lower() in (c.name or "").lower() else 10))
            selected = None
            
            # 1. Selection Logic: Explicit Target
            if target:
                for c in calendars:
                    if target.lower() in (c.name or "").lower():
                        if _is_cal_writable(c, user_creds['user'], redis_client):
                            selected = c
                            break
            
            # 2. Selection Logic: Stored Default
            if not selected:
                def_name = _get_user_default_cal(user_creds['user'], redis_client)
                if def_name:
                    for c in calendars:
                        if c.name == def_name and _is_cal_writable(c, user_creds['user'], redis_client):
                            selected = c
                            break
            
            # 3. Selection Logic: First Writable
            if not selected:
                for c in calendars:
                    if _is_cal_writable(c, user_creds['user'], redis_client):
                        selected = c
                        break

            if not selected: raise Exception("No suitable writable calendar found.")

            end = dt + timedelta(hours=1)
            selected.save_event(dtstart=dt, dtend=end, summary=summary)
            
            # Update default calendar preference
            _set_user_default_cal(user_creds['user'], selected.name, redis_client)
            
            return selected.name

        cal_name = await run_blocking(_add)
        msg = f"Scheduled '{summary}' for {dt.strftime('%Y-%m-%d %H:%M')} on '{cal_name}'."
        return {"status": "SUCCESS", "message": msg}
    except Exception as e:
        log.error(f"Calendar Add Error: {e}")
        return {"status": "FAILURE", "message": f"Failed to add event: {str(e)}"}

async def tool_calendar_delete(query: str, user_creds: Dict[str, str], model: str, redis_client) -> Dict[str, Union[str, bool]]:
    """Deletes an event by fuzzy matching name."""
    if not NEXTCLOUD_URL: 
        return {"status": "FAILURE", "message": "Error: Nextcloud not configured."}
    
    data = await extract_event_data(query, model)
    keyword = data.get("summary")
    target = data.get("calendar_target")
    if not keyword: 
        return {"status": "FAILURE", "message": "Missing event name."}

    try:
        def _delete():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            
            # Filter Candidates
            candidates = []
            for c in calendars:
                if target and target.lower() not in (c.name or "").lower(): continue
                if _is_cal_writable(c, user_creds['user'], redis_client):
                    candidates.append(c)

            count = 0
            start = datetime.now() - timedelta(days=1)
            end = start + timedelta(days=30)
            
            # Search loop
            for c in candidates:
                try:
                    events = c.search(start=start, end=end, event=True, expand=True)
                    for ev in events:
                        if keyword.lower() in ev.vobject_instance.vevent.summary.value.lower():
                            ev.delete()
                            count += 1
                            break
                    if count > 0: break
                except: pass
            return count

        cnt = await run_blocking(_delete)
        msg = f"Deleted event matching '{keyword}'." if cnt else f"No matching event found for '{keyword}'."
        return {"status": "SUCCESS", "message": msg}
    except Exception as e: 
        return {"status": "FAILURE", "message": f"Delete error: {e}"}

async def tool_calendar_update(query: str, user_creds: Dict[str, str], model: str, redis_client) -> Dict[str, Union[str, bool]]:
    """Updates/Reschedules an event."""
    if not NEXTCLOUD_URL: 
        return {"status": "FAILURE", "message": "Error: Nextcloud not configured."}
    
    data = await extract_event_data(query, model)
    keyword = data.get("summary")
    new_start = data.get("start_time")
    target = data.get("calendar_target")
    
    if not keyword or not new_start: 
        return {"status": "FAILURE", "message": "Missing details (need Event Name and New Time)."}
    
    dt = dateparser.parse(new_start, languages=['en'], settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now()})
    if not dt: 
        return {"status": "FAILURE", "message": f"Invalid date: {new_start}"}

    try:
        def _update():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            
            candidates = []
            for c in calendars:
                if target and target.lower() not in (c.name or "").lower(): continue
                if _is_cal_writable(c, user_creds['user'], redis_client):
                    candidates.append(c)
            
            count = 0
            start = datetime.now() - timedelta(days=1)
            end = start + timedelta(days=30)
            
            for c in candidates:
                try:
                    events = c.search(start=start, end=end, event=True, expand=True)
                    for ev in events:
                        ve = ev.vobject_instance.vevent
                        if keyword.lower() in ve.summary.value.lower():
                            # Calculate duration to preserve it
                            duration = timedelta(hours=1)
                            try:
                                if hasattr(ve, 'dtend'): 
                                    duration = ve.dtend.value - ve.dtstart.value
                            except: pass
                            
                            # Update fields
                            ve.dtstart.value = dt
                            ve.dtend.value = dt + duration
                            ev.save()
                            count += 1
                            break
                    if count > 0: break
                except: pass
            return count

        cnt = await run_blocking(_update)
        msg = f"Rescheduled '{keyword}' to {dt.strftime('%Y-%m-%d %H:%M')}." if cnt else "Event not found."
        return {"status": "SUCCESS", "message": msg}
    except Exception as e: 
        return {"status": "FAILURE", "message": f"Update error: {e}"}
