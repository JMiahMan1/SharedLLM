"""Nutrition-facts labels read from OCR text into a draft, never guessed."""
from services.common import nutrition_label

US_LABEL = """Nutrition Facts
8 servings per container
Serving size 2/3 cup (55g)
Amount per serving
Calories 230
Total Fat 8g 10%
Saturated Fat 1g
Trans Fat 0g
Sodium 160mg 7%
Total Carbohydrate 37g 13%
Dietary Fiber 4g
Total Sugars 12g
Includes 10g Added Sugars
Protein 3g"""


def test_a_us_label_reads_every_printed_value():
    out = nutrition_label.parse(US_LABEL)
    draft = out["draft"]
    assert draft["calories"] == 230
    assert draft["fat_g"] == 8 and draft["carbs_g"] == 37 and draft["protein_g"] == 3
    assert draft["fiber_g"] == 4 and draft["sugar_g"] == 12 and draft["sodium_mg"] == 160
    assert draft["serving_label"] == "2/3 cup" and draft["serving_grams"] == 55
    assert out["missing"] == []


def test_saturated_fat_never_stands_in_for_total_fat():
    out = nutrition_label.parse("Calories 90\nSaturated Fat 2g\nProtein 4g")
    assert out["draft"]["fat_g"] is None
    assert out["missing"] == ["carbs_g", "fat_g"]


def test_a_per_100g_label_with_kcal_and_commas():
    out = nutrition_label.parse("Nutrition per 100 g\nEnergy 1500 kJ / 357 kcal\nFat 1,5 g\nCarbohydrate 72 g\nProtein 12 g")
    assert out["draft"]["calories"] == 357
    assert out["draft"]["fat_g"] == 1.5
    assert out["per_100g"] is True


def test_no_label_means_nothing_found():
    out = nutrition_label.parse("A photo of a sandwich")
    assert out["found"] == []
    assert out["missing"] == ["calories", "protein_g", "carbs_g", "fat_g"]


#: A second real label: the total fat/sugar lines must win over the saturated
#: and added-sugar sub-lines printed beneath them.
YOGURT_LABEL = """Nutrition Facts
4 servings per container
Serving size 1 cup (240ml)
Calories 150
Total Fat 4.5g 6%
Saturated Fat 2.5g 13%
Trans Fat 0g
Cholesterol 15mg 5%
Sodium 130mg 6%
Total Carbohydrate 20g 7%
Dietary Fiber 0g
Total Sugars 18g
Includes 12g Added Sugars 24%
Protein 8g"""


def test_a_second_label_reads_the_totals_not_the_sub_lines():
    out = nutrition_label.parse(YOGURT_LABEL)
    draft = out["draft"]
    assert draft["calories"] == 150
    assert draft["fat_g"] == 4.5  # not the 2.5g saturated
    assert draft["carbs_g"] == 20 and draft["protein_g"] == 8
    assert draft["sugar_g"] == 18  # not the 12g added
    assert draft["sodium_mg"] == 130 and draft["fiber_g"] == 0
    assert draft["serving_label"] == "1 cup" and draft["serving_grams"] == 240
    assert out["missing"] == []


def test_a_label_without_a_serving_size_still_reads_the_numbers():
    out = nutrition_label.parse("Calories 100\nTotal Fat 3g\nTotal Carbohydrate 12g\nProtein 5g")
    assert out["missing"] == []
    assert out["draft"]["calories"] == 100
    assert out["draft"]["serving_label"] is None and out["draft"]["serving_grams"] is None
