# app/logic/calendar_ops.py
import json
import time
import os
import re
import caldav
import dateparser
import requests
from datetime import datetime, timedelta, date
from typing import Dict, Optional, Union
from dateutil import tz 

# Import settings and utils
from app.settings import (
    GlobalResources,
    get_user_creds,
    log,
    run_blocking, NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS,
    CALENDAR_EXTRACT_PROMPT
)
from .utils import clean_llm_output, call_ollama_generate

# Constants
CAL_LIST_TTL = 300
CAL_WRITE_TTL = 3600
LOCAL_TZ_NAME = os.getenv("TIMEZONE", "America/Phoenix") 

# --- Helper Functions ---

def _get_local_tz():
    return tz.gettz(LOCAL_TZ_NAME)

def _get_cal_client(creds: Dict[str, str]):
    url = f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav"
    return caldav.DAVClient(
        url=url, 
        username=NEXTCLOUD_USER, 
        password=creds.get('nc_pass', NEXTCLOUD_PASS), 
        timeout=60
    )

def _get_default_cal_key(user: str) -> str:
    return f"rag:cal_default:{user}"

def _get_writable_cache_key(url: str) -> str:
    return f"rag:cal_writable:{url.lower().rstrip('/')}"

def _get_cal_list_cache_key(user: str) -> str:
    return f"rag:cal_list:{user}"

def _is_cal_writable(cal, user: str, redis_client) -> bool:
    url = str(cal.url)
    if redis_client:
        cached = redis_client.get(_get_writable_cache_key(url))
        if cached is not None: return cached == "1"

    try:
        test_uid = f"RAG_WRITE_{int(time.time())}"
        ev = cal.save_event(dtstart=datetime.now() - timedelta(hours=1), summary=test_uid)
        ev.delete()
        if redis_client: 
            redis_client.setex(_get_writable_cache_key(url), CAL_WRITE_TTL, "1") 
        return True
    except:
        if redis_client: 
            redis_client.setex(_get_writable_cache_key(url), CAL_WRITE_TTL, "0")
        return False

def _set_user_default_cal(user: str, cal_name: str, redis_client):
    if redis_client:
        redis_client.set(_get_default_cal_key(user), cal_name)

def _get_user_default_cal(user: str, redis_client) -> Optional[str]:
    if redis_client:
        return redis_client.get(_get_default_cal_key(user))
    return None

def _normalize_event_time(dt_value):
    """Converts ANY event time to naive local time for comparison."""
    local_tz = _get_local_tz()
    
    if isinstance(dt_value, datetime):
        if dt_value.tzinfo is None:
            return dt_value
        return dt_value.astimezone(local_tz).replace(tzinfo=None)
    elif isinstance(dt_value, date):
        return datetime.combine(dt_value, datetime.min.time())
    return None

async def extract_event_data(query: str, model: str) -> Dict[str, str]:
    # 1. Regex Fallback for ADD/SCHEDULE
    match_add = re.search(r"(?:schedule|add|remind) (?:me to )?(.+?) (?:at|on|for) (.+)", query, re.IGNORECASE)
    if match_add:
        summary, time_str = match_add.groups()
        return {"summary": summary.strip(), "start_time": time_str.strip(), "calendar_target": None}

    # 2. Regex Fallback for DELETE/CANCEL
    match_del = re.search(r"(?:delete|cancel|remove) (?:the )?(.+?) (?:at|on|for) (.+)", query, re.IGNORECASE)
    if match_del:
        summary, time_str = match_del.groups()
        return {"summary": summary.strip(), "start_time": time_str.strip(), "calendar_target": None}

    # 3. LLM Extraction
    prompt = CALENDAR_EXTRACT_PROMPT.format(query=query)
    r = await call_ollama_generate(prompt, model=model)
    text = clean_llm_output(r.get("text", "{}"), is_voice=False).strip()
    try:
        match_json = re.search(r"\{.*\}", text, re.DOTALL)
        if match_json: return json.loads(match_json.group(0))
        return json.loads(text)
    except: return {}

# --- Tool Functions ---

async def tool_calendar_list(user_creds: Dict[str, str], redis_client) -> Dict[str, Union[str, bool]]:
    if not NEXTCLOUD_URL: return {"status": "FAILURE", "message": "Nextcloud configuration missing.", "service": "calendar_list"}
    
    user = user_creds.get("user")
    if redis_client and user:
        cached = redis_client.get(_get_cal_list_cache_key(user))
        if cached: return {"status": "SUCCESS", "message": cached, "service": "calendar_list"}

    try:
        def _fetch():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            valid = [f"- {c.name}" for c in calendars if "birthday" not in (c.name or "").lower() and "contact" not in (c.name or "").lower()]
            return "Available Calendars:\n" + "\n".join(valid) if valid else "No writable calendars."
        
        final_res = await run_blocking(_fetch)
        if redis_client and user: redis_client.setex(_get_cal_list_cache_key(user), CAL_LIST_TTL, final_res)
        return {"status": "SUCCESS", "message": final_res, "service": "calendar_list"}
    except Exception as e: 
        return {"status": "FAILURE", "message": f"Error listing calendars: {e}", "service": "calendar_list"}

async def tool_calendar_read(user_creds: Dict[str, str], redis_client) -> str:
    if not NEXTCLOUD_URL: return ""
    try:
        def _fetch():
            client = _get_cal_client(user_creds)
            found_events = []
            calendars = client.principal().calendars()
            log.info(f"[CALENDAR] Discovered {len(calendars)} calendars.")
            
            # Use naive UTC datetimes for widest compatibility (server usually assumes UTC if naive)
            now_utc = datetime.now(tz.tzutc())
            start_search = (now_utc - timedelta(hours=1)).replace(tzinfo=None)
            end_search = (now_utc + timedelta(days=7)).replace(tzinfo=None)
            log.info(f"[CALENDAR] Searching from {start_search} to {end_search} (Naive UTC)")

            for cal in calendars:
                cal_name = cal.name or "Untitled"
                if any(x in cal_name.lower() for x in ["birthday", "contact", "holiday"]):
                    continue

                try:
                    log.info(f"[CAL_BRUTE] Processing calendar: {cal_name}")
                    all_events = cal.events()
                    log.info(f"[CAL_BRUTE] {cal_name} has {len(all_events)} total events.")
                    
                    for ev in all_events:
                        try:
                            vo = ev.vobject_instance
                            if not hasattr(vo, 'vevent'): continue
                            ve = vo.vevent
                            
                            summary = ve.summary.value if hasattr(ve, 'summary') else "No Summary"
                            dt_start = ve.dtstart.value if hasattr(ve, 'dtstart') else None
                            
                            log.info(f"[CAL_EVENT] Local: {cal_name} | Event: '{summary}' | Start: {dt_start}")
                            
                            if dt_start:
                                norm_dt = _normalize_event_time(dt_start)
                                if norm_dt:
                                    t_str = norm_dt.strftime("%Y-%m-%d %I:%M %p")
                                    found_events.append(f"- [{t_str}] {summary} ({cal_name})")
                        except Exception as inner_e:
                            log.debug(f"[CAL_BRUTE] Error parsing event in {cal_name}: {inner_e}")
                            continue

                except Exception as e:
                    log.warning(f"Error reading calendar '{cal_name}': {e}")
                    pass
            
            # Sort events by time
            found_events.sort()
            log.info(f"[CAL_BRUTE] Final found_events count: {len(found_events)}")
            return "Upcoming Events:\n" + "\n".join(found_events) if found_events else "No events found on your calendars."
        
        return await run_blocking(_fetch)
    except Exception as e:
        log.error(f"Global calendar read error: {e}")
        return ""

async def tool_calendar_add(query: str, user_creds: Dict[str, str], model: str, redis_client) -> Dict[str, Union[str, bool]]:
    if not NEXTCLOUD_URL: return {"status": "FAILURE", "message": "Error: Nextcloud not configured.", "service": "calendar_add"}
    
    data = await extract_event_data(query, model)
    summary = data.get("summary")
    start = data.get("start_time")
    
    if not summary or not start: 
        return {"status": "FAILURE", "message": "Missing event details.", "service": "calendar_add"}
    
    local_tz = _get_local_tz()
    dt = dateparser.parse(start, settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now(local_tz).replace(tzinfo=None)})
    if not dt: return {"status": "FAILURE", "message": f"Invalid date: {start}", "service": "calendar_add"}

    if "am" not in start.lower() and "pm" not in start.lower():
        if dt.hour < 7:
            dt = dt + timedelta(hours=12)

    local_tz = _get_local_tz()
    dt_aware = dt.replace(tzinfo=local_tz) 
    dt_utc = dt_aware.astimezone(tz.tzutc())

    try:
        def _add():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            if not calendars: raise Exception("No calendars found.")
            
            calendars.sort(key=lambda c: 0 if "personal" in (c.name or "").lower() else 10)
            selected = None
            
            for c in calendars:
                if _is_cal_writable(c, user_creds['user'], redis_client):
                    selected = c
                    break
            
            if not selected: raise Exception("No suitable writable calendar found.")

            end_utc = dt_utc + timedelta(hours=1)
            selected.save_event(dtstart=dt_utc, dtend=end_utc, summary=summary)
            _set_user_default_cal(user_creds['user'], selected.name, redis_client)
            return selected.name

        cal_name = await run_blocking(_add)
        msg = f"Scheduled '{summary}' for {dt_aware.strftime('%Y-%m-%d %I:%M %p')} on '{cal_name}'."
        return {"status": "SUCCESS", "message": msg, "service": "calendar_add", "summary": summary, "calendar": cal_name}
    except Exception as e:
        return {"status": "FAILURE", "message": f"Failed to add event: {str(e)}", "service": "calendar_add"}

async def tool_calendar_delete(query: str, user_creds: Dict[str, str], model: str, redis_client) -> Dict[str, Union[str, bool]]:
    if not NEXTCLOUD_URL: return {"status": "FAILURE", "message": "Error: Nextcloud not configured.", "service": "calendar_delete"}
    
    data = await extract_event_data(query, model)
    keyword = data.get("summary", "")
    target_time_str = data.get("start_time")
    
    target_dt = None
    target_date_only = False
    
    local_tz = _get_local_tz()
    if target_time_str:
        target_dt = dateparser.parse(target_time_str, settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now(local_tz).replace(tzinfo=None)})
        # Check if the user only gave a date (e.g. "tomorrow") without a time
        if target_dt and "at" not in target_time_str and ":" not in target_time_str:
             target_date_only = True

    if not keyword and not target_dt:
        return {"status": "FAILURE", "message": "Please provide an event name or time to delete.", "service": "calendar_delete"}

    # Prepare cleaning
    original_keyword = keyword
    cleaned_keyword = re.sub(r"\b(appointment|meeting|event|session)\b", "", keyword, flags=re.IGNORECASE).strip()

    try:
        def _delete():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            count = 0
            
            # Expanded search window to ensure we catch events
            now_aware = datetime.now(local_tz)
            search_start = (target_dt.replace(tzinfo=local_tz) - timedelta(days=1)) if target_dt else (now_aware - timedelta(days=1))
            search_end = search_start + timedelta(days=60)

            for c in calendars:
                if not _is_cal_writable(c, user_creds['user'], redis_client): continue
                try:
                    events = c.search(start=search_start, end=search_end, event=True, expand=True)
                    for ev in events:
                        ve = ev.vobject_instance.vevent
                        event_summary = ve.summary.value.lower()
                        event_start_local = _normalize_event_time(ve.dtstart.value)
                        
                        log.info(f"Checking event: '{event_summary}' at {event_start_local}")

                        # --- FILTER 1: TIME CHECK ---
                        if target_dt:
                            if target_date_only:
                                # Match just the day
                                if event_start_local.date() != target_dt.date(): continue
                            else:
                                # Match exact time (allow 30m leeway)
                                diff = abs((event_start_local - target_dt).total_seconds())
                                if diff > 1800: continue 

                        # --- FILTER 2: NAME CHECK ---
                        if keyword:
                            if keyword.lower() in ["event", "appointment", "meeting"] and target_dt:
                                pass 
                            elif original_keyword.lower() in event_summary:
                                pass
                            elif cleaned_keyword and cleaned_keyword.lower() in event_summary:
                                pass
                            else:
                                continue 

                        ev.delete()
                        count += 1
                        break 
                    if count > 0: break
                except: pass
            return count

        cnt = await run_blocking(_delete)
        if cnt > 0:
            msg = f"Deleted {cnt} event(s)."
            return {"status": "SUCCESS", "message": msg, "service": "calendar_delete", "count": cnt}
        else:
            return {"status": "FAILURE", "message": "No matching event found.", "service": "calendar_delete"}
            
    except Exception as e: 
        return {"status": "FAILURE", "message": f"Delete error: {e}", "service": "calendar_delete"}

async def tool_calendar_update(query: str, user_creds: Dict[str, str], model: str, redis_client) -> Dict[str, Union[str, bool]]:
    if not NEXTCLOUD_URL: return {"status": "FAILURE", "message": "Error: Nextcloud not configured.", "service": "calendar_update"}
    
    data = await extract_event_data(query, model)
    keyword = data.get("summary")
    new_start = data.get("start_time")
    
    if not keyword or not new_start: 
        return {"status": "FAILURE", "message": "Missing details.", "service": "calendar_update"}
    
    local_tz = _get_local_tz()
    dt = dateparser.parse(new_start, settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now(local_tz).replace(tzinfo=None)})
    
    if "am" not in new_start.lower() and "pm" not in new_start.lower() and dt.hour < 7:
        dt = dt + timedelta(hours=12)

    local_tz = _get_local_tz()
    dt_aware = dt.replace(tzinfo=local_tz)
    dt_utc = dt_aware.astimezone(tz.tzutc())

    cleaned_keyword = re.sub(r"\b(appointment|meeting|event|session)\b", "", keyword, flags=re.IGNORECASE).strip()

    try:
        def _update():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            count = 0
            now_aware = datetime.now(local_tz)
            start_search = now_aware - timedelta(days=1)
            end_search = start_search + timedelta(days=60)
            
            for c in calendars:
                if not _is_cal_writable(c, user_creds['user'], redis_client): continue
                try:
                    events = c.search(start=start_search, end=end_search, event=True, expand=True)
                    for ev in events:
                        ve = ev.vobject_instance.vevent
                        summary_lower = ve.summary.value.lower()
                        
                        match = False
                        if keyword.lower() in summary_lower: match = True
                        elif cleaned_keyword and cleaned_keyword.lower() in summary_lower: match = True
                        
                        if match:
                            duration = timedelta(hours=1)
                            try:
                                if hasattr(ve, 'dtend'): 
                                    duration = ve.dtend.value - ve.dtstart.value
                            except: pass
                            
                            ve.dtstart.value = dt_utc
                            ve.dtend.value = dt_utc + duration
                            ev.save()
                            count += 1
                            break
                    if count > 0: break
                except: pass
            return count

        cnt = await run_blocking(_update)
        status = "SUCCESS" if cnt > 0 else "FAILURE"
        msg = f"Rescheduled '{keyword}' to {dt_aware.strftime('%Y-%m-%d %I:%M %p')}." if cnt else "Event not found."
        return {"status": status, "message": msg, "service": "calendar_update"}
    except Exception as e: 
        return {"status": "FAILURE", "message": f"Update error: {e}", "service": "calendar_update"}
