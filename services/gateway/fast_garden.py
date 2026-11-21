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

import aiohttp

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

#: A whole question about the day's care. Each alternative must match the whole
#: (normalised) sentence: "which herbs need full sun" asks about a plant, not
#: about what is due, and goes to the model.
_SUMMARY_RE = re.compile(
    r"^(?:"
    r"(?:what|which)(?:\s+(?:of\s+)?(?:the\s+|my\s+|our\s+)?(?:plants?|herbs?|garden))?"
    r"\s+(?:needs?|need)\s+(?:water|watering|care|tending|attention|to\s+be\s+watered)"
    r"|(?:what|which)\s+(?:of\s+)?(?:the\s+|my\s+|our\s+)?(?:plants?|herbs?)\s+(?:is|are)\s+(?:due|overdue|thirsty)"
    r"|(?:do|does)\s+(?:any\s+(?:of\s+)?)?(?:the\s+|my\s+|our\s+)?(?:plants?|herbs?|garden)"
    r"\s+needs?\s+(?:water|watering|care|anything)"
    r"|(?:is|are)\s+(?:there\s+)?anything\s+(?:due|that\s+needs\s+(?:water|watering|care))\s+(?:in|for)\s+the\s+garden"
    r"|does\s+anything\s+in\s+the\s+garden\s+need\s+(?:water|watering|care)"
    r"|(?:what(?:'s|\s+is)\s+)?(?:the\s+)?garden\s+(?:chores|status|summary|report)"
    r"|any\s+garden\s+chores"
    r"|what(?:'s|\s+is)\s+due\s+in\s+the\s+garden"
    r")(?:\s+(?:today|now|right\s+now|this\s+week|in\s+the\s+garden))*$"
)

#: Anything a care report may open with before its verb ("okay, I just watered").
_OPENER = (
    r"^(?:(?:ok(?:ay)?|so|yes|yeah|hey\s+jarvis|jarvis)[,\s]+)?"
    r"(?:(?:i|we)(?:'ve|\s+have)?\s+)?(?:just\s+|already\s+|also\s+)?"
)

#: Care verbs, anchored at the start of the sentence. Group ``t`` is the target,
#: which must be the whole rest of the sentence (time words are refused and
#: trailing courtesies stripped). The flag says the verb is a past-tense report.
_CARE_PATTERNS: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(_OPENER + r"mark(?:ed)?\s+(?P<t>.+?)\s+(?:as\s+)?watered$"), "water", True),
    (re.compile(_OPENER + r"watered\s+(?P<t>.+)$"), "water", True),
    (re.compile(_OPENER + r"water\s+(?P<t>.+)$"), "water", False),
    (re.compile(_OPENER + r"(?:fertilized|fed)\s+(?P<t>.+)$"), "fertilize", True),
    (re.compile(_OPENER + r"misted\s+(?P<t>.+)$"), "mist", True),
    (re.compile(_OPENER + r"pruned\s+(?P<t>.+)$"), "prune_pinch", True),
    (re.compile(_OPENER + r"pinched\s+(?:back\s+)?(?P<t>.+)$"), "prune_pinch", True),
)

_SNOOZE_RE = re.compile(
    r"^snooze\s+(?!(?:for|until|till|me|it|this|that)\b)(?:the\s+)?(?P<t>.+?)"
    r"(?:\s+(?:until|till|for)\s+(?P<when>.+))?$"
)
_STILL_DAMP_RE = re.compile(
    r"^(?P<t>.+?)\s+(?:is\s+|are\s+|looks\s+|seems\s+|feels\s+)?still\s+(?:damp|wet|moist)$"
)

_HARVEST_RE = re.compile(
    r"^(?:(?:i|we)\s+)?(?:just\s+)?"
    r"(?P<verb>log|logged|record|recorded|add|added|picked|harvested)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>pounds?|lbs?|ounces?|oz|bunches?|quarts?|pints?|cups?|pieces?|grams?|kilograms?|kg|g)?\s*"
    r"(?:of\s+)?(?P<t>.+)$"
)

#: Courtesies and "now" words that may trail a report without changing it.
_TRAILING_NOISE = re.compile(
    r"(?:\s+(?:today|just\s+now|right\s+now|now|please|for\s+me|thanks|thank\s+you|already|too|as\s+well))+$"
)
#: Past-tense reports may also say when today it happened.
_TRAILING_PAST = re.compile(r"(?:\s+(?:this\s+(?:morning|afternoon|evening)|earlier(?:\s+today)?))+$")
_LEADING_NOISE = re.compile(r"^(?:the|all|all\s+the|all\s+of\s+the|my|our|a|an)\s+")
#: "the living room plants" / "the kitchen herbs" name a room.
_COLLECTIVE_SUFFIX = re.compile(r"\s+(?:plants?|pots?|herbs|houseplants|beds?)$")
#: "the plants in the bedroom" names a room too.
_COLLECTIVE_PREFIX = re.compile(r"^(?:plants?|pots?|herbs|houseplants)\s+(?:in|on)\s+(?:the\s+)?")

#: Words that make a phrase a question, not a report of something done.
_QUESTION_START = re.compile(
    r"^(?:when|how|should|shall|do|does|did|can|could|would|will|what|which|why|who|where|"
    r"is|are|was|were|any|has|have)\b"
)

#: A report never denies itself: "don't water Monty" is an instruction to Jarvis.
_NEGATION = re.compile(
    r"\b(?:don'?t|do\s+not|didn'?t|did\s+not|doesn'?t|does\s+not|never|not|no|"
    r"can'?t|cannot|won'?t|shouldn'?t|haven'?t|hasn'?t|isn'?t|wasn'?t|aren'?t|stop|skip)\b"
)

#: Reminders and scheduling belong to Jarvis's own tools.
_SCHEDULING = re.compile(r"\b(?:remind|reminds|reminder|reminders|schedule|scheduled|set)\b")

#: A time that is not "now": logging it now would record the wrong moment.
_TIME_WORDS = re.compile(
    r"\b(?:tomorrow|tonight|later|next|every|each|daily|weekly|yesterday|o'?clock|noon|midnight|"
    r"a\.?m\.?|p\.?m\.?|seconds?|minutes?|mins?|hours?|hrs?|weekend|"
    r"mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?)\b"
    r"|\bat\s+\d|\bin\s+(?:a|an|\d+|one|two|three|four|five|ten)\s+(?:minute|hour|day|week)s?\b"
)

#: "snooze the alarm" is Jarvis's own timer, not a plant. Matched on word boundaries
#: ("snapdragon" is not "nap", "calla lily" is not "call", "outdoor" is not "door").
_NOT_A_PLANT = (
    "alarm", "timer", "reminder", "notification", "clock", "wake", "alarm clock",
    "meeting", "calendar", "nap", "music", "radio", "podcast", "volume", "light",
    "lights", "fan", "tv", "television", "call", "message", "email", "door", "lock",
)
_NOT_A_PLANT_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in _NOT_A_PLANT) + r")\b")

#: Shopping and cooking talk is never a harvest.
_NOT_A_HARVEST = re.compile(r"\b(?:shopping|list|grocery|groceries|cart|recipe|store|market|order|buy)\b")
#: What makes a bare "add 2 pounds of tomatoes" a harvest.
_HARVEST_CONTEXT = re.compile(r"\b(?:harvest|harvested|picked|from\s+the\s+garden|from\s+the\s+beds?)\b")
#: Words a harvest target never contains ("herbs to the list" is not a crop).
_HARVEST_TARGET_BAD = re.compile(r"\b(?:to|for|into|in|on|at|with)\b")

#: Said when the Garden service cannot be reached: never a guess, never a crash.
UNAVAILABLE = "The garden is unavailable right now, so I couldn't check or log that."


def is_question(text: str) -> bool:
    """Is this asking something? A question must never log care."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    return lowered.endswith("?") or bool(_QUESTION_START.match(lowered))


def names_something_else(text: str) -> bool:
    """Does the phrase name a non-plant thing (an alarm, a timer, a light)?"""
    return bool(_NOT_A_PLANT_RE.search((text or "").lower()))


def _normalise(text: str) -> str:
    """Lower case, one space, no closing punctuation."""
    value = re.sub(r"\s+", " ", (text or "").strip().lower())
    return value.strip(" .!?,;:")


def _refused(lowered: str) -> bool:
    """A sentence that may talk about plants but is never a care report."""
    return bool(_NEGATION.search(lowered) or _SCHEDULING.search(lowered))


def _clean(text: str) -> str:
    """A phrase reduced to the name inside it."""
    value = _normalise(text)
    previous = None
    while previous != value:
        previous = value
        value = _TRAILING_NOISE.sub("", value).strip()
        value = _LEADING_NOISE.sub("", value).strip()
        value = _COLLECTIVE_PREFIX.sub("", value).strip()
    return _COLLECTIVE_SUFFIX.sub("", value).strip()


def parse_summary(text: str) -> bool:
    """Is this a question about what needs care today?"""
    return bool(_SUMMARY_RE.match(_normalise(text)))


def parse_harvest(text: str) -> dict | None:
    """``{"quantity", "unit", "target"}`` from "log 3 pounds of tomatoes harvested"."""
    lowered = _normalise(text)
    if _NOT_A_HARVEST.search(lowered) or _refused(lowered) or _TIME_WORDS.search(lowered):
        return None  # "add 2 pounds of chicken to my shopping list"
    match = _HARVEST_RE.match(lowered)
    if not match:
        return None
    # "add/log/record 3 pounds of tomatoes" is only ours when the sentence says
    # it was picked: the shopping list and the food log have first claim otherwise.
    if match.group("verb") not in ("picked", "harvested") and not _HARVEST_CONTEXT.search(lowered):
        return None
    raw = re.sub(r"\s+(?:that\s+(?:i|we)\s+)?(?:harvested|picked)(?:\s+(?:today|just\s+now))?$", "", match.group("t"))
    raw = re.sub(r"\s+from\s+the\s+(?:garden|beds?)$", "", raw)
    target = _clean(raw)
    if not target or _HARVEST_TARGET_BAD.search(target):
        return None
    quantity = float(match.group("qty"))
    unit = _UNITS.get((match.group("unit") or "lb").strip(), "lb")
    return {"quantity": quantity, "unit": unit, "target": target}


_COUNT_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def _snooze_days(when: str) -> float | None:
    """Days from "tomorrow" / "3 days" / "a week"; None when no time was said.

    Raises ``ValueError`` for a time that is not in days ("10 minutes", "6 pm"):
    plants are snoozed by the day, so that sentence is someone else's.
    """
    when = when.strip()
    if not when:
        return None
    if when == "tomorrow":
        return 1.0
    match = re.fullmatch(r"(\d+|a|an|one|two|three|four|five|six|seven)\s+(days?|weeks?)", when)
    if not match:
        raise ValueError(when)
    count = float(_COUNT_WORDS.get(match.group(1)) or match.group(1))
    return count * (7 if match.group(2).startswith("week") else 1)


def parse_snooze(text: str) -> dict | None:
    """``{"target", "postpone_days"}`` for "snooze Fernie until tomorrow" / "Monty is still damp"."""
    lowered = _normalise(text)
    if names_something_else(lowered) or _refused(lowered):
        return None  # "snooze the alarm for 10 minutes" is Jarvis's own timer
    match = _STILL_DAMP_RE.match(lowered)
    if match:
        target = _clean(match.group("t"))
        return {"target": target, "postpone_days": 1.0} if target else None
    match = _SNOOZE_RE.match(lowered)
    if not match:
        return None
    try:
        days = _snooze_days(match.group("when") or "")
    except ValueError:
        return None  # "snooze Monty for 10 minutes": not a plant's day
    target = _clean(match.group("t"))
    if not target or _TIME_WORDS.search(target):
        return None
    return {"target": target, "postpone_days": days}


def parse_care(text: str) -> dict | None:
    """``{"action", "target"}`` for a watering / fertilizing / misting report.

    The verb must open the sentence, nothing may negate it, schedule it or put it
    at another time, and what follows the verb must be the whole target.
    """
    lowered = _normalise(text)
    if _refused(lowered) or names_something_else(lowered):
        return None
    for pattern, action, past in _CARE_PATTERNS:
        match = pattern.match(lowered)
        if not match:
            continue
        raw = match.group("t")
        if past:
            raw = _TRAILING_PAST.sub("", raw)
        if _TIME_WORDS.search(raw):
            return None  # "water Monty tomorrow" is a plan, not a report
        target = _clean(raw)
        # "water the plants" alone names no one: not a command we can act on.
        if not target or target in ("plants", "plant", "garden", "everything", "everything else", "herbs", "them", "it"):
            return None
        return {"action": action, "target": target}
    return None


async def try_handle(
    query: str,
    creds: Any,
    *,
    call: GardenCall = call_garden,
) -> str | None:
    """Answer a garden utterance, or None when it is not one.

    Only a sentence the parsers accept *and* the Garden resolves to a plant or
    room by its whole name is answered here. Anything else (no match, a name the
    garden does not know) goes back to the normal chat path. An unreachable
    Garden is said plainly instead of failing the chat turn.
    """
    text = (query or "").strip()
    if not text or len(text) > 300:
        return None
    try:
        return await _handle(text, creds, call)
    except (aiohttp.ClientError, TimeoutError, OSError, RuntimeError) as exc:
        log.warning("[FastPath] garden unavailable: %s: %s", type(exc).__name__, exc)
        return UNAVAILABLE


async def _handle(text: str, creds: Any, call: GardenCall) -> str | None:
    lowered = text.lower()

    # Questions first: a question never logs care. Only a whole "what needs
    # care today?" question is ours; "which herbs need full sun?" is the model's.
    if is_question(text):
        if not parse_summary(lowered):
            return None
        answer = await garden_assist.summarize(creds, call=call)
        if answer.get("status") == "error":
            return str(answer.get("message") or UNAVAILABLE)
        return str(answer.get("message") or "")
    if parse_summary(lowered):  # "garden status", "garden chores today"
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
            whole_phrase=True,
            call=call,
        )
        log.info("[FastPath] garden harvest '%s' -> %s", harvest["target"], answer.get("status"))
        return _spoken(answer)

    # 3. "Snooze Fernie until tomorrow" / "Monty is still damp".
    snooze = parse_snooze(lowered)
    if snooze is not None:
        answer = await garden_assist.log_care(
            creds,
            snooze["target"],
            still_damp=True,
            source="voice",
            postpone_days=snooze["postpone_days"],
            whole_phrase=True,
            call=call,
        )
        log.info("[FastPath] garden snooze '%s' -> %s", snooze["target"], answer.get("status"))
        return _spoken(answer)

    # 4. "I watered Monty" / "watered all the living room plants".
    care = parse_care(lowered)
    if care is not None:
        answer = await garden_assist.log_care(
            creds,
            care["target"],
            action=care["action"],
            source="voice",
            whole_phrase=True,
            call=call,
        )
        log.info("[FastPath] garden care '%s' (%s) -> %s", care["target"], care["action"], answer.get("status"))
        return _spoken(answer)

    return None


def _spoken(answer: dict) -> str | None:
    """The answer to say, or None to hand the sentence back to the chat path.

    A name the garden does not know ("my hair is still wet", "water Audrey") is
    not ours: the model answers it instead of "I couldn't find a plant ...".
    """
    if answer.get("status") == "not_found":
        return None
    if answer.get("status") == "unavailable":
        return UNAVAILABLE
    return str(answer.get("message") or "")
