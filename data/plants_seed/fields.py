"""Shared schema, enums, and record factory for the Jarvis Garden botanical seed catalog.

This module is the single source of truth for the read-only ``/data/plants-seed.db``
schema. It is imported by ``scripts/build_plants_seed.py`` (the builder) and by the
per-category data modules that ship the curated species. The Garden service reader
(``services/garden/plant_seed.py``) consumes the resulting SQLite file, so the column
names here must match what the reader expects.

Every record is one row in the ``plants`` table. Columns that do not apply to a
category (for example ``days_to_maturity`` on a houseplant) are stored as NULL.
"""

from __future__ import annotations

CATEGORIES = ("houseplant", "herb", "vegetable", "fruit")

# Enumerated values. The build script asserts that every non-null value belongs to
# these sets, and the reader relies on them for UI dropdowns and safety guardrails.
WATERING_METHODS = (
    "top_drain",
    "bottom_water",
    "soak_and_dry",
    "evenly_moist",
    "bark_soak",
    "air_bath",
)
DRYNESS_CUES = ("surface_damp", "top_1in_dry", "top_2in_dry", "mostly_dry", "bone_dry")
WATER_QUALITY = ("tap_ok", "filtered_or_rain_preferred")
LIGHT_LEVELS = ("low", "medium", "bright_indirect", "bright_direct", "full_sun", "partial_shade")
HUMIDITY_BANDS = ("low_tolerant", "average", "high_humidity")
MISTING_RULES = ("beneficial", "optional", "avoid_misting")
SOIL_MIXES = (
    "standard_potting",
    "chunky_aroid",
    "cactus_succulent",
    "orchid_bark",
    "african_violet",
    "moisture_retentive",
)
TOXICITY_LEVELS = ("non_toxic", "mild_irritant", "toxic_pets", "toxic_all")
FROST_TOLERANCE = (
    "tender",
    "half_hardy",
    "hardy",
    "tender_annual",
    "half_hardy_annual",
    "hardy_annual",
    "tender_perennial",
    "half_hardy_perennial",
    "hardy_perennial",
    "tender_biennial",
    "half_hardy_biennial",
    "hardy_biennial",
)
ACIDITY_CLASSES = ("high_acid", "low_acid")

# Columns that must be non-empty for each category (beyond the universal ones).
REQUIRED_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "houseplant": (
        "watering_interval_days",
        "watering_method",
        "dryness_cue",
        "water_quality",
        "light_level",
        "humidity_band",
        "misting_rule",
        "soil_mix",
        "toxicity_level",
    ),
    "herb": (
        "culinary_aliases",
        "pinching_bolting_notes",
        "regrowth_cadence",
        "herb_preservation_tips",
        "toxicity_level",
    ),
    "vegetable": (
        "usda_zone_min",
        "usda_zone_max",
        "frost_tolerance",
        "days_to_maturity",
        "plants_per_sqft",
        "crop_family",
        "culinary_aliases",
        "preservation_methods",
        "usda_acidity_class",
    ),
    "fruit": (
        "usda_zone_min",
        "usda_zone_max",
        "frost_tolerance",
        "days_to_maturity",
        "plants_per_sqft",
        "crop_family",
        "culinary_aliases",
        "preservation_methods",
        "usda_acidity_class",
    ),
}

# Minimum curated species per category enforced at build time.
MIN_COUNTS_BY_CATEGORY: dict[str, int] = {
    "houseplant": 50,
    "herb": 20,
    "vegetable": 40,
    "fruit": 25,
}

# Universal required columns (non-null, non-empty where text).
UNIVERSAL_REQUIRED = ("key", "category", "scientific_name", "common_name", "description")

# Enum column -> allowed values.
ENUM_COLUMNS: dict[str, tuple[str, ...]] = {
    "category": CATEGORIES,
    "watering_method": WATERING_METHODS,
    "dryness_cue": DRYNESS_CUES,
    "water_quality": WATER_QUALITY,
    "light_level": LIGHT_LEVELS,
    "humidity_band": HUMIDITY_BANDS,
    "misting_rule": MISTING_RULES,
    "soil_mix": SOIL_MIXES,
    "toxicity_level": TOXICITY_LEVELS,
    "frost_tolerance": FROST_TOLERANCE,
    "usda_acidity_class": ACIDITY_CLASSES,
}

# Known-toxic species that must never be recorded as non_toxic. Keys are the
# ``key`` values used in the catalog.
KNOWN_TOXIC_KEYS = {
    "lilium",                    # lilies (all Lilium)
    "epipremnum_aureum",         # pothos
    "monstera_deliciosa",        # monstera
    "spathiphyllum_wallisii",    # peace lily
    "dieffenbachia_seguine",     # dieffenbachia / dumb cane
    "cycas_revoluta",            # sago palm
    "zamioculcas_zamiifolia",    # ZZ plant
}

# Known low-acid vegetables that must require pressure canning (never water-bath
# unless pickled/acidified). Keys are the ``key`` values used in the catalog.
# Must match the ``key`` values in vegetable.json.
KNOWN_LOW_ACID_KEYS = {
    "green_bean_bush",
    "green_bean_pole",
    "corn_sweet",
    "pea_snap",
    "zucchini",
    "pumpkin",
    "carrot",
    "potato",
    "beet",
}


DEFAULTS: dict = {
    "category": None,
    "source": "curated",
    "scientific_name": "",
    "common_name": "",
    "family": "",
    "origin": "",
    "description": "",
    # --- Houseplant care ----------------------------------------------------
    # winter_multiplier is an *interval* multiplier (>= 1.0), not a divisor.
    # winter_days = watering_interval_days * winter_multiplier.
    # Values typically range 1.5-2.5: plants dry out more slowly in winter,
    # so the watering interval lengthens. E.g. interval 7 days x 2.0 = 14 days in winter.
    "watering_interval_days": None,
    "winter_multiplier": 2.0,
    "watering_method": None,
    "dryness_cue": None,
    "water_quality": None,
    "light_level": None,
    "temp_min_f": None,
    "temp_max_f": None,
    "cold_draft_sensitive": 0,
    "humidity_band": None,
    "misting_rule": None,
    "soil_mix": None,
    "fertilizing": "",
    "repot_interval_months": None,
    "rotation": "",
    "grooming_propagation": "",
    "common_pests": "",
    "symptoms": "",
    "toxicity_level": None,
    "toxicity_notes": "",
    # --- Outdoor agronomy ---------------------------------------------------
    "usda_zone_min": None,
    "usda_zone_max": None,
    "frost_tolerance": None,
    "germination_soil_temp_f": None,
    "seed_start_weeks_before_last_frost": None,
    "direct_sow_weeks_before_last_frost": None,
    "transplant_weeks_before_last_frost": None,
    "days_to_maturity": None,
    "harvest_window_days": None,
    "tall_trellis": 0,
    "plants_per_sqft": None,
    "yield_per_plant_lbs": None,
    "yield_low_lbs": None,
    "yield_typical_lbs": None,
    "yield_high_lbs": None,
    "crop_family": "",
    "companions": "",
    "antagonists": "",
    # --- Kitchen & preservation --------------------------------------------
    "culinary_aliases": "",
    "preservation_methods": "",
    "usda_acidity_class": None,
    "blanching_minutes": None,
    "preservation_conversion": "",
    # --- Herbs --------------------------------------------------------------
    "pinching_bolting_notes": "",
    "regrowth_cadence": "",
    "herb_preservation_tips": "",
}


def record(key: str, **overrides) -> dict:
    """Build one catalog record from the shared defaults plus category overrides."""
    entry: dict = dict(DEFAULTS)
    entry["key"] = key
    entry.update(overrides)
    return entry


#: Columns written to the ``plants`` table, in schema order.
COLUMNS: tuple[str, ...] = tuple(DEFAULTS.keys()) + ("key",)

#: FTS5 indexed columns (key is UNINDEXED, stored for the join).
FTS_COLUMNS = ("common_name", "scientific_name", "description", "culinary_aliases")
