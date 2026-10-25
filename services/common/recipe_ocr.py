"""Read a recipe out of a vision-OCR result, for an editable draft.

The Recipes page's photo import sends a picture of a recipe page, a recipe
card or handwriting through the same vision OCR as workspaces, Raven and the
Health food label (gateway ``POST /api/recipes/photo`` -> execution
``/execute/ocr`` with ``task="recipe"``). This module turns that transcription
into the fields the import preview edits. Only what the model read is used: a
missing field stays empty and is named in ``missing``; nothing is invented.
The raw transcription and any words the model flagged are kept so a person can
check the draft against the page before saving.
"""
from __future__ import annotations

import re
from typing import Any

#: The parts a recipe needs before it is worth saving; the rest are extras.
REQUIRED = ("title", "ingredients", "steps")

#: A plain number, optionally with a minutes/hours unit ("10", "10 min", "1 hour").
_MINUTES_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|hours?|hrs?)?\s*$", re.I)


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


def parse(result: dict[str, Any] | None) -> dict[str, Any]:
    """The editable recipe draft behind a photo import.

    ``result`` is the execution ``/execute/ocr`` detail: ``full_text`` is the
    raw transcription, ``recipe`` the model's structured reading and
    ``uncertain`` the words it was unsure about. A missing recipe object (an
    old or plain OCR result) yields an empty draft with the raw text kept, so
    the person types the rest rather than seeing a guess.
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
        "missing": missing,
    }
