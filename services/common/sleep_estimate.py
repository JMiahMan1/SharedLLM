"""Estimate a night's sleep from when a person's devices were in use.

Each phone or tablet assigned to the person reports the stretches its screen
was on between 18:00 the evening before and noon on the morning the person
woke. Joined across devices, the longest stretch with every screen off is the
night. A brief check in the middle of it (the phone lit for a few minutes at
3 a.m.) is counted as time awake inside the night, not the end of it.

This is an estimate and is labelled one: it is offered to the person to
correct before it is saved, and a hand log or a wearable outranks it. With too
little data (one block of use, or no long quiet stretch) it returns ``None``
rather than a guess.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

WINDOW_START = time(18, 0)  # the evening before
WINDOW_END = time(12, 0)  # the morning of the night
MERGE_MS = 60 * 1000  # screen-on stretches this close together are one block of use
CORE_MIN_MS = 2 * 60 * 60 * 1000  # the longest quiet stretch must be at least this
BRIEF_MS = 10 * 60 * 1000  # a block of use this short inside the night is a wake-up
QUIET_AFTER_BRIEF_MS = 30 * 60 * 1000  # ...when quiet follows it again for this long
SLEEP_MIN_MS = 3 * 60 * 60 * 1000  # a "night" shorter than this is not reported


def night_window(night: str, tz_name: str) -> tuple[int, int]:
    """Epoch millis of 18:00 the day before ``night`` and noon on ``night``."""
    tz = ZoneInfo(tz_name)
    day = date.fromisoformat(night)
    start = datetime.combine(day - timedelta(days=1), WINDOW_START, tzinfo=tz)
    end = datetime.combine(day, WINDOW_END, tzinfo=tz)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _blocks(reports: Iterable[dict[str, Any]], lo: int, hi: int) -> list[list[int]]:
    spans: list[tuple[int, int]] = []
    for report in reports:
        for pair in report.get("intervals") or []:
            try:
                start, end = int(pair[0]), int(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
            start, end = max(start, lo), min(end, hi)
            if end > start:
                spans.append((start, end))
            elif end == start and lo <= start <= hi:
                spans.append((start, start + 1))
    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] <= MERGE_MS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _iso(epoch_ms: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz).isoformat(timespec="minutes")


def estimate_night(reports: list[dict[str, Any]], night: str, tz_name: str) -> dict[str, Any] | None:
    """The estimated sleep for the night ending on ``night`` (YYYY-MM-DD), or ``None``.

    ``reports`` holds one entry per device: ``{"intervals": [[start_ms, end_ms], ...]}``.
    """
    lo, hi = night_window(night, tz_name)
    blocks = _blocks(reports, lo, hi)
    if len(blocks) < 2:
        return None

    # The longest quiet stretch between two blocks of use is the core of the night.
    gaps = [blocks[i + 1][0] - blocks[i][1] for i in range(len(blocks) - 1)]
    core = max(range(len(gaps)), key=lambda i: gaps[i])
    if gaps[core] < CORE_MIN_MS:
        return None

    # The night runs from the end of block `first` to the start of block `last`.
    first, last = core, core + 1
    while (
        first > 0
        and blocks[first][1] - blocks[first][0] <= BRIEF_MS
        and blocks[first][0] - blocks[first - 1][1] >= QUIET_AFTER_BRIEF_MS
    ):
        first -= 1
    while (
        last < len(blocks) - 1
        and blocks[last][1] - blocks[last][0] <= BRIEF_MS
        and blocks[last + 1][0] - blocks[last][1] >= QUIET_AFTER_BRIEF_MS
    ):
        last += 1

    bedtime, wake = blocks[first][1], blocks[last][0]
    wakeups = blocks[first + 1:last]
    awake_ms = sum(end - start for start, end in wakeups)
    asleep_ms = wake - bedtime - awake_ms
    if asleep_ms < SLEEP_MIN_MS:
        return None

    tz = ZoneInfo(tz_name)
    return {
        "night": night,
        "bedtime": _iso(bedtime, tz),
        "wake_time": _iso(wake, tz),
        "duration_minutes": round(asleep_ms / 60000),
        "wakeups": len(wakeups),
        "awake_minutes": round(awake_ms / 60000),
        "devices": sum(1 for report in reports if report.get("intervals")),
    }
