"""Tests for services/garden/plant_seed.py (PlantSeed reader).

Mirrors the FoodSeed read-only loader pattern: lazy read-only sqlite URI
connection, graceful absent handling (_absent flag), FTS5 bm25 search with
ORDER BY bm25, plus get() and list_by_category().
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.plants_seed import fields  # noqa: E402
from scripts.build_plants_seed import build_seed  # noqa: E402
from services.garden.plant_seed import PlantSeed  # noqa: E402


def _build_db(tmp_path) -> Path:
    out = tmp_path / "plants-seed.db"
    build_seed(out)
    return out


@pytest.fixture()
def db_path(tmp_path):
    return _build_db(tmp_path)


def test_reader_search_returns_best_match_first(db_path):
    seed = PlantSeed(db_path)
    assert seed.present is True
    results = seed.search("monstera", 5)
    assert len(results) > 0
    # First hit should mention monstera.
    assert "monstera" in results[0]["common_name"].lower() or "monstera" in results[0]["scientific_name"].lower()
    assert results[0]["toxicity_level"] != "non_toxic"  # monstera is toxic_pets


def test_reader_search_bm25_ordering(db_path):
    seed = PlantSeed(db_path)
    a = seed.search("snake plant", 3)
    b = seed.search("snake plant", 3)
    assert a == b  # deterministic


def test_reader_get_by_key(db_path):
    seed = PlantSeed(db_path)
    row = seed.get("monstera_deliciosa")
    assert row is not None
    assert row["key"] == "monstera_deliciosa"
    assert row["category"] == "houseplant"
    assert row["toxicity_level"] == "toxic_pets"
    assert row["watering_interval_days"] == 7


def test_reader_get_missing_key(db_path):
    seed = PlantSeed(db_path)
    assert seed.get("does_not_exist") is None


def test_reader_list_by_category(db_path):
    seed = PlantSeed(db_path)
    houseplants = seed.list_by_category("houseplant")
    assert len(houseplants) >= fields.MIN_COUNTS_BY_CATEGORY["houseplant"]
    assert all(r["category"] == "houseplant" for r in houseplants)
    # Ordered by common_name.
    names = [r["common_name"] for r in houseplants]
    assert names == sorted(names)


def test_reader_list_vegetables(db_path):
    seed = PlantSeed(db_path)
    veg = seed.list_by_category("vegetable")
    assert len(veg) >= fields.MIN_COUNTS_BY_CATEGORY["vegetable"]


def test_reader_absent_graceful(tmp_path):
    seed = PlantSeed(tmp_path / "nonexistent.db")
    assert seed.present is False
    assert seed.search("anything", 5) == []
    assert seed.get("anything") is None
    assert seed.list_by_category("houseplant") == []


def test_reader_read_only_cannot_write(db_path):
    seed = PlantSeed(db_path)
    conn = seed._connect()
    with pytest.raises(sqlite3.Error):
        conn.execute("INSERT INTO plants (key, category) VALUES ('x','houseplant')")


def test_reader_search_category_filter(db_path):
    seed = PlantSeed(db_path)
    # Search across all categories.
    all_results = seed.search("mint", 20)
    categories = {r["category"] for r in all_results}
    assert categories  # at least one category matched
    # Restrict to herbs only.
    herb_results = seed.search("mint", 20, category="herb")
    assert len(herb_results) > 0
    assert all(r["category"] == "herb" for r in herb_results)
    # Every herb result should still contain "mint" in name or description.
    for r in herb_results:
        text = (r["common_name"] + " " + (r.get("description") or "")).lower()
        assert "mint" in text


def test_reader_search_usda_zone_filter(db_path):
    seed = PlantSeed(db_path)
    # Zone 5 should exclude tender herbs like basil (zone 10-11) but include hardy ones.
    zone5 = seed.search("basil", 20, usda_zone=5)
    zone5_keys = {r["key"] for r in zone5}
    assert "basil_sweet" not in zone5_keys  # basil needs zone 10-11
    # Zone 10 should include basil.
    zone10 = seed.search("basil", 20, usda_zone=10)
    zone10_keys = {r["key"] for r in zone10}
    assert "basil_sweet" in zone10_keys


def test_reader_search_category_and_zone_together(db_path):
    seed = PlantSeed(db_path)
    results = seed.search("thyme", 20, category="herb", usda_zone=5)
    assert len(results) > 0
    assert all(r["category"] == "herb" for r in results)
    for r in results:
        assert r["usda_zone_min"] is not None and r["usda_zone_max"] is not None
        assert r["usda_zone_min"] <= 5 <= r["usda_zone_max"]


def test_reader_list_by_category_usda_zone(db_path):
    seed = PlantSeed(db_path)
    # All herbs.
    all_herbs = seed.list_by_category("herb")
    assert len(all_herbs) >= fields.MIN_COUNTS_BY_CATEGORY["herb"]
    # Herbs suitable for zone 5 (basil needs zone 10-11, should be excluded).
    zone5_herbs = seed.list_by_category("herb", usda_zone=5)
    zone5_keys = {r["key"] for r in zone5_herbs}
    assert "basil_sweet" not in zone5_keys
    assert len(zone5_herbs) < len(all_herbs)
    # Every result must be a herb suitable for zone 5.
    for r in zone5_herbs:
        assert r["category"] == "herb"
        assert r["usda_zone_min"] <= 5 <= r["usda_zone_max"]


def test_reader_list_by_category_invalid_category(db_path):
    seed = PlantSeed(db_path)
    results = seed.list_by_category("not_a_category")
    assert results == []
