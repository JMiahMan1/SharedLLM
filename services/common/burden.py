"""Burden signals (Phase 11, step 7: a voice for the quiet ones).

A child who rates their own mood or energy low on most recent mornings may
not say so out loud. Read only from their own check-ins; the parents are told
"a few hard days", never the answers, so the check-in stays the child's.
"""

from __future__ import annotations

from typing import Any

LOW = 2  # on the 1-5 scale: a 1 or a 2
WINDOW = 5  # the last five check-ins
HARD_DAYS = 3  # this many low mornings in the window


def hard_days(checkins: list[dict[str, Any]]) -> int:
    """Low mood or low energy among the most recent check-ins."""
    recent = sorted(checkins, key=lambda c: str(c.get("day")), reverse=True)[:WINDOW]
    return sum(
        1 for c in recent
        if (c.get("mood") is not None and c["mood"] <= LOW) or (c.get("energy") is not None and c["energy"] <= LOW)
    )


def is_burdened(checkins: list[dict[str, Any]]) -> bool:
    return hard_days(checkins) >= HARD_DAYS
