# services/execution/handlers/calendar.py
import os
import logging
import asyncio
import caldav
from datetime import datetime, timedelta, date
from dateutil import tz
from typing import Dict, List, Optional, Union

try:
    from ..schemas import CalendarRequest, ExecutionResult
    from .. import ha_client # Still needed for potential context
except ImportError:
    from schemas import CalendarRequest, ExecutionResult
    import ha_client

log = logging.getLogger("execution.calendar")

# Settings from env (provided by .env via docker-compose)
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
LOCAL_TZ_NAME = os.getenv("TIMEZONE", "America/Phoenix")

def _get_local_tz():
    return tz.gettz(LOCAL_TZ_NAME)

def _get_cal_client(nc_pass: Optional[str] = None):
    url = f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav"
    return caldav.DAVClient(
        url=url, 
        username=NEXTCLOUD_USER, 
        password=nc_pass or NEXTCLOUD_PASS, 
        timeout=60
    )

def _normalize_event_time(dt_value):
    """Converts ANY event time to AWARE local time."""
    local_tz = _get_local_tz()
    if isinstance(dt_value, datetime):
        if dt_value.tzinfo is None:
            return dt_value.replace(tzinfo=tz.tzutc()).astimezone(local_tz)
        return dt_value.astimezone(local_tz)
    elif isinstance(dt_value, date):
        target = datetime.combine(dt_value, datetime.min.time())
        return target.replace(tzinfo=local_tz)
    return None

async def handle_calendar(req: CalendarRequest) -> ExecutionResult:
    if not NEXTCLOUD_URL:
        return ExecutionResult(status="FAILURE", message="Nextcloud URL not configured.", service="calendar")

    action = req.action
    log.info(f"[calendar] Action: {action} for user: {req.user_context.user}")

    # Use a thread pool for blocking caldav calls
    loop = asyncio.get_running_loop()
    
    try:
        if action == "list":
            def _list():
                client = _get_cal_client()
                calendars = client.principal().calendars()
                valid = [f"- {c.name}" for c in calendars if "birthday" not in (c.name or "").lower()]
                return "Available Calendars:\n" + "\n".join(valid) if valid else "No calendars found."
            
            res_msg = await loop.run_in_executor(None, _list)
            return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_list")

        elif action == "read":
            def _read():
                client = _get_cal_client()
                found_events = []
                calendars = client.principal().calendars()
                local_tz = _get_local_tz()
                now_aware = datetime.now(local_tz)
                
                for cal in calendars:
                    if any(x in (cal.name or "").lower() for x in ["birthday", "contact", "holiday"]): continue
                    try:
                        events = cal.search(start=now_aware - timedelta(days=1), end=now_aware + timedelta(days=7), event=True, expand=True)
                        for ev in events:
                            ve = ev.vobject_instance.vevent
                            summary = ve.summary.value if hasattr(ve, 'summary') else "No Summary"
                            start_dt = _normalize_event_time(ve.dtstart.value)
                            if start_dt:
                                t_str = start_dt.strftime("%Y-%m-%d %I:%M %p")
                                found_events.append(f"- [{t_str}] {summary} ({cal.name})")
                    except: continue
                found_events.sort()
                return "Upcoming Events:\n" + "\n".join(found_events) if found_events else "No events found."

            res_msg = await loop.run_in_executor(None, _read)
            return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_read")

        elif action == "add":
            if not req.summary or not req.start_time:
                return ExecutionResult(status="FAILURE", message="Summary and start_time are required.", service="calendar_add")
            
            import dateparser
            dt = dateparser.parse(req.start_time, settings={'PREFER_DATES_FROM': 'future'})
            if not dt:
                return ExecutionResult(status="FAILURE", message=f"Could not parse date: {req.start_time}", service="calendar_add")
            
            def _add():
                client = _get_cal_client()
                calendars = client.principal().calendars()
                # Pick first writable calendar
                selected = None
                for c in calendars:
                    if "personal" in (c.name or "").lower() or "default" in (c.name or "").lower():
                        selected = c
                        break
                if not selected: selected = calendars[0]
                
                dt_utc = dt.astimezone(tz.tzutc())
                selected.save_event(dtstart=dt_utc, dtend=dt_utc + timedelta(hours=1), summary=req.summary)
                return f"Added '{req.summary}' to {selected.name} for {dt.strftime('%I:%M %p')}."

            res_msg = await loop.run_in_executor(None, _add)
            return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_add")

        return ExecutionResult(status="FAILURE", message=f"Action {action} not yet implemented.", service="calendar")

    except Exception as e:
        log.error(f"Calendar error: {e}")
        return ExecutionResult(status="FAILURE", message=f"Calendar error: {str(e)}", service="calendar")
