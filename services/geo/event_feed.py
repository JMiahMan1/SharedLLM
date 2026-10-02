"""A personal health event timeline, assembled from the events geo already holds.

Samsung Health's pattern: a dated, reverse-chronological list of *things that
happened*, rather than another chart. Useful because it answers "what did I
actually do last week", which no aggregate can.

Pure stdlib and no Redis, so the ordering, deduping and honesty rules are
testable without a server -- the same split `step_history` and
`metric_history` use.

Honesty rules encoded here, because each one has bitten us before:

* An event with no usable timestamp is **dropped, not dated as "now"**. An
  undated event would sort to the top of the timeline and read as the most
  recent thing you did, which is a lie of ordering.
* Events are sorted by timestamp, not by whatever order Redis returned.
* Same event, same timestamp, from two sources is **deduplicated** by kind
  and time, so a workout that is also a trip does not appear twice.
* `days_ago` is computed against an injected `now`, never the wall clock, so
  the output is reproducible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

#: Ordered by how much a person cares about it, and used to group the timeline
#: into day sections. Keep in sync with the UI's grouping order.
EVENT_KINDS = (
    "workout",
    "achievement",
    "goal",
    "drive",
    "personal_best",
)

#: Human label per kind. The UI renders this; keeping it server-side means the
#: timeline reads the same wherever it is shown.
KIND_LABELS = {
    "workout": "Workout",
    "achievement": "Achievement",
    "goal": "Goal",
    "drive": "Drive",
    "personal_best": "Personal best",
}

#: Emoji is a presentation choice, so it lives here rather than in the client,
#: and only for the timeline. Metrics never use one.
KIND_ICONS = {
    "workout": "🏃",
    "achievement": "🏅",
    "goal": "🎯",
    "drive": "🚗",
    "personal_best": "⭐",
}

#: Which kinds belong on which page's timeline.
#:
#: A drive is a movement, not a fitness event. Showing one under Health made the
#: page answer a question the reader did not ask and buried the workouts it
#: should have been about, so the two pages now have separate views of the same
#: events rather than one blended list. Wander's existing Total Distance tile
#: already treats driving as its own quantity, which is the same distinction.
TIMELINE_DOMAINS: dict[str, frozenset] = {
    "health": frozenset({"workout", "achievement", "goal", "personal_best"}),
    "wander": frozenset({"drive"}),
}


def events_for_domain(events: Iterable[Event], domain: str) -> list[Event]:
    """Narrow events to one page's domain.

    Raises ValueError for an unknown domain rather than defaulting, so a typo
    cannot quietly return the wrong page's content.
    """
    if domain not in TIMELINE_DOMAINS:
        raise ValueError(
            f"domain must be one of {', '.join(sorted(TIMELINE_DOMAINS))}"
        )
    allowed = TIMELINE_DOMAINS[domain]
    return [e for e in events if e.kind in allowed]

DAY_SECONDS = 86400.0


@dataclass
class Event:
    kind: str
    """One of EVENT_KINDS."""

    at: float
    """Epoch seconds. Always a real observed time, never `now`."""

    title: str
    detail: str = ""
    #: Small scalars the client may want for filtering or a link. Never free
    #: prose and never content: this timeline carries event metadata, not
    #: anything the user typed.
    meta: dict = field(default_factory=dict)
    #: Whole days between the event and "now" as supplied by the caller. Set by
    #: `build_timeline` so the client cannot re-derive it against a different
    #: clock and get a different answer.
    days_ago: int = 0
    #: Time of day ("8:05 AM") in the zone the server bucketed the day with.
    #: Set by `build_timeline`; "" for an event that was never placed.
    time_label: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "at": self.at,
            "title": self.title,
            "detail": self.detail,
            "meta": self.meta,
            "days_ago": self.days_ago,
            # The kind's name, NOT a time. Sent so the client never invents
            # its own vocabulary for a kind the server may add later.
            "label": KIND_LABELS.get(self.kind, self.kind),
            # Time of day as the server computed it, in the same zone it used
            # to bucket the day. The client must not format `at` itself: its
            # own timezone could file the event on a different day than the
            # group it is listed under, which is the bug the steps timezone
            # fix was about.
            "time_label": self.time_label,
            "icon": KIND_ICONS.get(self.kind, "•"),
        }


@dataclass
class DayGroup:
    """Events on one calendar day, newest day first."""

    day: str
    """ISO date."""

    events: list[Event] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "relative": self.relative_label,
            "events": [e.to_dict() for e in self.events],
        }

    @property
    def relative_label(self) -> str:
        return self._relative

    _relative: str = "Today"


def _finite(value: Any) -> Optional[float]:
    """Coerce to a finite float, or None. Rejects NaN, inf and junk."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _epoch_to_day(ts: float, tz: timezone) -> Optional[str]:
    """Calendar day for an epoch, or None if it is out of range.

    None rather than a guess: an event we cannot place on a day has no place in
    a day-grouped timeline, and inventing one would misfile it.
    """
    try:
        return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return None


def _epoch_to_time_label(ts: float, tz: timezone) -> str:
    """Time of day, 12-hour with no leading zero ("8:05 AM", "12:30 PM")."""
    try:
        return datetime.fromtimestamp(ts, tz).strftime("%-I:%M %p")
    except (ValueError, OSError, OverflowError):
        # An out-of-range epoch must not take the whole timeline down; the day
        # label still renders, so the row stays readable without a time.
        return ""


def relative_day_label(day: str, now: float, tz: timezone) -> str:
    """`Today` / `Yesterday` / `3 days ago` / the date itself.

    Older than a week it falls back to the date, because "12 days ago" stops
    being useful and an absolute date is more honest about how far back it is.
    """
    today = _epoch_to_day(now, tz)
    try:
        target = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=tz)
    except ValueError:
        return day
    delta = (datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=tz) - target).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if 1 < delta < 7:
        return f"{delta} days ago"
    if delta < 0:
        # A future-dated event means a clock skew somewhere upstream. Naming it
        # plainly beats rendering it under "Today".
        return "Future-dated"
    return target.strftime("%b %-d") if hasattr(target, "strftime") else target.strftime("%b %d")


def events_from_workouts(workouts: Iterable[dict], tz: timezone) -> list[Event]:
    """One event per recorded workout."""
    out: list[Event] = []
    for w in workouts or []:
        at = _finite(w.get("start_time"))
        if at is None:
            # Undated: dropping is the honest option, because dating it now
            # would float it to the top of the timeline.
            continue
        kind = str(w.get("activity_type") or "").strip()
        label = WORKOUT_LABELS.get(kind, kind.replace("_", " ").title() or "Workout")
        bits = []
        dur = _finite(w.get("duration_seconds"))
        if dur and dur > 0:
            bits.append(_format_duration(dur))
        dist = _finite(w.get("distance_miles"))
        if dist and dist > 0:
            bits.append(f"{dist:.1f} mi")
        out.append(
            Event(
                kind="workout",
                at=at,
                title=label,
                detail=" · ".join(bits),
                meta={
                    k: w.get(k)
                    for k in ("workout_id", "activity_type", "duration_seconds", "distance_miles")
                    if w.get(k) is not None
                },
            )
        )
    return out


WORKOUT_LABELS = {
    "walking": "Walk",
    "running": "Run",
    "cycling": "Bike Ride",
    "mountain_biking": "Mountain Bike",
    "dirtbiking": "Dirtbike Ride",
    "horseback_riding": "Horseback Ride",
}


def events_from_trips(trips: Iterable[dict], tz: timezone) -> list[Event]:
    """One event per drive.

    Only ``activity_type == "driving"`` counts: a trip record is also used for
    other activity, and a drive is the only one that belongs on a health
    timeline under its own name.
    """
    out: list[Event] = []
    for t in trips or []:
        if str(t.get("activity_type") or "driving") != "driving":
            continue
        at = _finite(t.get("start_time"))
        if at is None:
            continue
        bits = []
        dur = _finite(t.get("duration_seconds"))
        if dur and dur > 0:
            bits.append(_format_duration(dur))
        dist = _finite(t.get("distance_miles"))
        if dist and dist > 0:
            bits.append(f"{dist:.1f} mi")
        out.append(
            Event(
                kind="drive",
                at=at,
                title="Drive",
                detail=" · ".join(bits),
                meta={
                    k: t.get(k) for k in ("trip_id", "distance_miles", "duration_seconds") if t.get(k) is not None
                },
            )
        )
    return out


def events_from_achievements(
    ledger: dict[str, str], definitions: dict[str, Any]
) -> list[Event]:
    """One event per banked achievement.

    ``ledger`` is the ``geo:points:{user}`` hash of ``{achievement_id: earned_on}``
    where the value is a date string, not an epoch -- so it is parsed to the
    *end* of that day so an achievement earned today sorts after a morning
    workout rather than before it.
    """
    out: list[Event] = []
    for ach_id, earned_on in (ledger or {}).items():
        at = _parse_earned_on(earned_on, definitions.get(ach_id))
        if at is None:
            continue
        meta = definitions.get(ach_id)
        name = getattr(meta, "name", None) or (meta or {}).get("name") if meta else None
        out.append(
            Event(
                kind="achievement",
                at=at,
                title=f"Earned {name}" if name else "Achievement earned",
                detail=getattr(meta, "description", None) or "",
                meta={"achievement_id": ach_id},
            )
        )
    return out


def _parse_earned_on(earned_on: Any, definition: Any = None) -> Optional[float]:
    """Turn an ``earned_on`` value into epoch seconds at end of day.

    Accepts a date string, a datetime string, or a raw epoch. Anything else is
    dropped: a badge with an unparseable date cannot be placed in a timeline,
    and guessing would put it at the wrong end of the list.
    """
    if earned_on is None or isinstance(earned_on, bool):
        return None
    if isinstance(earned_on, (int, float)):
        val = _finite(earned_on)
        if val is None:
            return None
        return val
    text = str(earned_on).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if fmt == "%Y-%m-%d":
            # End of day, so today's badge lands after a morning workout.
            parsed = parsed + timedelta(hours=23, minutes=59, seconds=59)
        return parsed.timestamp()
    return None


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    mins = total // 60
    if mins < 60:
        return f"{mins} min"
    hours, rem = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h {rem}m" if rem else f"{hours}h"
    days, rem_h = divmod(hours, 24)
    return f"{days}d {rem_h}h"


def dedupe(events: Iterable[Event]) -> list[Event]:
    """Collapse the same event seen twice.

    A drive recorded in both the trips store and the workouts store is one
    event, not two. Keyed on kind plus the timestamp to the second, which is
    coarse enough to catch the duplicate and fine enough not to merge two
    genuine back-to-back workouts of the same type.
    """
    seen: set[tuple[str, int]] = set()
    out: list[Event] = []
    for e in events:
        key = (e.kind, int(e.at))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def build_timeline(
    events: Iterable[Event], now: float, tz: timezone, limit: int = 60
) -> list[DayGroup]:
    """Sort newest first, group by calendar day, cap the total.

    ``days_ago`` is attached to every event so the client does not have to
    re-derive it against a possibly-different clock.
    """
    ordered = sorted(events, key=lambda e: e.at, reverse=True)
    groups: list[DayGroup] = []
    seen_days: dict[str, DayGroup] = {}
    shown = 0
    for e in ordered:
        if shown >= limit:
            break
        day = _epoch_to_day(e.at, tz)
        if day is None:
            # Out of range: cannot be grouped, so it is dropped rather than
            # filed under a made-up day. Consistent with the rule that an event
            # with no usable timestamp is never dated "now".
            continue
        group = seen_days.get(day)
        if group is None:
            group = DayGroup(day=day, _relative=relative_day_label(day, now, tz))
            seen_days[day] = group
            groups.append(group)
        e.days_ago = max(0, int((now - e.at) // DAY_SECONDS))
        e.time_label = _epoch_to_time_label(e.at, tz)
        group.events.append(e)
        shown += 1
    return groups


def timeline_payload(groups: list[DayGroup], now: float) -> dict:
    """Response shape.

    ``empty`` is explicit so the client can say "nothing recorded yet" instead
    of rendering an empty list that looks broken, and ``total_events`` counts
    what is shown so a truncated timeline can say so honestly.

    ``day_count`` is the number of day groups returned, deliberately not named
    ``days`` -- the caller merges this alongside the *requested* window, and two
    different meanings behind one key silently loses the window the client asked
    for.
    """
    total = sum(len(g.events) for g in groups)
    return {
        "groups": [g.to_dict() for g in groups],
        "total_events": total,
        "empty": total == 0,
        "day_count": len(groups),
    }
