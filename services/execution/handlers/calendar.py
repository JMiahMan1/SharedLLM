# services/execution/handlers/calendar.py
import asyncio
import logging
import time
import urllib.request
from datetime import UTC, date, datetime, timedelta

import caldav
import dateparser
import pytz
from icalendar import Calendar

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


# ── Integration resolution (runtime-derived, never hardcoded) ──────────────


# Cache last-known Skylight configuration so a transient identity/host blip
# (which makes resolve_first_user() throw) doesn't silently disable Skylight.
_SKYLIGHT_CONFIG_CACHE: dict = {"enabled": None, "ts": 0.0}
_SKYLIGHT_CONFIG_TTL = 300  # seconds

# Per-source health to detect "soft" outages: a source that usually returns
# events but now returns an empty payload (without raising) is soft-unavailable.
_SOURCE_HEALTH: dict[str, dict] = {}  # type -> {"last_non_empty": float, "last_count": int}


async def _resolve_calendar_integrations(req: CalendarRequest, availability: dict | None = None) -> list[dict]:
    """Build the live list of calendar integrations for this user.

    `enabled` = configured (credentials/URLs present, not disabled) and is
    intentionally STABLE across transient backend blips. `available` = reachable
    right now (set from a live read; defaults to enabled when unknown). A source
    that is enabled but temporarily unavailable stays VISIBLE (greyed) so the UI
    can show a notice and keep retrying, instead of silently disappearing.
    """
    settings = (req.user_context.calendar_settings or {}) if getattr(req.user_context, "calendar_settings", None) else {}
    disabled = set(settings.get("disabled") or [])

    provider = resolve_personal_data_provider(req.user_context)
    nc_enabled = provider is not None and "nextcloud" not in disabled

    sk_enabled = False
    sk_error = None
    if "skylight" not in disabled:
        try:
            from services.execution.main import _skylight_configured

            configured = await _skylight_configured(req.user_context.user)
            _SKYLIGHT_CONFIG_CACHE["enabled"] = configured
            _SKYLIGHT_CONFIG_CACHE["ts"] = time.time()
            sk_enabled = configured
        except Exception as e:
            # Transient failure (identity/host blip). Fall back to last-known
            # config if recent so Skylight isn't dropped mid-outage.
            cached = _SKYLIGHT_CONFIG_CACHE
            if cached["enabled"] is not None and (time.time() - cached["ts"]) < _SKYLIGHT_CONFIG_TTL:
                sk_enabled = cached["enabled"]
                sk_error = "configuration temporarily unreachable"
            else:
                sk_enabled = False
                sk_error = str(e)[:120]

    ical_urls = settings.get("ical_urls") or []
    ical_enabled = bool(ical_urls) and "ical" not in disabled

    def _mk(t: str, en: bool, writable: bool, **extra: object) -> dict:
        av = (availability or {}).get(t)
        info: dict = {"type": t, "enabled": en, "writable": writable, "provides_calendar": True, **extra}
        if av is not None:
            info["available"] = av.get("available", en)
            if av.get("error"):
                info["error"] = av["error"]
            if av.get("soft"):
                info["soft"] = True
        else:
            info["available"] = en  # optimistic when not yet probed
        return info

    return [
        _mk("nextcloud", nc_enabled, True),
        _mk("skylight", sk_enabled, True, **({"error": sk_error} if sk_error else {})),
        _mk("ical", ical_enabled, False, urls=ical_urls),
    ]


def _default_integration(integrations: list[dict], settings: dict) -> tuple[str | None, bool]:
    """Pick the default integration — runtime-derived, never hardcoded.

    - explicit stored default (if still enabled)
    - else auto-default to the ONLY enabled calendar integration
    - else first by priority, and signal the UI to prompt (needs_default_choice)
    """
    enabled = [i for i in integrations if i["enabled"] and i["provides_calendar"]]
    if not enabled:
        return None, False

    default = settings.get("default")
    if default and any(i["type"] == default and i["enabled"] for i in integrations):
        return default, False

    if len(enabled) == 1:
        return enabled[0]["type"], False

    priority = settings.get("priority") or {}
    enabled.sort(key=lambda i: priority.get(i["type"], 100))
    return enabled[0]["type"], True


# ── Per-integration readers (each returns normalized event dicts) ──────────

# Normalized event shape consumed by UpcomingEventsWidget.parseEvent plus an
# `integration` tag for the merged-agenda UI:
#   { integration, summary, start_time(ISO-8601), end_time?, location?, calendar? }


def _nextcloud_list_calendars(client):
    try:
        calendars = client.principal().calendars()
    except Exception:
        return []
    return [
        (cal.url, cal.name)
        for cal in calendars
        if not any(x in (cal.name or "").lower() for x in ["birthday", "contact", "holiday"])
    ]


def _nextcloud_search_one(client, cal_url, cal_name, lo, hi):
    try:
        c = caldav.Calendar(client=client, url=cal_url)
        # NOTE: caldav's server-side time-range filter (start=/end=) is
        # unreliable here -- it silently returns 0 results for valid windows.
        # Fetch all VEVENTs for the calendar and filter in Python instead.
        events = c.search(event=True)
        out = []
        for ev in events:
            try:
                ve = ev.vobject_instance.vevent
            except Exception:
                continue
            if not hasattr(ve, "dtstart"):
                continue
            start_dt = _normalize_event_time(ve.dtstart.value)
            if not start_dt:
                continue
            # Drop events outside the requested window (calendars hold years
            # of historical data; only upcoming/near-past matter for the feed).
            if start_dt < lo or start_dt > hi:
                continue
            end_dt = _normalize_event_time(ve.dtend.value) if hasattr(ve, "dtend") else None
            loc = ve.location.value if hasattr(ve, "location") else None
            out.append({
                "integration": "nextcloud",
                "summary": (ve.summary.value if hasattr(ve, "summary") else "No Summary"),
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat() if end_dt else None,
                "location": loc,
                "calendar": cal_name,
            })
        return out
    except Exception:
        return []


async def _read_nextcloud(req: CalendarRequest, lo, hi) -> list[dict]:
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return []
    try:
        client = provider.calendar_client()
    except Exception:
        return []

    loop = asyncio.get_running_loop()
    try:
        targets = await asyncio.wait_for(loop.run_in_executor(None, _nextcloud_list_calendars, client), timeout=8)
    except Exception:
        return []

    async def _search(cal_url, cal_name):
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _nextcloud_search_one, client, cal_url, cal_name, lo, hi),
                timeout=6,
            )
        except Exception:
            return []

    results = await asyncio.gather(*(_search(u, n) for u, n in targets), return_exceptions=True)
    events: list[dict] = []
    for r in results:
        if isinstance(r, list):
            events.extend(r)
    return events


async def _read_skylight(req: CalendarRequest, lo, hi) -> list[dict]:
    try:
        from services.execution.main import _get_skylight_session, _skylight_request
    except Exception:
        return []
    session = await _get_skylight_session(req.user_context.user)
    if not session:
        return []
    params = {
        "date_min": lo.date().isoformat(),
        "date_max": hi.date().isoformat(),
        "timezone": LOCAL_TZ_NAME,
    }
    result = await _skylight_request(session, "GET", "/calendar_events", params=params)
    if not result:
        return []
    data = result.get("data", []) if isinstance(result, dict) else []
    out: list[dict] = []
    for ev in data:
        attrs = ev.get("attributes", {}) or {}
        start = attrs.get("starts_at")
        if not start:
            continue
        out.append({
            "id": ev.get("id"),
            "integration": "skylight",
            "summary": attrs.get("summary") or "Event",
            "start_time": start,
            "end_time": attrs.get("ends_at"),
            "location": attrs.get("location"),
            "calendar": attrs.get("calendar_id"),
        })
    return out


def _fetch_ics(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "SharedLLM/1.0"})
    with urllib.request.urlopen(request, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def _parse_ics(text: str, lo, hi) -> list[dict]:
    try:
        cal = Calendar.from_ical(text)
    except Exception:
        return []
    out: list[dict] = []
    local_tz = _get_local_tz()
    for comp in cal.walk("VEVENT"):
        dt = comp.get("dtstart")
        if not dt:
            continue
        val = dt.dt
        if isinstance(val, datetime):
            start = val if val.tzinfo else val.replace(tzinfo=UTC).astimezone(local_tz)
        elif isinstance(val, date):
            start = datetime.combine(val, datetime.min.time(), tzinfo=local_tz)
        else:
            continue
        if not (lo <= start <= hi):
            continue
        end = comp.get("dtend")
        end_dt = None
        if end:
            ev = end.dt
            if isinstance(ev, datetime):
                end_dt = ev if ev.tzinfo else ev.replace(tzinfo=UTC).astimezone(local_tz)
        out.append({
            "integration": "ical",
            "summary": str(comp.get("summary", "Event")),
            "start_time": start.isoformat(),
            "end_time": end_dt.isoformat() if end_dt else None,
            "location": str(comp.get("location", "")) or None,
            "calendar": "ical",
        })
    return out


async def _read_ical(req: CalendarRequest, lo, hi) -> list[dict]:
    settings = (req.user_context.calendar_settings or {}) if getattr(req.user_context, "calendar_settings", None) else {}
    urls = settings.get("ical_urls") or []
    if not urls:
        return []
    loop = asyncio.get_running_loop()
    out: list[dict] = []
    for url in urls:
        try:
            text = await loop.run_in_executor(None, _fetch_ics, url)
            out.extend(_parse_ics(text, lo, hi))
        except Exception:
            continue
    return out


# ── Action handlers ────────────────────────────────────────────────────────


async def handle_calendar(req: CalendarRequest) -> ExecutionResult:
    action = req.action
    log.info(f"[calendar] Action: {action} for user: {req.user_context.user}")

    integrations = await _resolve_calendar_integrations(req)
    settings = (req.user_context.calendar_settings or {}) if getattr(req.user_context, "calendar_settings", None) else {}
    default, needs_choice = _default_integration(integrations, settings)

    try:
        if action == "list":
            provider = resolve_personal_data_provider(req.user_context)
            calendars = []
            if provider:
                try:
                    client = provider.calendar_client()
                    for c in _nextcloud_list_calendars(client):
                        calendars.append({"id": str(c[0]), "display_name": c[1]})
                except Exception:
                    pass
            return ExecutionResult(
                status="SUCCESS",
                message=f"Loaded {len(calendars)} calendar(s).",
                service="calendar_list",
                detail={
                    "calendars": calendars,
                    "integrations": integrations,
                    "default": default,
                    "needs_default_choice": needs_choice,
                    "available_defaults": [i["type"] for i in integrations if i["enabled"] and i["provides_calendar"]],
                },
            )

        elif action == "read":
            disabled = set(settings.get("disabled") or [])
            want = (req.integration or "").strip().lower()
            if want and want != "all":
                targets = [i for i in integrations if i["type"] == want and i["enabled"]]
            else:
                targets = [
                    i for i in integrations
                    if i["enabled"] and i["provides_calendar"] and i["type"] not in disabled
                ]

            local_tz = _get_local_tz()
            now_lo = datetime.now(local_tz) - timedelta(days=1)
            now_hi = datetime.now(local_tz) + timedelta(days=7)

            async def _gather_one(i: dict) -> list[dict]:
                if i["type"] == "nextcloud":
                    return await _read_nextcloud(req, now_lo, now_hi)
                if i["type"] == "skylight":
                    return await _read_skylight(req, now_lo, now_hi)
                if i["type"] == "ical":
                    return await _read_ical(req, now_lo, now_hi)
                return []

            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*(_gather_one(i) for i in targets), return_exceptions=True),
                    timeout=12,
                )
            except TimeoutError:
                results = None

            # Per-integration liveness. A source is "hard" down when its read
            # raised or timed out. It is "soft" down when the read succeeded but
            # returned nothing AND this source normally carries events (it had
            # some within the last day) — a blip that yields an empty payload
            # instead of an error. A genuinely empty calendar (no recent data)
            # is left alone so we never cry wolf on a quiet period.
            availability: dict = {}
            if results is None:
                for i in targets:
                    availability[i["type"]] = {"available": False, "error": "timed out", "soft": False}
            else:
                for i, r in zip(targets, results, strict=False):
                    t = i["type"]
                    if isinstance(r, Exception):
                        availability[t] = {"available": False, "error": str(r)[:120], "soft": False}
                        continue
                    count = len(r) if isinstance(r, list) else 0
                    if count > 0:
                        _SOURCE_HEALTH[t] = {"last_non_empty": time.time(), "last_count": count}
                        availability[t] = {"available": True, "error": None, "soft": False}
                    else:
                        h = _SOURCE_HEALTH.get(t)
                        had_recent = bool(h) and (time.time() - h["last_non_empty"]) < 86400
                        availability[t] = {"available": not had_recent, "error": None, "soft": had_recent}

            integrations = await _resolve_calendar_integrations(req, availability)

            events: list[dict] = []
            sets = results if results is not None else []
            for s in sets:
                if isinstance(s, list):
                    events.extend(s)
            events.sort(key=lambda e: e.get("start_time") or "")

            return ExecutionResult(
                status="SUCCESS",
                message=f"Loaded {len(events)} event(s).",
                service="calendar_read",
                detail={
                    "integrations": integrations,
                    "default": default,
                    "needs_default_choice": needs_choice,
                    "available_defaults": [i["type"] for i in integrations if i["enabled"] and i["provides_calendar"]],
                },
                events=events,
            )

        elif action == "add":
            if not req.summary or not req.start_time:
                return ExecutionResult(status="FAILURE", message="Summary and start_time are required.", service="calendar_add")

            target = (req.integration or "").strip().lower() or default or "nextcloud"
            if target == "skylight":
                try:
                    from services.execution.main import _get_skylight_session, _skylight_request
                except Exception:
                    return ExecutionResult(status="FAILURE", message="Skylight module unavailable.", service="calendar_add")
                session = await _get_skylight_session(req.user_context.user)
                if not session:
                    return ExecutionResult(status="FAILURE", message="Skylight not configured.", service="calendar_add")
                dt = dateparser.parse(req.start_time, settings={"PREFER_DATES_FROM": "future"})
                if dt is None:
                    return ExecutionResult(status="FAILURE", message=f"Could not parse date: {req.start_time}", service="calendar_add")
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_get_local_tz())
                dt_end = dt + timedelta(hours=1)
                body = {
                    "summary": req.summary,
                    "starts_at": dt.isoformat(),
                    "ends_at": dt_end.isoformat(),
                }
                result = await _skylight_request(session, "POST", "/calendar_events", body)
                if result is not None:
                    return ExecutionResult(status="SUCCESS", message=f"Added '{req.summary}' to Skylight.", service="calendar_add")
                return ExecutionResult(status="FAILURE", message="Failed to add Skylight event.", service="calendar_add")

            if target == "ical":
                return ExecutionResult(status="FAILURE", message="iCal calendars are read-only.", service="calendar_add")

            # nextcloud
            provider = resolve_personal_data_provider(req.user_context)
            if not provider:
                return ExecutionResult(status="FAILURE", message="Nextcloud not configured.", service="calendar_add")

            dt = dateparser.parse(req.start_time, settings={"PREFER_DATES_FROM": "future"})
            if dt is None:
                return ExecutionResult(status="FAILURE", message=f"Could not parse date: {req.start_time}", service="calendar_add")
            local_tz = _get_local_tz()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_tz)
            dt_utc = dt.astimezone(UTC)

            def _add():
                client = provider.calendar_client()
                calendars = client.principal().calendars()
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
                if not selected and calendars:
                    selected = calendars[0]
                if not selected:
                    return "No writable calendar found."
                selected.save_event(dtstart=dt_utc, dtend=dt_utc + timedelta(hours=1), summary=req.summary)
                return f"Added '{req.summary}' to {selected.name} for {dt.strftime('%Y-%m-%d %I:%M %p %Z')}."

            try:
                loop = asyncio.get_running_loop()
                res_msg = await loop.run_in_executor(None, _add)
                return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_add")
            except Exception as e:
                return ExecutionResult(status="FAILURE", message=f"Calendar add error: {e!s}", service="calendar_add")

        elif action == "delete":
            target = (req.integration or "").strip().lower()
            if req.event_id and target == "skylight":
                try:
                    from services.execution.main import _get_skylight_session, _skylight_request
                except Exception:
                    return ExecutionResult(status="FAILURE", message="Skylight module unavailable.", service="calendar_delete")
                session = await _get_skylight_session(req.user_context.user)
                if not session:
                    return ExecutionResult(status="FAILURE", message="Skylight not configured.", service="calendar_delete")
                result = await _skylight_request(session, "DELETE", f"/calendar_events/{req.event_id}")
                if result is not None:
                    return ExecutionResult(status="SUCCESS", message="Deleted Skylight event.", service="calendar_delete")
                return ExecutionResult(status="FAILURE", message="Failed to delete Skylight event.", service="calendar_delete")

            if not req.query:
                return ExecutionResult(status="FAILURE", message="Query parameter is required for delete.", service="calendar_delete")

            provider = resolve_personal_data_provider(req.user_context)
            if not provider:
                return ExecutionResult(status="FAILURE", message="Nextcloud not configured.", service="calendar_delete")

            def _delete():
                client = provider.calendar_client()
                calendars = client.principal().calendars()
                deleted_count = 0
                query_lower = req.query.lower()
                for cal in calendars:
                    if any(x in (cal.name or "").lower() for x in ["birthday", "contact", "holiday"]):
                        continue
                    try:
                        events = cal.search(event=True, expand=True)
                        for ev in events:
                            ve = ev.vobject_instance.vevent
                            summary = ve.summary.value if hasattr(ve, "summary") else ""
                            if query_lower in summary.lower():
                                ev.delete()
                                deleted_count += 1
                    except Exception:
                        continue
                return f"Deleted {deleted_count} matching event(s)."

            try:
                loop = asyncio.get_running_loop()
                res_msg = await loop.run_in_executor(None, _delete)
                return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_delete")
            except Exception as e:
                return ExecutionResult(status="FAILURE", message=f"Calendar delete error: {e!s}", service="calendar_delete")

        elif action == "update":
            target = (req.integration or "").strip().lower()
            if req.event_id and target == "skylight":
                try:
                    from services.execution.main import _get_skylight_session, _skylight_request
                except Exception:
                    return ExecutionResult(status="FAILURE", message="Skylight module unavailable.", service="calendar_update")
                session = await _get_skylight_session(req.user_context.user)
                if not session:
                    return ExecutionResult(status="FAILURE", message="Skylight not configured.", service="calendar_update")
                attributes: dict = {}
                if req.summary:
                    attributes["summary"] = req.summary
                if req.start_time:
                    dt = dateparser.parse(req.start_time, settings={"PREFER_DATES_FROM": "future"})
                    if dt is None:
                        return ExecutionResult(status="FAILURE", message=f"Could not parse date: {req.start_time}", service="calendar_update")
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=_get_local_tz())
                    attributes["starts_at"] = dt.isoformat()
                    attributes["ends_at"] = (dt + timedelta(hours=1)).isoformat()
                if not attributes:
                    return ExecutionResult(status="FAILURE", message="Nothing to update.", service="calendar_update")
                body = {"data": {"type": "calendar_event", "id": req.event_id, "attributes": attributes}}
                result = await _skylight_request(session, "PATCH", f"/calendar_events/{req.event_id}", body)
                if result is not None:
                    return ExecutionResult(status="SUCCESS", message="Updated Skylight event.", service="calendar_update")
                return ExecutionResult(status="FAILURE", message="Failed to update Skylight event.", service="calendar_update")

            if not req.query or not req.summary:
                return ExecutionResult(status="FAILURE", message="Query and summary are required for update.", service="calendar_update")

            provider = resolve_personal_data_provider(req.user_context)
            if not provider:
                return ExecutionResult(status="FAILURE", message="Nextcloud not configured.", service="calendar_update")

            def _update():
                client = provider.calendar_client()
                calendars = client.principal().calendars()
                updated_count = 0
                query_lower = req.query.lower()
                for cal in calendars:
                    if any(x in (cal.name or "").lower() for x in ["birthday", "contact", "holiday"]):
                        continue
                    try:
                        events = cal.search(event=True, expand=True)
                        for ev in events:
                            ve = ev.vobject_instance.vevent
                            summary = ve.summary.value if hasattr(ve, "summary") else ""
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
                    except Exception:
                        continue
                return f"Updated {updated_count} matching event(s) to '{req.summary}'."

            try:
                loop = asyncio.get_running_loop()
                res_msg = await loop.run_in_executor(None, _update)
                return ExecutionResult(status="SUCCESS", message=res_msg, service="calendar_update")
            except Exception as e:
                return ExecutionResult(status="FAILURE", message=f"Calendar update error: {e!s}", service="calendar_update")

        return ExecutionResult(status="FAILURE", message=f"Unknown calendar action: {action}", service="calendar")
    except Exception as e:
        log.error(f"Calendar error: {e!r}")
        return ExecutionResult(status="FAILURE", message=f"Calendar error: {e!s}", service="calendar")
