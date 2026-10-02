"""Range aggregation for step history.

The UI needs week/month/quarter/year views, and the naive approach -- asking for
365 days of daily buckets and folding them in the browser -- sends a payload
an order of magnitude larger than the handful of bars actually drawn, and gets
worse every time a new range is added.

So aggregation happens here, once, in a way every surface can share. The
buckets are deliberately coarse for long ranges (a year is months, not days):
drawing 365 bars on a phone renders mush and costs more than it explains.

Two things here are about honesty rather than arithmetic:

* **A day with no data is not a day with zero steps.** Gaps are kept distinct
  from zeros, because conflating them is how a broken sensor ends up looking
  like a sedentary week.
* **Baselines come from the user's own history** (median, which ignores the one
  30,000-step hike), never a population average, and the response says plainly
  when there is not enough history to mean anything.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Literal, Optional

Range = Literal["D", "W", "M", "3M", "Y"]

#: How many days of raw history each range needs before its comparison is
#: meaningful. Below this the API still returns data, but says it is thin.
RANGE_DAYS: dict[str, int] = {"D": 1, "W": 7, "M": 30, "3M": 90, "Y": 365}

#: How many buckets to roll each range into. A year becomes months so the bars
#: stay legible; a day stays a day.
RANGE_BUCKETS: dict[str, int] = {"D": 1, "W": 7, "M": 30, "3M": 13, "Y": 12}

#: History below this many days is too thin to compare against a personal
#: baseline. Oura states its data requirements up front rather than quietly
#: drawing a confident-looking average off five days.
BASELINE_MIN_DAYS = 7

RANGE_LABELS = {
    "D": "Today",
    "W": "7 days",
    "M": "30 days",
    "3M": "3 months",
    "Y": "Year",
}


@dataclass
class Bucket:
    """One bar on the chart."""

    label: str
    start: str  # ISO date
    end: str  # ISO date, inclusive
    steps: int
    #: Days in the window that had no reading at all. Kept separate from
    #: `steps` so the UI can say "no data" instead of drawing a zero.
    days_missing: int
    days_recorded: int

    @property
    def complete(self) -> bool:
        return self.days_missing == 0


@dataclass
class Series:
    """Everything the chart and the summary strip need for one range."""

    range: str
    label: str
    buckets: list[Bucket] = field(default_factory=list)
    total: int = 0
    daily_average: int = 0
    best: Optional[Bucket] = None
    #: The user's own median daily steps over the window, or None when there is
    #: not enough history to be worth comparing against.
    baseline: Optional[int] = None
    baseline_days: int = 0
    #: True when the window is too short for the baseline to mean anything.
    thin: bool = False
    #: True when the window has at least one day with no reading.
    gaps: bool = False

    @property
    def enough_for_baseline(self) -> bool:
        return self.baseline is not None and not self.thin


def _parse_days(daily: dict[str, int]) -> dict[date, int]:
    out: dict[date, int] = {}
    for key, val in (daily or {}).items():
        try:
            out[date.fromisoformat(key)] = int(val)
        except (TypeError, ValueError):
            # A malformed bucket must not take the whole history down with it.
            continue
    return out


def _iso(d: date) -> str:
    return d.isoformat()


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, n: int) -> date:
    """Shift by n months, clamping to the last valid day.

    Naive `replace(month=...)` overflows on the 31st, and a chart that silently
    skips a month is worse than one that shortens it.
    """
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, last))


def bucket_windows(today: date, rng: str) -> list[tuple[str, date, date]]:
    """`(label, start, end)` for each bar of `rng`, oldest first, inclusive.

    Pure calendar arithmetic, deliberately separated from the arithmetic of
    *filling* a bar. The date rules here are where the awkward bugs live (month
    ends, Monday alignment), and step and workout history want the same
    calendar while disagreeing completely about what an empty day means -- so
    the calendar is written once and shared.
    """
    span = RANGE_DAYS.get(rng, 7)
    n_buckets = RANGE_BUCKETS.get(rng, 7)
    window_start = today - timedelta(days=span - 1)

    if rng == "D":
        return [(_iso(today), today, today)]

    if rng in ("W", "M"):
        # One bar per day, labelled the way a person would name that day.
        fmt = "%a" if rng == "W" else "%-d %b"
        out = []
        for i in range(span):
            day = window_start + timedelta(days=i)
            out.append((day.strftime(fmt), day, day))
        return out

    if rng == "3M":
        out = []
        cursor = window_start
        # Align to Monday so a week bucket is a real calendar week.
        cursor -= timedelta(days=cursor.weekday())
        while cursor <= today:
            end = min(cursor + timedelta(days=6), today)
            out.append((cursor.strftime("%-d %b"), cursor, end))
            cursor += timedelta(days=7)
        return out[-n_buckets:]

    # Y -> one bucket per calendar month, most recent last.
    out = []
    cursor = _month_start(window_start)
    while cursor <= today:
        next_month = _add_months(cursor, 1)
        last_day = min(next_month - timedelta(days=1), today)
        out.append((cursor.strftime("%b"), cursor, last_day))
        cursor = next_month
    return out[-n_buckets:]


def build_buckets(days_map: dict[date, int], today: date, rng: str) -> list[Bucket]:
    """Roll raw daily history into the bars for `rng`, oldest first.

    Step-specific in one deliberate way: a day with no reading is counted as
    *missing*, not as zero. See metric_history for the opposite convention.
    """
    out = []
    for label, start, end in bucket_windows(today, rng):
        total = 0
        recorded = 0
        d = start
        while d <= end:
            val = days_map.get(d)
            if val is not None:
                total += int(val)
                recorded += 1
            d += timedelta(days=1)
        span_days = (end - start).days + 1
        out.append(Bucket(
            label=label,
            start=_iso(start), end=_iso(end),
            steps=total, days_missing=span_days - recorded, days_recorded=recorded,
        ))
    return out


def personal_baseline(values: Iterable[int]) -> Optional[int]:
    """Median of the user's own history.

    Median rather than mean because one 30,000-step day should not redefine
    what "normal" means for the other 89.
    """
    clean = [int(v) for v in values if v is not None]
    if not clean:
        return None
    return int(round(statistics.median(clean)))


def build_series(daily: dict[str, int], today: date, rng: str, goal: Optional[int] = None) -> Series:
    """Assemble the chart buckets plus the summary numbers for one range."""
    days_map = _parse_days(daily)
    span = RANGE_DAYS.get(rng, 7)
    window_start = today - timedelta(days=span - 1)
    in_window = {d: v for d, v in days_map.items() if window_start <= d <= today}

    buckets = build_buckets(days_map, today, rng)

    recorded_days = len(in_window)
    total = sum(in_window.values())
    average = int(round(total / recorded_days)) if recorded_days else 0

    # Baseline is computed from every day we hold, not just the visible window:
    # a 7-day view should still be able to say how you usually do.
    all_values = list(days_map.values())
    baseline = personal_baseline(all_values)
    thin = len(all_values) < BASELINE_MIN_DAYS

    # "Best day" must mean a day we actually measured. max() over buckets that
    # are all zero would otherwise name an arbitrary day -- and a day with no
    # reading at all is not a slow day, it is an absence. Same rule as
    # metric_history: a window with no recorded steps has no best day.
    recorded = [b for b in buckets if b.days_recorded > 0]
    best = max(recorded, key=lambda b: b.steps) if recorded else None
    gaps = any(b.days_missing > 0 for b in buckets)

    return Series(
        range=rng,
        label=RANGE_LABELS.get(rng, rng),
        buckets=buckets,
        total=total,
        daily_average=average,
        best=best,
        baseline=baseline if not thin else None,
        baseline_days=len(all_values),
        thin=thin,
        gaps=gaps,
    )


def to_payload(series: Series, goal: Optional[int] = None) -> dict:
    """Wire format. Explicit about absence so the UI can say why."""
    return {
        "range": series.range,
        "label": series.label,
        "buckets": [
            {
                "label": b.label,
                "start": b.start,
                "end": b.end,
                "steps": b.steps,
                "days_missing": b.days_missing,
                "days_recorded": b.days_recorded,
                "complete": b.complete,
            }
            for b in series.buckets
        ],
        "total": series.total,
        "daily_average": series.daily_average,
        "days_recorded": series.baseline_days,
        "goal": goal,
        "baseline": series.baseline,
        "baseline_min_days": BASELINE_MIN_DAYS,
        # Says "not enough history yet" rather than letting the UI guess.
        "thin": series.thin,
        "has_gaps": series.gaps,
        "best": (
            {"label": series.best.label, "steps": series.best.steps}
            if series.best
            else None
        ),
    }
