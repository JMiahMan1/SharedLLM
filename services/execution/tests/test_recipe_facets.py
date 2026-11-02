"""What the Recipes page browses by, read from the recipe's own text."""
from services.execution.handlers.recipe_facets import facets

CHILI = """Tags: #level-2 #30-minutes
Times: prep 15 min · cook 30 min · servings 6
Source: https://www.food.com/recipe/chili - imported from food.com
Image: .attachments.42/chili.jpg
Added by: mom

## Ingredients
- [ ] 2 lb chicken thighs
- [ ] 4 cups chicken broth
- [ ] 2 cans white beans

## Steps
1. Simmer.

## Nutrition
*Per serving, as stated by the submitter - foodcom:* 337.8 kcal · 9.1 g fat
"""


def test_a_full_recipe_note():
    f = facets("White Chicken Chili", CHILI)
    assert f["meal"] == "soup" and f["proteins"] == ["chicken"]
    assert f["total_minutes"] == 45 and f["servings"] == 6 and f["calories"] == 338
    assert f["level"] == 2 and f["owner"] == "mom" and f["image"] == ".attachments.42/chili.jpg"
    assert f["ingredients"] == 3 and "30-minutes" in f["tags"]


def test_broth_alone_does_not_make_a_meat_dish():
    body = "## Ingredients\n- 1 cup rice\n- 2 cups chicken broth\n"
    assert facets("Rice Pilaf", body)["proteins"] == ["vegetarian"]


def test_the_meal_comes_from_tags_first_then_the_title():
    assert facets("Banana Bread", "Tags: #breakfast\n")["meal"] == "breakfast"
    assert facets("Chocolate Chip Cookies", "")["meal"] == "dessert"
    assert facets("Sunday Pot Roast", "## Ingredients\n- 3 lb chuck roast\n")["proteins"] == ["beef"]
    assert facets("Shrimp Tacos", "## Ingredients\n- 1 lb shrimp\n- bacon\n")["proteins"] == ["pork", "seafood"]


def test_the_photographed_original_is_the_cover_when_there_is_no_photo():
    card = "## Notes\nChill.\n![card.jpg](.attachments.7/card.jpg)\n"
    assert facets("Sugar Cookies", card)["image"] == ".attachments.7/card.jpg"
    assert facets("Sugar Cookies", "Image: .attachments.7/dish.jpg\n" + card)["image"] == ".attachments.7/dish.jpg"


def test_a_recipe_is_named_by_its_stored_title_not_its_file_name():
    from services.execution.handlers.recipe_import import note_title

    stored = "# 20 Minute White Bean Chili\nCategory: Recipes\n\nTags: #soup\n"
    assert note_title(stored, "/Recipes/20-minute-white-bean-chili.md") == "20 Minute White Bean Chili"
    assert note_title("Tags: #soup\n", "/Recipes/beef-and-bean-burritos.md") == "Beef and Bean Burritos"
    assert note_title("# 5-tacos\nCategory: Recipes\n", "/Recipes/5-tacos.md") == "5 Tacos"
    assert note_title("# Mom's Chili-Mac\nCategory: Recipes\n", "/Recipes/x.md") == "Mom's Chili-Mac"
