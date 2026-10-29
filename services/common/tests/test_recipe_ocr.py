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


#: A typed recipe card, transcribed as the vision model reads it off the page.
TYPED_CARD_TEXT = """Grandma's Banana Bread
Serves 8
Prep 15 min | Cook 55 min | Total 1 hr 10 min

Ingredients
3 ripe bananas, mashed
1/2 cup butter, melted
3/4 cup sugar
1 egg, beaten
1 1/2 cups all-purpose flour
1 tsp baking soda

Directions
Preheat the oven to 350F and grease a loaf pan.
Mash the bananas and stir in the melted butter and sugar.
Beat in the egg, then fold in the flour and baking soda.
Bake for 55 minutes, until a toothpick comes out clean."""


def test_a_typed_recipe_card_becomes_a_complete_draft():
    draft = recipe_ocr.parse(
        {
            "full_text": TYPED_CARD_TEXT,
            "recipe": {
                "title": "Grandma's Banana Bread",
                "servings": "8",
                "prep_minutes": 15,
                "cook_minutes": 55,
                "total_minutes": 70,
                "ingredients": [
                    "3 ripe bananas, mashed",
                    "1/2 cup butter, melted",
                    "3/4 cup sugar",
                    "1 egg, beaten",
                    "1 1/2 cups all-purpose flour",
                    "1 tsp baking soda",
                ],
                "steps": [
                    "Preheat the oven to 350F and grease a loaf pan.",
                    "Mash the bananas and stir in the melted butter and sugar.",
                    "Beat in the egg, then fold in the flour and baking soda.",
                    "Bake for 55 minutes, until a toothpick comes out clean.",
                ],
                "notes": "",
            },
            "uncertain": [],
        }
    )
    assert draft["missing"] == []
    assert draft["uncertain"] == []
    assert draft["raw_text"].startswith("Grandma's Banana Bread")
    recipe = draft["recipe"]
    assert recipe["title"] == "Grandma's Banana Bread"
    assert len(recipe["ingredients"]) == 6
    assert recipe["ingredients"][0] == "3 ripe bananas, mashed"
    assert len(recipe["instructions"]) == 4
    assert recipe["prep_minutes"] == 15
    assert recipe["cook_minutes"] == 55
    assert recipe["total_minutes"] == 70
    assert recipe["yield"] == "8"


#: A handwritten index card: the model reads the title and the list but cannot
#: make out the cooking step, and flags the words it had to guess at.
HANDWRITTEN_CARD_TEXT = """Ma's Chili
1 lb ground beef
1 onion, chopped
2 cans kidney beans
1 big can tomatoes
chili powder - a few spoons

brown the meat w/ the onion, add the rest, simmer"""


def test_a_handwritten_card_keeps_the_raw_text_and_names_what_it_could_not_read():
    draft = recipe_ocr.parse(
        {
            "full_text": HANDWRITTEN_CARD_TEXT,
            "recipe": {
                "title": "Ma's Chili",
                "ingredients": [
                    "1 lb ground beef",
                    "1 onion, chopped",
                    "2 cans kidney beans",
                    "1 big can tomatoes",
                    "chili powder - a few spoons",
                ],
                "steps": [],
            },
            "uncertain": ["simmer (line 8)", "a few spoons (line 5)"],
        }
    )
    # The step could not be read, so it is named rather than invented.
    assert draft["missing"] == ["steps"]
    assert draft["uncertain"] == ["simmer (line 8)", "a few spoons (line 5)"]
    assert draft["raw_text"].startswith("Ma's Chili")
    recipe = draft["recipe"]
    assert recipe["title"] == "Ma's Chili"
    assert recipe["ingredients"][0] == "1 lb ground beef"
    assert recipe["instructions"] == []
    # The card states no yield, so it stays empty rather than guessed.
    assert recipe["yield"] == ""


def test_the_models_own_suggestions_are_passed_through_for_the_cook_to_accept():
    draft = recipe_ocr.parse(
        {
            "recipe": {"title": "Sugar Cookies", "ingredients": ["1 cup sugar"], "steps": ["Bake."]},
            "uncertain": ["eved water"],
            "suggestions": {"eved water": ["cold water", "cold tap water"]},
        }
    )
    assert draft["suggestions"] == {"eved water": ["cold water", "cold tap water"]}


def test_a_near_miss_word_is_offered_a_fix_without_the_model():
    # The model flagged it but proposed nothing, so the offline kitchen list fills in.
    draft = recipe_ocr.parse(
        {"recipe": {"ingredients": ["1 cup suger"], "steps": ["Bake."]}, "uncertain": ["1 cup suger"]}
    )
    assert draft["suggestions"]["1 cup suger"] == ["1 cup sugar"]


def test_a_measure_the_model_could_not_read_offers_the_common_units():
    draft = recipe_ocr.parse({"uncertain": ["2 ... vanilla"], "recipe": {}})
    assert draft["suggestions"]["2 ... vanilla"] == ["2 tsp vanilla", "2 tbsp vanilla", "2 cup vanilla"]


def test_a_flagged_word_with_no_honest_reading_gets_no_suggestion():
    # "eved" is not close to any kitchen word, so nothing is invented for it.
    draft = recipe_ocr.parse({"uncertain": ["eved water"], "recipe": {}})
    assert draft["suggestions"]["eved water"] == []


def test_a_where_it_was_note_is_stripped_before_reading_the_word():
    draft = recipe_ocr.parse({"uncertain": ["simer (line 8)"], "recipe": {}})
    # The suggestion is for the word, not the line annotation.
    assert draft["suggestions"]["simer (line 8)"] == ["simmer"]


def test_a_word_the_model_already_read_gets_no_suggestion():
    draft = recipe_ocr.parse({"uncertain": ["1 cup sugar"], "recipe": {}})
    assert draft["suggestions"]["1 cup sugar"] == []


def test_a_plain_result_has_no_suggestions():
    draft = recipe_ocr.parse({"full_text": "nothing structured here"})
    assert draft["suggestions"] == {}
