#!/usr/bin/env python3
"""Build the offline recipe index (SQLite) from the CC0 Food.com corpus.

The Recipes page searches this index first: a small SQLite FTS5 database over
the whole CC0 Food.com recipe set, so a search stays offline and instant and a
pick saves without touching the network.

Two sources, joined on recipe name + author:

* **Spine** — the irkaal "Food.com - Recipes and Reviews" dataset (522,517
  recipes, CC0): instructions, times, taxonomy, ratings, images. It drops
  ingredient units ("4" + "blueberries"), so its lines are bare when the join
  misses.
* **Ingredient mirror** — the community parsed mirror of the same corpus
  (Karo8870 on Hugging Face; the fields were parsed from the raw recipe text):
  ingredient lines with their units and notes ("4 cups blueberries, fresh or
  frozen"), 82% of lines carry a unit.

Usage (one-off, workstation):

    python -m pip install kagglehub
    python -c "import kagglehub; print(kagglehub.dataset_download('irkaal/foodcom-recipes-and-reviews'))"
    .venv/bin/python scripts/build_recipe_seed.py --count-only     # sizes first
    .venv/bin/python scripts/build_recipe_seed.py                  # writes data/recipes/recipe-seed.db

The seed ships to the server data dir (``data/recipes/recipe-seed.db``), not
into any image; the execution service opens it read-only from
``RECIPE_SEED_PATH`` (default ``/data/recipes/recipe-seed.db``). A missing
file only ever loses the offline results — search falls back to TheMealDB.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

# The parsing helpers are shared with the note importer (single source of
# truth for the R-vector decoder and the slug). Import works both as a script
# and as ``scripts.build_recipe_seed`` (tests).
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from import_recipes import DEFAULT_RECIPES_CSV, dur_minutes, parse_r_list, slugify  # noqa: E402

csv.field_size_limit(min(sys.maxsize, 1 << 24))

DATASET_URL = "https://www.kaggle.com/datasets/irkaal/foodcom-recipes-and-reviews"
PARSED_URL = "https://huggingface.co/datasets/Karo8870/food.com-parsed-dataset"
LICENSE = (
    "Food.com recipes via the CC0 irkaal dataset "
    "(https://www.kaggle.com/datasets/irkaal/foodcom-recipes-and-reviews); "
    "ingredient units via the Karo8870 parsed mirror "
    "(https://huggingface.co/datasets/Karo8870/food.com-parsed-dataset)"
)
SCHEMA_VERSION = "1"

_REPO_ROOT = _SCRIPTS_DIR.parent
DEFAULT_PARSED_CSV = str(_REPO_ROOT / ".tmp" / "foodcom" / "parsed_recipes.csv")
DEFAULT_OUT = str(_REPO_ROOT / "data" / "recipes" / "recipe-seed.db")

_SCHEMA = """
CREATE TABLE recipes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    keywords TEXT,
    ingredients TEXT NOT NULL,
    instructions TEXT NOT NULL,
    prep_minutes INTEGER,
    cook_minutes INTEGER,
    total_minutes INTEGER,
    servings INTEGER,
    rating REAL,
    reviews INTEGER,
    image TEXT,
    source_url TEXT NOT NULL
);
CREATE VIRTUAL TABLE recipe_fts USING fts5(name, keywords, ingredients, category);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

_NA_WORDS = {"", "na", "nan", "none", "null"}


def _cell(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _clean(text: Any) -> str:
    value = str(text or "").strip()
    return "" if value.lower() in _NA_WORDS else value


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _int_or_none(text: str) -> int | None:
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return None
    return value


def _float_or_none(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _karo_lines(raw: Any) -> list[str]:
    """Render one mirror ``ingredients`` cell into note-style lines."""
    try:
        items = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qty = _clean(item.get("quantity"))
        unit = _clean(item.get("unit"))
        name = _clean(item.get("name"))
        desc = _clean(item.get("description"))
        if not (qty or name):
            continue
        line = " ".join(part for part in (qty, unit, name) if part)
        if desc:
            line += f", {desc}"
        lines.append(line)
    return lines


def load_parsed_index(
    path: str, log=print
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Read the ingredient mirror into lookup maps.

    Returns ``(by_key, by_title)``: ``by_key`` maps ``name|author`` to the
    rendered ingredient lines; ``by_title`` maps a title to the same lines but
    only when every mirror row with that title agrees (shared titles are
    ambiguous and marked ``None``, so the join never guesses).
    """
    by_key: dict[str, str] = {}
    by_title: dict[str, str | None] = {}
    seen: dict[str, str] = {}
    rows = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            lines = _karo_lines(row.get("ingredients"))
            if not lines:
                continue
            text = "\n".join(lines)
            title = _norm(_clean(row.get("name")))
            key = f"{title}|{_norm(_clean(row.get('author_name')))}"
            by_key[key] = text
            if not title:
                continue
            if title not in by_title:
                by_title[title] = text
                seen[title] = key
            elif seen[title] != key:
                by_title[title] = None
    log(f"[recipe-seed] parsed mirror: {rows:,} rows, {len(by_key):,} keyed entries")
    return by_key, by_title


def _spine_recipe(
    row: dict[str, Any],
    by_key: dict[str, str],
    by_title: dict[str, str | None],
) -> tuple[dict[str, Any], bool] | None:
    """One spine row as a seed record; ``None`` when the row cannot be cooked."""
    rid_raw = _cell(row, "RecipeId")
    if not rid_raw.isdigit():
        return None
    rid = int(rid_raw)
    servings = _int_or_none(_cell(row, "RecipeServings"))
    if servings is None or not (1 <= servings <= 99):
        return None
    name = html.unescape(_cell(row, "Name"))
    if not name:
        return None
    parts = [html.unescape(part) for part in parse_r_list(row.get("RecipeIngredientParts"))]
    instructions = [
        html.unescape(step) for step in parse_r_list(row.get("RecipeInstructions"))
    ]
    if len(parts) < 2 or len(instructions) < 2:
        return None

    author = html.unescape(_cell(row, "AuthorName"))
    title_key = _norm(name)
    joined = by_key.get(f"{title_key}|{_norm(author)}") or by_title.get(title_key)
    if joined:
        ingredients = joined.split("\n")
        from_mirror = True
    else:
        quantities = parse_r_list(row.get("RecipeIngredientQuantities"))
        ingredients = []
        for index, part in enumerate(parts):
            qty = quantities[index].strip() if index < len(quantities) else ""
            line = " ".join(piece for piece in (qty, part.strip()) if piece)
            if line:
                ingredients.append(line)
        from_mirror = False
    if len(ingredients) < 2:
        return None

    image = ""
    for candidate in parse_r_list(row.get("Images")):
        candidate = html.unescape(candidate).strip()
        if candidate.startswith("http"):
            image = candidate
            break

    record = {
        "id": rid,
        "name": name,
        "category": html.unescape(_cell(row, "RecipeCategory")),
        "keywords": " ".join(
            html.unescape(word) for word in parse_r_list(row.get("Keywords"))
        ),
        "ingredients": "\n".join(ingredients),
        "instructions": "\n".join(instructions),
        "prep_minutes": dur_minutes(_cell(row, "PrepTime") or None),
        "cook_minutes": dur_minutes(_cell(row, "CookTime") or None),
        "total_minutes": dur_minutes(_cell(row, "TotalTime") or None),
        "servings": servings,
        "rating": _float_or_none(_cell(row, "AggregatedRating")),
        "reviews": _int_or_none(_cell(row, "ReviewCount")) or 0,
        "image": image,
        "source_url": f"https://www.food.com/recipe/{slugify(name)}-{rid}",
    }
    return record, from_mirror


_INSERT_RECIPE = (
    "INSERT INTO recipes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
_INSERT_FTS = (
    "INSERT INTO recipe_fts(rowid, name, keywords, ingredients, category) "
    "VALUES (?,?,?,?,?)"
)


def build_seed(
    spine_csv: str,
    out_path: str,
    parsed_csv: str | None = None,
    expect: int | None = None,
    count_only: bool = False,
    log=print,
) -> dict[str, int]:
    """Build the seed (or count what would be kept). Pure file → SQLite."""
    stats = {"rows": 0, "kept": 0, "joined": 0, "fallback": 0}
    by_key: dict[str, str] = {}
    by_title: dict[str, str | None] = {}
    if parsed_csv:
        if os.path.exists(parsed_csv):
            by_key, by_title = load_parsed_index(parsed_csv, log=log)
        else:
            log(
                f"[recipe-seed] ingredient mirror not found at {parsed_csv}; "
                "ingredient lines will be bare (quantity + name)"
            )

    conn: sqlite3.Connection | None = None
    building = ""
    if not count_only:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        building = str(out.with_suffix(".building"))
        if os.path.exists(building):
            os.unlink(building)
        conn = sqlite3.connect(building)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript(_SCHEMA)
        pending_rows: list[tuple[Any, ...]] = []
        pending_fts: list[tuple[Any, ...]] = []

        def flush() -> None:
            assert conn is not None
            conn.executemany(_INSERT_RECIPE, pending_rows)
            conn.executemany(_INSERT_FTS, pending_fts)
            pending_rows.clear()
            pending_fts.clear()

    started = time.time()
    with open(spine_csv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            stats["rows"] += 1
            parsed = _spine_recipe(row, by_key, by_title)
            if not parsed:
                continue
            record, from_mirror = parsed
            stats["kept"] += 1
            stats["joined" if from_mirror else "fallback"] += 1
            if conn is None:
                continue
            pending_rows.append(tuple(record[key] for key in record))
            pending_fts.append(
                (
                    record["id"],
                    record["name"],
                    record["keywords"],
                    record["ingredients"],
                    record["category"],
                )
            )
            if len(pending_rows) >= 2000:
                flush()
    if conn is not None:
        flush()
        assert conn is not None
        meta = {
            "schema_version": SCHEMA_VERSION,
            "license": LICENSE,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rows": str(stats["kept"]),
            "joined_rows": str(stats["joined"]),
            "fallback_rows": str(stats["fallback"]),
            "spine_url": DATASET_URL,
            "parsed_url": PARSED_URL,
        }
        conn.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
        conn.execute("INSERT INTO recipe_fts(recipe_fts) VALUES('optimize')")
        conn.commit()
        conn.execute("VACUUM")
        conn.close()
        os.replace(building, out_path)

    elapsed = time.time() - started
    summary = (
        f"[recipe-seed] rows {stats['rows']:,} | kept {stats['kept']:,} "
        f"(ingredients from the mirror {stats['joined']:,}, bare {stats['fallback']:,})"
    )
    if conn is None:
        log(f"{summary} | {elapsed:.0f}s (count only)")
    else:
        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        log(f"{summary} | {out_path} ({size_mb:.1f} MB) | {elapsed:.0f}s")

    if expect is not None and stats["kept"] != expect:
        raise SystemExit(
            f"[recipe-seed] kept {stats['kept']:,} recipes but expected {expect:,}. "
            "If the corpus changed on purpose, re-run with --expect <count>."
        )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spine-csv", default=DEFAULT_RECIPES_CSV, help="irkaal recipes.csv")
    parser.add_argument(
        "--parsed-csv",
        default=DEFAULT_PARSED_CSV,
        help="ingredient mirror CSV (name + author_name + ingredients JSON)",
    )
    parser.add_argument("--no-parsed", action="store_true", help="skip the ingredient mirror")
    parser.add_argument("--out", default=DEFAULT_OUT, help="where the seed is written")
    parser.add_argument("--expect", type=int, default=None, help="assert the kept count")
    parser.add_argument("--count-only", action="store_true", help="count without writing")
    args = parser.parse_args(argv)

    if not os.path.exists(args.spine_csv):
        raise SystemExit(
            f"Spine dataset not found at {args.spine_csv}. Download it first:\n"
            "  python -m pip install kagglehub\n"
            "  python -c \"import kagglehub; print(kagglehub.dataset_download('irkaal/foodcom-recipes-and-reviews'))\"\n"
            "then pass its recipes.csv via --spine-csv (or RECIPES_CSV)."
        )
    stats = build_seed(
        args.spine_csv,
        args.out,
        parsed_csv=None if args.no_parsed else args.parsed_csv,
        expect=args.expect,
        count_only=args.count_only,
    )
    return 0 if stats["kept"] else 1


if __name__ == "__main__":
    sys.exit(main())
