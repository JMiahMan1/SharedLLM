"""Jarvis Garden botanical seed catalog.

The curated species catalog lives as version-controlled JSON data files under
``data/plants_seed/`` (one file per category: ``houseplants.json``,
``herbs.json``, ``vegetables.json``, ``fruits.json``). It is compiled by
``scripts/build_plants_seed.py`` into the read-only ``/data/plants-seed.db``
(SQLite + FTS5) consumed by ``services/garden/plant_seed.py``.

This package re-exports the shared schema/enums from :mod:`data.plants_seed.fields`.
Species records are *not* imported here as hardcoded Python lists; they are read
dynamically from the JSON files at build time so no species is hardcoded.
"""

from __future__ import annotations

from .fields import (  # noqa: F401
    CATEGORIES,
    DEFAULTS,
    ENUM_COLUMNS,
    REQUIRED_BY_CATEGORY,
    MIN_COUNTS_BY_CATEGORY,
    UNIVERSAL_REQUIRED,
    COLUMNS,
    FTS_COLUMNS,
    KNOWN_TOXIC_KEYS,
    KNOWN_LOW_ACID_KEYS,
    record,
)

__all__ = [
    "CATEGORIES",
    "DEFAULTS",
    "ENUM_COLUMNS",
    "REQUIRED_BY_CATEGORY",
    "MIN_COUNTS_BY_CATEGORY",
    "UNIVERSAL_REQUIRED",
    "COLUMNS",
    "FTS_COLUMNS",
    "KNOWN_TOXIC_KEYS",
    "KNOWN_LOW_ACID_KEYS",
    "record",
]
