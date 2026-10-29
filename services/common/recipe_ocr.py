"""Read a recipe out of a vision-OCR result, for an editable draft.

The Recipes page's photo import sends a picture of a recipe page, a recipe
card or handwriting through the same vision OCR as workspaces, Raven and the
Health food label (gateway ``POST /api/recipes/photo`` -> execution
``/execute/ocr`` with ``task="recipe"``). This module turns that transcription
into the fields the import preview edits. Only what the model read is used: a
missing field stays empty and is named in ``missing``; nothing is invented.
The raw transcription and any words the model flagged are kept so a person can
check the draft against the page before saving.

For each word the model flagged, a short list of readings is offered so the
person can accept a fix instead of retyping the line: the model's own
suggestions when it gave any, and otherwise a plain offline guess (a near-miss
kitchen word, or the common measures for a gap it could not read at all).
Suggestions are only ever offered - nothing is applied to the draft here.
"""
from __future__ import annotations

import re
from typing import Any

#: The parts a recipe needs before it is worth saving; the rest are extras.
REQUIRED = ("title", "ingredients", "steps")

#: A plain number, optionally with a minutes/hours unit ("10", "10 min", "1 hour").
_MINUTES_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|hours?|hrs?)?\s*$", re.I)

#: The "which line" note the model sometimes adds to a flagged word
#: ("simmer (line 8)", "simmer - line 8"); the line number is not part of it.
_LINE_NOTE_RE = re.compile(
    r"\s*[\(\[]\s*line\s*\d+[^\)\]]*[\)\]]\s*$|\s*[-\u2013\u2014]\s*line\s*\d+\s*$", re.I
)

#: A gap the model could not read at all ("2 ... vanilla", "1 ___ soda").
_GAP_RE = re.compile(r"\.{2,}|\u2026|_+|\?+")

#: How many readings to offer for one flagged word.
_MAX_SUGGESTIONS = 3

#: Measures offered when the model could not read a measure at all.
_UNIT_FALLBACKS = ("tsp", "tbsp", "cup")

#: Common kitchen words, so an offline near-miss ("1 cup suger") can still be
#: offered a fix without the model. Kept small and recipe-specific on purpose.
_VOCAB = (
    "cup", "cups", "tsp", "tbsp", "teaspoon", "teaspoons", "tablespoon",
    "tablespoons", "ounce", "ounces", "pound", "pounds", "gram", "grams",
    "can", "cans", "package", "packages", "clove", "cloves", "slice", "slices",
    "pinch", "dash", "stick", "sticks", "quart", "pint",
    "sugar", "flour", "butter", "shortening", "egg", "eggs", "milk", "water",
    "salt", "pepper", "vanilla", "soda", "powder", "tartar", "cinnamon",
    "nutmeg", "oil", "onion", "onions", "garlic", "tomato", "tomatoes",
    "beans", "rice", "pasta", "chicken", "beef", "cheese", "potato",
    "potatoes", "oats", "honey", "syrup", "yeast", "cornstarch", "cocoa",
    "chocolate", "cream", "yogurt", "banana", "bananas", "apple", "apples",
    "lemon", "carrot", "carrots", "celery", "mushroom", "mushrooms",
    "spinach", "broccoli", "corn", "peas", "bread", "crumbs", "walnuts",
    "pecans", "raisins",
    "mix", "stir", "bake", "simmer", "boil", "brown", "combine", "whisk",
    "fold", "heat", "preheat", "pour", "roll", "dip", "spread", "cook",
    "chill", "cool", "serve", "beat", "blend", "knead", "sprinkle",
    "season", "drain", "rinse", "chop", "dice", "mince", "grease", "melt",
    "sift", "measure", "transfer", "remove", "cover", "refrigerate",
    "freeze", "rest", "warm",
)
_VOCAB_SET = frozenset(_VOCAB)


def _text(value: Any) -> str:
    """One trimmed string from a model value (str, number, or None)."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


def _string_list(value: Any) -> list[str]:
    """A clean list of non-empty strings from a model value (list or scalar)."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _text(item)
        if text:
            out.append(text)
    return out


def _minutes(value: Any) -> int | None:
    """A positive whole number of minutes, or None when the model left it out."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        unit = ""
    else:
        match = _MINUTES_RE.match(str(value))
        if not match:
            return None
        number = float(match.group(1))
        unit = (match.group(2) or "").lower()
    if unit.startswith(("hour", "hr")):
        number *= 60
    minutes = round(number)
    return minutes if minutes > 0 else None


def clean_uncertain(phrase: str) -> str:
    """A flagged phrase without the model's "which line" note."""
    return _LINE_NOTE_RE.sub("", _text(phrase)).strip()


def _distance(left: str, right: str) -> int:
    """Levenshtein distance, for finding the word a misread was meant to be."""
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, lc in enumerate(left, 1):
        current = [i]
        for j, rc in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (lc != rc)))
        previous = current
    return previous[-1]


def _tolerance(word: str) -> int:
    """How far off a word may be before it is a different word, not a misread."""
    if len(word) <= 4:
        return 1
    if len(word) <= 7:
        return 2
    return 3


def _near_words(word: str) -> list[str]:
    """Kitchen words close enough to be the intended reading of ``word``."""
    low = word.lower()
    close: list[tuple[str, int]] = []
    for candidate in _VOCAB:
        if candidate == low:
            continue
        gap = _distance(low, candidate)
        if 0 < gap <= _tolerance(low):
            close.append((candidate, gap))
    close.sort(key=lambda pair: (pair[1], _VOCAB.index(pair[0])))
    return [candidate for candidate, _ in close]


def _gap_readings(text: str) -> list[str]:
    """A missing measure filled with the common ones, keeping the rest of the line."""
    if not re.search(r"\d", text):
        return []
    if not re.search(r"[A-Za-z]{3,}", _GAP_RE.sub(" ", text)):
        return []
    return [_GAP_RE.sub(unit, text, count=1) for unit in _UNIT_FALLBACKS]


def suggest_readings(phrase: str) -> list[str]:
    """Offline readings for a word the model could not read, best first.

    Used when the model itself offered nothing: a gap ("2 ... vanilla") gets the
    common measures, and a near-miss ("1 cup suger") gets the kitchen word it
    was a letter or two away from. Nothing is returned when there is no honest
    guess, so the person types it rather than accepting a wrong word.
    """
    text = clean_uncertain(phrase)
    if not text:
        return []
    if _GAP_RE.search(text):
        return _gap_readings(text)
    out: list[str] = []
    for token in re.findall(r"[A-Za-z]+", text):
        # A word the kitchen list already knows is not the misread one.
        if token.lower() in _VOCAB_SET:
            continue
        for word in _near_words(token):
            fixed = re.sub(rf"\b{re.escape(token)}\b", word, text, count=1)
            if fixed not in out:
                out.append(fixed)
            if len(out) >= _MAX_SUGGESTIONS:
                return out
    return out


def _model_suggestions(value: Any) -> dict[str, list[str]]:
    """The model's proposed readings, keyed by the words it flagged."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, readings in value.items():
        phrase = _text(key)
        cleaned = _string_list(readings)[:_MAX_SUGGESTIONS]
        if phrase and cleaned:
            out[phrase] = cleaned
    return out


def _readings_for(model: dict[str, list[str]], phrase: str) -> list[str]:
    """The model's readings for ``phrase``, matched loosely on wording."""
    if phrase in model:
        return model[phrase]
    key = clean_uncertain(phrase).lower()
    for candidate, readings in model.items():
        if clean_uncertain(candidate).lower() == key:
            return readings
    return []


def parse(result: dict[str, Any] | None) -> dict[str, Any]:
    """The editable recipe draft behind a photo import.

    ``result`` is the execution ``/execute/ocr`` detail: ``full_text`` is the
    raw transcription, ``recipe`` the model's structured reading,
    ``uncertain`` the words it was unsure about and ``suggestions`` the
    readings it offered for them. A missing recipe object (an old or plain OCR
    result) yields an empty draft with the raw text kept, so the person types
    the rest rather than seeing a guess.
    """
    result = result or {}
    raw = _text(result.get("full_text"))
    recipe = result.get("recipe") if isinstance(result.get("recipe"), dict) else {}

    title = _text(recipe.get("title"))
    ingredients = _string_list(recipe.get("ingredients"))
    steps = _string_list(recipe.get("steps") or recipe.get("instructions"))
    servings = _text(recipe.get("servings") or recipe.get("yield"))
    notes = _text(recipe.get("notes"))
    uncertain = _string_list(result.get("uncertain"))

    model_suggestions = _model_suggestions(result.get("suggestions"))
    suggestions: dict[str, list[str]] = {}
    for phrase in uncertain:
        offered = _readings_for(model_suggestions, phrase)
        suggestions[phrase] = offered or suggest_readings(phrase)

    found = {"title": bool(title), "ingredients": bool(ingredients), "steps": bool(steps)}
    missing = [name for name in REQUIRED if not found[name]]

    return {
        "recipe": {
            "title": title,
            "description": "",
            "ingredients": ingredients,
            "instructions": steps,
            "prep_minutes": _minutes(recipe.get("prep_minutes")),
            "cook_minutes": _minutes(recipe.get("cook_minutes")),
            "total_minutes": _minutes(recipe.get("total_minutes")),
            "yield": servings,
            "image": None,
            "notes": notes,
        },
        "raw_text": raw,
        "uncertain": uncertain,
        "suggestions": suggestions,
        "missing": missing,
    }
