"""Paste-a-link recipe import: schema.org JSON-LD parsing and the handler."""
import json

import aiohttp

from services.execution.handlers import recipe_import
from services.execution.schemas import RecipeRequest

RECIPE_NODE = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Best Lemonade",
    "description": "Cold and tart.",
    "recipeIngredient": ["4 cups water", "1 cup lemon juice", "1/2 cup sugar"],
    "recipeInstructions": [
        {"@type": "HowToStep", "text": "Juice the lemons."},
        {
            "@type": "HowToSection",
            "name": "Assemble",
            "itemListElement": [
                {"@type": "HowToStep", "text": "Stir the sugar into the water."},
                {"@type": "HowToStep", "text": "Add the lemon juice and chill."},
            ],
        },
    ],
    "prepTime": "PT15M",
    "cookTime": "PT0M",
    "totalTime": "PT1H15M",
    "recipeYield": "6 servings",
    "image": [{"@type": "ImageObject", "url": "https://example.com/lemonade.jpg"}],
}

PAGE = (
    "<html><head>"
    '<script type="application/ld+json">not json at all</script>'
    '<script type="application/ld+json">'
    + json.dumps({"@context": "https://schema.org", "@graph": [{"@type": "WebSite"}, RECIPE_NODE]})
    + "</script></head><body>Lemonade!</body></html>"
)

EMPTY_PAGE = "<html><body>No recipe markup here.</body></html>"


class _Resp:
    def __init__(self, text: str, status: int = 200, url: str = "https://example.com/recipes/lemonade"):
        self.status = status
        self.url = url
        self._body = text.encode("utf-8")

    def get_encoding(self) -> str:
        return "utf-8"

    @property
    def content(self):
        body = self._body

        class _Content:
            async def read(self, n: int = -1) -> bytes:
                return body

        return _Content()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _Session:
    def __init__(self, resp: _Resp):
        self._resp = resp

    def get(self, url, **kwargs):
        return self._resp


def _patch_fetch(monkeypatch, resp: _Resp | None = None, exc: Exception | None = None):
    async def fake_get_session(host):
        if exc is not None:
            raise exc
        return _Session(resp)

    monkeypatch.setattr(recipe_import, "get_session", fake_get_session)


def _request(**overrides):
    payload = {
        "user_context": {"user": "dad"},
        "action": "url_import",
        "url": "https://example.com/recipes/lemonade",
    }
    payload.update(overrides)
    return RecipeRequest(**payload)


def test_reads_a_recipe_out_of_the_json_ld():
    recipe = recipe_import.extract_recipe(PAGE, "https://example.com/recipes/lemonade")
    assert recipe is not None
    assert recipe["title"] == "Best Lemonade"
    assert recipe["ingredients"] == ["4 cups water", "1 cup lemon juice", "1/2 cup sugar"]
    # Section steps flatten in order.
    assert recipe["instructions"] == [
        "Juice the lemons.",
        "Stir the sugar into the water.",
        "Add the lemon juice and chill.",
    ]
    assert recipe["prep_minutes"] == 15
    assert recipe["cook_minutes"] is None  # PT0M is no time at all
    assert recipe["total_minutes"] == 75
    assert recipe["yield"] == "6 servings"
    assert recipe["image"] == "https://example.com/lemonade.jpg"
    assert recipe["source_url"] == "https://example.com/recipes/lemonade"
    assert recipe["site"] == "example.com"


def test_skips_broken_blocks_and_pages_without_recipes():
    assert recipe_import.extract_recipe(EMPTY_PAGE, "https://example.com") is None
    only_website = (
        '<script type="application/ld+json">'
        + json.dumps({"@context": "https://schema.org", "@type": "WebSite", "name": "x"})
        + "</script>"
    )
    assert recipe_import.extract_recipe(only_website, "https://example.com") is None


def test_finds_a_recipe_inside_an_array_root():
    block = (
        '<script type="application/ld+json">'
        + json.dumps([{"@type": "BreadcrumbList"}, RECIPE_NODE])
        + "</script>"
    )
    recipe = recipe_import.extract_recipe(block, "https://example.com/lemonade")
    assert recipe is not None
    assert recipe["title"] == "Best Lemonade"


async def test_a_link_that_is_not_http_fails_before_any_fetch(monkeypatch):
    def boom(host):  # pragma: no cover - must never run
        raise AssertionError("fetch must not run for a bad link")

    monkeypatch.setattr(recipe_import, "get_session", boom)
    result = await recipe_import.handle_recipe(_request(url="just some words"))
    assert result.status == "FAILURE"
    assert "http://" in result.message


async def test_imports_a_page_through_the_fetcher(monkeypatch):
    _patch_fetch(monkeypatch, _Resp(PAGE))
    result = await recipe_import.handle_recipe(_request())
    assert result.status == "SUCCESS"
    assert result.service == "recipe_import"
    recipe = result.detail["recipe"]
    assert recipe["title"] == "Best Lemonade"
    assert recipe["source_url"] == "https://example.com/recipes/lemonade"


async def test_a_page_without_recipe_data_fails_plainly(monkeypatch):
    _patch_fetch(monkeypatch, _Resp(EMPTY_PAGE))
    result = await recipe_import.handle_recipe(_request())
    assert result.status == "FAILURE"
    assert "does not publish recipe data" in result.message


async def test_a_network_error_fails_plainly(monkeypatch):
    _patch_fetch(monkeypatch, exc=aiohttp.ClientError("boom"))
    result = await recipe_import.handle_recipe(_request())
    assert result.status == "FAILURE"
    assert "could not be reached" in result.message


def test_prefers_the_full_yield_when_the_site_sends_two():
    recipe = recipe_import.parse_recipe(
        {**RECIPE_NODE, "recipeYield": ["12", "12 cookies"]},
        "https://example.com/recipes/lemonade",
    )
    assert recipe["yield"] == "12 cookies"


MEAL = {
    "idMeal": "52772",
    "strMeal": "Chocolate Gateau",
    "strCategory": "Dessert",
    "strArea": "French",
    "strInstructions": "Preheat the oven.\r\n\r\nMelt the butter.\r\nBake.",
    "strMealThumb": "https://www.themealdb.com/images/media/meals/tqtywx1468317395.jpg",
    "strIngredient1": "Butter",
    "strMeasure1": "175g",
    "strIngredient2": "Dark Chocolate",
    "strMeasure2": "200g",
    "strIngredient3": "",
    "strMeasure3": "",
}


def test_themealdb_maps_a_meal_into_the_import_shape():
    recipe = recipe_import._themealdb_recipe(MEAL)
    assert recipe["title"] == "Chocolate Gateau"
    assert recipe["description"] == "Dessert · French"
    assert recipe["ingredients"] == ["175g Butter", "200g Dark Chocolate"]
    assert recipe["instructions"] == ["Preheat the oven.", "Melt the butter.", "Bake."]
    assert recipe["source_url"] == "https://www.themealdb.com/meal/52772"
    assert recipe["site"] == "themealdb.com"
    assert recipe["image"].startswith("https://www.themealdb.com/images/")


# --- the offline index: recipe_search leads with it, shelf_fetch reads it ---


class _FakeSeed:
    """Stands in for RecipeSeed so the handler runs without a database."""

    def __init__(self, present=True, hits=None, recipe=None):
        self.present = present
        self.hits = hits or []
        self.recipe = recipe
        self.queries: list[tuple[str, int]] = []
        self.fetched: list[int] = []

    def search(self, query, limit=12):
        self.queries.append((query, limit))
        return self.hits[:limit]

    def fetch(self, recipe_id):
        self.fetched.append(recipe_id)
        return self.recipe


def _seed_hit(**overrides):
    hit = {
        "id": 38,
        "title": "Low-Fat Berry Blue Frozen Dessert",
        "category": "Frozen Desserts",
        "total_minutes": 255,
        "servings": 4,
        "rating": 4.5,
        "image": None,
        "source_url": "https://www.food.com/recipe/low-fat-berry-blue-frozen-dessert-38",
        "site": "food.com",
        "source": "foodcom",
    }
    hit.update(overrides)
    return hit


def _meal_hit(**overrides):
    hit = {
        "id": "52772",
        "title": "Chocolate Gateau",
        "description": "Dessert · French",
        "image": None,
        "ingredients": ["175g Butter"],
        "instructions": ["Bake."],
        "source_url": "https://www.themealdb.com/meal/52772",
        "site": "themealdb.com",
        "source": "themealdb",
    }
    hit.update(overrides)
    return hit


async def test_recipe_search_leads_with_the_offline_index(monkeypatch):
    seed = _FakeSeed(hits=[_seed_hit()])
    monkeypatch.setattr(recipe_import, "get_seed", lambda: seed)
    seen = {}

    async def fake_hits(query, limit):
        seen["limit"] = limit
        return [_meal_hit()]

    monkeypatch.setattr(recipe_import, "_themealdb_hits", fake_hits)
    result = await recipe_import.handle_recipe(
        _request(action="recipe_search", query="berry", url=None)
    )

    assert result.status == "SUCCESS"
    assert seen["limit"] == 11  # the seed filled one of the twelve slots
    assert seed.queries == [("berry", 12)]
    assert result.detail["seed"] == {"present": True, "results": 1}
    assert result.detail["sources"] == ["foodcom", "themealdb"]
    assert result.detail["offline_sources"] == []
    assert [r["source"] for r in result.detail["results"]] == ["foodcom", "themealdb"]


async def test_recipe_search_skips_themealdb_when_the_index_fills_the_page(monkeypatch):
    seed = _FakeSeed(hits=[_seed_hit(id=i) for i in range(12)])
    monkeypatch.setattr(recipe_import, "get_seed", lambda: seed)

    async def boom(query, limit):  # pragma: no cover - must never run
        raise AssertionError("themealdb should be skipped")

    monkeypatch.setattr(recipe_import, "_themealdb_hits", boom)
    result = await recipe_import.handle_recipe(
        _request(action="recipe_search", query="berry", url=None)
    )

    assert result.status == "SUCCESS"
    assert len(result.detail["results"]) == 12
    assert result.detail["sources"] == ["foodcom"]
    assert result.detail["seed"] == {"present": True, "results": 12}


async def test_recipe_search_falls_back_to_themealdb_without_the_index(monkeypatch):
    monkeypatch.setattr(recipe_import, "get_seed", lambda: _FakeSeed(present=False))

    async def fake_hits(query, limit):
        return [_meal_hit()]

    monkeypatch.setattr(recipe_import, "_themealdb_hits", fake_hits)
    result = await recipe_import.handle_recipe(
        _request(action="recipe_search", query="gateau", url=None)
    )

    assert result.status == "SUCCESS"
    assert result.detail["seed"] == {"present": False, "results": 0}
    assert result.detail["sources"] == ["themealdb"]
    assert result.detail["results"][0]["source"] == "themealdb"


async def test_recipe_search_survives_a_dead_themealdb_when_the_index_answers(monkeypatch):
    monkeypatch.setattr(recipe_import, "get_seed", lambda: _FakeSeed(hits=[_seed_hit()]))

    async def dead(query, limit):
        return None

    monkeypatch.setattr(recipe_import, "_themealdb_hits", dead)
    result = await recipe_import.handle_recipe(
        _request(action="recipe_search", query="berry", url=None)
    )

    assert result.status == "SUCCESS"
    assert result.detail["sources"] == ["foodcom"]
    assert result.detail["offline_sources"] == ["themealdb"]


async def test_recipe_search_fails_plainly_when_nothing_can_answer(monkeypatch):
    monkeypatch.setattr(recipe_import, "get_seed", lambda: _FakeSeed(present=False))

    async def dead(query, limit):
        return None

    monkeypatch.setattr(recipe_import, "_themealdb_hits", dead)
    result = await recipe_import.handle_recipe(
        _request(action="recipe_search", query="berry", url=None)
    )

    assert result.status == "FAILURE"
    assert "could not be reached" in result.message


async def test_shelf_fetch_returns_the_full_recipe(monkeypatch):
    full = {
        "title": "Low-Fat Berry Blue Frozen Dessert",
        "description": "Frozen Desserts",
        "ingredients": ["4 cups blueberries, fresh or frozen"],
        "instructions": ["Mix everything."],
        "prep_minutes": 15,
        "cook_minutes": 240,
        "total_minutes": 255,
        "yield": "4 servings",
        "image": None,
        "source_url": "https://www.food.com/recipe/low-fat-berry-blue-frozen-dessert-38",
        "site": "food.com",
    }
    seed = _FakeSeed(recipe=full)
    monkeypatch.setattr(recipe_import, "get_seed", lambda: seed)
    result = await recipe_import.handle_recipe(_request(action="shelf_fetch", id=38, url=None))

    assert result.status == "SUCCESS"
    assert result.detail["recipe"] == full
    assert seed.fetched == [38]


async def test_shelf_fetch_is_plain_when_the_index_or_row_is_missing(monkeypatch):
    result = await recipe_import.handle_recipe(_request(action="shelf_fetch", url=None))
    assert result.status == "FAILURE"
    assert "Which recipe" in result.message

    monkeypatch.setattr(recipe_import, "get_seed", lambda: _FakeSeed(present=False))
    result = await recipe_import.handle_recipe(_request(action="shelf_fetch", id=38, url=None))
    assert result.status == "FAILURE"
    assert "not available" in result.message

    monkeypatch.setattr(recipe_import, "get_seed", lambda: _FakeSeed(recipe=None))
    result = await recipe_import.handle_recipe(_request(action="shelf_fetch", id=999, url=None))
    assert result.status == "FAILURE"
    assert "not in the offline index" in result.message


# --- the duplicate-import guard: which source URLs are already on the shelf ---


class _FakeProvider:
    username = "dad"
    password = "secret"

    def file_url(self, path: str) -> str:
        return f"https://cloud.example/remote.php/dav/files/dad/{path}"


def test_extract_source_url_reads_the_imported_line():
    assert (
        recipe_import.extract_source_url(
            "# Chili\nCategory: Recipes\n\nSource: https://www.food.com/recipe/chili-1 - imported from food.com"
        )
        == "https://www.food.com/recipe/chili-1"
    )
    # A photo import has no URL, and a note without a source matches nothing.
    assert recipe_import.extract_source_url("Source: photo import") == ""
    assert recipe_import.extract_source_url("## Ingredients\n- [ ] eggs") == ""


async def test_recipe_sources_lists_what_is_already_imported(monkeypatch):
    notes = [
        {"title": "Chili", "path": "Recipes/Chili.md", "modified": "a", "size": 10},
        {"title": "Gateau", "path": "Recipes/Gateau.md", "modified": "b", "size": 20},
        {"title": "Handwriting", "path": "Recipes/Handwriting.md", "modified": "c", "size": 30},
    ]
    bodies = {
        "Recipes/Chili.md": "Source: https://www.food.com/recipe/chili-1 - imported from food.com",
        "Recipes/Gateau.md": "Source: https://www.themealdb.com/meal/52772 - imported from themealdb.com",
        "Recipes/Handwriting.md": "Source: photo import",
    }

    monkeypatch.setattr(recipe_import, "resolve_personal_data_provider", lambda _ctx: _FakeProvider())
    monkeypatch.setattr(recipe_import, "_walk_webdav_dir", _async_return(notes))

    async def fake_read(_provider, note):
        return recipe_import.extract_source_url(bodies[note["path"]])

    monkeypatch.setattr(recipe_import, "_read_note_source", fake_read)
    result = await recipe_import.handle_recipe(_request(action="recipe_sources", url=None))

    assert result.status == "SUCCESS"
    assert result.detail["sources"] == [
        "https://www.food.com/recipe/chili-1",
        "https://www.themealdb.com/meal/52772",
    ]
    assert result.detail["notes"] == 3


async def test_recipe_sources_is_plain_without_a_nextcloud_account(monkeypatch):
    monkeypatch.setattr(recipe_import, "resolve_personal_data_provider", lambda _ctx: None)
    result = await recipe_import.handle_recipe(_request(action="recipe_sources", url=None))
    assert result.status == "FAILURE"
    assert "Nextcloud account" in result.message


def _async_return(value):
    async def inner(*_args, **_kwargs):
        return value

    return inner


# --- staple-overlap ranking: which dinners need nothing special ---


def test_staple_matches_reads_the_household_list():
    assert recipe_import.staple_matches("2 lb ground beef, 1 can tomatoes, 2 eggs, rice, cheese") == [
        "ground meat",
        "rice",
        "canned tomatoes",
        "cheese",
        "eggs",
    ]
    # Case-insensitive, and a plain cake has no staples.
    assert recipe_import.staple_matches("CHICKEN and GARLIC") == ["chicken", "garlic"]
    assert recipe_import.staple_matches("flour, sugar, butter") == []
    assert recipe_import.staple_matches(None) == []


async def test_recipe_staples_ranks_the_shelf(monkeypatch):
    notes = [
        {"title": "Chili", "path": "Recipes/Chili.md", "modified": "a", "size": 10},
        {"title": "Cake", "path": "Recipes/Cake.md", "modified": "b", "size": 20},
        {"title": "Chicken Rice", "path": "Recipes/Chicken Rice.md", "modified": "c", "size": 30},
    ]
    bodies = {
        "Recipes/Chili.md": "## Ingredients\n- [ ] ground beef\n- [ ] beans\n- [ ] canned tomatoes\n",
        "Recipes/Cake.md": "## Ingredients\n- [ ] flour\n- [ ] sugar\n",
        "Recipes/Chicken Rice.md": "## Ingredients\n- [ ] chicken\n- [ ] rice\n",
    }

    monkeypatch.setattr(recipe_import, "resolve_personal_data_provider", lambda _ctx: _FakeProvider())
    monkeypatch.setattr(recipe_import, "_walk_webdav_dir", _async_return(notes))

    async def fake_body(_provider, note):
        return bodies[note["path"]]

    monkeypatch.setattr(recipe_import, "_read_note_body", fake_body)
    result = await recipe_import.handle_recipe(_request(action="recipe_staples", url=None))

    assert result.status == "SUCCESS"
    ranked = result.detail["ranked"]
    # Chili (3 staples) first, then the 2-staple and 0-staple recipes.
    assert [row["title"] for row in ranked] == ["Chili", "Chicken Rice", "Cake"]
    assert ranked[0]["score"] == 3
    assert ranked[0]["staples"] == ["ground meat", "canned tomatoes", "beans"]
    assert ranked[-1]["score"] == 0
    assert result.detail["notes"] == 3


async def test_recipe_staples_ties_break_alphabetically(monkeypatch):
    notes = [
        {"title": "Zucchini", "path": "Recipes/Zucchini.md", "modified": "a", "size": 1},
        {"title": "Apple", "path": "Recipes/Apple.md", "modified": "b", "size": 2},
    ]

    monkeypatch.setattr(recipe_import, "resolve_personal_data_provider", lambda _ctx: _FakeProvider())
    monkeypatch.setattr(recipe_import, "_walk_webdav_dir", _async_return(notes))
    monkeypatch.setattr(recipe_import, "_read_note_body", _async_return("eggs"))
    result = await recipe_import.handle_recipe(_request(action="recipe_staples", url=None))

    assert [row["title"] for row in result.detail["ranked"]] == ["Apple", "Zucchini"]


async def test_recipe_staples_is_plain_without_a_nextcloud_account(monkeypatch):
    monkeypatch.setattr(recipe_import, "resolve_personal_data_provider", lambda _ctx: None)
    result = await recipe_import.handle_recipe(_request(action="recipe_staples", url=None))
    assert result.status == "FAILURE"
    assert "Nextcloud account" in result.message


async def test_recipe_staples_reports_a_listing_failure(monkeypatch):
    monkeypatch.setattr(recipe_import, "resolve_personal_data_provider", lambda _ctx: _FakeProvider())

    async def boom(*_args, **_kwargs):
        raise RuntimeError("webdav down")

    monkeypatch.setattr(recipe_import, "_walk_webdav_dir", boom)
    result = await recipe_import.handle_recipe(_request(action="recipe_staples", url=None))
    assert result.status == "FAILURE"
    assert "Could not read the recipe shelf" in result.message
