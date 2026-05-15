# services/execution/handlers/calendar.py
import os
import sys
import logging
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from config import TIMEZONE
from datetime import datetime, timedelta, date
from dateutil import tz

try:
    from schemas import CalendarRequest, ExecutionResult
    from personal_data import resolve_personal_data_provider
except ImportError:
    from schemas import CalendarRequest, ExecutionResult
    from personal_data import resolve_personal_data_provider

log = logging.getLogger("execution.calendar")

LOCAL_TZ_NAME = TIMEZONE

def _get_local_tz():
    return tz.gettz(LOCAL_TZ_NAME)

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
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return ExecutionResult(status="FAILURE", message="Nextcloud URL not configured.", service="calendar")

    action = req.action
    log.info(f"[calendar] Action: {action} for user: {req.user_context.user}")

    # Use a thread pool for blocking caldav calls
    loop = asyncio.get_running_loop()
    
    try:
        if action == "list":
            def _list():
                client = provider.calendar_client()
                calendars = client.principal().calendars()
                return [
                    {
                        "id": c.id,
                        "display_name": c.name,
                        "color": getattr(c, 'calendar_color', None)
                    } for c in calendars if "birthday" not in (c.name or "").lower()
                ]
            
            res_data = await loop.run_in_executor(None, _list)
            return ExecutionResult(
                status="SUCCESS",
                message=f"Loaded {len(res_data)} calendar(s).",
                service="calendar_list",
                detail={"calendars": res_data},
            )

        elif action == "read":
            def _read():
                client = provider.calendar_client()
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
                client = provider.calendar_client()
                calendars = client.principal().calendars()
                # Pick first writable calendar
                selected = None
                if req.calendar_name:
                    target_name = req.calendar_name.strip().lower()
                    for c in calendars:
                        if (c.name or "").strip().lower() == target_name:
                            selected = c
                            break
                if not selected:
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
