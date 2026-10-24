"""Gentle weekly goals for one person, and the coach's one suggestion.

A goal is a number of days this week on which the person does one thing the
store can measure (the same kinds as the family challenge: water goal days,
sleep goal nights, cook nights). At most two goals a week, so the week stays
light. The suggestion looks at last week and proposes one small step up --
last week's days plus one -- for the kind with the most room, never a jump.
"""
from __future__ import annotations

from services.health import challenges

MAX_GOALS_PER_WEEK = 2


def suggestion(last_week_counts: dict[str, int], taken: set[str]) -> dict | None:
    """One small next step from last week's counts, or None when nothing fits."""
    options = [(kind, count) for kind, count in last_week_counts.items() if kind not in taken and count < 7]
    if not options:
        return None
    # The most room to grow first; ties go to the order the kinds are listed in.
    order = list(challenges.KINDS)
    kind, count = min(options, key=lambda item: (item[1], order.index(item[0])))
    target = min(7, count + 1)
    meta = challenges.KINDS[kind]
    if count == 0:
        reason = f"Last week had none. One {meta['unit'][:-1]} is a fine start."
    else:
        reason = f"Last week you managed {count}. One more is a small step."
    return {"kind": kind, "target_days": target, "title": meta["title"], "unit": meta["unit"], "reason": reason}
