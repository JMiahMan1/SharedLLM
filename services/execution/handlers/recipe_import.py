# services/execution/handlers/recipe_import.py
"""Recipe import: read a pasted link's schema.org recipe.

Most recipe sites publish their recipe as JSON-LD (``<script
type="application/ld+json">`` with an ``@type: Recipe`` node) because Google's
rich results demand it. That block carries everything the family shelf needs —
ingredients with units, ordered steps, times, yield, an image — so the importer
reads the page once and never has to scrape HTML. Sites that do not publish it
get a plain "cannot read this page" answer instead of a guess.
"""
import html
import json
import logging
import re
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from services.execution.handlers.recipe_seed import get_seed
from services.execution.http_client import get_session, host_of
from services.execution.schemas import ExecutionResult, RecipeRequest

log = logging.getLogger("execution.recipe_import")

_URL_RE = re.compile(r"^https?://", re.I)
_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?)?$",
    re.I,
)
_MAX_BYTES = 1_500_000
_TIMEOUT = aiohttp.ClientTimeout(total=25.0, connect=10.0)
_UA = "Jarvis-OS/1.0 (family recipe import; self-hosted)"


def _text(value: Any) -> str:
    """Flatten a schema.org string-ish value (str, list, or {name/text})."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(part for part in (_text(item) for item in value) if part)
    if isinstance(value, dict):
        value = value.get("name") or value.get("text") or ""
    return html.unescape(str(value)).strip()


def _minutes(value: Any) -> int | None:
    """ISO-8601 duration (PT1H30M) to minutes, or None when unreadable."""
    text = _text(value)
    if not text:
        return None
    match = _DURATION_RE.match(text)
    if not match:
        return None
    total = (
        int(match.group("days") or 0) * 1440
        + int(match.group("hours") or 0) * 60
        + int(match.group("minutes") or 0)
    )
    return total or None


def _steps(value: Any) -> list[str]:
    """Ordered step texts from recipeInstructions (steps, sections, strings)."""
    out: list[str] = []

    def walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, str):
            text = _text(node)
            if text:
                out.append(text)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, dict):
            # HowToSection wraps HowToSteps; HowToStep carries text.
            if node.get("itemListElement"):
                walk(node.get("itemListElement"))
                return
            walk(node.get("text") or node.get("name"))

    walk(value)
    return out


def _image(value: Any) -> str | None:
    if isinstance(value, list):
        return _image(value[0]) if value else None
    if isinstance(value, dict):
        return _text(value.get("url")) or None
    return _text(value) or None


def _find_recipe(data: Any) -> dict | None:
    """Find the Recipe node in a JSON-LD blob (arrays, @graph, nesting)."""
    if isinstance(data, list):
        for item in data:
            found = _find_recipe(item)
            if found:
                return found
        return None
    if isinstance(data, dict):
        node_type = data.get("@type")
        if node_type == "Recipe" or (
            isinstance(node_type, list) and "Recipe" in node_type
        ):
            return data
        for key in ("@graph", "mainEntity", "itemListElement"):
            if data.get(key) is not None:
                found = _find_recipe(data.get(key))
                if found:
                    return found
    return None


def _yield_text(value: Any) -> str:
    """Sites often send yield as ["12", "12 cookies"]; prefer the fuller one."""
    if isinstance(value, list):
        parts = [_text(item) for item in value if _text(item)]
        if len(parts) >= 2 and parts[-1].startswith(parts[0]):
            return parts[-1]
        seen: list[str] = []
        for part in parts:
            if part not in seen:
                seen.append(part)
        return " ".join(seen)
    return _text(value)


def parse_recipe(recipe: dict, source_url: str) -> dict:
    """Normalize a schema.org Recipe node into the shape the app saves."""
    ingredients = [
        text for text in (_text(item) for item in recipe.get("recipeIngredient") or []) if text
    ]
    return {
        "title": _text(recipe.get("name")),
        "description": _text(recipe.get("description")),
        "ingredients": ingredients,
        "instructions": _steps(recipe.get("recipeInstructions")),
        "prep_minutes": _minutes(recipe.get("prepTime")),
        "cook_minutes": _minutes(recipe.get("cookTime")),
        "total_minutes": _minutes(recipe.get("totalTime")),
        "yield": _yield_text(recipe.get("recipeYield")),
        "image": _image(recipe.get("image")),
        "source_url": source_url,
        "site": urlparse(source_url).netloc,
    }


def extract_recipe(page: str, source_url: str) -> dict | None:
    """The first readable Recipe on the page, or None."""
    for block in _LD_JSON_RE.findall(page):
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        found = _find_recipe(data)
        if found:
            recipe = parse_recipe(found, source_url)
            if recipe["title"]:
                return recipe
    return None


# --- TheMealDB search (free API; content is shown with attribution: the saved
# note's source line links back to the meal page on themealdb.com) ---
_THEMEALDB_HOST = "https://www.themealdb.com"
_THEMEALDB_SEARCH_PATH = "/api/json/v1/1/search.php"


async def _themealdb_get(path: str) -> dict | None:
    """One TheMealDB API call; None on a non-200 (callers answer plainly)."""
    session = await get_session(_THEMEALDB_HOST)
    async with session.get(
        f"{_THEMEALDB_HOST}{path}", headers={"User-Agent": _UA}, timeout=_TIMEOUT
    ) as resp:
        if resp.status != 200:
            return None
        return await resp.json(content_type=None)


def _themealdb_recipe(meal: dict) -> dict:
    """Map a TheMealDB meal into the same shape the importer saves."""
    ingredients: list[str] = []
    for index in range(1, 21):
        name = _text(meal.get(f"strIngredient{index}"))
        if not name:
            continue
        measure = _text(meal.get(f"strMeasure{index}"))
        ingredients.append(f"{measure} {name}".strip())
    instructions = [
        line.strip()
        for line in (meal.get("strInstructions") or "").splitlines()
        if line.strip()
    ]
    category = _text(meal.get("strCategory"))
    area = _text(meal.get("strArea"))
    return {
        "title": _text(meal.get("strMeal")),
        "description": " · ".join(part for part in (category, area) if part),
        "ingredients": ingredients,
        "instructions": instructions,
        "prep_minutes": None,
        "cook_minutes": None,
        "total_minutes": None,
        "yield": "",
        "image": _text(meal.get("strMealThumb")) or None,
        "source_url": f"{_THEMEALDB_HOST}/meal/{meal.get('idMeal')}",
        "site": "themealdb.com",
        "id": str(meal.get("idMeal") or ""),
        "source": "themealdb",
    }


async def _themealdb_hits(query: str, limit: int) -> list[dict] | None:
    """TheMealDB results for a query, or ``None`` when the API cannot answer."""
    if limit <= 0:
        return []
    try:
        data = await _themealdb_get(f"{_THEMEALDB_SEARCH_PATH}?s={quote(query)}")
    except Exception as exc:  # a dead API must never 500 the route
        log.warning(f"[recipe_import] themealdb search failed: {type(exc).__name__}: {exc}")
        return None
    meals = (data or {}).get("meals") or []
    recipes = [_themealdb_recipe(meal) for meal in meals if isinstance(meal, dict)]
    return [recipe for recipe in recipes if recipe["title"]][:limit]


async def _themealdb_search(req: RecipeRequest) -> ExecutionResult:
    query = (req.query or "").strip()
    if len(query) < 2:
        return ExecutionResult.fail(
            "Type at least two letters to search.", service="recipe_import"
        )
    recipes = await _themealdb_hits(query, 12)
    if recipes is None:
        return ExecutionResult.fail(
            "TheMealDB could not be reached right now; try again shortly.",
            service="recipe_import",
        )
    if not recipes:
        return ExecutionResult.ok(
            f"No recipes found for “{query}”.",
            service="recipe_import",
            detail={"recipes": []},
        )
    return ExecutionResult.ok(
        f"Found {len(recipes)} recipe(s).",
        service="recipe_import",
        detail={"recipes": recipes},
    )


async def _recipe_search(req: RecipeRequest) -> ExecutionResult:
    """Offline index first, then TheMealDB fills the rest of the page."""
    query = (req.query or "").strip()
    if len(query) < 2:
        return ExecutionResult.fail(
            "Type at least two letters to search.", service="recipe_import"
        )
    seed = get_seed()
    seed_hits = seed.search(query, 12) if seed.present else []
    offline: list[str] = []
    meal_hits: list[dict] = []
    remaining = 12 - len(seed_hits)
    if remaining > 0:
        fetched = await _themealdb_hits(query, remaining)
        if fetched is None:
            if not seed_hits:
                return ExecutionResult.fail(
                    "The recipe search could not be reached right now; "
                    "try again shortly.",
                    service="recipe_import",
                )
            offline.append("themealdb")
        else:
            meal_hits = fetched
    results = seed_hits + meal_hits
    sources: list[str] = []
    if seed_hits:
        sources.append("foodcom")
    if meal_hits:
        sources.append("themealdb")
    if not results:
        return ExecutionResult.ok(
            f"No recipes found for “{query}”.",
            service="recipe_import",
            detail={
                "results": [],
                "sources": [],
                "offline_sources": offline,
                "seed": {"present": seed.present, "results": 0},
            },
        )
    return ExecutionResult.ok(
        f"Found {len(results)} recipe(s).",
        service="recipe_import",
        detail={
            "results": results,
            "sources": sources,
            "offline_sources": offline,
            "seed": {"present": seed.present, "results": len(seed_hits)},
        },
    )


async def _shelf_fetch(req: RecipeRequest) -> ExecutionResult:
    """The full offline recipe behind a picked search card."""
    if not req.id:
        return ExecutionResult.fail(
            "Which recipe? Give its id.", service="recipe_import"
        )
    seed = get_seed()
    if not seed.present:
        return ExecutionResult.fail(
            "The offline recipe index is not available on this server.",
            service="recipe_import",
        )
    recipe = seed.fetch(int(req.id))
    if not recipe:
        return ExecutionResult.fail(
            "That recipe is not in the offline index.", service="recipe_import"
        )
    return ExecutionResult.ok(
        f"Found “{recipe['title']}”.", service="recipe_import", detail={"recipe": recipe}
    )


async def handle_recipe(req: RecipeRequest) -> ExecutionResult:
    if req.action == "themealdb_search":
        return await _themealdb_search(req)
    if req.action == "recipe_search":
        return await _recipe_search(req)
    if req.action == "shelf_fetch":
        return await _shelf_fetch(req)
    if req.action != "url_import":
        return ExecutionResult.fail(
            f"Unknown recipe action: {req.action}", service="recipe_import"
        )

    url = (req.url or "").strip()
    if not _URL_RE.match(url):
        return ExecutionResult.fail(
            "Give a full recipe link that starts with http:// or https://.",
            service="recipe_import",
        )

    try:
        session = await get_session(host_of(url))
        async with session.get(
            url,
            headers={"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"},
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                return ExecutionResult.fail(
                    f"That page answered HTTP {resp.status}; recipes usually live "
                    "at a normal web address.",
                    service="recipe_import",
                )
            raw = await resp.content.read(_MAX_BYTES)
            encoding = resp.get_encoding() or "utf-8"
            final_url = str(resp.url)
    except aiohttp.ClientError as exc:
        log.warning(f"[recipe_import] fetch failed for {url}: {type(exc).__name__}: {exc}")
        return ExecutionResult.fail(
            "That page could not be reached; check the link and try again.",
            service="recipe_import",
        )
    except Exception as exc:  # a bad link must never 500 the route
        log.warning(f"[recipe_import] read failed for {url}: {type(exc).__name__}: {exc}")
        return ExecutionResult.fail(
            "That page could not be read right now.", service="recipe_import"
        )

    recipe = extract_recipe(raw.decode(encoding, errors="replace"), final_url)
    if not recipe:
        return ExecutionResult.fail(
            "That page does not publish recipe data Jarvis can read — try a "
            "different link or add the recipe by hand.",
            service="recipe_import",
        )
    return ExecutionResult.ok(
        f"Found “{recipe['title']}”.",
        service="recipe_import",
        detail={"recipe": recipe},
    )
