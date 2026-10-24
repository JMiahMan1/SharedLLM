"""The weekly recap for parents (Phase 11, step 6): a child's week in one page.

Built from what the phone and Jarvis already collected for the child: screen
time (and how it moved from the week before), top apps, what they played and
watched, sites visited and blocked, how many calls and messages and with how
many people (never the content), Bible reading, and the week's concerns from
content monitoring. Pure: the scheduler and the on-demand route both call it,
and a week with nothing reported says so instead of showing zeros.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

MEDIA_FEATURES = ("media", "music", "video", "podcast", "audiobook")
LATE_HOUR = 22  # a last use at or after this (household time) is a late night


def week_bounds(day: date) -> tuple[date, date]:
    """Monday to Sunday around ``day``."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _in_week(at: str | None, start: date, end: date) -> bool:
    if not at:
        return False
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00")).date()
    except ValueError:
        return False
    return start <= when <= end


def _minutes(ms: Any) -> int:
    try:
        return max(0, int(ms or 0)) // 60000
    except (TypeError, ValueError):
        return 0


def build(
    usage_days: list[dict[str, Any]],
    events: list[dict[str, Any]],
    concerns: list[dict[str, Any]],
    start: date,
    end: date,
    tz_name: str = "UTC",
) -> dict[str, Any]:
    """The recap for ``start``..``end`` (inclusive); ``usage_days`` may include the week before."""
    tz = ZoneInfo(tz_name)
    prev_start, prev_end = start - timedelta(days=7), start - timedelta(days=1)
    this_days = [d for d in usage_days if start.isoformat() <= str(d.get("day")) <= end.isoformat()]
    prev_days = [d for d in usage_days if prev_start.isoformat() <= str(d.get("day")) <= prev_end.isoformat()]

    screen = None
    if this_days:
        total = sum(_minutes(d.get("millis")) for d in this_days)
        prev_total = sum(_minutes(d.get("millis")) for d in prev_days) if prev_days else None
        busiest = max(this_days, key=lambda d: _minutes(d.get("millis")))
        screen = {
            "total_min": total,
            "daily_avg_min": round(total / len(this_days)),
            "days_reported": len(this_days),
            "prev_total_min": prev_total,
            "change_pct": round((total - prev_total) * 100 / prev_total) if prev_total else None,
            "busiest_day": busiest.get("day"),
            "busiest_min": _minutes(busiest.get("millis")),
            "late_nights": sum(
                1 for d in this_days
                if d.get("last_use") and datetime.fromtimestamp(int(d["last_use"]) / 1000, tz).hour >= LATE_HOUR
            ),
        }

    apps: Counter[str] = Counter()
    for d in this_days:
        for app in d.get("apps") or []:
            if isinstance(app, dict) and app.get("package"):
                apps[str(app["package"])] += _minutes(app.get("millis"))

    week_events = [e for e in events if _in_week(e.get("at"), start, end)]
    media = Counter(e.get("item") for e in week_events if e.get("feature") in MEDIA_FEATURES and e.get("item"))
    visits: Counter[str] = Counter()
    blocked: Counter[str] = Counter()
    for e in week_events:
        if e.get("feature") != "web" or not e.get("item"):
            continue
        detail = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        visits[e["item"]] += int(detail.get("visits") or 0)
        blocked[e["item"]] += int(detail.get("blocked") or 0)
    calls = [e for e in week_events if e.get("feature") == "calls"]
    texts = [e for e in week_events if e.get("feature") == "sms"]
    bible_days = {str(e.get("at"))[:10] for e in week_events if e.get("feature") == "bible"}

    week_concerns = [c for c in concerns if _in_week(c.get("at"), start, end)]
    by_category: dict[str, dict[str, Any]] = {}
    for c in week_concerns:
        entry = by_category.setdefault(c["category"], {
            "category": c["category"], "label": c.get("label"), "level": c.get("level"), "count": 0, "latest": None,
        })
        entry["count"] += 1
        if entry["latest"] is None or str(c.get("at")) > str(entry["latest"]["at"]):
            entry["latest"] = {"at": c.get("at"), "excerpt": c.get("excerpt"), "feature": c.get("feature")}
    order = {"severe": 0, "high": 1, "medium": 2}
    concern_list = sorted(by_category.values(), key=lambda x: (order.get(x["level"], 3), -x["count"]))

    highlights: list[str] = []
    if bible_days:
        highlights.append(f"Read the Bible on {len(bible_days)} day{'s' if len(bible_days) != 1 else ''}")
    if screen and screen["change_pct"] is not None and abs(screen["change_pct"]) >= 15:
        highlights.append(f"Screen time {'up' if screen['change_pct'] > 0 else 'down'} {abs(screen['change_pct'])}% from last week")
    if sum(blocked.values()):
        highlights.append(f"{sum(blocked.values())} blocked site attempt{'s' if sum(blocked.values()) != 1 else ''}")

    return {
        "week": {"start": start.isoformat(), "end": end.isoformat()},
        "has_data": bool(this_days or week_events or week_concerns),
        "screen": screen,
        "top_apps": [{"package": p, "minutes": m} for p, m in apps.most_common(5) if m > 0],
        "media": [{"title": t, "count": n} for t, n in media.most_common(5)],
        "web": {
            "top": [{"domain": d, "visits": n} for d, n in visits.most_common(5) if n > 0],
            "blocked": [{"domain": d, "count": n} for d, n in blocked.most_common(5) if n > 0],
        },
        "comms": {
            "calls": len(calls),
            "call_contacts": len({e.get("item") for e in calls if e.get("item")}),
            "messages": len(texts),
            "message_contacts": len({e.get("item") for e in texts if e.get("item")}),
        },
        "faith": {"bible_days": len(bible_days)},
        "concerns": concern_list,
        "highlights": highlights,
    }


def summary_line(name: str, recap: dict[str, Any]) -> str:
    """One line for the parents' notification."""
    if not recap.get("has_data"):
        return f"Nothing was reported from {name}'s phone this week."
    parts = []
    screen = recap.get("screen")
    if screen:
        hours, minutes = divmod(screen["daily_avg_min"], 60)
        parts.append(f"about {hours} h {minutes} min a day on the phone" if hours else f"about {minutes} min a day on the phone")
    if recap.get("top_apps"):
        parts.append(f"most in {recap['top_apps'][0]['package'].rsplit('.', 1)[-1].title()}")
    severe = [c for c in recap.get("concerns") or [] if c.get("level") in ("severe", "high")]
    if severe:
        parts.append(f"{len(severe)} concern{'s' if len(severe) != 1 else ''} to look at")
    return f"{name}'s week: " + (", ".join(parts) if parts else "a quiet week") + "."
