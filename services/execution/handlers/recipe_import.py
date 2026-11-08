# services/execution/handlers/recipe_import.py
"""Recipe import: read a pasted link's schema.org recipe.

Most recipe sites publish their recipe as JSON-LD (``<script
type="application/ld+json">`` with an ``@type: Recipe`` node) because Google's
rich results demand it. That block carries everything the family shelf needs -
ingredients with units, ordered steps, times, yield, an image - so the importer
reads the page once and never has to scrape HTML. Sites that do not publish it
get a plain "cannot read this page" answer instead of a guess.
"""
import asyncio
import html
import json
import logging
import re
from collections import OrderedDict
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlsplit, urlunsplit

import aiohttp

from services.execution.handlers.note import _walk_webdav_dir, note_body
from services.execution.handlers.recipe_facets import facets
from services.execution.handlers.recipe_seed import get_seed
from services.execution.http_client import get_session, host_of
from services.execution.personal_data import missing_account_message, resolve_personal_data_provider
from services.execution.schemas import ExecutionResult, RecipeRequest

log = logging.getLogger("execution.recipe_import")

_URL_RE = re.compile(r"^https?://", re.I)
_LD_JSON_RE = re.compile(
    r'<script[^>]+type\s*=\s*["\']application/ld\+json[^"\']*["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.I | re.S)
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?)?$",
    re.I,
)
_MAX_BYTES = 1_500_000
_TIMEOUT = aiohttp.ClientTimeout(total=25.0, connect=10.0)
_UA = "Jarvis-OS/1.0 (family recipe import; self-hosted)"

# --- duplicate-import guard -------------------------------------------------
# The Recipes page needs to know which source URLs are already on the shelf
# before it offers a search result again. A note's ``Source:`` line carries the
# URL; the notes ``list`` previews only the newest few notes, so the guard reads
# every recipe note (through the same WebDAV listing the notes page uses) and
# caches each read by its version, re-reading only what changed.
_RECIPES_DIR = "Recipes"
_SOURCE_URL_RE = re.compile(r"^Source:\s*(https?://\S+)", re.I | re.M)
_SOURCE_CONCURRENCY = 4
_SOURCE_CACHE_MAX = 512
_source_cache: "OrderedDict[str, tuple[str, str]]" = OrderedDict()

# --- staple-overlap ranking -------------------------------------------------
# The Recipes page can rank the shelf by how many household staples a recipe
# uses, so "what can I cook tonight" floats up the dinners that need nothing
# special. The shelf listing carries titles only, so the ingredients live in
# each note's body; every body is read once and cached by its version, exactly
# like the source guard above. The label set and patterns are the same ones the
# offline importer uses (``scripts/import_recipes.py``) so the page and the
# seed agree on what counts as a staple.
_STAPLES: "tuple[tuple[str, re.Pattern[str]], ...]" = (
    ("chicken", re.compile(r"\bchicken\b", re.I)),
    ("ground meat", re.compile(r"\b(?:ground\s+(?:beef|turkey|chicken)|hamburger)\b", re.I)),
    ("rice", re.compile(r"\brice\b", re.I)),
    ("pasta", re.compile(r"\b(?:pasta|spaghetti|noodle|macaroni|penne|ziti)\b", re.I)),
    ("canned tomatoes", re.compile(r"\btomato(?:es)?\b", re.I)),
    ("beans", re.compile(r"\bbeans?\b", re.I)),
    ("cheese", re.compile(r"\bcheese\b", re.I)),
    ("tortillas", re.compile(r"\btortillas?\b", re.I)),
    ("eggs", re.compile(r"\beggs?\b", re.I)),
    ("potatoes", re.compile(r"\bpotato(?:es)?\b", re.I)),
    ("onions", re.compile(r"\bonions?\b", re.I)),
    ("garlic", re.compile(r"\bgarlic\b", re.I)),
    ("frozen vegetables", re.compile(r"\bfrozen\s+(?:mixed\s+)?veg(?:etables?)?\b", re.I)),
)
_BODY_CACHE_MAX = 512
_body_cache: "OrderedDict[str, tuple[str, str]]" = OrderedDict()


def staple_matches(text: str | None) -> list[str]:
    """The staple labels a recipe's text mentions, in the list's own order."""
    haystack = text or ""
    return [label for label, pattern in _STAPLES if pattern.search(haystack)]


def extract_source_url(text: str | None) -> str:
    """The URL on a note's ``Source:`` line, or ``""`` when it has none.

    Imported notes end with ``Source: <url> - imported from <site>``; a photo
    import has no URL (``Source: photo import``), so it matches nothing.
    """
    match = _SOURCE_URL_RE.search(note_body(text or ""))
    return match.group(1) if match else ""



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


#: schema.org NutritionInformation -> the app's per-serving names.
_NUTRITION_FIELDS = (
    ("calories", "calories"),
    ("fatContent", "fat_g"),
    ("saturatedFatContent", "sat_fat_g"),
    ("cholesterolContent", "cholesterol_mg"),
    ("sodiumContent", "sodium_mg"),
    ("carbohydrateContent", "carbs_g"),
    ("fiberContent", "fiber_g"),
    ("sugarContent", "sugar_g"),
    ("proteinContent", "protein_g"),
)
_LEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)")


def _nutrition(value: Any) -> dict | None:
    """The page's stated nutrition ("350 calories", "12 g"); None when it states none.

    schema.org means it per serving. A value that does not start with a number
    is left out rather than guessed.
    """
    if not isinstance(value, dict):
        return None
    found = {}
    for field, name in _NUTRITION_FIELDS:
        match = _LEADING_NUMBER.match(_text(value.get(field)))
        if match:
            found[name] = float(match.group(1))
    return found or None


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
        "nutrition": _nutrition(recipe.get("nutrition")),
    }


def _ldjson_candidates(page: str) -> list[str]:
    """Every block on the page that might carry a schema.org Recipe.

    ld+json blocks first, then any other script whose body mentions a Recipe:
    some sites ship their recipe data in a differently-typed script tag.
    """
    candidates = list(_LD_JSON_RE.findall(page))
    for block in _SCRIPT_RE.findall(page):
        inner = block.strip()
        if inner and ("Recipe" in inner or "schema.org" in inner) and inner not in candidates:
            candidates.append(block)
    return candidates


def _parse_json_ld(block: str):
    """One candidate block as JSON, or ``None`` - ld+json is often CDATA-wrapped
    or HTML-escaped by template engines."""
    text = block.strip()
    if "<![CDATA[" in text:
        text = text.replace("<![CDATA[", "").replace("]]>", "").strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        return json.loads(html.unescape(text))
    except (json.JSONDecodeError, ValueError):
        return None


def extract_recipe(page: str, source_url: str) -> dict | None:
    """The first readable Recipe on the page, or None."""
    for block in _ldjson_candidates(page):
        data = _parse_json_ld(block)
        if data is None:
            continue
        found = _find_recipe(data)
        if found:
            recipe = parse_recipe(found, source_url)
            if recipe["title"]:
                return recipe
    return None


class _HttpStatus(Exception):
    """The page answered with a non-200 status."""

    def __init__(self, status: int) -> None:
        super().__init__(str(status))
        self.status = status


async def _fetch_page(session, url: str) -> tuple[str, str]:
    """Fetch a page and decode it; raises ``_HttpStatus``/aiohttp errors."""
    # Ask for a fresh page, not whatever the CDN cached: Cloudflare/BigScoots
    # serve whatever variant the edge cached for this URL - usually the real
    # page, sometimes stale wp-rocket "shells" with no content. Revalidation
    # headers make the edge fetch from origin (deterministic full page).
    async with session.get(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity",
            # The CDN serves stale/variant pages for plain requests; a
            # revalidated fetch gets the origin's real page every time.
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise _HttpStatus(resp.status)
        raw = await resp.content.read(_MAX_BYTES)
        content_encoding = (resp.headers.get("Content-Encoding") or "").lower()
        if "br" in content_encoding:
            try:
                import brotli  # optional; only when the server insisted on br

                raw = brotli.decompress(raw)
            except Exception:
                log.warning(f"[recipe_import] could not decode br body for {url}")
        encoding = resp.get_encoding() or "utf-8"
        return raw.decode(encoding, errors="replace"), str(resp.url)


def _nowprocket_url(url: str) -> str:
    """The same URL with wp-rocket's cache-bypass parameter appended."""
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    if any(key == "nowprocket" for key, _ in params):
        return url
    params.append(("nowprocket", "1"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))


# --- WordPress REST fallback --------------------------------------------------
# Optimization plugins can serve non-browser clients a content-free shell (or a
# cache-variant lottery) while the ordinary page renders fine for people. The
# REST API of the same site is one lever page caches do not cover: the post's
# rendered content carries the recipe card (WP Recipe Maker) as HTML classes
# instead of JSON-LD, so it is read with regexes, not json.

def _strip_tags(text: str) -> str:
    """Flatten HTML to plain text: tags out, entities decoded, whitespace collapsed."""
    flat = re.sub(r"<[^>]+>", " ", text)
    flat = html.unescape(flat).replace("\xa0", " ")
    return re.sub(r"\s+", " ", flat).strip()


def _slug_from_url(url: str) -> str:
    """The page's slug (its last path segment), for /wp-json/wp/v2/{posts,pages}."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if "/" in path else path
    for suffix in (".html", ".php"):
        if slug.lower().endswith(suffix):
            slug = slug[: -len(suffix)]
    return slug


def _wprm_span(chunk: str, kind: str) -> str:
    match = re.search(
        r'class="[^"]*wprm-recipe-ingredient-' + re.escape(kind) + r'[^"]*"[^>]*>(.*?)</span>',
        chunk,
        re.S | re.I,
    )
    return _strip_tags(match.group(1)) if match else ""


def _wprm_parts(content: str, source_url: str, source_title: str = "") -> dict | None:
    """Normalize a WP Recipe Maker card out of rendered post/page content."""
    if "wprm-recipe" not in content:
        return None

    def wprm_part(kind: str) -> int | None:
        match = re.search(
            r'wprm-recipe-' + re.escape(kind) + r'"[^>]*>\s*(\d+)', content, re.S | re.I
        )
        return int(match.group(1)) if match else None

    name_match = re.search(
        r'<h[1-6][^>]*class="[^"]*wprm-recipe-name[^"]*"[^>]*>(.*?)</h[1-6]>', content, re.S | re.I
    )
    title = _strip_tags(name_match.group(1)) if name_match else _strip_tags(source_title)

    ingredients = []
    for li in re.findall(r'<li[^>]*wprm-recipe-ingredient[^>]*>(.*?)</li>', content, re.S | re.I):
        amount = _wprm_span(li, "amount")
        unit = _wprm_span(li, "unit")
        name = _wprm_span(li, "name")
        notes = _wprm_span(li, "notes")
        joined = " ".join(part for part in (amount, unit, name) if part).strip()
        if not joined:
            continue  # an empty placeholder row in the card
        if notes:
            joined = f"{joined}, {notes}"
        ingredients.append(joined)

    steps = []
    for block in re.findall(
        r'class="[^"]*wprm-recipe-instruction-text[^"]*"[^>]*>(.*?)</div>', content, re.S | re.I
    ):
        step = _strip_tags(block)
        if step:
            steps.append(step)

    if not ingredients or not steps:
        return None

    prep = wprm_part("prep_time-minutes")
    cook = wprm_part("cook_time-minutes")
    hours = wprm_part("total_time-hours") or 0
    total = wprm_part("total_time-minutes")
    total = (hours * 60 + total) if total is not None else None

    image_match = None
    image_at = content.find("wprm-recipe-image")
    if image_at != -1:
        image_match = re.search(r'src="(https?://[^"]+)"', content[image_at:])

    servings = re.search(r'a-servings="(\d+)"', content)

    site = urlparse(source_url).netloc
    return {
        "title": title,
        "description": "",
        "ingredients": ingredients,
        "instructions": steps,
        "prep_minutes": prep,
        "cook_minutes": cook,
        "total_minutes": total,
        "yield": f"{servings.group(1)} servings" if servings else "",
        "image": image_match.group(1) if image_match else None,
        "source_url": source_url,
        "site": site,
        "nutrition": None,
    }


async def _wp_rest_recipe(session, url: str) -> dict | None:
    """The recipe from the site's own REST API, or ``None`` when it cannot answer.

    Page caches cover the page but not the API, so this is the retry that
    actually reaches WordPress when the page comes back as an empty shell.
    """
    parts = urlsplit(url)
    slug = _slug_from_url(url)
    if not slug:
        return None
    base = f"{parts.scheme}://{parts.netloc}"
    for endpoint in ("posts", "pages"):
        api = f"{base}/wp-json/wp/v2/{endpoint}?slug={quote(slug)}&_fields=title,content&per_page=1"
        try:
            async with session.get(
                api,
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    continue
                rows = await resp.json(content_type=None)
        except Exception as exc:
            log.warning(f"[recipe_import] wp-json {endpoint} read failed for {api}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(rows, list) or not rows:
            continue
        row = rows[0]
        if not isinstance(row, dict):
            continue
        rendered = html.unescape(str((row.get("content") or {}).get("rendered") or ""))
        title_html = str((row.get("title") or {}).get("rendered") or "")
        recipe = _wprm_parts(rendered, url, title_html)
        if recipe is not None:
            log.warning(f"[recipe_import] recipe found via wp-json for {url}")
            return recipe
        log.warning(f"[recipe_import] wp-json {endpoint} had no WP Recipe Maker card for {url}")
        return None  # the REST answered; the page simply is not a WP recipe
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


async def _read_note_source(provider, note: dict) -> str:
    """The source URL of one note, cached by the note's version.

    A read that fails is a blank source, never an error: the guard only loses
    the "already imported" mark for that note.
    """
    from ..http_client import request as http_request

    url = provider.file_url(note["path"].lstrip("/"))
    version = f"{note.get('modified', '')}|{note.get('size', 0)}"
    cached = _source_cache.get(url)
    if cached and cached[0] == version:
        _source_cache.move_to_end(url)
        return cached[1]
    try:
        resp = await http_request(
            "GET", url, auth=(provider.username, provider.password), verify=False, timeout=20
        )
    except Exception as exc:  # one unreadable note must not fail the scan
        log.warning("[recipe_import] source read failed for %s: %s", note["path"], str(exc) or type(exc).__name__)
        return ""
    if resp["status_code"] != 200:
        return ""
    source = extract_source_url(resp["text"])
    _source_cache[url] = (version, source)
    _source_cache.move_to_end(url)
    while len(_source_cache) > _SOURCE_CACHE_MAX:
        _source_cache.popitem(last=False)
    return source


#: Recipes/ also holds the week plan, the shopping list and the cookbook's
#: suggested changes; none of them is a recipe on the shelf.
_NOT_SHELF = frozenset({"Meal plan.md", "Meal-plan.md", "Shopping list.md", "Shopping-list.md"})


def _shelf(notes: list[dict]) -> list[dict]:
    out = []
    for note in notes:
        rel = str(note.get("path") or "").strip("/").removeprefix(_RECIPES_DIR + "/")
        if rel in _NOT_SHELF or rel.startswith("Suggestions/"):
            continue
        out.append(note)
    return out


async def _recipe_sources(req: RecipeRequest) -> ExecutionResult:
    """The source URLs already imported into ``Recipes/`` (the duplicate guard).

    Reads every recipe note so the answer is exact, not a preview heuristic;
    each read is cached by the note's version, so a repeat search only re-reads
    notes that changed.
    """
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return ExecutionResult.fail(missing_account_message(req.user_context), service="recipe_sources")
    try:
        notes = _shelf(await _walk_webdav_dir(provider, _RECIPES_DIR))
    except Exception as exc:  # a listing failure is a blank guard, not a 500
        log.warning("[recipe_import] recipe listing failed: %s", str(exc) or type(exc).__name__)
        return ExecutionResult.fail("Could not read the recipe shelf right now.", service="recipe_sources")

    gate = asyncio.Semaphore(_SOURCE_CONCURRENCY)

    async def one(note: dict) -> str:
        async with gate:
            return await _read_note_source(provider, note)

    found = await asyncio.gather(*(one(note) for note in notes))
    sources = sorted({source for source in found if source})
    return ExecutionResult.ok(
        f"{len(sources)} of {len(notes)} recipe(s) name a source already imported.",
        service="recipe_sources",
        detail={"sources": sources, "notes": len(notes)},
    )


async def _read_note_body(provider, note: dict) -> str:
    """One note's readable body (see ``_read_note_text``)."""
    return note_body(await _read_note_text(provider, note))


_SMALL_WORDS = {"a", "an", "and", "as", "at", "for", "in", "of", "on", "or", "the", "to", "with"}


def readable_title(name: str) -> str:
    """A file-name title made readable: "beef-and-bean-burritos" is "Beef and
    Bean Burritos". A title with spaces in it is already a name and is kept."""
    name = name.strip()
    if " " in name or not re.search(r"[-_]", name):
        return name
    words = [w for w in re.split(r"[-_]+", name) if w]
    return " ".join(
        w if i and w.lower() in _SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words)
    )


def note_title(text: str, path: str) -> str:
    """A recipe's name: the "# <title>" its note was stored with, else its file name."""
    first = (text or "").lstrip().split("\n", 1)[0]
    stem = path.rsplit("/", 1)[-1].removesuffix(".md")
    stored = first[2:].strip() if first.startswith("# ") else ""
    # The stored name exactly ("$5 Tacos", "Chili-Mac"), unless it is only
    # the lower-case file name it was saved under ("5-tacos"), shown as words.
    if stored and (stored != stem or stored != stored.lower()):
        return stored
    return readable_title(stored or stem)


async def _read_note_text(provider, note: dict) -> str:
    """One note's whole text, stored header included, cached by the note's version.

    A read that fails is an empty body, never an error: ranking simply treats
    that note as having no staples.
    """
    from ..http_client import request as http_request

    url = provider.file_url(note["path"].lstrip("/"))
    version = f"{note.get('modified', '')}|{note.get('size', 0)}"
    cached = _body_cache.get(url)
    if cached and cached[0] == version:
        _body_cache.move_to_end(url)
        return cached[1]
    try:
        resp = await http_request(
            "GET", url, auth=(provider.username, provider.password), verify=False, timeout=20
        )
    except Exception as exc:  # one unreadable note must not fail the ranking
        log.warning("[recipe_import] body read failed for %s: %s", note["path"], str(exc) or type(exc).__name__)
        return ""
    if resp["status_code"] != 200:
        return ""
    text = resp["text"]
    _body_cache[url] = (version, text)
    _body_cache.move_to_end(url)
    while len(_body_cache) > _BODY_CACHE_MAX:
        _body_cache.popitem(last=False)
    return text


async def _recipe_staples(req: RecipeRequest) -> ExecutionResult:
    """The shelf ranked by how many household staples each recipe uses.

    Reads every recipe note (the listing carries titles only, so the
    ingredients live in the body) and counts the staple labels each mentions.
    Ties break alphabetically so the order is stable between calls; a note that
    cannot be read scores zero rather than failing the whole ranking.
    """
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return ExecutionResult.fail(missing_account_message(req.user_context), service="recipe_staples")
    try:
        notes = _shelf(await _walk_webdav_dir(provider, _RECIPES_DIR))
    except Exception as exc:  # a listing failure is reported, not a 500
        log.warning("[recipe_import] recipe listing failed: %s", str(exc) or type(exc).__name__)
        return ExecutionResult.fail("Could not read the recipe shelf right now.", service="recipe_staples")

    gate = asyncio.Semaphore(_SOURCE_CONCURRENCY)

    async def one(note: dict) -> dict:
        async with gate:
            text = await _read_note_text(provider, note)
        staples = staple_matches(note_body(text))
        return {
            "title": note_title(text, str(note.get("path") or note.get("title") or "")),
            "path": note.get("path") or "",
            "score": len(staples),
            "staples": staples,
        }

    ranked = list(await asyncio.gather(*(one(note) for note in notes)))
    ranked.sort(key=lambda row: (-row["score"], row["title"].lower()))
    best = ranked[0]["score"] if ranked else 0
    return ExecutionResult.ok(
        f"Ranked {len(ranked)} recipe(s); the best uses {best} staple(s).",
        service="recipe_staples",
        detail={"ranked": ranked, "notes": len(ranked)},
    )


async def _recipe_catalog(req: RecipeRequest) -> ExecutionResult:
    """Every recipe with what the shelf browses by: meal, protein, time,
    servings, calories, staples, owner and photo. Bodies are read through the
    same version cache as the staple ranking, so a repeat call is cheap."""
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return ExecutionResult.fail(missing_account_message(req.user_context), service="recipe_catalog")
    try:
        notes = _shelf(await _walk_webdav_dir(provider, _RECIPES_DIR))
    except Exception as exc:  # a listing failure is reported, not a 500
        log.warning("[recipe_import] recipe listing failed: %s", str(exc) or type(exc).__name__)
        return ExecutionResult.fail("Could not read the recipe shelf right now.", service="recipe_catalog")

    gate = asyncio.Semaphore(_SOURCE_CONCURRENCY)

    async def one(note: dict) -> dict:
        async with gate:
            text = await _read_note_text(provider, note)
        body = note_body(text)
        title = note_title(text, str(note.get("path") or note.get("title") or ""))
        staples = staple_matches(body)
        return {
            "title": title,
            "path": str(note.get("path") or "").lstrip("/"),
            "modified": note.get("modified"),
            **facets(title, body),
            "staples": staples,
        }

    recipes = list(await asyncio.gather(*(one(note) for note in notes)))
    recipes.sort(key=lambda row: row["title"].lower())
    return ExecutionResult.ok(f"{len(recipes)} recipe(s)", service="recipe_catalog", detail={"recipes": recipes})


async def handle_recipe(req: RecipeRequest) -> ExecutionResult:
    if req.action == "recipe_search":
        return await _recipe_search(req)
    if req.action == "shelf_fetch":
        return await _shelf_fetch(req)
    if req.action == "recipe_sources":
        return await _recipe_sources(req)
    if req.action == "recipe_staples":
        return await _recipe_staples(req)
    if req.action == "recipe_catalog":
        return await _recipe_catalog(req)
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
        page, final_url = await _fetch_page(session, url)
    except _HttpStatus as exc:
        return ExecutionResult.fail(
            f"That page answered HTTP {exc.status}; recipes usually live "
            "at a normal web address.",
            service="recipe_import",
        )
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

    recipe = extract_recipe(page, final_url)
    if recipe is None:
        # Cache/optimization plugins (wp-rocket and friends) serve
        # content-free shells to non-browser clients; wp-rocket's documented
        # bypass parameter asks for the real page. Ask once; other sites ignore it.
        bypass = _nowprocket_url(url)
        if bypass != url:
            log.warning(f"[recipe_import] no recipe found at {url}; retrying via {bypass}")
            try:
                page, final_url = await _fetch_page(session, bypass)
                recipe = extract_recipe(page, final_url)
            except Exception as exc:
                log.warning(f"[recipe_import] bypass read failed for {bypass}: {type(exc).__name__}: {exc}")
    if recipe is None:
        # Cache/optimization plugins (wp-rocket and friends) serve
        # content-free shells to non-browser clients; the site's REST API is
        # not covered by page caches and carries the recipe as rendered HTML.
        recipe = await _wp_rest_recipe(session, url)
    if not recipe:
        return ExecutionResult.fail(
            "That page does not publish recipe data Jarvis can read - try a "
            "different link or add the recipe by hand.",
            service="recipe_import",
        )
    return ExecutionResult.ok(
        f"Found “{recipe['title']}”.",
        service="recipe_import",
        detail={"recipe": recipe},
    )
