"""Content monitoring (Phase 11, step 6): what a parent needs to know.

Scans the text a child's phone and Jarvis already collect (text messages,
media titles, sites, page names) for a small, reviewed set of concerns and
returns each one with its level. Local and deterministic: nothing leaves the
house, no model guesses, and every match can be traced to a phrase here.

Levels follow Bark: ``severe`` (self-harm, a threat, an adult grooming a
child, sexual exploitation) pages both parents at once; ``high`` (bullying,
drugs, sexual content, secrecy with someone) and ``medium`` (alcohol,
vaping, gambling) wait for the weekly recap. A parent's per-child
sensitivity decides what pages them: ``all`` (high and severe), ``severe``
(the default) or ``off`` — severe self-harm and exploitation always page.

Phrases match on word boundaries, case-insensitively; a hit returns a short
excerpt around it so a parent sees the context, not just a label.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

LEVELS = ("medium", "high", "severe")
# Always page a parent, whatever the sensitivity.
ALWAYS_ON = ("self_harm", "exploitation")

CATEGORIES: dict[str, dict[str, Any]] = {
    "self_harm": {
        "level": "severe",
        "label": "Self-harm or suicide",
        "phrases": [
            r"kill myself", r"killing myself", r"want to die", r"wanna die", r"end my life", r"suicid\w*",
            r"cut myself", r"cutting myself", r"hurt myself", r"no reason to live", r"better off dead",
            r"kms",
        ],
    },
    "exploitation": {
        "level": "severe",
        "label": "Sexual exploitation or grooming",
        "phrases": [
            r"send (me )?nudes?", r"send (me )?a pic of you(rself)?", r"(don'?t|do not) tell your (mom|dad|parents)",
            r"our (little )?secret", r"how old are you\??\s*(are you|r u) alone", r"delete (this|these|our) (chat|messages?)",
            r"meet (me )?alone",
        ],
    },
    "violence": {
        "level": "severe",
        "label": "A threat of violence",
        "phrases": [r"i('?m| am) going to (kill|shoot|stab) \w+", r"bring a gun", r"shoot up the school", r"i will hurt you"],
    },
    "bullying": {
        "level": "high",
        "label": "Bullying",
        "phrases": [r"everyone hates you", r"nobody likes you", r"kill yourself", r"kys", r"you'?re so ugly", r"loser"],
    },
    "sexual": {
        "level": "high",
        "label": "Sexual content",
        "phrases": [r"nudes?", r"sexting", r"porn\w*", r"onlyfans", r"hentai", r"xxx"],
    },
    "drugs": {
        "level": "high",
        "label": "Drugs",
        "phrases": [r"weed", r"edibles?", r"xanax", r"percs?", r"molly", r"cocaine", r"get high", r"plug me"],
    },
    "secrecy": {
        "level": "high",
        "label": "Secrecy with someone",
        "phrases": [r"don'?t tell (anyone|anybody)", r"keep (this|it) between us", r"use (a|my) (burner|second) (account|phone)"],
    },
    "substances": {
        "level": "medium",
        "label": "Alcohol or vaping",
        "phrases": [r"vape\w*", r"juul", r"get drunk", r"wasted", r"shots tonight"],
    },
    "gambling": {
        "level": "medium",
        "label": "Gambling",
        "phrases": [r"sports ?bet\w*", r"casino", r"draftkings", r"fanduel", r"stake\.com"],
    },
}

_COMPILED = {
    name: re.compile(r"(?<![\w])(" + "|".join(spec["phrases"]) + r")(?![\w])", re.IGNORECASE)
    for name, spec in CATEGORIES.items()
}


def _excerpt(text: str, start: int, end: int, width: int = 40) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    snippet = text[left:right].replace("\n", " ").strip()
    return ("…" if left else "") + snippet + ("…" if right < len(text) else "")


def _texts(event: dict[str, Any]) -> Iterable[str]:
    """The human text an activity event carries."""
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
    for value in (event.get("item"), detail.get("body"), detail.get("title"), detail.get("text")):
        if isinstance(value, str) and value.strip():
            yield value


def scan_text(text: str) -> list[dict[str, Any]]:
    """Every category found in ``text`` (at most one hit per category)."""
    found = []
    for name, pattern in _COMPILED.items():
        match = pattern.search(text or "")
        if match:
            spec = CATEGORIES[name]
            found.append({
                "category": name,
                "label": spec["label"],
                "level": spec["level"],
                "excerpt": _excerpt(text, match.start(), match.end()),
            })
    return found


def scan_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Concerns in one activity event, tagged with where they came from.

    A message the child received and one they sent are both scanned: a bully's
    words and a child's own cry for help both matter.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text in _texts(event):
        for hit in scan_text(text):
            if hit["category"] in seen:
                continue
            seen.add(hit["category"])
            out.append({**hit, "feature": event.get("feature"), "action": event.get("action"), "at": event.get("at")})
    return out


def pages_parent(concern: dict[str, Any], sensitivity: str | None) -> bool:
    """Whether this concern notifies the parents now (else it waits for the recap)."""
    if concern.get("category") in ALWAYS_ON:
        return True
    mode = sensitivity or "severe"
    if mode == "off":
        return False
    if mode == "all":
        return concern.get("level") in ("high", "severe")
    return concern.get("level") == "severe"
