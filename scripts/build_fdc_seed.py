#!/usr/bin/env python3
"""Build the local USDA FoodData Central seed database for the Health service.

The Health hub searches foods for logging. Branded products come from
OpenFoodFacts; generic whole foods (and the household measures that turn
"1 cup" into grams) come from a read-only SQLite seed this script builds from
USDA's bulk downloads:

* Foundation Foods (2026-04-30) - best lab data, mostly 100 g portions
* SR Legacy (2018-04) - breadth, frozen since April 2018
* FNDDS survey foods (2024-10-31) - survey portions, the household-measure table

FDC data are public domain (CC0 1.0); attribution is requested, not required.
Counts are asserted after import: a USDA release that changes them needs a human
glance and an explicit ``--expect`` override.

Usage:
    python scripts/build_fdc_seed.py [--work-dir .tmp/fdc] [--out data/health/fdc-seed.db]

The archives are downloaded once into the work dir and reused on later runs
(``--refresh`` re-downloads). The finished seed lands wherever ``--out`` says;
the Health service reads it read-only (env ``FDC_SEED_PATH``, default next to
its SQLite database, ``/data/fdc-seed.db`` in the container).
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCES = (
    {
        "name": "foundation",
        "release": "2026-04-30",
        "filename": "FoodData_Central_foundation_food_csv_2026-04-30.zip",
        "url": "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_csv_2026-04-30.zip",
        "bytes": 3_825_741,
    },
    {
        "name": "sr_legacy",
        "release": "2018-04",
        "filename": "FoodData_Central_sr_legacy_food_csv_2018-04.zip",
        "url": "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip",
        "bytes": 6_074_592,
    },
    {
        "name": "fndds",
        "release": "2024-10-31",
        "filename": "FoodData_Central_survey_food_json_2024-10-31.zip",
        "url": "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_survey_food_json_2024-10-31.zip",
        "bytes": 3_835_292,
    },
)

# Measured on the releases above (2026-10-22). A count mismatch stops the build
# so a changed release is inspected instead of silently imported.
DEFAULT_EXPECT = {"foundation": 395, "sr_legacy": 7_793, "fndds": 5_432}

LICENSE = (
    "USDA FoodData Central - public domain (CC0 1.0); attribution requested. "
    "Contains data from Foundation Foods, SR Legacy and FNDDS."
)

# nutrient id (references nutrient.csv "id") -> (seed column, priority within
# the column). Energy prefers 208/Atwater general/Atwater specific; sugars
# prefer 269 (Total Sugars) over 269.3.
NUTRIENT_COLUMNS = {
    "1008": ("calories_kcal", 0),
    "2047": ("calories_kcal", 1),
    "2048": ("calories_kcal", 2),
    "1003": ("protein_g", 0),
    "1004": ("fat_g", 0),
    "1005": ("carbs_g", 0),
    "1079": ("fiber_g", 0),
    "2000": ("sugar_g", 0),
    "1063": ("sugar_g", 1),
    "1093": ("sodium_mg", 0),
    "1087": ("calcium_mg", 0),
    "1089": ("iron_mg", 0),
    "1092": ("potassium_mg", 0),
    "1162": ("vitamin_c_mg", 0),
    "1253": ("cholesterol_mg", 0),
    "1258": ("saturated_fat_g", 0),
}

FOOD_COLUMNS = (
    "calories_kcal",
    "protein_g",
    "fat_g",
    "carbs_g",
    "fiber_g",
    "sugar_g",
    "sodium_mg",
    "calcium_mg",
    "iron_mg",
    "potassium_mg",
    "vitamin_c_mg",
    "cholesterol_mg",
    "saturated_fat_g",
)

BOILERPLATE_PORTIONS = {"quantity not specified", "quantity not specified."}
MAX_PORTIONS_PER_FOOD = 12


def _num(text) -> float | None:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value


def _trim_qty(text) -> str:
    text = (text or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _portion_label(qty: str, unit: str, desc: str) -> str:
    """Human label for a household measure, e.g. "1 cup, shredded"."""
    if unit:
        base = f"{qty} {unit}".strip()
        return f"{base}, {desc}" if desc else base
    if desc:
        if desc[:1].isdigit():
            return desc
        return f"{qty} {desc}".strip() if qty else desc
    return qty or "portion"


def _csv_rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


def _find(root: Path, name: str) -> Path:
    if root.is_file():
        return root
    for path in root.rglob(name):
        return path
    raise SystemExit(f"expected {name} under {root}; re-extract the archive")


def _create_schema(conn: sqlite3.Connection) -> None:
    columns = ", ".join(f"{name} REAL" for name in FOOD_COLUMNS)
    conn.executescript(
        f"""
        CREATE TABLE foods (
            key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            fdc_id TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            {columns}
        );
        CREATE TABLE portions (
            food_key TEXT NOT NULL,
            seq INTEGER NOT NULL,
            label TEXT NOT NULL,
            grams REAL NOT NULL
        );
        CREATE INDEX idx_portions_food_key ON portions(food_key);
        CREATE VIRTUAL TABLE food_fts USING fts5(key UNINDEXED, description);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )


def _insert_food(
    conn: sqlite3.Connection,
    key: str,
    source: str,
    fdc_id: str,
    description: str,
    category: str,
    nutrients: dict[str, float],
) -> None:
    names = ", ".join(FOOD_COLUMNS)
    marks = ", ".join("?" for _ in FOOD_COLUMNS)
    conn.execute(
        f"INSERT INTO foods (key, source, fdc_id, description, category, {names}) VALUES (?, ?, ?, ?, ?, {marks})",
        (key, source, fdc_id, description, category, *[nutrients.get(name) for name in FOOD_COLUMNS]),
    )
    conn.execute("INSERT INTO food_fts (key, description) VALUES (?, ?)", (key, description))


def _insert_portions(conn: sqlite3.Connection, key: str, portions: list[tuple[str, float]]) -> None:
    seen: set[tuple[str, float]] = set()
    written = 0
    for label, grams in portions:
        grams = round(grams, 1)
        signature = (label.lower(), grams)
        if signature in seen:
            continue
        seen.add(signature)
        conn.execute(
            "INSERT INTO portions (food_key, seq, label, grams) VALUES (?, ?, ?, ?)",
            (key, written, label, grams),
        )
        written += 1
        if written >= MAX_PORTIONS_PER_FOOD:
            break


def _import_csv_source(conn: sqlite3.Connection, root: Path, source: str) -> int:
    """Import Foundation Foods or SR Legacy from an extracted CSV archive."""
    listing_name = "foundation_food.csv" if source == "foundation" else "sr_legacy_food.csv"
    listing: dict[str, str] = {}
    for row in _csv_rows(_find(root, listing_name)):
        fid = (row.get("fdc_id") or "").strip()
        if fid:
            listing[fid] = (row.get("NDB_number") or "").strip()

    categories: dict[str, str] = {}
    for row in _csv_rows(_find(root, "food_category.csv")):
        categories[(row.get("id") or "").strip()] = (row.get("description") or "").strip()

    foods: dict[str, tuple[str, str]] = {}
    for row in _csv_rows(_find(root, "food.csv")):
        fid = (row.get("fdc_id") or "").strip()
        if fid in listing and fid not in foods:
            foods[fid] = (
                (row.get("description") or "").strip(),
                categories.get((row.get("food_category_id") or "").strip(), ""),
            )

    nutrients: dict[str, dict[str, float]] = {}
    for row in _csv_rows(_find(root, "food_nutrient.csv")):
        fid = (row.get("fdc_id") or "").strip()
        if fid not in listing:
            continue
        column = NUTRIENT_COLUMNS.get(str(row.get("nutrient_id", "")).strip())
        if column is None:
            continue
        name, priority = column
        amount = _num(row.get("amount"))
        if amount is None:
            continue
        chosen = nutrients.setdefault(fid, {})
        current = chosen.get(f"{name}!priority")
        if current is None or priority < current:
            chosen[name] = amount
            chosen[f"{name}!priority"] = priority

    units: dict[str, str] = {}
    for row in _csv_rows(_find(root, "measure_unit.csv")):
        name = (row.get("name") or "").strip()
        if name.lower() != "undetermined":
            units[(row.get("id") or "").strip()] = name

    portions: dict[str, list[tuple[str, float]]] = {}
    for row in _csv_rows(_find(root, "food_portion.csv")):
        fid = (row.get("fdc_id") or "").strip()
        if fid not in listing:
            continue
        grams = _num(row.get("gram_weight"))
        if grams is None or grams <= 0:
            continue
        unit = units.get((row.get("measure_unit_id") or "").strip(), "")
        desc = (row.get("portion_description") or "").strip() or (row.get("modifier") or "").strip()
        if desc.lower() in BOILERPLATE_PORTIONS:
            desc = ""
        label = _portion_label(_trim_qty(row.get("amount")), unit, desc)
        portions.setdefault(fid, []).append((label, grams))

    count = 0
    for fid, (description, category) in foods.items():
        if not description:
            continue
        ndb = listing.get(fid, "")
        key = f"{source}:{ndb or fid}"
        chosen = {name: value for name, value in nutrients.get(fid, {}).items() if not name.endswith("!priority")}
        _insert_food(conn, key, source, fid, description, category, chosen)
        _insert_portions(conn, key, portions.get(fid, []))
        count += 1
    return count


def _import_fndds(conn: sqlite3.Connection, json_path: Path) -> int:
    """Import FNDDS survey foods (compact JSON download)."""
    with json_path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    count = 0
    for food in data.get("SurveyFoods", []):
        food_code = str(food.get("foodCode") or "").strip()
        description = (food.get("description") or "").strip()
        if not food_code or not description:
            continue
        category = ((food.get("wweiaFoodCategory") or {}).get("wweiaFoodCategoryDescription") or "").strip()

        chosen: dict[str, tuple[int, float]] = {}
        for entry in food.get("foodNutrients") or []:
            nutrient = entry.get("nutrient") or {}
            column = NUTRIENT_COLUMNS.get(str(nutrient.get("id", "")).strip())
            if column is None:
                continue
            name, priority = column
            amount = _num(entry.get("amount"))
            if amount is None:
                continue
            current = chosen.get(name)
            if current is None or priority < current[0]:
                chosen[name] = (priority, amount)

        portions: list[tuple[str, float]] = []
        for entry in food.get("foodPortions") or []:
            grams = _num(entry.get("gramWeight"))
            if grams is None or grams <= 0:
                continue
            label = (entry.get("portionDescription") or "").strip()
            if not label or label.lower() in BOILERPLATE_PORTIONS:
                unit = ((entry.get("measureUnit") or {}).get("name") or "").strip()
                label = "" if unit.lower() == "undetermined" else unit
            if not label:
                continue
            portions.append((label, grams))

        key = f"fndds:{food_code}"
        _insert_food(
            conn,
            key,
            "fndds",
            str(food.get("fdcId") or "").strip(),
            description,
            category,
            {name: value for name, (_, value) in chosen.items()},
        )
        _insert_portions(conn, key, portions)
        count += 1
    return count


def _check_counts(counts: dict[str, int], expect: dict[str, int]) -> None:
    mismatches = {
        name: (counts.get(name), wanted)
        for name, wanted in expect.items()
        if counts.get(name) != wanted
    }
    if mismatches:
        lines = "; ".join(f"{name}: got {got}, expected {wanted}" for name, (got, wanted) in mismatches.items())
        raise SystemExit(
            f"food counts do not match the pinned release ({lines}).\n"
            "If USDA shipped a new release, inspect the counts and rerun with\n"
            f"--expect {','.join(f'{name}={counts.get(name)}' for name in expect)}"
        )


def build_seed(
    out_path: str | Path,
    foundation_dir: str | Path,
    sr_dir: str | Path,
    fndds_json: str | Path,
    expect: dict[str, int] | None = None,
) -> dict[str, int]:
    """Import extracted USDA files into a fresh SQLite seed. Returns the counts."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_name(out_path.name + ".building")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    conn.execute("PRAGMA synchronous = OFF")
    try:
        _create_schema(conn)
        counts = {
            "foundation": _import_csv_source(conn, Path(foundation_dir), "foundation"),
            "sr_legacy": _import_csv_source(conn, Path(sr_dir), "sr_legacy"),
            "fndds": _import_fndds(conn, Path(fndds_json)),
        }
        _check_counts(counts, dict(expect or DEFAULT_EXPECT))
        built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta = {"schema_version": "1", "license": LICENSE, "built_at": built_at, "counts": json.dumps(counts)}
        for source in SOURCES:
            meta[f"source_{source['name']}"] = source["url"]
            meta[f"release_{source['name']}"] = source["release"]
        for key, value in meta.items():
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.execute("VACUUM")
    except BaseException:
        conn.close()
        tmp_path.unlink(missing_ok=True)
        raise
    conn.close()
    tmp_path.replace(out_path)
    return counts


# --- downloads ---------------------------------------------------------------


def _download(source: dict, work_dir: Path, refresh: bool, out=print) -> Path:
    target = work_dir / source["filename"]
    if target.exists() and not refresh:
        out(f"reusing {target.name} ({target.stat().st_size:,} bytes)")
        return target
    part = target.with_suffix(target.suffix + ".part")
    out(f"downloading {source['url']}")
    request = urllib.request.Request(
        source["url"],
        headers={"User-Agent": "Jarvis-OS seed builder (family self-host; github.com/jmiahman1)"},
    )
    with urllib.request.urlopen(request, timeout=120) as resp, part.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    size = part.stat().st_size
    if source["bytes"] and size != source["bytes"]:
        out(f"warning: {target.name} is {size:,} bytes, expected {source['bytes']:,}")
    try:
        broken = zipfile.ZipFile(part).testzip()
    except zipfile.BadZipFile:
        broken = "not a zip archive"
    if broken is not None:
        part.unlink(missing_ok=True)
        raise SystemExit(f"{target.name} is corrupt ({broken}); rerun with --refresh")
    part.rename(target)
    out(f"saved {target.name} ({size:,} bytes)")
    return target


def _extract(archive: Path, target_dir: Path, refresh: bool, out=print) -> Path:
    if target_dir.exists() and any(target_dir.iterdir()) and not refresh:
        out(f"reusing {target_dir}")
        return target_dir
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    out(f"extracting {archive.name} -> {target_dir}")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target_dir)
    return target_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work-dir", default=str(ROOT / ".tmp" / "fdc"), help="download and extraction dir")
    parser.add_argument("--out", default=str(ROOT / "data" / "health" / "fdc-seed.db"), help="seed database to write")
    parser.add_argument("--expect", default=None, help="override counts, e.g. foundation=395,sr_legacy=7793,fndds=5432")
    parser.add_argument("--refresh", action="store_true", help="re-download and re-extract the archives")
    args = parser.parse_args(argv)

    expect = None
    if args.expect:
        expect = {}
        for chunk in args.expect.split(","):
            name, _, value = chunk.partition("=")
            expect[name.strip()] = int(value)

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    extracted = work_dir / "extracted"
    for source in SOURCES:
        archive = _download(source, work_dir, args.refresh)
        _extract(archive, extracted / source["name"], args.refresh)

    fndds_json = _find(extracted / "fndds", "surveyDownload.json")
    counts = build_seed(
        args.out,
        extracted / "foundation",
        extracted / "sr_legacy",
        fndds_json,
        expect=expect,
    )
    total = sum(counts.values())
    size_mb = Path(args.out).stat().st_size / (1024 * 1024)
    print(f"seed written: {args.out}")
    print(f"  foundation {counts['foundation']}, sr_legacy {counts['sr_legacy']}, fndds {counts['fndds']} ({total} foods)")
    print(f"  {size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
