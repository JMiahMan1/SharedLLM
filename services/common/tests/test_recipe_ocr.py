"""Recipe photo import: turning a vision-OCR result into an editable draft."""
from services.common import recipe_ocr


def test_a_structured_reading_becomes_a_draft():
    result = {
        "full_text": "Nana's Pancakes\n2 cups flour\n1 tsp salt\n\nMix. Bake.",
        "recipe": {
            "title": "Nana's Pancakes",
            "servings": "4",
            "prep_minutes": 10,
            "cook_minutes": 20,
            "total_minutes": 30,
            "ingredients": ["2 cups flour", "1 tsp salt"],
            "steps": ["Mix the dry ingredients.", "Bake for 20 minutes."],
            "notes": "Best with butter.",
        },
        "uncertain": ["flour (line 2)"],
    }
    draft = recipe_ocr.parse(result)
    assert draft["missing"] == []
    assert draft["uncertain"] == ["flour (line 2)"]
    assert draft["raw_text"].startswith("Nana's Pancakes")
    recipe = draft["recipe"]
    assert recipe["title"] == "Nana's Pancakes"
    assert recipe["ingredients"] == ["2 cups flour", "1 tsp salt"]
    assert recipe["instructions"] == ["Mix the dry ingredients.", "Bake for 20 minutes."]
    assert recipe["prep_minutes"] == 10
    assert recipe["cook_minutes"] == 20
    assert recipe["total_minutes"] == 30
    assert recipe["yield"] == "4"
    assert recipe["notes"] == "Best with butter."


def test_a_plain_transcription_without_a_recipe_keeps_the_text_and_names_what_is_missing():
    draft = recipe_ocr.parse({"full_text": "some handwriting nobody could read"})
    assert draft["recipe"]["title"] == ""
    assert draft["recipe"]["ingredients"] == []
    assert draft["recipe"]["instructions"] == []
    assert draft["missing"] == ["title", "ingredients", "steps"]
    assert draft["raw_text"] == "some handwriting nobody could read"
    assert draft["uncertain"] == []


def test_a_missing_result_is_an_empty_draft_not_a_guess():
    draft = recipe_ocr.parse(None)
    assert draft["recipe"]["title"] == ""
    assert draft["missing"] == ["title", "ingredients", "steps"]


def test_junk_values_are_dropped_rather_than_invented():
    result = {
        "recipe": {
            "title": "  Soup  ",
            "ingredients": ["stock", "", None, "   "],
            "steps": "Simmer.\nServe.",  # a string is split into lines
            "prep_minutes": "10 min",
            "cook_minutes": None,
            "total_minutes": "not a number",
            "servings": 6,
        }
    }
    recipe = recipe_ocr.parse(result)["recipe"]
    assert recipe["title"] == "Soup"
    assert recipe["ingredients"] == ["stock"]
    assert recipe["instructions"] == ["Simmer.", "Serve."]
    assert recipe["prep_minutes"] == 10
    assert recipe["cook_minutes"] is None
    assert recipe["total_minutes"] is None
    assert recipe["yield"] == "6"


def test_an_hour_is_converted_to_minutes():
    recipe = recipe_ocr.parse({"recipe": {"title": "Roast", "cook_minutes": "1 hour"}})["recipe"]
    assert recipe["cook_minutes"] == 60
