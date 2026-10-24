"""The Health hub's streak habits; the forgiving rule is ``services/common/streaks.py``."""
from __future__ import annotations

from services.common.streaks import streak

__all__ = ["HABITS", "RULE", "streak"]

#: key -> (title, what counts). Every habit is fine for a child: none is about
#: calories or weight.
HABITS: dict[str, tuple[str, str]] = {
    "logging": ("Showing up", "Days you logged anything: food, water, sleep or a check-in."),
    "checkin": ("Morning check-in", "Days you answered the morning check-in."),
    "water_goal": ("Water goal", "Days your logged water reached your water goal."),
    "sleep_logged": ("Sleep logged", "Nights you logged your sleep."),
}
RULE = "One missed day in any seven is a rest day and keeps the streak; today counts once it is logged."
