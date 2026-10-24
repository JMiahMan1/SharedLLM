"""Cheers: a kind word for a family member's day (Phase 7).

A cheer is one of a fixed set, never free text, so it is always kind and fine
between any two members, children included. Who may cheer whom follows what is
already visible: someone who shares a health scope with you, or who is in one
of this week's family challenges with you. A cheer never reveals anything
about the day it cheers; it only says who cheered and which cheer.
"""
from __future__ import annotations

#: kind -> (emoji, label)
KINDS: dict[str, tuple[str, str]] = {
    "well_done": ("👏", "Well done"),
    "keep_going": ("💪", "Keep going"),
    "proud": ("⭐", "Proud of you"),
    "rest_well": ("😴", "Rest well"),
    "good_food": ("🥕", "Good eating"),
}


def catalog() -> list[dict[str, str]]:
    return [{"kind": kind, "emoji": emoji, "label": label} for kind, (emoji, label) in KINDS.items()]
