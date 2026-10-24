"""Weekly family challenges: what counts, and the leaderboard.

A challenge runs Monday to Sunday and asks for a number of *days* on which a
member did one measurable thing. Only kinds the health store can measure from
what people actually logged are offered:

* ``water_days`` -- days the water logged reached the member's water goal;
* ``sleep_nights`` -- nights the logged sleep reached the member's sleep need;
* ``cook_nights`` -- days a dinner was logged from a family recipe.

Steps live in geo and veg servings are not recorded per food, so neither is a
kind here: a challenge that cannot be measured honestly would have to guess.
A day with nothing logged is simply not counted -- never estimated -- and no
kind is about calories or weight, so the same challenge is fine for a child.
"""
from __future__ import annotations

from datetime import date, timedelta

from services.common.streaks import week_start

__all__ = ["KINDS", "day_counts", "leaderboard", "progress", "week_days", "week_start"]

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
}


def week_days(start: date) -> list[str]:
    return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]


def day_counts(kind: str, day: dict, *, water_goal_ml: float, sleep_need_minutes: float) -> bool:
    """Whether one logged day counts towards ``kind``.

    ``day`` is one record of what was logged (``water_ml``, ``sleep_minutes``,
    ``recipe_dinner``); a missing value is ``None`` and never counts.
    """
    if kind == "water_days":
        water = day.get("water_ml")
        return water is not None and water >= water_goal_ml
    if kind == "sleep_nights":
        minutes = day.get("sleep_minutes")
        return minutes is not None and minutes >= sleep_need_minutes
    if kind == "cook_nights":
        return bool(day.get("recipe_dinner"))
    raise ValueError(f"unknown challenge kind: {kind}")


def progress(kind: str, days: list[dict], *, water_goal_ml: float, sleep_need_minutes: float) -> dict:
    """The days that counted for one member, in order, and how many."""
    counted = [
        record["day"]
        for record in days
        if day_counts(kind, record, water_goal_ml=water_goal_ml, sleep_need_minutes=sleep_need_minutes)
    ]
    return {"count": len(counted), "days": counted}


def leaderboard(members: list[dict], target_days: int) -> list[dict]:
    """Members by days counted (most first), ties by name; each says if they reached the target."""
    ranked = sorted(members, key=lambda member: (-member["count"], member["user_id"]))
    return [{**member, "reached": member["count"] >= target_days} for member in ranked]
