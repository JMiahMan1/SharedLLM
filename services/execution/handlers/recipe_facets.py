"""What the Recipes page browses by, read from a recipe note's own text.

A recipe note is plain markdown (``Tags:``, ``Times:``, ``Source:`` and
``Added by:`` lines, then ``## Ingredients``, ``## Steps``, ``## Nutrition``).
This turns one into the facts a shelf is filtered and sorted on: the kind of
meal, the main protein, how long it takes, how many it serves and its stated
calories. Nothing is guessed that the text does not say, except the meal and
protein, which come from the recipe's own words (its tags first).
"""
from __future__ import annotations

import re

_TAGS_LINE = re.compile(r"^Tags:\s*(.*)$", re.I | re.M)
_TIMES_LINE = re.compile(r"^Times:\s*(.*)$", re.I | re.M)
_OWNER_LINE = re.compile(r"^Added by:\s*(\S.*?)\s*$", re.I | re.M)
_SOURCE_LINE = re.compile(r"^Source:\s*(.*)$", re.I | re.M)
_IMAGE_LINE = re.compile(r"^Image:\s*(\S+)\s*$", re.I | re.M)
_TAG = re.compile(r"#([a-z0-9][a-z0-9-]*)", re.I)
_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)
_CALORIES = re.compile(r"(\d+(?:\.\d+)?)\s*kcal", re.I)
_SERVINGS = re.compile(r"(?:servings?|serves|yield)\D{0,3}(\d{1,3})", re.I)

MEALS = ("breakfast", "main", "soup", "salad", "side", "bread", "dessert", "snack", "drink")
PROTEINS = ("chicken", "beef", "pork", "turkey", "seafood", "lamb", "vegetarian")

#: A tag naming the meal outright wins over any keyword.
_MEAL_TAGS = {
    "breakfast": "breakfast", "brunch": "breakfast", "dinner": "main", "lunch": "main", "main": "main",
    "entree": "main", "soup": "soup", "stew": "soup", "salad": "salad", "side": "side", "sides": "side",
    "bread": "bread", "dessert": "dessert", "baking": "dessert", "snack": "snack", "appetizer": "snack",
    "drink": "drink", "beverage": "drink",
}
#: Checked in order against the title (then the ingredients for dessert/drink cues).
_MEAL_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dessert", re.compile(r"\b(cookies?|cakes?|cupcakes?|brownies?|pies?|cobbler|crisp|pudding|fudge|ice cream|cheesecake|tart|bars|frosting|candy|truffles?)\b", re.I)),
    ("breakfast", re.compile(r"\b(pancakes?|waffles?|oatmeal|granola|omelett?e|frittata|french toast|breakfast|scrambled|quiche|crepes?|muffins?)\b", re.I)),
    ("drink", re.compile(r"\b(lemonade|smoothie|punch|cocoa|latte|tea|coffee|shake|cider)\b", re.I)),
    ("soup", re.compile(r"\b(soup|chili|stew|chowder|bisque|gumbo|pozole|ramen)\b", re.I)),
    ("salad", re.compile(r"\bsalad\b", re.I)),
    ("bread", re.compile(r"\b(bread|rolls?|biscuits?|buns?|focaccia|bagels?|scones?|cornbread)\b", re.I)),
    ("snack", re.compile(r"\b(dip|salsa|guacamole|hummus|popcorn|trail mix|snack|appetizer|wings)\b", re.I)),
    ("side", re.compile(r"\b(side|mashed|roasted (?:potatoes|vegetables|carrots|broccoli)|coleslaw|green beans|rice pilaf)\b", re.I)),
)
#: Broth, stock and bouillon flavour a dish without making it a meat dish.
_NOT_PROTEIN = re.compile(r"\b(?:chicken|beef|pork|turkey|fish|vegetable)\s+(?:broth|stock|bouillon|base|gravy\s+mix)\b", re.I)
_PROTEIN_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("chicken", re.compile(r"\bchicken\b", re.I)),
    ("beef", re.compile(r"\b(beef|steak|brisket|hamburger|chuck|sirloin|meatballs?|pot roast|veal)\b", re.I)),
    ("pork", re.compile(r"\b(pork|bacon|ham|sausages?|prosciutto|chorizo|pepperoni|kielbasa|carnitas|ribs)\b", re.I)),
    ("turkey", re.compile(r"\bturkey\b", re.I)),
    ("seafood", re.compile(r"\b(fish|salmon|tuna|shrimp|prawns?|cod|tilapia|crab|lobster|scallops?|halibut|mahi|clams?|mussels?|trout|anchov(?:y|ies)|catfish)\b", re.I)),
    ("lamb", re.compile(r"\b(lamb|mutton)\b", re.I)),
)


def sections(body: str) -> dict[str, str]:
    """``## Heading`` -> the text under it (lowercased heading)."""
    out: dict[str, str] = {}
    marks = list(_SECTION.finditer(body or ""))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        out[mark.group(1).strip().lower()] = body[mark.end():end]
    return out


def ingredient_lines(body: str) -> list[str]:
    text = sections(body).get("ingredients", "")
    lines = []
    for line in text.splitlines():
        clean = re.sub(r"^\s*(?:[-*]\s*(?:\[[ xX]\]\s*)?|\d+[.)]\s*)", "", line).strip()
        if clean:
            lines.append(clean)
    return lines


def _minutes(times: str, label: str) -> int | None:
    m = re.search(rf"\b{label}\s+(\d+)\s*min", times, re.I)
    if m:
        return int(m.group(1))
    m = re.search(rf"\b{label}\s+(\d+)\s*h(?:ours?|r)?(?:\s+(\d+)\s*min)?", times, re.I)
    return int(m.group(1)) * 60 + int(m.group(2) or 0) if m else None


def meal_of(title: str, tags: list[str], ingredients: list[str]) -> str:
    for tag in tags:
        if tag in _MEAL_TAGS:
            return _MEAL_TAGS[tag]
    for meal, pattern in _MEAL_WORDS:
        if pattern.search(title):
            return meal
    return "main"


def proteins_of(title: str, tags: list[str], ingredients: list[str]) -> list[str]:
    if "vegetarian" in tags or "vegan" in tags:
        return ["vegetarian"]
    text = _NOT_PROTEIN.sub(" ", " \n".join([title, *ingredients]))
    found = [name for name, pattern in _PROTEIN_WORDS if pattern.search(text)]
    return found or ["vegetarian"]


def facets(title: str, body: str) -> dict:
    """The browseable facts of one recipe note's body (stored header already removed)."""
    text = body or ""
    tags_line = _TAGS_LINE.search(text)
    tags = [t.lower() for t in _TAG.findall(tags_line.group(1))] if tags_line else []
    times_line = _TIMES_LINE.search(text)
    times = times_line.group(1) if times_line else ""
    prep, cook, total = _minutes(times, "prep"), _minutes(times, "cook"), _minutes(times, "total")
    if total is None and (prep or cook):
        total = (prep or 0) + (cook or 0)
    servings_match = _SERVINGS.search(times) or _SERVINGS.search(text)
    nutrition = sections(text).get("nutrition", "")
    calories = _CALORIES.search(nutrition)
    owner = _OWNER_LINE.search(text)
    source = _SOURCE_LINE.search(text)
    image = _IMAGE_LINE.search(text)
    ingredients = ingredient_lines(text)
    level = next((t.split("-", 1)[1] for t in tags if re.fullmatch(r"level-\d", t)), None)
    return {
        "tags": tags,
        "meal": meal_of(title, tags, ingredients),
        "proteins": proteins_of(title, tags, ingredients),
        "total_minutes": total,
        "servings": int(servings_match.group(1)) if servings_match else None,
        "calories": round(float(calories.group(1))) if calories else None,
        "ingredients": len(ingredients),
        "level": int(level) if level else None,
        "owner": owner.group(1).strip().lower() if owner else None,
        "source": source.group(1).strip() if source else "",
        # A relative link is a photo kept beside the note; a URL is not pulled yet.
        "image": image.group(1) if image else None,
    }
