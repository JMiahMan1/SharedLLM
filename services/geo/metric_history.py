"""Range aggregation for event-based metrics: workouts, and distances.

`step_history` handles a metric that arrives as a *daily reading* from a sensor,
where "no reading" and "a reading of zero" are different facts worth telling
apart. Workouts are the opposite: a workout is an event someone recorded, so a
day with none is a real zero, not a gap. Reusing the step bucketing here would
draw a row of "no data" across a quiet month and make an uneventful fortnight
look like a broken sensor.

So this module shares the calendar arithmetic (via `bucket_windows`) and
deliberately does not share the empty-day semantics.

One metric is deliberately absent. **Calories.** `calories_burned` is written as
a hardcoded ``None`` when a workout starts and nothing ever fills it in, and
geo records no daily calorie totals at all. Rather than ship a card with a
confident 0 -- or invent a burn estimate from body weight -- `available_metrics`
reports it as untracked so the UI can say so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from services.geo.step_history import RANGE_DAYS, RANGE_LABELS, bucket_windows

Range = Literal["D", "W", "M", "3M", "Y"]

#: Metrics the API will answer for. Adding one here without a source behind it
#: is how a dashboard ends up showing a confident zero for something nobody
#: measures, so the list is explicit and `unavailable_metrics` names the rest.
AVAILABLE_METRICS: dict[str, dict] = {
    "workouts": {"label": "Workouts", "unit": "count", "format": "count"},
    "workout_minutes": {"label": "Workout time", "unit": "minutes", "format": "duration"},
    "workout_miles": {"label": "Workout distance", "unit": "miles", "format": "distance"},
    "drive_miles": {"label": "Driving distance", "unit": "miles", "format": "distance"},
}

#: Tracked-but-unimplemented / not-recorded metrics, with the reason, so the UI
#: can explain the absence instead of showing an empty card.
UNAVAILABLE_METRICS: dict[str, str] = {
    "calories": "Never recorded: the calories field is written as None and nothing fills it in.",
    "active_minutes": "Never recorded: no minute-by-minute activity intensity is stored.",
    "sleep": "Not recorded by any current source.",
    "heart_rate": "Not recorded by any current source.",
    "weight": "Not recorded by any current source.",
}


@dataclass
class MetricBucket:
    label: str
    start: str  # ISO date
    end: str  # ISO date, inclusive
    value: float
    #: Days in the window on which something actually happened. Not a gap count
    #: -- an event metric has no "missing" days, only quiet ones.
    active_days: int

    @property
    def quiet(self) -> bool:
        return self.value == 0


@dataclass
class MetricSeries:
    range: str
    metric: str
    label: str
    unit: str
    buckets: list[MetricBucket] = field(default_factory=list)
    total: float = 0
    #: Average per *active* day, not per calendar day. Averaging over 30 days
    #: to say someone averages 0.4 workouts is not a useful thing to say.
    per_active_day: float = 0
    active_days: int = 0
    best: Optional[MetricBucket] = None
    #: True when the window contains no events at all -- "no workouts recorded"
    #: is different from "workouts, averaging one a month".
    empty: bool = False


def _day_of(ts: float, tz) -> str:
    return datetime.fromtimestamp(float(ts), tz).strftime("%Y-%m-%d")


def daily_from_workouts(
    workouts: list[dict], tz, *, fields: Optional[dict[str, str]] = None
) -> dict[str, dict[str, float]]:
    """Fold a list of workout dicts into per-day totals, keyed by metric.

    ``fields`` maps metric name -> workout key, e.g.
    ``{"workout_minutes": "duration_seconds", "workout_miles": "distance_miles"}``.
    A workout with no timestamp is skipped rather than filed under today: an
    unplaceable event would otherwise inflate whichever day happened to be
    queried.
    """
    fields = fields or {}
    out: dict[str, dict[str, float]] = {name: {} for name in fields}
    for w in workouts or []:
        ts = w.get("start_time")
        if not ts:
            continue
        day = _day_of(ts, tz)
        for metric, key in fields.items():
            raw = w.get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            out[metric][day] = out[metric].get(day, 0.0) + value
    return out


def daily_from_trips(trips: list[dict], tz, key: str = "distance_miles") -> dict[str, float]:
    """Per-day driving distance. Same timestamp rule as workouts."""
    out: dict[str, float] = {}
    for t in trips or []:
        ts = t.get("start_time")
        if not ts:
            continue
        raw = t.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        day = _day_of(ts, tz)
        out[day] = out.get(day, 0.0) + value
    return out


def count_workouts_by_day(workouts: list[dict], tz) -> dict[str, float]:
    counts: dict[str, float] = {}
    for w in workouts or []:
        ts = w.get("start_time")
        if not ts:
            continue
        day = _day_of(ts, tz)
        counts[day] = counts.get(day, 0.0) + 1
    return counts


def build_metric_series(
    daily: dict[str, float], today: date, rng: str, metric: str
) -> MetricSeries:
    """Buckets and summary for one event metric over one range."""
    spec = AVAILABLE_METRICS.get(metric, {"label": metric, "unit": ""})
    parsed: dict[date, float] = {}
    for key, val in (daily or {}).items():
        try:
            parsed[date.fromisoformat(key)] = float(val)
        except (TypeError, ValueError):
            # One malformed day must not take the series down.
            continue

    span = RANGE_DAYS.get(rng, 7)
    window_start = today - timedelta(days=span - 1)
    in_window = {d: v for d, v in parsed.items() if window_start <= d <= today}

    buckets: list[MetricBucket] = []
    for label, start, end in bucket_windows(today, rng):
        total = 0.0
        active = 0
        d = start
        while d <= end:
            val = parsed.get(d)
            if val:
                total += val
                active += 1
            d += timedelta(days=1)
        buckets.append(
            MetricBucket(
                label=label,
                start=start.isoformat(),
                end=end.isoformat(),
                value=total,
                active_days=active,
            )
        )

    # max() over all-zero buckets would name an arbitrary quiet day as "best",
    # so a window with no events reports no best at all.
    best = max(buckets, key=lambda b: b.value) if buckets else None
    if best is not None and best.value <= 0:
        best = None
    active_days = len(in_window)
    total = sum(in_window.values())
    is_count = spec.get("format") == "count"

    return MetricSeries(
        range=rng,
        metric=metric,
        label=spec.get("label", metric),
        unit=spec.get("unit", ""),
        buckets=buckets,
        total=total,
        per_active_day=round(total / active_days, 1) if active_days else 0.0,
        active_days=active_days,
        best=best,
        empty=active_days == 0,
    )


def to_payload(series: MetricSeries) -> dict:
    spec = AVAILABLE_METRICS.get(series.metric, {})
    return {
        "metric": series.metric,
        "label": series.label,
        "unit": series.unit,
        "format": spec.get("format", "count"),
        "range": series.range,
        "range_label": RANGE_LABELS.get(series.range, series.range),
        "buckets": [
            {
                "label": b.label,
                "start": b.start,
                "end": b.end,
                "value": round(b.value, 2),
                "active_days": b.active_days,
                "quiet": b.quiet,
            }
            for b in series.buckets
        ],
        "total": round(series.total, 2),
        "per_active_day": series.per_active_day,
        "active_days": series.active_days,
        # Distinct from "recorded a few": says the window is empty.
        "empty": series.empty,
        "best": (
            {"label": series.best.label, "value": round(series.best.value, 2)}
            if series.best
            else None
        ),
    }


def metric_catalog() -> dict:
    """What the panel can and cannot show, with reasons for the gaps."""
    return {
        "available": list(AVAILABLE_METRICS),
        "unavailable": UNAVAILABLE_METRICS,
    }
