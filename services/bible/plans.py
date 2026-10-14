# services/bible/plans.py
"""Reading plans, generated from the canonical book table.

A plan is data (``games/plans.json``): a list of *tracks*, each a run of books
spread evenly over the plan's days. "New Testament in 90 days" is one track;
"Psalms and Proverbs" is two read side by side. The day-by-day readings are
computed from the chapter counts in ``books.py``, so a plan can never name a
chapter that does not exist and never skips one.

Progress is a set of completed day numbers. Where the reader *should* be is
derived from the start date, so being behind is a number the app can show and
"catch me up" is simply moving the start date so the next unread day is today.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from .books import BOOKS

_PLANS_PATH = Path(__file__).parent / "games" / "plans.json"
_ORDER = [b["osis"] for b in BOOKS]
_BY_OSIS = {b["osis"]: b for b in BOOKS}


class PlanError(ValueError):
    """An unknown plan or day."""


@lru_cache(maxsize=1)
def _catalogue() -> tuple[dict, ...]:
    plans = json.loads(_PLANS_PATH.read_text(encoding="utf-8"))["plans"]
    return tuple(plans)


def catalogue() -> list[dict]:
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "blurb": p["blurb"],
            "days": p["days"],
            "category": p.get("category", ""),
            "chapters": sum(len(_chapters(t)) for t in p["tracks"]),
        }
        for p in _catalogue()
    ]


def find(plan_id: str) -> dict:
    for plan in _catalogue():
        if plan["id"] == plan_id:
            return plan
    raise PlanError(f"There is no reading plan called {plan_id!r}.")


def _chapters(track: dict) -> list[tuple[str, int]]:
    """Every chapter a track covers, in canonical order."""
    start, end = _ORDER.index(track["from"]), _ORDER.index(track["to"])
    out = []
    for osis in _ORDER[start : end + 1]:
        out.extend((osis, c) for c in range(1, _BY_OSIS[osis]["chapters"] + 1))
    return out


def _spread(chapters: list[tuple[str, int]], days: int) -> list[list[tuple[str, int]]]:
    """Split chapters over days as evenly as possible, never splitting a chapter.

    Day ``d`` reads chapters ``floor(d*n/days)`` up to ``floor((d+1)*n/days)``,
    so the load differs by at most one chapter between days and every chapter
    is read exactly once. A track shorter than the plan (the New Testament
    beside the Old over a year) leaves some days without a reading on that
    track rather than starting it over.
    """
    n = len(chapters)
    return [chapters[math.floor(d * n / days) : math.floor((d + 1) * n / days)] for d in range(days)]


def _label(run: list[tuple[str, int]]) -> list[dict]:
    """Group a day's chapters into readable references: "Genesis 1-3"."""
    groups: list[dict] = []
    for osis, chapter in run:
        last = groups[-1] if groups else None
        if last and last["osis"] == osis and last["end"] == chapter - 1:
            last["end"] = chapter
        else:
            groups.append({"osis": osis, "start": chapter, "end": chapter})
    out = []
    for g in groups:
        name = _BY_OSIS[g["osis"]]["name"]
        ref = f"{name} {g['start']}" if g["start"] == g["end"] else f"{name} {g['start']}-{g['end']}"
        out.append({"ref": ref, "osis": g["osis"], "chapter_start": g["start"], "chapter_end": g["end"]})
    return out


@lru_cache(maxsize=64)
def schedule(plan_id: str) -> tuple[tuple[dict, ...], ...]:
    """Each day's readings, 1-based day ``d`` at index ``d - 1``."""
    plan = find(plan_id)
    per_track = [_spread(_chapters(t), plan["days"]) for t in plan["tracks"]]
    days = []
    for d in range(plan["days"]):
        readings: list[dict] = []
        for track in per_track:
            readings.extend(_label(track[d]))
        days.append(tuple(readings))
    return tuple(days)


def day_readings(plan_id: str, day: int) -> list[dict]:
    plan = find(plan_id)
    if not 1 <= day <= plan["days"]:
        raise PlanError(f"{plan['name']} has days 1 to {plan['days']}; there is no day {day}.")
    return list(schedule(plan_id)[day - 1])


def progress(plan_id: str, started_on: str, completed: list[int], today: date | None = None) -> dict:
    """Where the reader is in a plan, and how far behind the calendar."""
    plan = find(plan_id)
    total = plan["days"]
    today = today or date.today()
    done = sorted(set(d for d in completed if 1 <= d <= total))
    calendar_day = min(total, max(1, (today - date.fromisoformat(started_on)).days + 1))
    next_day = next((d for d in range(1, total + 1) if d not in done), None)
    # Days before today still unread: today's own reading is not "behind" yet.
    behind = sum(1 for d in range(1, calendar_day) if d not in done)
    # Today's reading is today's own day if it is still open, else the first
    # open day: a reader who is ahead keeps going, one who is behind catches up.
    current = calendar_day if calendar_day not in done else next_day
    return {
        "days": total,
        "completed": len(done),
        "completed_days": done,
        "percent": round(100 * len(done) / total),
        "calendar_day": calendar_day,
        "current_day": current,
        "next_day": next_day,
        "behind": behind,
        "finished": next_day is None,
    }


def catch_up_start(next_day: int, today: date | None = None) -> str:
    """The start date that makes ``next_day`` fall on today."""
    today = today or date.today()
    return (today - timedelta(days=next_day - 1)).isoformat()
