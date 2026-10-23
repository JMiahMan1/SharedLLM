"""Paste-a-link recipe import: schema.org JSON-LD parsing and the handler."""
import json

import aiohttp
import pytest

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
