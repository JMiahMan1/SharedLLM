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
