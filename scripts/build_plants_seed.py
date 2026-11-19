#!/usr/bin/env python3
"""Build the read-only Jarvis Garden botanical seed database.

Reads the curated, version-controlled species catalog from JSON data files under
``data/plants_seed/`` (one file per category), validates every record against the
shared schema/enums/safety rules in ``data/plants_seed/fields.py``, and writes a
read-only SQLite+FTS5 seed at ``/data/plants-seed.db`` (overridable with ``--out``).

The finished seed is consumed read-only by the Garden service reader
(``services/garden/plant_seed.py``). A missing seed is degraded gracefully, never
a hard failure.

Usage:
    python scripts/build_plants_seed.py [--work-dir .tmp/plants] [--out data/plants/plants-seed.db]

The build writes to ``<out>.building`` inside the work dir then atomically replaces
``--out``. Counts per category are asserted against ``MIN_COUNTS_BY_CATEGORY``
(overridable with ``--expect``); a mismatch stops the build so a changed catalog is
inspected instead of silently imported.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.plants_seed import fields  # noqa: E402  (ROOT must be on sys.path first)

CATALOG_DIR = ROOT / "data" / "plants_seed"

# Column -> SQLite storage class. Numeric agronomy/harvest columns are REAL or
# INTEGER; everything descriptive is TEXT.
COLUMN_TYPES: dict[str, str] = {
    "category": "TEXT",
    "source": "TEXT",
    "scientific_name": "TEXT",
    "common_name": "TEXT",
    "family": "TEXT",
    "origin": "TEXT",
    "description": "TEXT",
    "watering_interval_days": "INTEGER",
    "winter_multiplier": "REAL",
    "watering_method": "TEXT",
    "dryness_cue": "TEXT",
    "water_quality": "TEXT",
    "light_level": "TEXT",
    "temp_min_f": "INTEGER",
    "temp_max_f": "INTEGER",
    "cold_draft_sensitive": "INTEGER",
    "humidity_band": "TEXT",
    "misting_rule": "TEXT",
    "soil_mix": "TEXT",
    "fertilizing": "TEXT",
    "repot_interval_months": "INTEGER",
    "rotation": "TEXT",
    "grooming_propagation": "TEXT",
    "common_pests": "TEXT",
    "symptoms": "TEXT",
    "toxicity_level": "TEXT",
    "toxicity_notes": "TEXT",
    "usda_zone_min": "INTEGER",
    "usda_zone_max": "INTEGER",
    "frost_tolerance": "TEXT",
    "germination_soil_temp_f": "INTEGER",
    "seed_start_weeks_before_last_frost": "INTEGER",
    "direct_sow_weeks_before_last_frost": "INTEGER",
    "transplant_weeks_before_last_frost": "INTEGER",
    "days_to_maturity": "INTEGER",
    "harvest_window_days": "INTEGER",
    "tall_trellis": "INTEGER",
    "plants_per_sqft": "REAL",
    "yield_per_plant_lbs": "REAL",
    "yield_low_lbs": "REAL",
    "yield_typical_lbs": "REAL",
    "yield_high_lbs": "REAL",
    "crop_family": "TEXT",
    "companions": "TEXT",
    "antagonists": "TEXT",
    "culinary_aliases": "TEXT",
    "preservation_methods": "TEXT",
    "usda_acidity_class": "TEXT",
    "blanching_minutes": "INTEGER",
    "preservation_conversion": "TEXT",
    "pinching_bolting_notes": "TEXT",
    "regrowth_cadence": "TEXT",
    "herb_preservation_tips": "TEXT",
}

# COLUMNS ends with "key" (added in fields); ensure it is TEXT PRIMARY KEY.
_COLUMN_DEFS = ", ".join(
    f"{name} {COLUMN_TYPES.get(name, 'TEXT')}" + (" PRIMARY KEY" if name == "key" else "")
    for name in fields.COLUMNS
)


def _load_catalog(catalog_dir: Path = CATALOG_DIR) -> dict[str, list[dict]]:
    """Load every ``<category>.json`` under ``catalog_dir``."""
    catalog: dict[str, list[dict]] = {category: [] for category in fields.CATEGORIES}
    for path in sorted(catalog_dir.glob("*.json")):
        category = path.stem
        if category not in fields.CATEGORIES:
            print(f"warning: skipping unknown catalog file {path.name}", file=sys.stderr)
            continue
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise SystemExit(f"{path.name}: top-level JSON must be a list of records")
        for record in data:
            record["category"] = category
            catalog[category].append(record)
    return catalog


def _text(value) -> str:
    return "" if value is None else str(value)


def _validate_record(record: dict, category: str) -> list[str]:
    """Return a list of validation problem messages for one record (empty = ok)."""
    problems: list[str] = []
    key = record.get("key", "<missing key>")

    # Universal required columns present and non-empty.
    for column in fields.UNIVERSAL_REQUIRED:
        value = record.get(column)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"{key}: missing required {column}")

    # Category-specific required columns present and non-null/non-empty.
    for column in fields.REQUIRED_BY_CATEGORY.get(category, ()):
        value = record.get(column)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"{key}: missing category-required {column}")

    # Enum columns belong to allowed sets (when non-null).
    for column, allowed in fields.ENUM_COLUMNS.items():
        value = record.get(column)
        if value is not None and value not in allowed:
            problems.append(f"{key}: {column}={value!r} not in {allowed}")

    # Known-toxic species must never be non_toxic.
    if key in fields.KNOWN_TOXIC_KEYS and record.get("toxicity_level") == "non_toxic":
        problems.append(f"{key}: known-toxic species marked non_toxic")

    # winter_multiplier must be an interval multiplier >= 1.0.
    wm = record.get("winter_multiplier")
    if wm is not None:
        try:
            wm_float = float(wm)
        except (TypeError, ValueError):
            problems.append(f"{key}: winter_multiplier={wm!r} is not numeric")
        else:
            if wm_float < 1.0:
                problems.append(
                    f"{key}: winter_multiplier={wm_float} must be >= 1.0 "
                    "(winter_days = watering_interval_days * winter_multiplier)"
                )

    # Low-acid canning safety: low-acid crops must use pressure_canning and may
    # only use water_bath_canning when pickled/acidified. Only preservation_methods
    # counts as a method declaration (not the descriptive preservation_conversion).
    if category == "vegetable" and key in fields.KNOWN_LOW_ACID_KEYS:
        acidity = record.get("usda_acidity_class")
        methods = record.get("preservation_methods") or ""
        methods_lower = methods.lower()
        if acidity != "low_acid":
            problems.append(
                f"{key}: known low-acid key but usda_acidity_class={acidity!r} (must be low_acid)"
            )
        if "pressure_canning" not in methods_lower:
            problems.append(
                f"{key}: low-acid crop must list pressure_canning in preservation_methods"
            )
        if "water_bath_canning" in methods_lower and not (
            "pickled" in methods_lower or "acidified" in methods_lower
        ):
            problems.append(
                f"{key}: low-acid crop lists water_bath_canning without pickled/acidified "
                "(pressure canning only unless acidified)"
            )

    # Frost offset sign convention: positive = weeks BEFORE last frost,
    # negative = weeks AFTER last frost. Tender crops must go outdoors after
    # the last frost, so their direct_sow / transplant offsets must be <= 0.
    frost = record.get("frost_tolerance") or ""
    is_tender = "tender" in frost
    for offset_col in ("direct_sow_weeks_before_last_frost", "transplant_weeks_before_last_frost"):
        val = record.get(offset_col)
        if val is None:
            continue
        try:
            val_int = int(val)
        except (TypeError, ValueError):
            problems.append(f"{key}: {offset_col}={val!r} is not an integer")
        else:
            if is_tender and val_int > 0:
                problems.append(
                    f"{key}: tender crop has positive {offset_col}={val_int} "
                    "(tender crops must direct-sow/transplant after last frost, offset <= 0)"
                )

    return problems


def _create_schema(conn: sqlite3.Connection) -> None:
    fts_cols = ", ".join(fields.FTS_COLUMNS)
    conn.executescript(
        f"""
        CREATE TABLE plants (
            {_COLUMN_DEFS}
        );
        CREATE VIRTUAL TABLE plant_fts USING fts5(key UNINDEXED, {fts_cols});
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )


def _insert_record(conn: sqlite3.Connection, record: dict) -> None:
    names = ", ".join(fields.COLUMNS)
    marks = ", ".join("?" for _ in fields.COLUMNS)
    values = [record.get(name) for name in fields.COLUMNS]
    conn.execute(f"INSERT INTO plants ({names}) VALUES ({marks})", values)
    fts_cols = ("key",) + fields.FTS_COLUMNS
    fts_values = [record.get(name) for name in fts_cols]
    conn.execute(f"INSERT INTO plant_fts ({', '.join(fts_cols)}) VALUES ({', '.join('?' for _ in fts_cols)})", fts_values)


def _check_counts(counts: dict[str, int], expect: dict[str, int]) -> None:
    mismatches = {
        name: (counts.get(name, 0), wanted)
        for name, wanted in expect.items()
        if counts.get(name, 0) < wanted
    }
    if mismatches:
        lines = "; ".join(f"{name}: got {got}, need >= {wanted}" for name, (got, wanted) in mismatches.items())
        raise SystemExit(
            f"plant counts below the pinned minimums ({lines}).\n"
            "Inspect the catalog and rerun with\n"
            f"--expect {','.join(f'{name}={counts.get(name, 0)}' for name in expect)}\n"
            "only after confirming the new minimum is intentional."
        )


def build_seed(
    out_path: str | Path,
    expect: dict[str, int] | None = None,
    catalog_dir: str | Path = CATALOG_DIR,
) -> dict[str, int]:
    """Validate the JSON catalog and write a fresh SQLite+FTS5 seed.

    Returns the per-category counts. Writes to ``<out>.building`` then atomically
    replaces ``out``.
    """
    out_path = Path(out_path)
    catalog_dir = Path(catalog_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".building")
    tmp_path.unlink(missing_ok=True)

    catalog = _load_catalog(catalog_dir)

    problems: list[str] = []
    counts: dict[str, int] = {}
    for category in fields.CATEGORIES:
        records = catalog[category]
        for record in records:
            problems.extend(_validate_record(record, category))
        counts[category] = len(records)

    if problems:
        tmp_path.unlink(missing_ok=True)
        detail = "\n".join(f"  - {p}" for p in problems[:50])
        raise SystemExit(
            f"catalog validation failed ({len(problems)} problem(s)):\n{detail}\n"
            "Fix the JSON data files and rerun."
        )

    expect = dict(expect or fields.MIN_COUNTS_BY_CATEGORY)
    _check_counts(counts, expect)

    conn = sqlite3.connect(tmp_path)
    conn.execute("PRAGMA synchronous = OFF")
    try:
        _create_schema(conn)
        for category in fields.CATEGORIES:
            for record in catalog[category]:
                _insert_record(conn, record)
        conn.commit()
        built_at = __import__("datetime").datetime.now(__import__("datetime").UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta = {
            "schema_version": "1",
            "built_at": built_at,
            "counts": json.dumps(counts),
            "catalog_dir": str(catalog_dir),
        }
        for key, value in meta.items():
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        # VACUUM cannot run inside a transaction; switch to autocommit for it.
        conn.isolation_level = None
        conn.execute("VACUUM")
    except BaseException:
        conn.close()
        tmp_path.unlink(missing_ok=True)
        raise
    conn.close()
    tmp_path.replace(out_path)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--work-dir",
        default=str(ROOT / ".tmp" / "plants"),
        help="scratch dir (unused by this build; kept for CLI parity with the FDC builder)",
    )
    parser.add_argument("--out", default=str(ROOT / "data" / "plants" / "plants-seed.db"), help="seed database to write")
    parser.add_argument(
        "--expect",
        default=None,
        help="override minimum counts, e.g. houseplant=50,herb=20,vegetable=40,fruit=25",
    )
    parser.add_argument(
        "--catalog-dir",
        default=str(CATALOG_DIR),
        help="directory containing <category>.json catalog files",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="accepted for CLI parity; the JSON catalog is always re-read",
    )
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    expect = None
    if args.expect:
        expect = {}
        for chunk in args.expect.split(","):
            name, _, value = chunk.partition("=")
            expect[name.strip()] = int(value)

    counts = build_seed(args.out, expect=expect, catalog_dir=args.catalog_dir)
    total = sum(counts.values())
    size_mb = Path(args.out).stat().st_size / (1024 * 1024)
    print(f"seed written: {args.out}")
    print(f"  " + ", ".join(f"{cat}={counts[cat]}" for cat in fields.CATEGORIES) + f" ({total} plants)")
    print(f"  {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
