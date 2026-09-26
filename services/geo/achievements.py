"""Goals and achievements.

Achievements are *derived* — computed from the same day buckets and workouts
the app already records — so there is nothing to backfill and a rules fix
corrects history immediately. Definitions are data (`achievements.json`) so new
badges and thresholds do not need a deploy.

Points are the single currency: achievements award points, and points are what
the Skylight bridge converts into stars (see docs/ACHIEVEMENTS.md).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("geo.achievements")

DEFINITIONS_PATH = Path(__file__).with_name("achievements.json")

DEFAULT_GOALS: dict[str, int] = {
    "daily_steps": 10000,
    "weekly_steps": 70000,
    "workouts_per_week": 4,
    "weekly_distance_miles": 15,
}

GOAL_BOUNDS: dict[str, tuple[int, int]] = {
    "daily_steps": (1000, 100000),
    "weekly_steps": (5000, 700000),
    "workouts_per_week": (1, 50),
    "weekly_distance_miles": (0, 1000),
}

RULE_TYPES = (
    "steps_any",
    "steps_in_day",
    "steps_in_week",
    "goal_days",
    "goal_streak",
    "goal_after_gap",
    "workouts_total",
    "workouts_in_week",
    "distance_miles",
)


@dataclass
class Achievement:
    id: str
    name: str
    description: str
    points: int
    rule_type: str
    rule_value: float


@dataclass
class Earned:
    achievement: Achievement
    earned_on: str
    points: int


@dataclass
class Progress:
    achievement: Achievement
    current: float
    target: float
    remaining: float
    percent: int


@dataclass
class Evaluation:
    earned: list[Earned] = field(default_factory=list)
    next_up: list[Progress] = field(default_factory=list)

    @property
    def points(self) -> int:
        return sum(e.points for e in self.earned)


def load_definitions(path: Path | None = None) -> list[Achievement]:
    """Load and validate achievement definitions.

    A malformed entry is skipped with a warning rather than taking down the
    endpoint: a bad badge must never break step reporting.
    """
    source = path or DEFINITIONS_PATH
    try:
        raw = json.loads(source.read_text())
    except Exception as e:
        log.error("Could not read achievement definitions (%s): %s", source, e)
        return []
    if raw.get("kind") != "jarvis.achievements":
        log.error("Achievement definitions have unexpected kind: %r", raw.get("kind"))
        return []

    out: list[Achievement] = []
    for entry in raw.get("achievements", []):
        try:
            rule = entry.get("rule") or {}
            rule_type = str(rule.get("type"))
            if rule_type not in RULE_TYPES:
                raise ValueError(f"unknown rule type {rule_type!r}")
            out.append(
                Achievement(
                    id=str(entry["id"]),
                    name=str(entry["name"]),
                    description=str(entry.get("description", "")),
                    points=int(entry.get("points", 0)),
                    rule_type=rule_type,
                    rule_value=float(rule.get("value", 0)),
                )
            )
        except Exception as e:
            log.warning("Skipping invalid achievement definition %r: %s", entry, e)
    return out


# ── metric helpers (pure, so the rules are unit-testable) ────────────────────

def _parse_day(day: str) -> date | None:
    try:
        return date.fromisoformat(day)
    except (TypeError, ValueError):
        return None


def max_steps_in_day(daily_steps: dict[str, int]) -> float:
    return float(max(daily_steps.values(), default=0))


def sum_steps(daily_steps: dict[str, int]) -> float:
    return float(sum(daily_steps.values()))


def max_steps_in_week(daily_steps: dict[str, int]) -> float:
    """Best ISO week (Mon–Sun) total in the window."""
    weeks: dict[tuple[int, int], int] = {}
    for day, steps in daily_steps.items():
        parsed = _parse_day(day)
        if parsed is None:
            continue
        iso = parsed.isocalendar()
        key = (iso[0], iso[1])
        weeks[key] = weeks.get(key, 0) + int(steps)
    return float(max(weeks.values(), default=0))


def goal_dates(daily_steps: dict[str, int], daily_goal: int) -> list[date]:
    out: list[date] = []
    for day, steps in daily_steps.items():
        parsed = _parse_day(day)
        if parsed is not None and steps >= daily_goal:
            out.append(parsed)
    return sorted(out)


def longest_goal_streak(daily_steps: dict[str, int], daily_goal: int) -> int:
    """Longest run of consecutive calendar days meeting the goal."""
    days = goal_dates(daily_steps, daily_goal)
    if not days:
        return 0
    best = current = 1
    for prev, nxt in zip(days, days[1:]):
        current = current + 1 if (nxt - prev) == timedelta(days=1) else 1
        best = max(best, current)
    return best


def has_goal_after_gap(daily_steps: dict[str, int], daily_goal: int, gap_days: int) -> bool:
    """True when a goal day follows `gap_days` consecutive below-goal days.

    The gap only counts days we actually have data for — an unrecorded day is
    unknown, not a failure, and must not fabricate a comeback.
    """
    days = sorted(d for d in (_parse_day(k) for k in daily_steps) if d is not None)
    if not days:
        return False
    below = 0
    for day in days:
        if daily_steps.get(day.isoformat(), 0) >= daily_goal:
            if below >= gap_days:
                return True
            below = 0
        else:
            below += 1
    return False


def workouts_in_best_week(workouts: Iterable[dict[str, Any]]) -> int:
    weeks: dict[tuple[int, int], int] = {}
    for workout in workouts:
        start = workout.get("start_time")
        if not start:
            continue
        try:
            parsed = datetime.fromtimestamp(float(start))
        except (TypeError, ValueError, OSError):
            continue
        iso = parsed.isocalendar()
        key = (iso[0], iso[1])
        weeks[key] = weeks.get(key, 0) + 1
    return max(weeks.values(), default=0)


def total_workout_miles(workouts: Iterable[dict[str, Any]]) -> float:
    total = 0.0
    for workout in workouts:
        try:
            total += float(workout.get("distance_miles") or 0)
        except (TypeError, ValueError):
            continue
    return total


def measure(
    rule_type: str,
    daily_steps: dict[str, int],
    workouts: list[dict[str, Any]],
    goals: dict[str, int],
) -> float:
    """Current value for a rule, used for both unlocking and progress."""
    if rule_type == "steps_any":
        return max_steps_in_day(daily_steps)
    if rule_type == "steps_in_day":
        return max_steps_in_day(daily_steps)
    if rule_type == "steps_in_week":
        return max_steps_in_week(daily_steps)
    if rule_type == "goal_days":
        return float(len(goal_dates(daily_steps, goals["daily_steps"])))
    if rule_type == "goal_streak":
        return float(longest_goal_streak(daily_steps, goals["daily_steps"]))
    if rule_type == "goal_after_gap":
        return 0.0  # boolean rule; handled explicitly in evaluate()
    if rule_type == "workouts_total":
        return float(len(workouts))
    if rule_type == "workouts_in_week":
        return float(workouts_in_best_week(workouts))
    if rule_type == "distance_miles":
        return total_workout_miles(workouts)
    return 0.0


def evaluate(
    definitions: list[Achievement],
    daily_steps: dict[str, int],
    workouts: list[dict[str, Any]],
    goals: dict[str, int],
    previously_earned: dict[str, str] | None = None,
    today: str | None = None,
) -> Evaluation:
    """Work out what is earned, what is close, and what is already banked.

    `previously_earned` maps achievement id -> date first earned, so a badge is
    reported once with a stable date instead of being re-awarded every read.
    """
    earned_map = dict(previously_earned or {})
    result = Evaluation()
    today_str = today or date.today().isoformat()

    for definition in definitions:
        banked_on = earned_map.get(definition.id)
        if banked_on:
            result.earned.append(Earned(definition, banked_on, definition.points))
            continue

        if definition.rule_type == "goal_after_gap":
            unlocked = has_goal_after_gap(daily_steps, goals["daily_steps"], int(definition.rule_value))
            if unlocked:
                result.earned.append(Earned(definition, today_str, definition.points))
            continue

        current = measure(definition.rule_type, daily_steps, workouts, goals)
        if current >= definition.rule_value:
            result.earned.append(Earned(definition, today_str, definition.points))
        else:
            remaining = max(0.0, definition.rule_value - current)
            percent = int(round((current / definition.rule_value) * 100)) if definition.rule_value else 0
            result.next_up.append(
                Progress(
                    achievement=definition,
                    current=current,
                    target=definition.rule_value,
                    remaining=remaining,
                    percent=max(0, min(99, percent)),
                )
            )

    result.next_up.sort(key=lambda p: p.remaining)
    return result


# ── storage helpers ─────────────────────────────────────────────────────────

def clean_goals(raw: dict[str, Any] | None) -> dict[str, int]:
    """Merge stored goals over defaults, ignoring out-of-range junk."""
    goals = dict(DEFAULT_GOALS)
    for key, value in (raw or {}).items():
        if key not in GOAL_BOUNDS:
            continue
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            continue
        low, high = GOAL_BOUNDS[key]
        if low <= parsed <= high:
            goals[key] = parsed
    return goals


async def load_goals(r, user: str) -> dict[str, int]:
    try:
        stored = await r.hgetall(f"geo:goals:{user}")
    except Exception as e:
        log.warning("Goal read failed for %s: %s", user, e)
        stored = {}
    return clean_goals(stored)


async def save_goals(r, user: str, updates: dict[str, Any]) -> dict[str, int]:
    clean = clean_goals({**(await load_goals(r, user)), **updates})
    await r.hset(f"geo:goals:{user}", mapping={k: str(v) for k, v in clean.items()})
    return clean


async def load_points_ledger(r, user: str) -> dict[str, str]:
    try:
        return await r.hgetall(f"geo:points:{user}") or {}
    except Exception as e:
        log.warning("Points ledger read failed for %s: %s", user, e)
        return {}


async def record_awards(r, user: str, earned: list[Earned]) -> None:
    """Bank newly earned achievements (idempotent)."""
    if not earned:
        return
    mapping = {e.achievement.id: e.earned_on for e in earned}
    try:
        await r.hset(f"geo:points:{user}", mapping=mapping)
    except Exception as e:
        log.warning("Points ledger write failed for %s: %s", user, e)


def total_points(ledger: dict[str, str], definitions: list[Achievement]) -> int:
    by_id = {d.id: d for d in definitions}
    return sum(by_id[key].points for key in ledger if key in by_id)
