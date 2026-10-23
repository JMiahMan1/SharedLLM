"""Tests for the USDA seed reader (services/health/food_seed.py)."""
from __future__ import annotations

from services.health.food_seed import FoodSeed


class TestFoodSeed:
    def test_search_returns_seed_shape(self, mini_seed_path):
        seed = FoodSeed(mini_seed_path)
        assert seed.present
        hits = seed.search("broccoli", 5)
        assert len(hits) == 1
        hit = hits[0]
        assert hit["code"] == "foundation:1111"
        assert hit["name"] == "Broccoli, raw"
        assert hit["brand"] == ""
        assert hit["image"] is None
        assert hit["source"] == "usda_fdc"
        assert hit["per_100g"]["calories"] == 34.0
        assert hit["per_100g"]["protein_g"] == 2.8
        assert hit["per_100g"]["sodium_mg"] is None  # missing stays missing, never zero
        assert hit["per_100g"]["sugar_g"] == 3.0  # 269 (Total Sugars) preferred over 269.3
        assert hit["portions"] == [
            {"label": "1 cup, chopped", "grams": 36.0},
            {"label": "1 serving", "grams": 21.0},
        ]

    def test_prefix_and_multi_token_search(self, mini_seed_path):
        seed = FoodSeed(mini_seed_path)
        assert [hit["code"] for hit in seed.search("broc", 5)] == ["foundation:1111"]
        codes = {hit["code"] for hit in seed.search("rice cooked", 5)}
        assert codes == {"sr_legacy:3333", "fndds:56205190"}

    def test_fndds_food_and_portions(self, mini_seed_path):
        seed = FoodSeed(mini_seed_path)
        hits = [hit for hit in seed.search("rice", 5) if hit["code"] == "fndds:56205190"]
        assert len(hits) == 1
        hit = hits[0]
        assert hit["name"] == "Rice, white, cooked"
        assert hit["per_100g"]["calories"] == 97.0
        assert hit["per_100g"]["protein_g"] == 2.0
        assert hit["per_100g"]["carbs_g"] is None  # amount missing in the fixture
        assert hit["portions"] == [{"label": "1 cup, cooked", "grams": 174.0}]

    def test_portions_are_capped(self, mini_seed_path):
        seed = FoodSeed(mini_seed_path)
        sr_hit = [hit for hit in seed.search("rice", 5) if hit["code"] == "sr_legacy:3333"][0]
        assert len(sr_hit["portions"]) == 12
        assert sr_hit["portions"][0] == {"label": "1 cup, cooked", "grams": 158.0}
        assert sr_hit["portions"][-1]["label"] == "1 item 11"

    def test_absent_seed_is_empty_not_an_error(self, tmp_path):
        seed = FoodSeed(tmp_path / "nope.db")
        assert seed.present is False
        assert seed.search("anything", 5) == []

    def test_empty_sqlite_file_is_absent(self, tmp_path):
        path = tmp_path / "empty.db"
        path.write_bytes(b"")
        seed = FoodSeed(path)
        assert seed.present is False

    def test_blank_query_returns_nothing(self, mini_seed_path):
        seed = FoodSeed(mini_seed_path)
        assert seed.search("!!!", 5) == []
        assert seed.search("", 5) == []
