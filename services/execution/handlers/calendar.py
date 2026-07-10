# services/execution/handlers/calendar.py
import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

import caldav
import dateparser
import pytz

from services.config import TIMEZONE
from services.execution.schemas import CalendarRequest, ExecutionResult

from ..personal_data import resolve_personal_data_provider

log = logging.getLogger("execution.calendar")

LOCAL_TZ_NAME = TIMEZONE

def _get_local_tz():
           return pytz.timezone(LOCAL_TZ_NAME)

def _normalize_event_time(dt_value):
    """Converts ANY event time to AWARE local time."""
    local_tz = _get_local_tz()
    if isinstance(dt_value, datetime):
        if dt_value.tzinfo is None:
            return dt_value.replace(tzinfo=UTC).astimezone(local_tz)
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
            local_tz = _get_local_tz()
            now_lo = datetime.now(local_tz) - timedelta(days=1)
            now_hi = datetime.now(local_tz) + timedelta(days=7)

            def _list_calendars():
                client = provider.calendar_client()
                calendars = client.principal().calendars()
                return [
                    (cal.url, cal.name)
                    for cal in calendars
                    if not any(x in (cal.name or "").lower() for x in ["birthday", "contact", "holiday"])
                ]

            def _search_one(cal_url, cal_name, lo, hi):
                try:
                    c = provider.calendar_client()
                    target = caldav.Calendar(client=c, url=cal_url)
                    events = target.search(start=lo, end=hi, event=True, expand=True)
                    out = []
                    for ev in events:
                        ve = ev.vobject_instance.vevent
                        summary = ve.summary.value if hasattr(ve, "summary") else "No Summary"
                        start_dt = _normalize_event_time(ve.dtstart.value)
                        if start_dt:
                            out.append((start_dt, f"- [{start_dt.strftime('%Y-%m-%d %I:%M %p')}] {summary} ({cal_name})"))
                    return out
                except Exception:
                    return []

            async def _search(cal_url, cal_name):
                try:
                    return await asyncio.wait_for(
                        loop.run_in_executor(None, _search_one, cal_url, cal_name, now_lo, now_hi),
                        timeout=8,
                    )
                except Exception:
                    return []

            try:
                targets = await asyncio.wait_for(loop.run_in_executor(None, _list_calendars), timeout=10)
            except Exception:
                return ExecutionResult(status="SUCCESS", message="No events found.", service="calendar_read")
            results = await asyncio.gather(*(_search(u, n) for u, n in targets), return_exceptions=True)
            found = []
            for r in results:
                if isinstance(r, list):
                    found.extend(r)
            found.sort(key=lambda x: x[0])
            lines = [item[1] for item in found]
            res_msg = "Upcoming Events:\n" + "\n".join(lines) if lines else "No events found."
            return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_read")

        elif action == "add":
            if not req.summary or not req.start_time:
                return ExecutionResult(status="FAILURE", message="Summary and start_time are required.", service="calendar_add")

            dt = dateparser.parse(req.start_time, settings={"PREFER_DATES_FROM": "future"})
            if dt is None:
                return ExecutionResult(status="FAILURE", message=f"Could not parse date: {req.start_time}", service="calendar_add")

            # Ensure datetime is aware in the user's configured timezone
            local_tz = _get_local_tz()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_tz)
            dt_utc = dt.astimezone(UTC)

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

                selected.save_event(dtstart=dt_utc, dtend=dt_utc + timedelta(hours=1), summary=req.summary)
                return f"Added '{req.summary}' to {selected.name} for {dt.strftime('%Y-%m-%d %I:%M %p %Z')}."

            res_msg = await loop.run_in_executor(None, _add)
            return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_add")

        elif action == "delete":
            if not req.query:
                return ExecutionResult(status="FAILURE", message="Query parameter is required for delete.", service="calendar_delete")

            def _delete():
                client = provider.calendar_client()
                calendars = client.principal().calendars()
                deleted_count = 0
                query_lower = req.query.lower()
                for cal in calendars:
                    if any(x in (cal.name or "").lower() for x in ["birthday", "contact", "holiday"]): continue
                    try:
                        events = cal.search(event=True, expand=True)
                        for ev in events:
                            ve = ev.vobject_instance.vevent
                            summary = ve.summary.value if hasattr(ve, 'summary') else ""
                            if query_lower in summary.lower():
                                ev.delete()
                                deleted_count += 1
                    except: continue
                return f"Deleted {deleted_count} matching event(s)."

            res_msg = await loop.run_in_executor(None, _delete)
            return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_delete")

        elif action == "update":
            if not req.query or not req.summary:
                return ExecutionResult(status="FAILURE", message="Query and summary are required for update.", service="calendar_update")

            def _update():
                client = provider.calendar_client()
                calendars = client.principal().calendars()
                updated_count = 0
                query_lower = req.query.lower()
                for cal in calendars:
                    if any(x in (cal.name or "").lower() for x in ["birthday", "contact", "holiday"]): continue
                    try:
                        events = cal.search(event=True, expand=True)
                        for ev in events:
                            ve = ev.vobject_instance.vevent
                            summary = ve.summary.value if hasattr(ve, 'summary') else ""
                            if query_lower in summary.lower():
                                ve.summary.value = req.summary
                                if req.start_time:
                                    dt = dateparser.parse(req.start_time)
                                    if dt is not None:
                                        local_tz = _get_local_tz()
                                        if dt.tzinfo is None:
                                            dt = dt.replace(tzinfo=local_tz)
                                        ve.dtstart.value = dt
                                ev.save()
                                updated_count += 1
                    except: continue
                return f"Updated {updated_count} matching event(s) to '{req.summary}'."

            res_msg = await loop.run_in_executor(None, _update)
            return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_update")

        return ExecutionResult(status="FAILURE", message=f"Unknown calendar action: {action}", service="calendar")

    except Exception as e:
        log.error(f"Calendar error: {e!r}")
        return ExecutionResult(status="FAILURE", message=f"Calendar error: {e!s}", service="calendar")
