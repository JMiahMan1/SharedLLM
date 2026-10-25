"""Weekly family challenges: what counts, and the leaderboard.

A challenge runs Monday to Sunday and asks for a number of *days* on which a
member did one measurable thing. Only kinds the health store can measure from
what people actually logged are offered:

* ``water_days`` -- days the water logged reached the member's water goal;
* ``sleep_nights`` -- nights the logged sleep reached the member's sleep need;
* ``cook_nights`` -- days a dinner was logged from a family recipe;
* ``step_days`` -- days the member's steps reached their own daily step goal.

Steps live in geo, not in the health store, so ``step_days`` is in
``GEO_KINDS``: the health service lists the members and the gateway reads each
member's daily steps and step goal from geo (the same read the active-minutes
week uses) and scores them with these same rules. A day geo has no steps for
is missing, not zero.

Veg servings are not a kind: a logged food keeps no link to a food category
(a USDA seed food is saved by name and nutrients only, and OpenFoodFacts,
recipes and hand-typed entries have no category at all), so a veg count would
have to guess from food names.

A day with nothing logged is simply not counted -- never estimated -- and no
kind is about calories or weight, so the same challenge is fine for a child.
"""
from __future__ import annotations

from datetime import date, timedelta

from services.common.streaks import week_start

__all__ = [
    "GEO_KINDS",
    "KINDS",
    "STORE_KINDS",
    "day_counts",
    "leaderboard",
    "progress",
    "step_progress",
    "week_days",
    "week_start",
]

KINDS: dict[str, dict[str, str]] = {
    "water_days": {
        "title": "Water goal days",
        "unit": "days",
        "detail": "Days your logged water reaches your water goal.",
    },
    "sleep_nights": {
        "title": "Sleep goal nights",
        "unit": "nights",
        "detail": "Nights your logged sleep reaches your sleep need.",
    },
    "cook_nights": {
        "title": "Cook nights",
        "unit": "nights",
        "detail": "Days a dinner is logged from a family recipe.",
    },
    "step_days": {
        "title": "Step goal days",
        "unit": "days",
        "detail": "Days your steps reach your daily step goal.",
    },
}

# Kinds measured from another service (geo owns steps); the gateway fills them in.
GEO_KINDS: frozenset[str] = frozenset({"step_days"})
# Kinds the health store measures on its own (personal weekly goals use only these).
STORE_KINDS: dict[str, dict[str, str]] = {kind: meta for kind, meta in KINDS.items() if kind not in GEO_KINDS}


def week_days(start: date) -> list[str]:
    return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]


def day_counts(
    kind: str,
    day: dict,
    *,
    water_goal_ml: float | None = None,
    sleep_need_minutes: float | None = None,
    step_goal: float | None = None,
) -> bool:
    """Whether one logged day counts towards ``kind``.

    ``day`` is one record of what was logged (``water_ml``, ``sleep_minutes``,
    ``recipe_dinner``, ``steps``); a missing value is ``None`` and never counts.
    A day measured against a goal that is not known never counts either.
    """
    if kind == "water_days":
        water = day.get("water_ml")
        return water is not None and water_goal_ml is not None and water >= water_goal_ml
    if kind == "sleep_nights":
        minutes = day.get("sleep_minutes")
        return minutes is not None and sleep_need_minutes is not None and minutes >= sleep_need_minutes
    if kind == "cook_nights":
        return bool(day.get("recipe_dinner"))
    if kind == "step_days":
        steps = day.get("steps")
        return steps is not None and step_goal is not None and steps >= step_goal
    raise ValueError(f"unknown challenge kind: {kind}")


def progress(
    kind: str,
    days: list[dict],
    *,
    water_goal_ml: float | None = None,
    sleep_need_minutes: float | None = None,
    step_goal: float | None = None,
) -> dict:
    """The days that counted for one member, in order, and how many."""
    counted = [
        record["day"]
        for record in days
        if day_counts(
            kind, record, water_goal_ml=water_goal_ml, sleep_need_minutes=sleep_need_minutes, step_goal=step_goal
        )
    ]
    return {"count": len(counted), "days": counted}


def step_progress(start: date, daily_steps: dict[str, int], step_goal: float) -> dict:
    """``progress`` for ``step_days`` from geo's ``{day: steps}`` history.

    Only the challenge week is read; a day geo holds no steps for stays
    ``None`` (missing), so it can never count.
    """
    records = [{"day": day, "steps": daily_steps.get(day)} for day in week_days(start)]
    return progress("step_days", records, step_goal=step_goal)


def leaderboard(members: list[dict], target_days: int) -> list[dict]:
    """Members by days counted (most first), ties by name; each says if they reached the target."""
    ranked = sorted(members, key=lambda member: (-member["count"], member["user_id"]))
    return [{**member, "reached": member["count"] >= target_days} for member in ranked]
