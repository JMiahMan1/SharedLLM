"""Garden achievement definitions and evaluation (Phase 2).

Loads ``services/garden/achievements.json`` and evaluates each rule against
a member's care logs and plant counts. Returns earned achievements and total
points for inclusion in ``GET /streaks``.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

from services.garden import clock
from services.garden.models import CareLog

log = logging.getLogger("garden.achievements")

_DEFINITIONS_PATH = Path(__file__).with_name("achievements.json")


def _load_definitions() -> list[dict]:
    try:
        with open(_DEFINITIONS_PATH) as f:
            data = json.load(f)
        return data.get("achievements", [])
    except Exception as e:
        log.warning("Could not load garden achievement definitions: %s", e)
        return []


def _rule_matches(rule: dict, ctx: dict) -> bool:
    """Evaluate one achievement rule against the context."""
    rtype = rule["type"]
    value = rule["value"]
    if rtype == "plants_total":
        return ctx["plants_total"] >= value
    if rtype == "water_streak":
        return ctx["water_streak"] >= value
    if rtype == "harvests_total":
        return ctx["harvests_total"] >= value
    if rtype == "snoozes_total":
        return ctx["snoozes_total"] >= value
    if rtype == "room_water_total":
        return ctx["room_water_total"] >= value
    if rtype == "pantry_batches_total":
        return ctx["pantry_batches_total"] >= value
    return False


def longest_run(days: set) -> int:
    """The longest run of consecutive days in ``days`` (0 for none)."""
    best = 0
    for day in days:
        if day - timedelta(days=1) in days:
            continue  # not the start of a run
        length = 1
        while day + timedelta(days=length) in days:
            length += 1
        best = max(best, length)
    return best


def evaluate(
    session: Session,
    user: str,
    *,
    plants_total: int = 0,
    harvests_total: int = 0,
    snoozes_total: int = 0,
    room_water_total: int = 0,
    pantry_batches_total: int = 0,
) -> list[dict]:
    """Evaluate all achievements for a user and return earned ones with points."""
    definitions = _load_definitions()
    if not definitions:
        return {"earned": [], "points": 0}

    # Compute water streak from care logs.
    logs = session.exec(
        select(CareLog)
        .where(CareLog.performed_by == user, CareLog.action == "water")
        .order_by(CareLog.created_at.desc())
    ).all()
    logged_dates: set = set()
    for log in logs:
        try:
            logged_dates.add(clock.local_date(log.created_at))
        except (ValueError, TypeError):
            continue
    # A streak award is earned by the longest run of household days ever
    # watered, so "Week of Watering" does not vanish after one missed day.
    water_streak = longest_run(logged_dates)

    ctx = {
        "plants_total": plants_total,
        "water_streak": water_streak,
        "harvests_total": harvests_total,
        "snoozes_total": snoozes_total,
        "room_water_total": room_water_total,
        "pantry_batches_total": pantry_batches_total,
    }

    earned = []
    total_points = 0
    for ach in definitions:
        if _rule_matches(ach["rule"], ctx):
            earned.append(
                {
                    "id": ach["id"],
                    "name": ach["name"],
                    "description": ach.get("description", ""),
                    "points": ach.get("points", 0),
                }
            )
            total_points += ach.get("points", 0)

    return {"earned": earned, "points": total_points}
