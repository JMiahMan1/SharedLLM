"""Read a nutrition-facts label out of OCR text, for an editable draft.

The Health hub's photo logging sends a picture of a food label through the
vision OCR (execution's ``/execute/ocr``), and this turns the transcription
into the fields a food entry needs. It only reads numbers that are printed:
a value it cannot find is ``None`` and named in ``missing``, never estimated,
and the result is always a draft the person edits before anything is saved.
"""
from __future__ import annotations

import re
from typing import Any

_NUMBER = r"(\d+(?:[.,]\d+)?)"

# field -> patterns tried in order; the first match wins. "Total" forms come
# first so "Saturated Fat 3g" or "Added Sugars 4g" never stand in for totals.
_FIELDS: dict[str, list[str]] = {
    "calories": [rf"\bcalories\b[^\d\n]{{0,12}}{_NUMBER}", rf"{_NUMBER}\s*kcal\b"],
    "fat_g": [rf"\btotal\s+fat\b[^\d\n]{{0,6}}{_NUMBER}\s*g", rf"(?<!saturated )(?<!trans )\bfat\b[^\d\n]{{0,6}}{_NUMBER}\s*g"],
    "carbs_g": [
        rf"\btotal\s+carbohydrates?\b[^\d\n]{{0,6}}{_NUMBER}\s*g",
        rf"\bcarbohydrates?\b[^\d\n]{{0,6}}{_NUMBER}\s*g",
        rf"\btotal\s+carbs?\b[^\d\n]{{0,6}}{_NUMBER}\s*g",
    ],
    "protein_g": [rf"\bprotein\b[^\d\n]{{0,6}}{_NUMBER}\s*g"],
    "fiber_g": [rf"\b(?:dietary\s+)?fib(?:er|re)\b[^\d\n]{{0,6}}{_NUMBER}\s*g"],
    "sugar_g": [rf"\btotal\s+sugars?\b[^\d\n]{{0,6}}{_NUMBER}\s*g", rf"(?<!added )\bsugars?\b[^\d\n]{{0,6}}{_NUMBER}\s*g"],
    "sodium_mg": [rf"\bsodium\b[^\d\n]{{0,6}}{_NUMBER}\s*mg"],
}
#: Calories, protein, carbs and fat make a usable entry; the rest are extras.
REQUIRED = ("calories", "protein_g", "carbs_g", "fat_g")

_SERVING = re.compile(r"\bserving\s+size\b[:\s]*([^\n(]*?)\s*\(\s*" + _NUMBER + r"\s*(g|ml)\s*\)", re.IGNORECASE)
_SERVING_GRAMS_ONLY = re.compile(r"\bserving\s+size\b[:\s]*" + _NUMBER + r"\s*(g|ml)\b", re.IGNORECASE)


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def parse(text: str) -> dict[str, Any]:
    """Draft values (per serving as printed), which were found, which are missing."""
    body = (text or "").lower()
    values: dict[str, float | None] = {}
    for field, patterns in _FIELDS.items():
        values[field] = None
        for pattern in patterns:
            match = re.search(pattern, body)
            if match:
                values[field] = _number(match.group(1))
                break

    serving_label: str | None = None
    serving_grams: float | None = None
    match = _SERVING.search(text or "")
    if match:
        serving_label = match.group(1).strip() or None
        serving_grams = _number(match.group(2))
    else:
        grams_only = _SERVING_GRAMS_ONLY.search(text or "")
        if grams_only:
            serving_grams = _number(grams_only.group(1))

    found = [field for field, value in values.items() if value is not None]
    return {
        "draft": {**values, "serving_label": serving_label, "serving_grams": serving_grams},
        "found": found,
        "missing": [field for field in REQUIRED if values[field] is None],
        "per_100g": "per 100" in body and serving_grams is None,
    }
