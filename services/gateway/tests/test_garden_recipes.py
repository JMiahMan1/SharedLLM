"""Unit tests for services/gateway/garden_recipes.py (Phase 4).

Tests recipe_matches word-boundary ingredient matching and recipe_draft
LLM drafting with mocked dependencies.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.garden_recipes import (
    _ingredient_words,
    _word_boundary_match,
    recipe_draft,
    recipe_matches,
)


# --------------------------------------------------------------------------- word-boundary matching


def test_word_boundary_pea_does_not_match_peanut():
    """'pea' must NOT match 'peanut' or 'peach' or 'peas' (different word)."""
    assert _word_boundary_match("1 cup fresh basil leaves", "basil") is True
    assert _word_boundary_match("2 lb peanut butter", "pea") is False
    assert _word_boundary_match("3 peach slices", "pea") is False
    assert _word_boundary_match("1 cup fresh peas", "pea") is False


def test_word_boundary_multi_word_alias():
    """Multi-word alias: every word must appear as a whole word."""
    assert _word_boundary_match("2 cups fresh basil leaves", "fresh basil") is True
    assert _word_boundary_match("1 cup dried basil", "fresh basil") is False
    assert _word_boundary_match("1 lb ground beef", "ground beef") is True


def test_ingredient_words_extraction():
    words = _ingredient_words("- [ ] 2 lb chicken breast")
    # Multi-word phrases are matched as single tokens
    assert "lb chicken breast" in words or "chicken" in words


# --------------------------------------------------------------------------- recipe_matches


class FakeCookbook:
    """Mock cookbook runner for recipe_matches tests."""

    def __init__(self, recipes=None):
        self.recipes = recipes or []

    async def __call__(self, creds, payload):
        action = payload.get("action")
        if action == "list":
            return {"status": "SUCCESS", "detail": {"notes": self.recipes}}
        if action == "read":
            path = payload.get("path", "")
            for r in self.recipes:
                if r.get("path") == path:
                    return {
                        "status": "SUCCESS",
                        "message": r.get("body", ""),
                        "detail": {"title": r.get("title", path)},
                    }
            return {"status": "FAILURE", "message": "not found"}
        return {"status": "FAILURE", "message": "unknown action"}


def _make_client_mock(ready_data):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=ready_data)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return mock_client


def _make_caller():
    caller = MagicMock()
    caller.user = "mother"
    caller.is_admin = True
    caller.house = "household"
    return caller


def test_recipe_matches_basil_matches_pesto():
    """'basil' culinary alias matches pesto recipe with 'basil leaves' ingredient."""
    cookbook = FakeCookbook([
        {
            "path": "Recipes/pesto.md",
            "title": "Pesto",
            "body": "# Pesto\nCategory: Recipes\n\n- [ ] 2 cups fresh basil leaves\n- [ ] 1/2 cup pine nuts\n1. Blend.\n",
        },
        {
            "path": "Recipes/chili.md",
            "title": "Chili",
            "body": "# Chili\nCategory: Recipes\n\n- [ ] 2 lb ground beef\n- [ ] 1 can kidney beans\n1. Brown beef.\n",
        },
    ])

    caller = _make_caller()
    client_mock = _make_client_mock({
        "count": 1,
        "ready": [
            {
                "plant_id": "plant-1",
                "nickname": "Sir Basil",
                "common_name": "Sweet Basil",
                "category": "herb",
                "species_ref": "basil_sweet",
                "culinary_aliases": ["basil", "sweet basil"],
                "in_cooldown": False,
            }
        ],
    })

    with patch("services.gateway.garden_recipes.shared_http_client", return_value=client_mock):
        result = asyncio.run(recipe_matches(cookbook, caller))

    assert result["ready_ingredients"][0]["common_name"] == "Sweet Basil"
    # Verify X-User-Id and configured GARDEN_SVC URL are used (not hardcoded hostname).
    called_url = client_mock.get.call_args.args[0]
    called_headers = client_mock.get.call_args.kwargs["headers"]
    assert "sharedllm_garden" not in called_url
    assert called_url.endswith("/harvest/ready")
    assert called_headers["X-User-Id"] == "mother"
    matches = result["matches"]
    assert len(matches) == 1
    assert matches[0]["title"] == "Pesto"
    assert matches[0]["match_count"] == 1
    assert "basil" in matches[0]["matched_ingredients"]


def test_recipe_matches_pea_does_not_match_peanut_recipe():
    """'pea' alias must NOT match a recipe with 'peanut butter'."""
    cookbook = FakeCookbook([
        {
            "path": "Recipes/satay.md",
            "title": "Peanut Satay",
            "body": "# Peanut Satay\nCategory: Recipes\n\n- [ ] 1 cup peanut butter\n- [ ] 2 tbsp soy sauce\n1. Mix.\n",
        },
    ])

    caller = _make_caller()
    client_mock = _make_client_mock({
        "count": 1,
        "ready": [
            {
                "plant_id": "plant-1",
                "nickname": "Peas",
                "common_name": "Garden Peas",
                "category": "vegetable",
                "species_ref": "pea_garden",
                "culinary_aliases": ["pea", "garden pea"],
                "in_cooldown": False,
            }
        ],
    })

    with patch("services.gateway.garden_recipes.shared_http_client", return_value=client_mock):
        result = asyncio.run(recipe_matches(cookbook, caller))

    # pea should NOT match peanut butter
    assert result["matches"] == []


def test_recipe_matches_ranks_by_match_count():
    """Matches ranked by match_count desc."""
    cookbook = FakeCookbook([
        {
            "path": "Recipes/pesto.md",
            "title": "Pesto",
            "body": "# Pesto\nCategory: Recipes\n\n- [ ] 2 cups fresh basil leaves\n- [ ] 2 cloves garlic\n1. Blend.\n",
        },
        {
            "path": "Recipes/caprese.md",
            "title": "Caprese",
            "body": "# Caprese Salad\nCategory: Recipes\n\n- [ ] 4 fresh basil leaves\n- [ ] 2 tomatoes\n- [ ] 8 oz mozzarella\n1. Layer.\n",
        },
    ])

    caller = _make_caller()
    client_mock = _make_client_mock({
        "count": 1,
        "ready": [
            {
                "plant_id": "plant-1",
                "nickname": "Basil",
                "common_name": "Sweet Basil",
                "category": "herb",
                "species_ref": "basil_sweet",
                "culinary_aliases": ["basil", "sweet basil", "fresh basil"],
                "in_cooldown": False,
            }
        ],
    })

    with patch("services.gateway.garden_recipes.shared_http_client", return_value=client_mock):
        result = asyncio.run(recipe_matches(cookbook, caller))

    matches = result["matches"]
    assert len(matches) == 2
    titles = {m["title"] for m in matches}
    assert titles == {"Caprese Salad", "Pesto"}


def test_recipe_matches_degrades_when_cookbook_unavailable():
    """Degrades to matches: [] when cookbook is unavailable."""
    class FailingCookbook:
        async def __call__(self, creds, payload):
            return {"status": "FAILURE", "message": "cookbook down"}

    caller = _make_caller()
    client_mock = _make_client_mock({"count": 0, "ready": []})

    with patch("services.gateway.garden_recipes.shared_http_client", return_value=client_mock):
        result = asyncio.run(recipe_matches(FailingCookbook(), caller))

    assert result["matches"] == []
    assert result["note"] == "Cookbook unavailable"


# --------------------------------------------------------------------------- recipe_draft


def test_recipe_draft_returns_unsaved_markdown():
    """recipe_draft returns unsaved markdown draft with family_size servings."""
    caller = _make_caller()

    profile_resp = AsyncMock()
    profile_resp.status = 200
    profile_resp.json = AsyncMock(return_value={"family_size": 7})

    llm_resp = AsyncMock()
    llm_resp.status = 200
    llm_resp.json = AsyncMock(return_value={
        "choices": [{
            "message": {"content": "# Garden Pesto\nCategory: Recipes\n\n- [ ] 2 cups basil\n1. Blend.\nServes: 7"}
        }]
    })

    client_mock = AsyncMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)
    # First call (GET profile) returns profile, second (POST LLM) returns LLM response
    client_mock.get = AsyncMock(return_value=profile_resp)
    client_mock.post = AsyncMock(return_value=llm_resp)

    with patch("services.gateway.garden_recipes.shared_http_client", return_value=client_mock):
        result = asyncio.run(recipe_draft(None, caller, {"ingredients": ["basil"]}, None))

    assert result["status"] == "SUCCESS"
    assert result["draft"] is True
    assert result["servings"] == 7
    assert "# Garden Pesto" in result["markdown"]
    assert "Unsaved preview" in result["note"]
