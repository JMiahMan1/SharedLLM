"""The offline recipe index: building the seed and reading it back."""
import csv
import json

from scripts.build_recipe_seed import build_seed
from services.execution.handlers import recipe_seed
from services.execution.handlers.recipe_seed import RecipeSeed

SPINE_HEADERS = [
    "RecipeId",
    "Name",
    "AuthorName",
    "CookTime",
    "PrepTime",
    "TotalTime",
    "Images",
    "RecipeCategory",
    "Keywords",
    "RecipeIngredientQuantities",
    "RecipeIngredientParts",
    "RecipeInstructions",
    "AggregatedRating",
    "ReviewCount",
    "RecipeServings",
]


def _spine_row(rid, name, author, servings, parts, steps, quantities, **overrides):
    row = {
        "RecipeId": str(rid),
        "Name": name,
        "AuthorName": author,
        "CookTime": "PT3H",
        "PrepTime": "PT10M",
        "TotalTime": "PT3H10M",
        "Images": 'c("https://example.com/blue.jpg")',
        "RecipeCategory": "Frozen Desserts",
        "Keywords": 'c("Dessert")',
        "RecipeIngredientQuantities": quantities,
        "RecipeIngredientParts": parts,
        "RecipeInstructions": steps,
        "AggregatedRating": "4.5",
        "ReviewCount": "4",
        "RecipeServings": str(servings),
    }
    row.update(overrides)
    return row


def _write_spine(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SPINE_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_mirror(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "author_name", "ingredients"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _mirror_cell(items):
    return json.dumps(items)


BASE_ROWS = [
    _spine_row(
        100,
        "Blueberry Delight",
        "Dancer",
        4,
        'c("blueberries", "vanilla yogurt")',
        'c("Mix everything.", "Freeze until firm.")',
        'c("4", "1/4")',
    ),
    _spine_row(
        200,
        "Simple Toast",
        "Cooker",
        2,
        'c("bread", "butter")',
        'c("Toast the bread.", "Butter it.")',
        'c("2", "1")',
        Images="c()",
        RecipeCategory="Breakfast",
        Keywords='c("Easy", "5 Ingredients or Less")',
        PrepTime="PT2M",
        CookTime="PT3M",
        TotalTime="PT5M",
        AggregatedRating="4.0",
        ReviewCount="2",
    ),
    # One step is not a recipe; the builder must drop it.
    _spine_row(
        300,
        "Water",
        "Nobody",
        1,
        'c("water")',
        'c("Pour.")',
        'c("1 cup")',
    ),
]

BASE_MIRROR = [
    {
        "name": "Blueberry Delight",
        "author_name": "Dancer",
        "ingredients": _mirror_cell(
            [
                {
                    "quantity": "4",
                    "unit": "cups",
                    "name": "blueberries",
                    "description": "fresh or frozen",
                },
                {"quantity": "0.25", "unit": "cup", "name": "vanilla yogurt", "description": ""},
            ]
        ),
    },
]


def _build(tmp_path, spine_rows=None, mirror_rows=None, **kwargs):
    spine = tmp_path / "recipes.csv"
    mirror = tmp_path / "parsed.csv"
    out = tmp_path / "recipe-seed.db"
    _write_spine(spine, spine_rows if spine_rows is not None else BASE_ROWS)
    _write_mirror(mirror, mirror_rows if mirror_rows is not None else BASE_MIRROR)
    stats = build_seed(str(spine), str(out), parsed_csv=str(mirror), log=lambda *_: None, **kwargs)
    return stats, out


def test_builds_the_seed_and_searches_it(tmp_path):
    stats, out = _build(tmp_path)
    assert stats["rows"] == 3
    assert stats["kept"] == 2
    assert stats["joined"] == 1
    assert stats["fallback"] == 1

    seed = RecipeSeed(str(out))
    assert seed.present is True

    hits = seed.search("blueberry")
    assert [hit["title"] for hit in hits] == ["Blueberry Delight"]
    hit = hits[0]
    assert hit["source"] == "foodcom"
    assert hit["site"] == "food.com"
    assert hit["id"] == 100
    assert hit["total_minutes"] == 190
    assert hit["source_url"].endswith("-100")
    assert hit["image"] == "https://example.com/blue.jpg"

    # Prefix search answers while typing, and stray input stays calm.
    assert seed.search("blueb")
    assert seed.search("blueberry")[0]["id"] == 100
    assert seed.search("zzzz") == []
    assert seed.search("") == []


def test_fetch_returns_the_full_recipe(tmp_path):
    _, out = _build(tmp_path)
    seed = RecipeSeed(str(out))

    recipe = seed.fetch(100)
    assert recipe is not None
    assert recipe["title"] == "Blueberry Delight"
    assert recipe["ingredients"] == [
        "4 cups blueberries, fresh or frozen",
        "0.25 cup vanilla yogurt",
    ]
    assert recipe["instructions"] == ["Mix everything.", "Freeze until firm."]
    assert recipe["prep_minutes"] == 10
    assert recipe["cook_minutes"] == 180
    assert recipe["total_minutes"] == 190
    assert recipe["yield"] == "4 servings"
    assert recipe["image"] == "https://example.com/blue.jpg"
    assert recipe["site"] == "food.com"
    assert seed.fetch(999999) is None


def test_mirror_misses_fall_back_to_the_spine_lines(tmp_path):
    _, out = _build(tmp_path)
    seed = RecipeSeed(str(out))

    recipe = seed.fetch(200)
    assert recipe is not None
    assert recipe["ingredients"] == ["2 bread", "1 butter"]
    assert recipe["image"] is None


def test_ambiguous_titles_do_not_borrow_the_wrong_ingredients(tmp_path):
    spine_rows = [
        _spine_row(
            400,
            "Two Ways",
            "Chef X",
            2,
            'c("salt", "pepper")',
            'c("Mix.", "Serve.")',
            'c("1", "1")',
        )
    ]
    mirror_rows = [
        {
            "name": "Two Ways",
            "author_name": "Chef A",
            "ingredients": _mirror_cell(
                [{"quantity": "9", "unit": "lbs", "name": "wrong thing", "description": ""}]
            ),
        },
        {
            "name": "Two Ways",
            "author_name": "Chef B",
            "ingredients": _mirror_cell(
                [{"quantity": "8", "unit": "lbs", "name": "other wrong thing", "description": ""}]
            ),
        },
    ]
    _, out = _build(tmp_path, spine_rows=spine_rows, mirror_rows=mirror_rows)
    seed = RecipeSeed(str(out))
    recipe = seed.fetch(400)
    assert recipe is not None
    assert recipe["ingredients"] == ["1 salt", "1 pepper"]


def test_a_missing_seed_only_loses_offline_results(tmp_path):
    seed = RecipeSeed(str(tmp_path / "absent.db"))
    assert seed.present is False
    assert seed.search("blueberry") == []
    assert seed.fetch(1) is None

    # A file that is not a seed is just as calm.
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    seed = RecipeSeed(str(empty))
    assert seed.present is False
    assert seed.fetch(1) is None


def test_count_only_reports_without_writing(tmp_path):
    stats, out = _build(tmp_path, count_only=True)
    assert stats["kept"] == 2
    assert not out.exists()


def test_get_seed_reads_the_env_override(tmp_path, monkeypatch):
    _, out = _build(tmp_path)
    monkeypatch.setenv("RECIPE_SEED_PATH", str(out))
    monkeypatch.setattr(recipe_seed, "_SEED_CACHE", {})

    first = recipe_seed.get_seed()
    second = recipe_seed.get_seed()
    assert first is second
    assert first.present is True
    assert first.search("blueberry")[0]["id"] == 100
