"""Estimate a recipe's nutrition from the USDA seed (Phase 10).

When an imported or edited recipe states no nutrition of its own, this walks
its ingredient lines and matches each one against the health hub's USDA seed
(``food_seed.FoodSeed``): the FTS match finds the food, and the seed's FNDDS
household portions turn a measure into grams. The result is an **estimate**,
never the recipe's own numbers, and it is clearly labelled as such.

Nothing is guessed. An ingredient with no seed match, no household portion for
its unit, or a food whose calories the seed does not report is ``unknown`` and
is listed as such - it contributes nothing to the total. A total built from
only some of the ingredients says so (``complete`` is false).
"""
from __future__ import annotations

import re

from . import food_seed

#: Ingredient lines one estimate will walk; a huge paste is capped rather than
#: allowed to run hundreds of seed searches.
MAX_INGREDIENTS = 60

_UNICODE_FRACTIONS = {
    "\u00bd": 0.5,
    "\u2153": 1 / 3,
    "\u2154": 2 / 3,
    "\u00bc": 0.25,
    "\u00be": 0.75,
    "\u2155": 0.2,
    "\u2156": 0.4,
    "\u2157": 0.6,
    "\u2158": 0.8,
    "\u2159": 1 / 6,
    "\u215a": 5 / 6,
    "\u215b": 0.125,
    "\u215c": 0.375,
    "\u215d": 0.625,
    "\u215e": 0.875,
}

# A quantity the seed cannot weigh directly; grams come from the seed's
# household portions, so these units are matched against a portion label.
_MASS_UNITS = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "kilogram": 1000.0,
    "kilograms": 1000.0,
    "mg": 0.001,
    "milligram": 0.001,
    "milligrams": 0.001,
    "oz": 28.3495,
    "ounce": 28.3495,
    "ounces": 28.3495,
    "lb": 453.592,
    "lbs": 453.592,
    "pound": 453.592,
    "pounds": 453.592,
}

# Volume and count units, matched against the seed's household portion labels
# ("1 cup, chopped", "1 clove", "1 large"). Aliases map onto one key.
_UNIT_ALIASES = {
    "cup": {"cup", "cups"},
    "tbsp": {"tbsp", "tbs", "tablespoon", "tablespoons"},
    "tsp": {"tsp", "teaspoon", "teaspoons"},
    "ml": {"ml", "milliliter", "milliliters", "millilitre", "millilitres"},
    "l": {"l", "liter", "liters", "litre", "litres"},
    "pint": {"pint", "pints"},
    "quart": {"quart", "quarts"},
    "gallon": {"gallon", "gallons"},
    "clove": {"clove", "cloves"},
    "slice": {"slice", "slices"},
    "can": {"can", "cans"},
    "package": {"package", "packages", "pkg", "pkt"},
    "stick": {"stick", "sticks"},
    "bunch": {"bunch", "bunches"},
    "head": {"head", "heads"},
    "stalk": {"stalk", "stalks"},
    "piece": {"piece", "pieces"},
    "fillet": {"fillet", "fillets"},
    "large": {"large"},
    "medium": {"medium"},
    "small": {"small"},
    "whole": {"whole"},
    "item": {"item", "items"},
    "serving": {"serving", "servings"},
}

_UNIT_LOOKUP: dict[str, str] = {}
for _key, _aliases in _UNIT_ALIASES.items():
    for _alias in _aliases:
        _UNIT_LOOKUP[_alias] = _key

_QUANTITY_RE = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)")
_WORD_RE = re.compile(r"^([A-Za-z]+)")
_PAREN_RE = re.compile(r"\([^)]*\)")
_LEADING_QTY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")

_NUTRIENT_FIELDS = ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg")
_CORE_FIELDS = ("calories", "protein_g", "carbs_g", "fat_g")


def _normalize_fractions(text: str) -> str:
    """``1½`` and ``½`` become ``1.5`` and ``0.5`` so one quantity regex works."""
    for char, value in _UNICODE_FRACTIONS.items():
        text = re.sub(
            rf"(\d)\s*{re.escape(char)}",
            lambda match, value=value: str(int(match.group(1)) + value),
            text,
        )
        text = text.replace(char, f" {value} ")
    return text


def parse_ingredient(text: str) -> dict:
    """Split an ingredient line into a quantity, a unit and a searchable name.

    ``"2 lb chicken"`` -> ``{"quantity": 2.0, "unit": "lb", "name": "chicken"}``;
    a line with no readable quantity keeps ``quantity`` ``None`` (which the
    caller treats as unknown rather than assuming one).
    """
    raw = (text or "").strip()
    # Drop parenthetical asides ("1 (14 oz) can tomatoes" -> "1 can tomatoes").
    cleaned = _PAREN_RE.sub(" ", raw).strip()
    cleaned = _normalize_fractions(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    quantity: float | None = None
    match = _QUANTITY_RE.match(cleaned)
    if match:
        token = match.group(1)
        try:
            if "/" in token:
                parts = token.split()
                whole = float(parts[0]) if len(parts) == 2 else 0.0
                num, den = (parts[-1]).split("/")
                quantity = whole + float(num) / float(den)
            else:
                quantity = float(token)
        except (ValueError, ZeroDivisionError):
            quantity = None
        cleaned = cleaned[match.end():].strip()

    unit = ""
    word = _WORD_RE.match(cleaned)
    if word:
        candidate = word.group(1).lower()
        if candidate in _MASS_UNITS:
            unit = candidate
            cleaned = cleaned[word.end():].strip()
        elif candidate in _UNIT_LOOKUP:
            unit = _UNIT_LOOKUP[candidate]
            cleaned = cleaned[word.end():].strip()

    # A leading "of" ("1 cup of rice") is noise, not part of the name.
    cleaned = re.sub(r"^of\s+", "", cleaned, flags=re.I).strip()
    return {"quantity": quantity, "unit": unit, "name": cleaned, "raw": raw}


def _portion_quantity(label: str) -> float:
    match = _LEADING_QTY_RE.match(label)
    if not match:
        return 1.0
    value = float(match.group(1))
    return value if value > 0 else 1.0


def _grams_for(parsed: dict, hit: dict) -> float | None:
    """Grams for one ingredient, or ``None`` when the seed cannot say.

    A mass unit converts directly; anything else needs a household portion in
    the seed whose label names that unit, so a cup or a clove is weighed the
    way USDA weighed it. No portion, no number.
    """
    quantity = parsed["quantity"]
    unit = parsed["unit"]
    if quantity is None or not unit:
        return None
    if unit in _MASS_UNITS:
        return quantity * _MASS_UNITS[unit]

    key = _UNIT_LOOKUP.get(unit, unit)
    aliases = _UNIT_ALIASES.get(key, {unit})
    for portion in hit.get("portions") or []:
        label = (portion.get("label") or "").lower()
        if any(re.search(rf"\b{re.escape(alias)}\b", label) for alias in aliases):
            grams = portion.get("grams")
            if grams:
                return quantity * (float(grams) / _portion_quantity(portion["label"]))
    return None


def _search_terms(name: str) -> list[str]:
    """The name to search, then a shorter fallback before the first comma.

    "chicken breast, diced" searches as itself first (it may be a seed food);
    if that finds nothing, "chicken breast" is tried, which is how USDA names
    its foods. Nothing is matched loosely beyond that.
    """
    terms = [name.strip()]
    head = name.split(",", 1)[0].strip()
    if head and head != terms[0]:
        terms.append(head)
    return [term for term in terms if term]


def _match(hit: dict) -> dict | None:
    """The nutrient block for a hit, or ``None`` when it reports no calories."""
    per_100g = hit.get("per_100g") or {}
    if per_100g.get("calories") is None:
        return None
    return per_100g


def _scaled(per_100g: dict, grams: float) -> dict:
    factor = grams / 100.0
    return {
        field: (None if per_100g.get(field) is None else round(float(per_100g[field]) * factor, 2))
        for field in _NUTRIENT_FIELDS
    }


def _unknown(text: str, reason: str) -> dict:
    return {"text": text, "status": "unknown", "reason": reason}


def estimate_ingredients(ingredients: list[str], seed: food_seed.FoodSeed) -> dict:
    """Match each ingredient against the seed and total what can be weighed.

    The result is an estimate by construction: ``estimate`` is true, the source
    is named, and every ingredient the seed cannot match is listed as
    ``unknown`` with the reason. ``totals`` covers only the matched ingredients.
    """
    lines = [line.strip() for line in (ingredients or []) if line and line.strip()][:MAX_INGREDIENTS]
    present = seed.present
    if not present:
        return {
            "estimate": True,
            "source": "usda_fdc",
            "seed": {"present": False},
            "ingredients": [
                _unknown(line, "USDA food data is not installed on this server") for line in lines
            ],
            "matched": 0,
            "unknown": len(lines),
            "totals": None,
            "complete": False,
        }

    out: list[dict] = []
    totals = {field: 0.0 for field in _NUTRIENT_FIELDS}
    contributed: set[str] = set()
    matched = 0
    complete = True
    for line in lines:
        parsed = parse_ingredient(line)
        if parsed["quantity"] is None:
            out.append(_unknown(line, "No quantity given"))
            continue
        if not parsed["unit"]:
            out.append(_unknown(line, "No unit given"))
            continue
        hit = None
        for term in _search_terms(parsed["name"]):
            hits = seed.search(term, 3)
            hit = next((candidate for candidate in hits if _match(candidate)), None)
            if hit:
                break
        if not hit:
            out.append(_unknown(line, f"No USDA match for “{parsed['name'] or line}”"))
            continue
        grams = _grams_for(parsed, hit)
        if grams is None:
            out.append(_unknown(line, f"No USDA portion for “{parsed['unit']}”"))
            continue
        nutrients = _scaled(_match(hit) or {}, grams)
        out.append(
            {
                "text": line,
                "status": "matched",
                "food": hit.get("name") or "",
                "code": hit.get("code") or "",
                "grams": round(grams, 1),
                "nutrients": nutrients,
            }
        )
        matched += 1
        for field in _NUTRIENT_FIELDS:
            value = nutrients.get(field)
            if value is None:
                if field in _CORE_FIELDS:
                    complete = False
            else:
                totals[field] += value
                contributed.add(field)

    total_out = None
    if matched:
        total_out = {
            field: (
                round(totals[field], 1)
                if field not in ("fiber_g", "sugar_g", "sodium_mg") or field in contributed
                else None
            )
            for field in _NUTRIENT_FIELDS
        }
    return {
        "estimate": True,
        "source": "usda_fdc",
        "seed": {"present": True},
        "ingredients": out,
        "matched": matched,
        "unknown": len(lines) - matched,
        "totals": total_out,
        "complete": complete and matched > 0,
    }
