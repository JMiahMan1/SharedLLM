# services/execution/handlers/meal_plan.py
"""Plan a dinner into the week's meal-plan note from a spoken request.

The plan is plain markdown in Nextcloud at ``Recipes/Meal plan.md``: a
``#meal-plan`` tag and one checklist line per day (``- [ ] Tue · Chicken
Chili``). The Recipes page owns that format (``services/ui/src/lib/mealPlan.ts``
-> ``parseMealPlan`` / ``buildMealPlan``); this handler mirrors it line for line
so the assistant writes exactly what the page reads back.

The handler owns no storage of its own: it reads and rewrites the note through
the ordinary note API (``handlers/note.py``), so it inherits the same
credentials, header handling and failure text. A read that fails for any reason
other than "the note is not there yet" is reported, never flattened into an
empty plan that would silently drop the week's dinners.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from services.execution.schemas import ExecutionResult, MealPlanRequest, NoteRequest

from .note import handle_note, note_body

log = logging.getLogger("execution.meal_plan")

PLAN_TITLE = "Meal plan"
PLAN_PATH = "Recipes/Meal plan.md"
RECIPES_DIR = "Recipes"
#: The plan's seven slots, Monday first - the same order ``mealPlan.ts`` uses.
PLAN_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: Every spelling a spoken request may use for a plan day.
_WEEKDAY_ALIASES = {
    "monday": "Mon", "mon": "Mon",
    "tuesday": "Tue", "tue": "Tue", "tues": "Tue",
    "wednesday": "Wed", "wed": "Wed", "weds": "Wed",
    "thursday": "Thu", "thu": "Thu", "thur": "Thu", "thurs": "Thu",
    "friday": "Fri", "fri": "Fri",
    "saturday": "Sat", "sat": "Sat",
    "sunday": "Sun", "sun": "Sun",
}
#: "today" / "tonight" / "tomorrow" resolved against the local date.
_RELATIVE_DAYS = {"today": 0, "tonight": 0, "tomorrow": 1}

#: A stored day line: ``- [ ] Tue · Chicken Chili`` (the ``·`` and title are optional).
_DAY_LINE = re.compile(
    r"^-\s*\[([ xX])\]\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:\s*·\s*(.*?))?\s*$",
    re.IGNORECASE,
)

#: A day word anywhere in a sentence, longest spellings first so "thursday"
#: is not clipped to "thu".
_DAY_WORD = re.compile(
    r"\b(?:" + "|".join(sorted(_WEEKDAY_ALIASES, key=len, reverse=True)) + r"|today|tonight|tomorrow)\b",
    re.IGNORECASE,
)
#: The verb a spoken request opens with ("add a …", "plan …", "put …").
_LEAD = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
    r"(?:add|plan|put|schedule|make|set\s+up|throw)\s+",
    re.IGNORECASE,
)
#: A preposition left dangling once the day word is removed ("… to Tuesday").
_TRAILING_PREPOSITION = re.compile(r"(?:^|\s+)(?:for|to|on|this|next|the)\s*$", re.IGNORECASE)
#: An article the phrase opens with ("a slow-cooker meal").
_ARTICLE = re.compile(r"^\s*(?:a|an|the|some|my|our)\s+", re.IGNORECASE)
#: A generic noun the phrase ends with ("… meal", "… dinner").
_TAIL_NOUN = re.compile(r"\s+(?:meal|dinner|dish|recipe|menu|please)\s*$", re.IGNORECASE)
_EDGES = re.compile(r"^[\s,.;:!?]+|[\s,.;:!?]+$")


def plan_day_of(day: date) -> str:
    """The plan slot for a local date (the plan week starts on Monday).

    The same Monday-based mapping as ``planDayOf`` in ``mealPlan.ts``: the plan
    stores weekdays, so "next Tuesday" and "Tuesday" land on the same slot.
    """
    return PLAN_DAYS[day.weekday()]


def normalize_plan_day(value: str | None) -> str | None:
    """A day name ("Tue", "Tuesday") as the plan's three-letter slot, or None."""
    word = (value or "").strip().lower().rstrip(".")
    return _WEEKDAY_ALIASES.get(word)


def resolve_plan_day(text: str | None, today: date | None = None) -> str | None:
    """The plan day a sentence names, or None when it names none.

    A weekday name wins over "today"/"tomorrow"; the plan is weekday-keyed, so
    the day is the slot, not a date.
    """
    haystack = text or ""
    for word, slot in _WEEKDAY_ALIASES.items():
        if re.search(rf"\b{re.escape(word)}\b", haystack, re.IGNORECASE):
            return slot
    for word, offset in _RELATIVE_DAYS.items():
        if re.search(rf"\b{word}\b", haystack, re.IGNORECASE):
            return plan_day_of((today or date.today()) + timedelta(days=offset))
    return None


def parse_recipe_name(text: str | None) -> str:
    """The recipe phrase a sentence names, with the day and filler stripped.

    "plan pizza for Friday" -> "pizza"; "add a slow-cooker meal to Tuesday" ->
    "slow-cooker". It is a name, never a lookup: matching it against the shelf
    is ``match_recipe_title``'s job.
    """
    cleaned = _DAY_WORD.sub(" ", text or "")
    cleaned = _LEAD.sub("", cleaned)
    # "… to Tuesday" leaves a dangling preposition, and "… on the menu" leaves
    # a phrase; strip either end, repeatedly, until neither applies.
    while True:
        before = cleaned
        cleaned = _TRAILING_PREPOSITION.sub("", cleaned)
        cleaned = _TAIL_NOUN.sub("", cleaned)
        if cleaned == before:
            break
    cleaned = _ARTICLE.sub("", cleaned)
    return _EDGES.sub("", cleaned).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _contains_contiguous(haystack: list[str], needle: list[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(haystack[i:i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))


def match_recipe_title(name: str, titles: list[str]) -> str:
    """The shelf note title a spoken name best names, else the name as typed.

    An exact (case-insensitive) title wins. Otherwise the shortest note whose
    title contains the spoken words in order is used - "slow-cooker" finds
    "Slow Cooker Chicken Chili". An ambiguous fragment resolves to one title
    deterministically and the reply names it, so a wrong pick is visible and
    correctable. The note is never created: the plan editor only ever picks
    from the shelf, so planning a title does not invent a recipe.
    """
    wanted = (name or "").strip()
    if not wanted:
        return ""
    lowered = wanted.lower()
    for title in titles:
        if title.strip().lower() == lowered:
            return title
    needle = _tokens(lowered)
    if needle:
        matches = [title for title in titles if _contains_contiguous(_tokens(title), needle)]
        if matches:
            return sorted(matches, key=lambda title: (len(title), title.lower()))[0]
    return wanted


def _day_line(day: str, title: str | None, cooked: bool) -> str:
    mark = "x" if cooked else " "
    return f"- [{mark}] {day}" + (f" · {title}" if title else "")


def apply_meal(body: str | None, day: str, title: str, cooked: bool | None = None) -> str:
    """Replace (or append) one day's line, leaving every other byte alone.

    The plan editor rewrites all seven lines; the assistant touches only the
    day it was asked about, so a plan edited elsewhere keeps its text. A day
    that is not there yet is inserted in ``PLAN_DAYS`` order among the lines
    that are. ``cooked`` is preserved when the day already existed.
    """
    lines = (body or "").split("\n")
    day = normalize_plan_day(day) or day
    order = {slot: index for index, slot in enumerate(PLAN_DAYS)}
    for index, line in enumerate(lines):
        match = _DAY_LINE.match(line)
        if match and match.group(2).lower() == day.lower():
            already = match.group(1).lower() == "x"
            lines[index] = _day_line(day, title, already if cooked is None else cooked)
            return "\n".join(lines)

    new_line = _day_line(day, title, bool(cooked))
    at = None
    for index, line in enumerate(lines):
        match = _DAY_LINE.match(line)
        slot = _WEEKDAY_ALIASES.get(match.group(2).lower()) if match else None
        if slot and order.get(slot, 0) > order.get(day, 0):
            at = index
            break
    if at is None:
        last = max((index for index, line in enumerate(lines) if _DAY_LINE.match(line)), default=None)
        at = last + 1 if last is not None else len(lines)
    lines.insert(at, new_line)
    return "\n".join(lines)


def _note_missing(message: str | None) -> bool:
    """A note-read failure that only means the note is not there yet."""
    return "not found" in (message or "").lower()


async def _plan_body(req: MealPlanRequest) -> tuple[str | None, ExecutionResult | None]:
    """The stored plan body, or a failure result to return instead.

    A missing note is a first run (an empty body). Every other failure - a
    timeout, a refusal - is handed back so the caller reports the server's own
    words rather than planning over a plan it could not read.
    """
    read = await handle_note(NoteRequest(
        user_context=req.user_context,
        action="read",
        storage="nextcloud",
        title=PLAN_TITLE,
        path=PLAN_PATH,
    ))
    if read.status == "SUCCESS":
        return note_body(read.message), None
    if _note_missing(read.message):
        return None, None
    return None, ExecutionResult.fail(read.message, service="meal_plan")


async def _recipe_title(req: MealPlanRequest, name: str) -> str:
    """The shelf title that best names the recipe, else the name as typed.

    A listing that fails leaves the name as typed: the plan still works and the
    reply names the title, so nothing is silently dropped.
    """
    listing = await handle_note(NoteRequest(
        user_context=req.user_context,
        action="list",
        storage="nextcloud",
        directories=[RECIPES_DIR],
    ))
    titles: list[str] = []
    if listing.status == "SUCCESS":
        notes = (listing.detail or {}).get("notes") or []
        titles = [str(note.get("title")) for note in notes if note.get("title")]
    return match_recipe_title(name, titles)


async def handle_meal_plan(req: MealPlanRequest) -> ExecutionResult:
    """Plan one dinner into the week's meal plan."""
    service = "meal_plan"
    day = normalize_plan_day(req.day) or resolve_plan_day(req.text)
    if not day:
        return ExecutionResult.fail(
            'Which day should I plan that for? Try "add chili to Tuesday".',
            service=service,
        )

    name = (req.recipe or "").strip() or parse_recipe_name(req.text)
    if not name:
        return ExecutionResult.fail(f"What should I plan for {day}?", service=service)

    body, failure = await _plan_body(req)
    if failure is not None:
        return failure
    title = await _recipe_title(req, name)

    # An empty note seeds the plan's own header so the appended line reads the
    # way the page writes it.
    if not (body or "").strip():
        body = "#meal-plan\n"
    updated = apply_meal(body, day, title)

    write = await handle_note(NoteRequest(
        user_context=req.user_context,
        action="write",
        storage="nextcloud",
        title=PLAN_TITLE,
        category=RECIPES_DIR,
        path=PLAN_PATH,
        content=updated,
    ))
    if write.status != "SUCCESS":
        return ExecutionResult.fail(write.message, service=service)

    return ExecutionResult.ok(
        f"Planned {title} for {day}.",
        service=service,
        detail={"day": day, "recipe": title, "path": PLAN_PATH},
    )
