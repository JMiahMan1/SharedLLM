"""Achievements for the reading app.

Same shape as the geo engine (see ``services/geo/achievements.py``) so the two
systems read alike, but the inputs are different: everything here is derived
from the reading-state tables in ``models.py`` rather than from Redis step
buckets. Badges are therefore derived, not stored — nothing to backfill, and a
rules fix corrects history immediately.

Definitions live in ``achievements.json`` so a new badge or threshold does not
need a deploy. Points are the SharedLLM currency; this service converts them
into geo stars through the existing ``POST /api/geo/stars`` bridge rather than
inventing a second ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
import logging
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger("bible.achievements")

DEFINITIONS_PATH = Path(__file__).with_name("achievements.json")

RULE_TYPES = (
    "chapters_total",
    "books_read",
    "read_streak",
    "plan_days_total",
    "plan_completed",
    "memory_mastered",
    "quizzes_total",
    "quiz_perfect",
)

RULE_LABELS: dict[str, str] = {
    "chapters_total": "chapters",
    "books_read": "books",
    "read_streak": "day streak",
    "plan_days_total": "plan days",
    "plan_completed": "plans finished",
    "memory_mastered": "verses memorized",
    "quizzes_total": "quizzes",
    "quiz_perfect": "perfect quizzes",
}

#: A verse counts as memorized once it reaches this recall level on the ladder.
MASTERED_LEVEL = 4

#: A quiz counts as perfect at this score.
PERFECT_SCORE = 100


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
    unit: str


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
    endpoint: a bad badge must never break the reader.
    """
    source = path or DEFINITIONS_PATH
    try:
        raw = json.loads(source.read_text())
    except Exception as e:
        log.error("Could not read achievement definitions (%s): %s", source, e)
        return []
    if raw.get("kind") != "jarvis.bible.achievements":
        log.error("Achievement definitions have unexpected kind: %r", raw.get("kind"))
        return []

    out: list[Achievement] = []
    seen: set[str] = set()
    for entry in raw.get("achievements", []):
        try:
            rule = entry.get("rule") or {}
            rule_type = str(rule.get("type"))
            if rule_type not in RULE_TYPES:
                raise ValueError(f"unknown rule type {rule_type!r}")
            identifier = str(entry["id"])
            if identifier in seen:
                raise ValueError(f"duplicate achievement id {identifier!r}")
            seen.add(identifier)
            out.append(
                Achievement(
                    id=identifier,
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


def _parse_day(day: Any) -> date | None:
    try:
        return date.fromisoformat(str(day))
    except (TypeError, ValueError):
        return None


def longest_streak(active_days: Any) -> int:
    """Longest run of consecutive calendar days in ``active_days``.

    A day nobody recorded is unknown, not a failure, so a gap breaks the run
    rather than being counted through.
    """
    days = sorted(d for d in (_parse_day(d) for d in active_days) if d is not None)
    if not days:
        return 0
    best = current = 1
    for prev, nxt in zip(days, days[1:]):
        current = current + 1 if (nxt - prev) == timedelta(days=1) else 1
        best = max(best, current)
    return best


def current_streak(active_days: Any, today: date | None = None) -> int:
    """Run of consecutive days ending today or yesterday.

    Yesterday still counts: a streak is not broken before the day is over.
    """
    days = sorted({d for d in (_parse_day(d) for d in active_days) if d is not None})
    if not days:
        return 0
    anchor = today or date.today()
    if days[-1] < anchor - timedelta(days=1) or days[-1] > anchor:
        return 0
    run = 1
    for prev, nxt in zip(reversed(days[:-1]), reversed(days[1:])):
        if (nxt - prev) == timedelta(days=1):
            run += 1
        else:
            break
    return run


def measure(rule_type: str, metrics: Mapping[str, float]) -> float:
    """Current value for a rule, used for both unlocking and progress."""
    try:
        return float(metrics.get(rule_type, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def evaluate(
    definitions: list[Achievement],
    metrics: Mapping[str, float],
    previously_earned: Mapping[str, str] | None = None,
    today: str | None = None,
) -> Evaluation:
    """Work out what is earned, what is close, and what is already banked.

    ``previously_earned`` maps achievement id -> date first earned, so a badge is
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
        current = measure(definition.rule_type, metrics)
        if current >= definition.rule_value:
            result.earned.append(Earned(definition, today_str, definition.points))
        else:
            remaining = max(0.0, definition.rule_value - current)
            percent = (
                int(round((current / definition.rule_value) * 100))
                if definition.rule_value
                else 0
            )
            result.next_up.append(
                Progress(
                    achievement=definition,
                    current=current,
                    target=definition.rule_value,
                    remaining=remaining,
                    percent=max(0, min(99, percent)),
                    unit=RULE_LABELS.get(definition.rule_type, ""),
                )
            )

    # Rank by completion ratio, not raw remaining: "12 chapters to go" and
    # "1 quiz to go" are not comparable units, but 96% vs 4% is.
    result.next_up.sort(key=lambda p: (-p.percent, p.remaining))
    return result


def total_points(earned: list[Earned]) -> int:
    return sum(item.points for item in earned)


def star_grants(earned: list[Earned]) -> list[dict[str, Any]]:
    """Turn freshly earned badges into geo star grants.

    The caller banks these before announcing them, so a geo or Talk outage
    cannot cost someone the badge itself.
    """
    return [
        {
            "stars": item.achievement.points,
            "reason": "achievement",
            "detail": item.achievement.name,
        }
        for item in earned
    ]