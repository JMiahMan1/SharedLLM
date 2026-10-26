"""USDA-seed recipe estimate: parsing ingredients and weighing what the seed can."""
from __future__ import annotations

from services.health import recipe_estimate
from services.health.food_seed import FoodSeed


class TestParseIngredient:
    def test_reads_a_mass_measure(self):
        parsed = recipe_estimate.parse_ingredient("2 lb chicken")
        assert parsed["quantity"] == 2.0
        assert parsed["unit"] == "lb"
        assert parsed["name"] == "chicken"

    def test_reads_a_fraction(self):
        parsed = recipe_estimate.parse_ingredient("1/2 cup sugar")
        assert parsed["quantity"] == 0.5
        assert parsed["unit"] == "cup"
        assert parsed["name"] == "sugar"

    def test_reads_a_unicode_fraction(self):
        parsed = recipe_estimate.parse_ingredient("1\u00bd cups flour")
        assert parsed["quantity"] == 1.5
        assert parsed["unit"] == "cup"
        assert parsed["name"] == "flour"

    def test_drops_a_parenthetical_and_reads_the_can(self):
        parsed = recipe_estimate.parse_ingredient("1 (14 oz) can diced tomatoes")
        assert parsed["quantity"] == 1.0
        assert parsed["unit"] == "can"
        assert parsed["name"] == "diced tomatoes"

    def test_reads_a_size_word_as_the_unit(self):
        parsed = recipe_estimate.parse_ingredient("2 large eggs")
        assert parsed["quantity"] == 2.0
        assert parsed["unit"] == "large"
        assert parsed["name"] == "eggs"

    def test_strips_a_leading_of(self):
        assert recipe_estimate.parse_ingredient("1 cup of rice")["name"] == "rice"

    def test_keeps_a_line_with_no_quantity_as_unknown(self):
        parsed = recipe_estimate.parse_ingredient("salt to taste")
        assert parsed["quantity"] is None
        assert parsed["unit"] == ""
        assert parsed["name"] == "salt to taste"


class TestEstimate:
    def test_weighs_matches_and_names_the_unknowns(self, mini_seed_path):
        seed = FoodSeed(mini_seed_path)
        result = recipe_estimate.estimate_ingredients(
            ["2 cups broccoli", "1 cup rice", "2 lb chicken", "salt to taste"], seed
        )
        assert result["estimate"] is True
        assert result["source"] == "usda_fdc"
        assert result["seed"] == {"present": True}
        assert result["matched"] == 2
        assert result["unknown"] == 2

        broccoli = result["ingredients"][0]
        assert broccoli["status"] == "matched"
        assert broccoli["grams"] == 72.0  # 2 x the seed's "1 cup, chopped" (36 g)
        assert broccoli["nutrients"]["calories"] == 24.48

        rice = result["ingredients"][1]
        assert rice["status"] == "matched"
        assert rice["grams"] in (158.0, 174.0)  # sr_legacy or fndds portion

        chicken = result["ingredients"][2]
        assert chicken["status"] == "unknown"
        assert "No USDA match" in chicken["reason"]

        salt = result["ingredients"][3]
        assert salt["status"] == "unknown"
        assert salt["reason"] == "No quantity given"

        # Broccoli's fixture reports no carbs, so the total is partial, never a
        # guess: the matched rows still total what the seed does report.
        assert result["complete"] is False
        assert result["totals"] is not None

    def test_a_unit_the_seed_cannot_weigh_is_unknown(self, mini_seed_path):
        seed = FoodSeed(mini_seed_path)
        result = recipe_estimate.estimate_ingredients(["3 cloves broccoli"], seed)
        assert result["matched"] == 0
        assert result["unknown"] == 1
        assert "No USDA portion" in result["ingredients"][0]["reason"]

    def test_an_absent_seed_is_all_unknown(self, tmp_path):
        seed = FoodSeed(tmp_path / "nope.db")
        result = recipe_estimate.estimate_ingredients(["2 cups broccoli"], seed)
        assert result["seed"] == {"present": False}
        assert result["matched"] == 0
        assert result["unknown"] == 1
        assert result["totals"] is None
        assert "not installed" in result["ingredients"][0]["reason"]

    def test_an_empty_recipe_estimates_nothing(self, mini_seed_path):
        result = recipe_estimate.estimate_ingredients([], FoodSeed(mini_seed_path))
        assert result["ingredients"] == []
        assert result["matched"] == 0
        assert result["totals"] is None


class TestEstimateEndpoint:
    def test_estimates_through_the_api(self, client, mini_seed_path, monkeypatch):
        monkeypatch.setenv("FDC_SEED_PATH", str(mini_seed_path))
        resp = client.post("/food/estimate", json={"ingredients": ["2 cups broccoli", "2 lb chicken"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["estimate"] is True
        assert body["source"] == "usda_fdc"
        assert body["matched"] == 1
        assert body["unknown"] == 1
        assert body["ingredients"][0]["status"] == "matched"
        assert body["ingredients"][1]["status"] == "unknown"
