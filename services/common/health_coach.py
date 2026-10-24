"""The chat coach: plain answers to "how did I sleep?" from the person's own data.

Jarvis chat asks this before any model: a first-person question about sleep,
food, water, readiness, the week or this week's goals is answered from the
health store's own summary, weekly digest and goals -- never a guess, never
another member's data. A topic whose card the person switched off, or the
coach itself switched off, falls through to the ordinary chat. In kid mode
food answers name what was logged and protein, never calories.
"""
from __future__ import annotations

import re
from typing import Any

# topic -> the hub card switch it belongs to (``services/health/preferences.py``).
TOPIC_SWITCH = {
    "sleep": "sleep",
    "food": "nutrition",
    "water": "water",
    "readiness": "readiness",
    "week": "weekly_digest",
    "goals": "coach",
}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("goals", re.compile(r"\bmy (weekly )?goals?\b|\bhow are my goals\b|\bset (me )?a (weekly )?goal\b")),
    ("week", re.compile(r"\bhow('?s| is| was) my week\b|\bmy (health|weekly) (week|digest|summary)\b|\bmy week in health\b")),
    ("sleep", re.compile(r"\bhow (did|have) i (sleep|slept)\b|\bmy sleep\b|\bhow much (did i sleep|sleep did i get)\b")),
    (
        "food",
        re.compile(
            r"\bhow much (protein|food) (is |do i have )?left\b|\bprotein left\b|\bcalories left\b"
            r"|\bhow (many|much) (calories|protein) (have i|did i)\b|\bwhat have i eaten\b|\bmy (food|nutrition) today\b"
        ),
    ),
    ("water", re.compile(r"\bhow much water (have i|did i)\b|\bmy water\b|\bwater (left|to go)\b")),
    ("readiness", re.compile(r"\bmy readiness\b|\bhow ready am i\b|\bam i ready (for|to)\b")),
]


def topic(query: str) -> str | None:
    """The health topic a first-person question is about, or None."""
    q = (query or "").lower().replace("\u2019", "'")
    for name, pattern in _PATTERNS:
        if pattern.search(q):
            return name
    return None


def _hm(minutes: float | None) -> str:
    total = round(minutes or 0)
    hours, rest = divmod(total, 60)
    if hours and rest:
        return f"{hours} h {rest} min"
    return f"{hours} h" if hours else f"{rest} min"


def _switched_on(summary: dict[str, Any], key: str) -> bool:
    metrics = ((summary or {}).get("profile") or {}).get("metrics") or {}
    return metrics.get(key) is True


def allowed(name: str, summary: dict[str, Any]) -> bool:
    """The coach and this topic's card are both on."""
    return _switched_on(summary, "coach") and _switched_on(summary, TOPIC_SWITCH[name])


def _sleep(summary: dict[str, Any], week: dict[str, Any] | None) -> str:
    sleep = summary.get("sleep") or {}
    night = sleep.get("last_night")
    need = sleep.get("need_minutes")
    if not night:
        return "There is no sleep logged for last night yet. Log it on Health → Sleep and I can tell you how it went."
    line = f"You slept {_hm(night['duration_minutes'])} last night"
    score = sleep.get("score")
    if score:
        line += f", a sleep score of {score['score']} ({score['label']})"
    out = [line + "."]
    if need and night["duration_minutes"] < need - 30:
        out.append(f"That is about {_hm(need - night['duration_minutes'])} under your {_hm(need)} need, so an earlier wind-down tonight would help.")
    average = ((week or {}).get("averages") or {}).get("sleep_minutes")
    if average:
        out.append(f"Your average this week is {_hm(average)}.")
    return " ".join(out)


def _food(summary: dict[str, Any]) -> str:
    nutrition = summary.get("nutrition") or {}
    kid = bool((summary.get("profile") or {}).get("kid_mode"))
    if not nutrition.get("logged"):
        return "Nothing is logged for today yet. Add a meal on Health → Nutrition and I can tell you how the day looks."
    totals = nutrition.get("totals") or {}
    target = nutrition.get("target") or {}
    protein = round(totals.get("protein_g") or 0)
    if kid:
        count = nutrition.get("entries_count") or 0
        return f"You have logged {count} thing{'s' if count != 1 else ''} today, with about {protein} g of protein. Fruit or vegetables with the next meal is always a good idea."
    out = [f"You have logged {round(totals.get('calories') or 0):,} kcal today with {protein} g of protein."]
    if target.get("min") and target.get("max"):
        out.append(f"Your range is {target['min']:,} to {target['max']:,} kcal.")
    if target.get("protein_g"):
        left = target["protein_g"] - protein
        out.append(f"About {left} g of protein to go." if left > 0 else "You have reached your protein target.")
    return " ".join(out)


def _water(summary: dict[str, Any]) -> str:
    water = summary.get("water") or {}
    goal = water.get("goal_ml")
    if not water.get("logged"):
        return f"No water logged today yet{f'; your goal is {goal:,} ml' if goal else ''}."
    ml = round(water.get("ml") or 0)
    if goal and ml < goal:
        return f"You have logged {ml:,} ml of water today, {goal - ml:,} ml to go."
    return f"You have logged {ml:,} ml of water today{', your goal is done' if goal else ''}."


def _readiness(summary: dict[str, Any]) -> str:
    ready = summary.get("readiness")
    if not ready:
        return "There is no readiness score yet today. Log last night's sleep or answer the morning check-in."
    return f"Readiness is {ready['score']} ({ready['label']}). {ready['detail']}"


_METRIC_WORDS = {
    "sleep_minutes": "sleep",
    "sleep_score": "sleep score",
    "calories": "calories",
    "water_ml": "water",
    "energy": "energy",
    "mood": "mood",
}


def _week(week: dict[str, Any] | None, goals: dict[str, Any] | None) -> str:
    if not week or not week.get("days_logged"):
        return "Nothing is logged this week yet, so there is no week to describe."
    out = [f"You logged something on {week['days_logged']} of the last 7 days."]
    for change in (week.get("changes") or [])[:2]:
        out.append(f"Your {_METRIC_WORDS.get(change['metric'], change['metric'])} is {change['direction']} on last week.")
    if not week.get("changes"):
        out.append("A steady week compared with the one before.")
    out.append(_goals(goals))
    return " ".join(out)


def _goals(goals: dict[str, Any] | None) -> str:
    rows = (goals or {}).get("goals") or []
    if rows:
        parts = [f"{row['title']}: {row['count']} of {row['target_days']} {row['unit']}{' ✓' if row['reached'] else ''}" for row in rows]
        return "This week's goals — " + "; ".join(parts) + "."
    pick = (goals or {}).get("suggestion")
    if pick:
        return f"No goal set this week. A gentle one: {pick['title'].lower()}, {pick['target_days']} {pick['unit']}. {pick['reason']} Set it on Health → Today."
    return "No goal set this week."


def answer(name: str, summary: dict[str, Any], week: dict[str, Any] | None, goals: dict[str, Any] | None) -> str:
    if name == "sleep":
        return _sleep(summary, week)
    if name == "food":
        return _food(summary)
    if name == "water":
        return _water(summary)
    if name == "readiness":
        return _readiness(summary)
    if name == "week":
        return _week(week, goals)
    return _goals(goals)
