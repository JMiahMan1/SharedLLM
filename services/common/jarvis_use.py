"""What a person does most inside Jarvis (Phase 10).

The web bundle posts one light activity event per visited page and the
recorders add plays, wins and chapters. This turns a person's own feed into a
small, friendly summary - the features they use most, over a window - without
ever handing back the raw stream, so a member can see their own picture and the
parents' feed stays the parents'.

Pure and deterministic: a day with nothing recorded is empty, never a zero
dressed up as a value, and an unknown feature keeps its own name rather than
being guessed into a category.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

#: Raw activity features that are *not* what someone does inside Jarvis: the
#: phone collects calls, texts and sites for the parents. They are left out of
#: a member's own summary on purpose.
EXTERNAL = frozenset({"web", "sms", "calls"})

#: In-app feature groups, in display order: (label, {features}). A feature not
#: named here keeps its own name, so a new recorder shows up without a change.
GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("Music, video and podcasts", frozenset({"media", "music", "video", "podcast", "audiobook"})),
    ("Bible", frozenset({"bible"})),
    ("Games", frozenset({"game", "trivia"})),
    ("Recipes", frozenset({"recipes"})),
    ("Notes", frozenset({"notes"})),
    ("Calendar", frozenset({"calendar"})),
    ("Family and chat", frozenset({"family"})),
    ("Health", frozenset({"health", "fitness"})),
    ("Dashboard", frozenset({"dashboard"})),
    ("Map", frozenset({"wander"})),
    ("Remote", frozenset({"remote"})),
    ("Settings", frozenset({"settings"})),
)
_GROUP_OF = {feature: label for label, features in GROUPS for feature in features}
_ORDER = {label: index for index, (label, _) in enumerate(GROUPS)}


def _local_day(at: Any) -> date | None:
    """The local day an event belongs to, or None when its time is unusable."""
    if not at:
        return None
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).astimezone().date()
    except ValueError:
        return None


def _label(feature: str) -> str:
    return _GROUP_OF.get(feature, feature.replace("_", " ").title() or "Jarvis")


def summarize(
    events: list[dict[str, Any]] | None,
    days: int = 7,
    today: date | None = None,
    top: int = 5,
) -> dict[str, Any]:
    """The caller's in-app use over the ``days`` ending on ``today`` (inclusive).

    Returns the most-used features and a total, or ``has_data: false`` with an
    empty ``top`` when nothing landed in the window. ``days`` counts the window
    length; the window start is said out loud so a short record is honest.
    """
    today = today or date.today()
    days = max(1, int(days))
    first = today - timedelta(days=days - 1)
    counts: Counter[str] = Counter()
    for event in events or []:
        if not isinstance(event, dict):
            continue
        feature = str(event.get("feature") or "").strip().lower()
        if not feature or feature in EXTERNAL:
            continue
        when = _local_day(event.get("at"))
        if when is None or when < first or when > today:
            continue
        counts[_label(feature)] += 1
    ranked = sorted(
        counts.items(),
        key=lambda item: (-item[1], _ORDER.get(item[0], len(_ORDER)), item[0]),
    )
    return {
        "days": days,
        "window_start": first.isoformat(),
        "has_data": bool(counts),
        "total": sum(counts.values()),
        "top": [{"label": label, "count": count} for label, count in ranked[: max(1, int(top))]],
    }
