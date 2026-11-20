"""Garden achievement definitions and evaluation (Phase 2).

Loads ``services/garden/achievements.json`` and evaluates each rule against
a member's care logs and plant counts. Returns earned achievements and total
points for inclusion in ``GET /streaks``.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select

from services.garden.models import CareLog, CareRule, Plant

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
            logged_dates.add(datetime.fromisoformat(log.created_at).date())
        except (ValueError, TypeError):
            continue
    water_streak = 0
    d = datetime.now(UTC).date()
    while d in logged_dates:
        water_streak += 1
        d -= timedelta(days=1)

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
