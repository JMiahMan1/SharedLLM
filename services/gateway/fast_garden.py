"""Tier 1 garden commands: deterministic, under 100 ms, no model round-trip.

"I watered Monty", "watered the monstera", "watered all the living room plants",
"snooze Fernie until tomorrow", "what needs watering today?" and "log 3 pounds of
tomatoes harvested" are all answered here, from the same ``services/garden``
calls the UI makes, with the acting person's own identity.

Matching mirrors ``fast_lights``: a phrase must name a garden action *and* a
target, so an ordinary sentence falls through untouched to the model. A name
that fits more than one plant is never guessed -- the caller is asked.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from services.gateway import garden_assist
from services.gateway.garden_assist import GardenCall, call_garden

log = logging.getLogger("gateway.fast_garden")

#: A spoken quantity's unit, as the garden stores it.
_UNITS = {
    "pound": "lb", "pounds": "lb", "lb": "lb", "lbs": "lb",
    "ounce": "oz", "ounces": "oz", "oz": "oz",
    "bunch": "bunch", "bunches": "bunch",
    "quart": "quart", "quarts": "quart",
    "pint": "pint", "pints": "pint",
    "cup": "cup", "cups": "cup",
    "piece": "piece", "pieces": "piece",
    "gram": "g", "grams": "g", "g": "g",
    "kilogram": "kg", "kilograms": "kg", "kg": "kg",
}

_SUMMARY_RE = re.compile(
    r"\b(?:what|which|any)\b[^.?!]*\b(?:plants?|garden|herbs?)\b[^.?!]*"
    r"\b(?:water|watering|care|chores|need|due|harvest)\b"
    r"|\bwhat\s+needs\s+(?:water|watering|care)\b"
    r"|\bgarden\s+(?:chores|status|summary|today)\b"
    r"|\banything\s+(?:due|need\s+care)\b"
)

#: Care verbs, most specific first. Group 1 (when present) is the target.
_CARE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmark\s+(?:the\s+)?(.+?)\s+(?:as\s+)?watered\b"), "water"),
    (re.compile(r"\b(?:i|we)?\s*(?:just\s+)?watered\s+(?:the\s+|all\s+the\s+|all\s+)?(.+)$"), "water"),
    (re.compile(r"\b(?:i|we)?\s*water\s+(?:the\s+|all\s+the\s+|all\s+)?(.+)$"), "water"),
    (re.compile(r"\b(?:i|we)?\s*(?:just\s+)?fertilized\s+(?:the\s+)?(.+)$"), "fertilize"),
    (re.compile(r"\b(?:i|we)?\s*(?:just\s+)?misted\s+(?:the\s+)?(.+)$"), "mist"),
    (re.compile(r"\b(?:i|we)?\s*(?:just\s+)?pruned\s+(?:the\s+)?(.+)$"), "prune_pinch"),
    (re.compile(r"\b(?:i|we)?\s*(?:just\s+)?pinched\s+(?:back\s+)?(?:the\s+)?(.+)$"), "prune_pinch"),
)

_SNOOZE_RE = re.compile(r"\bsnooze\s+(?:the\s+)?(.+?)(?:\s+(?:until|till|for)\s+(.+))?$")
_STILL_DAMP_RE = re.compile(r"^(.+?)\s*(?:is|looks|seems)?\s*(?:still\s+damp|still\s+wet)\b")

_HARVEST_RE = re.compile(
    r"\b(?:log|logged|record|add|i\s+picked|picked|harvested)\s+"
    r"(\d+(?:\.\d+)?)\s*"
    r"(pounds?|lbs?|ounces?|oz|bunches?|quarts?|pints?|cups?|pieces?|grams?|kilograms?|kg|g)?\s*"
    r"(?:of\s+)?(.+)$"
)

_TRAILING_NOISE = re.compile(r"\b(?:today|please|now|thanks|thank\s+you|for\s+me)\b")
_LEADING_NOISE = re.compile(r"^(?:the|all|all\s+the|my|our|a|an)\s+")


def _clean(text: str) -> str:
    """A phrase reduced to the name inside it."""
    value = (text or "").strip().strip(".!?,;:")
    value = _TRAILING_NOISE.sub(" ", value.lower())
    value = re.sub(r"\s+", " ", value).strip()
    previous = None
    while previous != value:
        previous = value
        value = _LEADING_NOISE.sub("", value).strip()
    return re.sub(r"\s+(?:plants?|pots?)$", "", value).strip()


def parse_summary(text: str) -> bool:
    """Is this a question about what needs care today?"""
    return bool(_SUMMARY_RE.search(text or ""))


def parse_harvest(text: str) -> dict | None:
    """``{"quantity", "unit", "target"}`` from "log 3 pounds of tomatoes harvested"."""
    match = _HARVEST_RE.search((text or "").lower())
    if not match:
        return None
    quantity = float(match.group(1))
    unit = _UNITS.get((match.group(2) or "lb").strip(), "lb")
    target = _clean(re.sub(r"\b(?:harvested|picked|today|now)\b", " ", match.group(3) or ""))
    if not target:
        return None
    return {"quantity": quantity, "unit": unit, "target": target}


def parse_snooze(text: str) -> dict | None:
    """``{"target", "postpone_days"}`` for "snooze Fernie until tomorrow" / "Monty is still damp"."""
    lowered = (text or "").lower().strip().strip(".!?")
    match = _STILL_DAMP_RE.search(lowered)
    if match:
        target = _clean(match.group(1))
        return {"target": target, "postpone_days": 1.0} if target else None
    match = _SNOOZE_RE.search(lowered)
    if not match:
        return None
    target = _clean(match.group(1))
    if not target:
        return None
    days: float | None = None
    when = (match.group(2) or "").lower()
    if "tomorrow" in when:
        days = 1.0
    elif "day" in when:
        number = re.search(r"(\d+)", when)
        if number:
            days = float(number.group(1))
    return {"target": target, "postpone_days": days}


def parse_care(text: str) -> dict | None:
    """``{"action", "target"}`` for a watering / fertilizing / misting phrase."""
    lowered = (text or "").lower().strip().strip(".!?")
    for pattern, action in _CARE_PATTERNS:
        match = pattern.search(lowered)
        if not match:
            continue
        target = _clean(match.group(1) or "")
        if not target:
            continue
        # "water the plants" alone names no one: not a command we can act on.
        if target in ("plants", "plant", "garden", "everything", "everything else"):
            return None
        return {"action": action, "target": target}
    return None


async def try_handle(
    query: str,
    creds: Any,
    *,
    call: GardenCall = call_garden,
) -> str | None:
    """Answer a garden utterance, or None when it is not one."""
    text = (query or "").strip()
    if not text or len(text) > 300:
        return None
    lowered = text.lower()

    # 1. "What needs water today?" -- a question, so answer with the day's care.
    if parse_summary(lowered):
        answer = await garden_assist.summarize(creds, call=call)
        return str(answer.get("message") or "")

    # 2. "Log 3 pounds of tomatoes harvested" -- a number plus a crop.
    harvest = parse_harvest(lowered)
    if harvest is not None:
        answer = await garden_assist.log_harvest(
            creds,
            harvest["target"],
            quantity=harvest["quantity"],
            unit=harvest["unit"],
            call=call,
        )
        log.info("[FastPath] garden harvest '%s' -> %s", harvest["target"], answer.get("status"))
        return str(answer.get("message") or "")

    # 3. "Snooze Fernie until tomorrow" / "Monty is still damp".
    snooze = parse_snooze(lowered)
    if snooze is not None and _names_a_plant(lowered, snooze["target"]):
        answer = await garden_assist.log_care(
            creds,
            snooze["target"],
            still_damp=True,
            source="voice",
            postpone_days=snooze["postpone_days"],
            call=call,
        )
        log.info("[FastPath] garden snooze '%s' -> %s", snooze["target"], answer.get("status"))
        return str(answer.get("message") or "")

    # 4. "I watered Monty" / "watered all the living room plants".
    care = parse_care(lowered)
    if care is not None:
        answer = await garden_assist.log_care(
            creds,
            care["target"],
            action=care["action"],
            source="voice",
            call=call,
        )
        log.info("[FastPath] garden care '%s' (%s) -> %s", care["target"], care["action"], answer.get("status"))
        return str(answer.get("message") or "")

    return None


def _names_a_plant(text: str, target: str) -> bool:
    """A snooze needs a real phrase on both sides ("snooze the timer" is not ours)."""
    return len(target) >= 2 and ("snooze" in text or "still damp" in text or "still wet" in text)
