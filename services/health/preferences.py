"""Which Health hub cards a person wants: the per-metric on/off switches.

Every metric can be turned off ("Opt-in and quiet" in docs/HEALTH_HUB.md).
Turning a card off hides it on the person's own screens and keeps the chat
coach from bringing it up; it never deletes data and never changes what a
family member was granted. Fasting is the one card that starts off, and it is
adults-only: a profile in kid mode or under 18 never gets it.
"""
from __future__ import annotations

from typing import Any

#: key -> (label, on by default, adults only). Order is the settings order.
SWITCHES: dict[str, tuple[str, bool, bool]] = {
    "readiness": ("Readiness score", True, False),
    "checkin": ("Morning check-in", True, False),
    "sleep": ("Sleep", True, False),
    "nutrition": ("Food", True, False),
    "water": ("Water", True, False),
    "steps": ("Steps", True, False),
    "active_minutes": ("Active minutes", True, False),
    "streaks": ("Streaks", True, False),
    "weekly_digest": ("Weekly digest", True, False),
    "family_challenge": ("Family challenge", True, False),
    "cheers": ("Cheers", True, False),
    "lessons": ("Lessons", True, False),
    "coach": ("Coach and reminders", True, False),
    "fasting": ("Fasting window", False, True),
}


def catalog(*, minor: bool) -> list[dict[str, Any]]:
    """The switches a person may change, for the settings screen."""
    return [
        {"key": key, "label": label, "default": default}
        for key, (label, default, adults_only) in SWITCHES.items()
        if not (adults_only and minor)
    ]


def resolve(stored: dict[str, Any] | None, *, minor: bool) -> dict[str, bool]:
    """Every switch with the person's choice, else its default.

    Adults-only switches are always off for a minor, whatever is stored.
    """
    stored = stored or {}
    out: dict[str, bool] = {}
    for key, (_label, default, adults_only) in SWITCHES.items():
        value = stored.get(key)
        out[key] = False if adults_only and minor else (value if isinstance(value, bool) else default)
    return out


def validate(changes: dict[str, Any], *, minor: bool) -> dict[str, bool]:
    """Check a partial update; raises ``ValueError`` with a plain reason."""
    unknown = sorted(set(changes) - set(SWITCHES))
    if unknown:
        raise ValueError(f"unknown metrics: {', '.join(unknown)}; valid metrics: {', '.join(SWITCHES)}")
    clean: dict[str, bool] = {}
    for key, value in changes.items():
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        if value and minor and SWITCHES[key][2]:
            raise ValueError(f"{SWITCHES[key][0]} is for adults only")
        clean[key] = value
    return clean
