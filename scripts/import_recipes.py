#!/usr/bin/env python3
"""Import kid-tiered family recipe notes from the CC0 Food.com corpus.

Selection uses the ``irkaal/foodcom-recipes-and-reviews`` dataset (Kaggle,
CC0) - 522,517 recipes with categories, keyword tags, times, ratings and
per-serving nutrition. The dataset strips units from ingredient quantities,
so each chosen finalist is fetched from its food.com page and rendered from
the page's schema.org JSON-LD instead (full ingredient lines with units,
clean instructions, the same nutrition). Fetching a page only needs the
recipe id; food.com redirects a wrong slug, so a slug derived from the title
is always safe.

The corpus is downloaded once (no Kaggle account required):

    python -m pip install kagglehub
    python -c "import kagglehub; print(kagglehub.dataset_download('irkaal/foodcom-recipes-and-reviews'))"

then pass the printed directory's ``recipes.csv`` via --recipes-csv (or
RECIPES_CSV, or leave the default kagglehub cache path).

Runs are dry by default: it prints the per-level funnel, the picked recipes
and a couple of fully rendered preview notes, but writes nothing. Add
--write to render every picked recipe into --out (default .tmp/recipes-notes)
as Nextcloud-Notes-compatible markdown (``# Title`` / ``Category: Recipes``),
quantities scaled to 7 servings, tags inline, nutrition per serving tagged
``foodcom``.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DATASET_URL = "https://www.kaggle.com/datasets/irkaal/foodcom-recipes-and-reviews"
DEFAULT_RECIPES_CSV = os.path.join(
    Path.home(),
    ".cache/kagglehub/datasets/irkaal/foodcom-recipes-and-reviews/versions/2/recipes.csv",
)
UA = "Jarvis-OS recipe import (family self-host; github.com/jmiahman1)"
SERVINGS_TARGET = 7
FILLER = re.compile(r"^\s*(?:$|,|\(\))*$")

# Ranking staples: the kitchen always has these (see docs/RECIPES.md).
STAPLES = (
    ("chicken", re.compile(r"\bchicken\b")),
    ("ground meat", re.compile(r"\b(ground (?:beef|turkey|chicken)|hamburger)\b")),
    ("rice", re.compile(r"\brice\b")),
    ("pasta", re.compile(r"\b(pasta|spaghetti|noodle|macaroni|penne|ziti)\b")),
    ("canned tomatoes", re.compile(r"\btomato(?:es)?\b")),
    ("beans", re.compile(r"\bbeans?\b")),
    ("cheese", re.compile(r"\bcheese\b")),
    ("tortillas", re.compile(r"\btortillas?\b")),
    ("eggs", re.compile(r"\beggs?\b")),
    ("potatoes", re.compile(r"\bpotato(?:es)?\b")),
    ("onions", re.compile(r"\bonions?\b")),
    ("garlic", re.compile(r"\bgarlic\b")),
    ("frozen vegetables", re.compile(r"\bfrozen (?:mixed )?veg(?:etables?)?\b")),
)

HEAT = re.compile(
    r"\b(bake|baked|baking|oven|stove|stovetop|skillet|boil|boiling|fry|fried|frying|"
    r"grill|broil|simmer|roast|saute|saut|sear|toaster|griddle|barbecue|smoker|"
    r"crock|slow cooker|pressure cook|instant pot|brown|browned|browning|"
    r"heat (?:a|the|until|over)|pan over|medium[ -]high)\b",
    re.I,
)
ALCOHOL = re.compile(r"\b(wine|beer|rum|vodka|bourbon|whiskey|tequila|liqueur|brandy)\b", re.I)
NOISE_CATEGORIES = {
    "dessert",
    "beverages",
    "sauces",
    "pie",
    "bar cookie",
    "cookie & brownie",
    "quick breads",
    "breads",
    "condiments",
}
MAIN_CATEGORIES = {
    "chicken",
    "meat",
    "pork",
    "beef",
    "turkey",
    "seafood",
    "fish",
    "one dish meal",
    "main dish",
    "ground beef",
    "chicken breast",
    "pasta",
    "rice",
    "vegetable",
    "lunch/snacks",
    "breakfast",
    "potato",
    "eggs",
}
SLOW_COOKER = re.compile(r"\b(slow cooker|crock ?pot)\b", re.I)
PRESSURE = re.compile(r"\b(pressure cooker|instant pot)\b", re.I)


def parse_r_list(text: str) -> list[str]:
    """Decode an R vector column like ``c("a", "b")`` (post-CSV)."""
    text = (text or "").strip()
    if not text or text == "NA" or text == "c()":
        return []
    if not text.startswith("c("):
        return [text]
    inner = text[2:-1]
    out: list[str] = []
    i, n = 0, len(inner)
    while i < n:
        ch = inner[i]
        if ch in ", ":
            i += 1
            continue
        if ch == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                c = inner[i]
                if c == '"':
                    if i + 1 < n and inner[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(c)
                i += 1
            out.append("".join(buf))
        else:
            j = i
            while j < n and inner[j] != ",":
                j += 1
            tok = inner[i:j].strip().strip('"')
            if tok:
                out.append(tok)
            i = j + 1
    return out


def dur_minutes(iso: str | None) -> int | None:
    if not iso or iso == "NA":
        return None
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso.strip())
    if not m:
        return None
    h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return h * 60 + mi + sec // 60


def fmt_minutes(minutes: int | None) -> str:
    if minutes is None:
        return "?"
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h" if not m else f"{h} h {m} min"


UNICODE_FRACTIONS = {"¼": "1/4", "½": "1/2", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"}


def parse_quantity(text: str | None) -> tuple[float, ...] | None:
    """Parse ``4``, ``1/4``, ``1 1/2``, ``2 -3`` (returns low[, high])."""
    if not text:
        return None
    text = text.strip()
    for uni, plain in UNICODE_FRACTIONS.items():
        text = text.replace(uni, f" {plain}")
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text.upper() == "NA":
        return None
    parts = re.split(r"\s*-\s*", text)
    try:
        values = tuple(_parse_single(p) for p in parts)
    except ValueError:
        return None
    if any(v is None for v in values):
        return None
    return values  # type: ignore[return-value]


def _parse_single(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    m = re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", text)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.fullmatch(r"(\d+)/(\d+)", text)
    if m:
        return int(m.group(1)) / int(m.group(2))
    return float(text)


def fmt_qty(value: float, count: bool = False) -> str:
    """Render a scaled quantity as a clean mixed number."""
    if value <= 0:
        return ""
    if count:
        whole = round(value * 2) / 2
        if whole == int(whole):
            return str(int(whole))
    if value >= 20:
        return str(round(value))
    whole = int(value)
    frac = value - whole
    frac = round(frac * 4) / 4
    if frac >= 1:
        whole += 1
        frac = 0
    if frac == 0:
        return str(whole) if whole else "1/4"
    label = {0.25: "1/4", 0.5: "1/2", 0.75: "3/4"}.get(frac)
    if label is None:  # e.g. 0.125 rounded oddity
        label = "1/4"
    return f"{whole} {label}" if whole else label


def scale_quantity(text: str | None, factor: float, count: bool = False) -> str:
    values = parse_quantity(text)
    if not values:
        return ""
    scaled = [fmt_qty(v * factor, count=count) for v in values]
    scaled = [s for s in scaled if s]
    if not scaled:
        return ""
    if len(values) > 1:
        return " -".join(scaled[:2]) if len(scaled) >= 2 else scaled[0]
    return scaled[0]


def slugify(name: str) -> str:
    text = name.lower().replace("&", " and ").replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"-{2,}", "-", text)[:80].strip("-") or "recipe"


def parse_yield_servings(text: str | None) -> int | None:
    if not text or text.strip().upper() == "NA":
        return None
    m = re.search(r"(\d+)", text)
    if not m:
        return None
    value = int(m.group(1))
    return value if 0 < value < 100 else None


@dataclass
class Candidate:
    rid: int
    name: str
    servings: int
    total_minutes: int | None
    prep_minutes: int | None
    cook_minutes: int | None
    keywords: tuple[str, ...]
    category: str
    n_ingredients: int
    staples: tuple[str, ...]
    rating: float | None
    reviews: int | None
    steps: int
    parts: tuple[str, ...]
    instructions: tuple[str, ...]
    nutrition: dict[str, str] = field(default_factory=dict)

    @property
    def staple_score(self) -> int:
        return len(self.staples)


def stream_candidates(
    csv_path: Path, funnel: dict[str, list[int]]
) -> dict[int, list[Candidate]]:
    """One pass over the corpus, keeping rankable candidates per level."""
    pools: dict[int, list[Candidate]] = {1: [], 2: [], 3: []}
    seen_titles: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        idx = {c: i for i, c in enumerate(header)}
        for row in rd:
            funnel["rows"][0] += 1
            servings = parse_yield_servings(row[idx["RecipeServings"]])
            if not servings:
                continue
            funnel["numeric servings"][0] += 1
            name = html.unescape(row[idx["Name"]].strip())
            category = row[idx["RecipeCategory"]].strip().lower()
            keywords = tuple(k.strip().lower() for k in parse_r_list(row[idx["Keywords"]]))
            if category in NOISE_CATEGORIES or "dessert" in keywords:
                continue
            funnel["no dessert/drink noise"][0] += 1
            parts = tuple(html.unescape(p) for p in parse_r_list(row[idx["RecipeIngredientParts"]]))
            instructions = tuple(html.unescape(i) for i in parse_r_list(row[idx["RecipeInstructions"]]))
            n_ingredients = len(parts)
            steps = len(instructions)
            if not (2 <= n_ingredients <= 15 and 2 <= steps <= 15):
                continue
            funnel["ingredient/step bounds"][0] += 1
            blob = f"{name} {' '.join(parts)} {' '.join(instructions)}"
            if ALCOHOL.search(blob):
                continue
            funnel["no alcohol"][0] += 1
            rating_raw = row[idx["AggregatedRating"]].strip()
            rating = float(rating_raw) if rating_raw not in ("", "NA") else None
            if rating is not None and rating < 4.0:
                continue
            reviews_raw = row[idx["ReviewCount"]].strip()
            reviews = int(float(reviews_raw)) if reviews_raw not in ("", "NA") else None
            instr_blob = " ".join(instructions)
            staples = tuple(label for label, rx in STAPLES if rx.search(" ".join(parts)))
            cand = Candidate(
                rid=int(row[idx["RecipeId"]]),
                name=name,
                servings=servings,
                total_minutes=dur_minutes(row[idx["TotalTime"]]),
                prep_minutes=dur_minutes(row[idx["PrepTime"]]),
                cook_minutes=dur_minutes(row[idx["CookTime"]]),
                keywords=keywords,
                category=category,
                n_ingredients=n_ingredients,
                staples=staples,
                rating=rating,
                reviews=reviews,
                steps=steps,
                parts=parts,
                instructions=instructions,
                nutrition={
                    "calories": row[idx["Calories"]].strip(),
                    "fatContent": row[idx["FatContent"]].strip(),
                    "saturatedFatContent": row[idx["SaturatedFatContent"]].strip(),
                    "cholesterolContent": row[idx["CholesterolContent"]].strip(),
                    "sodiumContent": row[idx["SodiumContent"]].strip(),
                    "carbohydrateContent": row[idx["CarbohydrateContent"]].strip(),
                    "fiberContent": row[idx["FiberContent"]].strip(),
                    "sugarContent": row[idx["SugarContent"]].strip(),
                    "proteinContent": row[idx["ProteinContent"]].strip(),
                },
            )
            key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
            time_bound = "< 30 mins" in keywords or (
                cand.total_minutes is not None and cand.total_minutes <= 35
            )
            # Level 1: ages 7-10, no heat or sharp tools, simple assembly.
            if (
                "kid friendly" in keywords
                and n_ingredients <= 8
                and steps <= 8
                and not HEAT.search(instr_blob)
                and key not in seen_titles
            ):
                pools[1].append(cand)
                seen_titles.add(key)
            # Level 2: ages 11-14, stovetop/oven basics, around half an hour.
            elif (
                ("easy" in keywords or "beginner cook" in keywords)
                and time_bound
                and n_ingredients <= 12
                and steps <= 12
                and key not in seen_titles
            ):
                pools[2].append(cand)
                seen_titles.add(key)
            # Level 3: ages 15-18, full meals and appliances.
            elif (
                (category in MAIN_CATEGORIES or cand.staple_score >= 3)
                and (cand.staple_score >= 2 or PRESSURE.search(blob) or SLOW_COOKER.search(blob))
                and key not in seen_titles
            ):
                pools[3].append(cand)
                seen_titles.add(key)

            # Track per-level funnel counts (approximate: pre-dedupe).
            funnel["L1 eligible"][0] += 1 if (
                "kid friendly" in keywords and n_ingredients <= 8 and steps <= 8
                and not HEAT.search(instr_blob)
            ) else 0
            funnel["L2 eligible"][0] += 1 if (
                ("easy" in keywords or "beginner cook" in keywords) and time_bound
            ) else 0
            funnel["L3 eligible"][0] += 1 if (
                (category in MAIN_CATEGORIES or cand.staple_score >= 3)
                and (cand.staple_score >= 2 or PRESSURE.search(blob) or SLOW_COOKER.search(blob))
            ) else 0
    return pools


def rank(pool: list[Candidate]) -> list[Candidate]:
    return sorted(
        pool,
        key=lambda c: (
            -c.staple_score,
            c.n_ingredients,
            c.total_minutes if c.total_minutes is not None else 9999,
            -(c.rating or 0),
            -(c.reviews or 0),
        ),
    )


def pick(pool: list[Candidate], per_level: int) -> list[Candidate]:
    """Rank, cap repeated categories, dedupe titles."""
    chosen: list[Candidate] = []
    per_category: dict[str, int] = {}
    seen: set[str] = set()
    for cand in rank(pool):
        key = re.sub(r"[^a-z0-9]+", " ", cand.name.lower()).strip()
        if key in seen:
            continue
        if per_category.get(cand.category, 0) >= 6:
            continue
        chosen.append(cand)
        seen.add(key)
        per_category[cand.category] = per_category.get(cand.category, 0) + 1
        if len(chosen) >= per_level:
            break
    return chosen


def fetch_jsonld(rid: int, name: str, retries: int = 1) -> dict | None:
    url = f"https://www.food.com/recipe/{slugify(name)}-{rid}"
    for _attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                final_url = resp.geturl()
            for m in re.finditer(
                r'<script type="application/ld\+json">(.*?)</script>', html, re.S
            ):
                try:
                    data = json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
                for item in data if isinstance(data, list) else [data]:
                    if isinstance(item, dict) and item.get("@type") == "Recipe":
                        item["_url"] = final_url
                        return item
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        time.sleep(1.5)
    return None


def ingredient_lines(jsonld: dict, factor: float) -> list[str]:
    lines: list[str] = []
    for item in jsonld.get("recipeIngredient") or []:
        if isinstance(item, dict):
            qty = scale_quantity(str(item.get("quantity") or ""), factor,
                                  count=not str(item.get("unit") or "").strip())
            unit = (item.get("unit") or "").strip()
            name = (item.get("name") or "").strip()
            text = " ".join(x for x in (qty, unit, name) if x)
        else:
            text = str(item).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            lines.append(f"- [ ] {text}")
    return lines


def step_lines(jsonld: dict, fallback: tuple[str, ...]) -> list[str]:
    raw = jsonld.get("recipeInstructions")
    steps: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            text = (item.get("text") or "") if isinstance(item, dict) else str(item)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"^\s*\d+[.)]\s*", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                steps.append(text)
    if not steps:
        steps = [re.sub(r"\s+", " ", s).strip() for s in fallback if s.strip()]
    return [f"{i}. {s}" for i, s in enumerate(steps, start=1)]


def nutrition_line(jsonld: dict, cand: Candidate) -> str:
    nut = jsonld.get("nutrition") if isinstance(jsonld.get("nutrition"), dict) else {}
    nut = nut or {}
    fields = (
        ("calories", "kcal"),
        ("fatContent", "g fat"),
        ("saturatedFatContent", "g sat fat"),
        ("cholesterolContent", "mg cholesterol"),
        ("sodiumContent", "mg sodium"),
        ("carbohydrateContent", "g carbs"),
        ("fiberContent", "g fiber"),
        ("sugarContent", "g sugar"),
        ("proteinContent", "g protein"),
    )
    parts = []
    for key, label in fields:
        value = str(nut.get(key) or "").strip()
        if value and value.upper() != "NA":
            parts.append(f"{value} {label}")
    if not parts:
        return "*Nutrition was not stated by the submitter.*"
    return (
        "*Per serving, as stated by the submitter - foodcom:* "
        + " · ".join(parts)
    )


def tags_for(cand: Candidate, level: int, blob: str) -> list[str]:
    tags = [f"#level-{level}"]
    if level == 1 and "no cook" in cand.keywords:
        tags.append("#no-cook")
    if cand.n_ingredients <= 5:
        tags.append("#5-ingredients")
    if "< 30 mins" in cand.keywords or (
        cand.total_minutes is not None and cand.total_minutes <= 35
    ):
        tags.append("#30-minutes")
    if SLOW_COOKER.search(blob):
        tags.append("#slow-cooker")
    if PRESSURE.search(blob):
        tags.append("#instant-pot")
    return tags


def render_note(cand: Candidate, level: int, jsonld: dict | None) -> str:
    factor = SERVINGS_TARGET / cand.servings
    jsonld = jsonld or {}
    title = html.unescape((jsonld.get("name") or cand.name).strip())
    blob = f"{title} {' '.join(cand.instructions)}"
    if jsonld:
        blob += " " + " ".join(str(i) for i in jsonld.get("recipeIngredient") or [])
    tags = " ".join(tags_for(cand, level, blob))
    prep = (jsonld.get("prepTime") and dur_minutes(jsonld.get("prepTime"))) or cand.prep_minutes
    cook = (jsonld.get("cookTime") and dur_minutes(jsonld.get("cookTime"))) or cand.cook_minutes
    total = (jsonld.get("totalTime") and dur_minutes(jsonld.get("totalTime"))) or cand.total_minutes
    source_url = jsonld.get("_url") or f"https://www.food.com/recipe/{slugify(cand.name)}-{cand.rid}"
    ings = ingredient_lines(jsonld, factor) if jsonld else [
        f"- [ ] {' '.join(x for x in (scale_quantity(q, factor, count=not u.strip()), u, p) if x)}".strip()
        for q, u, p in _fallback_ingredients(cand)
    ]
    steps = step_lines(jsonld, cand.instructions)
    ings = [html.unescape(x) for x in ings]
    steps = [html.unescape(x) for x in steps]
    nutrition = nutrition_line(jsonld, cand) if jsonld else nutrition_line(
        {"nutrition": cand.nutrition}, cand
    )
    known_times = [
        f"{label} {fmt_minutes(value)}"
        for label, value in (("prep", prep), ("cook", cook), ("total", total))
        if value is not None
    ]
    times_line = " · ".join(
        [*known_times, f"servings {SERVINGS_TARGET} (scaled from {cand.servings})"]
    )
    lines = [
        f"# {title}",
        "",
        "Category: Recipes",
        "",
        f"Tags: {tags}",
        f"Times: {times_line}",
        "",
        f"Source: {source_url} - Food.com, CC0 dataset ({DATASET_URL})",
        "",
        "## Ingredients",
        *ings,
        "",
        "## Steps",
        *steps,
        "",
        "## Nutrition",
        nutrition,
        "",
    ]
    return "\n".join(lines)


def _fallback_ingredients(cand: Candidate):
    # The dataset strips units from quantities (and the pipeline normally
    # fills them in from the fetched page), so the fallback lists bare
    # ingredient names only.
    for part in cand.parts:
        yield ("", "", part)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--recipes-csv",
        default=os.getenv("RECIPES_CSV", DEFAULT_RECIPES_CSV),
        help="Path to the corpus recipes.csv (see module docstring)",
    )
    ap.add_argument("--out", default=".tmp/recipes-notes", help="Directory for --write runs")
    ap.add_argument("--write", action="store_true", help="Write notes (default: dry run)")
    ap.add_argument("--per-level", type=int, default=40, help="Recipes per level")
    ap.add_argument("--level", type=int, action="append", choices=(1, 2, 3), help="Limit levels")
    ap.add_argument("--preview", type=int, default=2, help="Preview notes per level in dry runs")
    ap.add_argument("--delay", type=float, default=0.7, help="Seconds between food.com fetches")
    args = ap.parse_args(argv)

    csv_path = Path(args.recipes_csv)
    if not csv_path.exists():
        print(f"[FAIL] corpus not found: {csv_path}\n       see the module docstring for the download command")
        return 1
    levels = args.level or [1, 2, 3]

    funnel = {
        "rows": [0],
        "numeric servings": [0],
        "no dessert/drink noise": [0],
        "ingredient/step bounds": [0],
        "no alcohol": [0],
        "L1 eligible": [0],
        "L2 eligible": [0],
        "L3 eligible": [0],
    }
    print(f"Scanning {csv_path} ...")
    pools = stream_candidates(csv_path, funnel)
    print("Funnel: " + " | ".join(f"{k}: {v[0]}" for k, v in funnel.items()))
    print()

    out_dir = Path(args.out)
    if args.write:
        out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[int, str]] = []
    used_filenames: set[str] = set()
    for level in levels:
        picked = pick(pools[level], args.per_level)
        print(f"== Level {level}: {len(pools[level])} candidates, picked {len(picked)}")
        fetched = 0
        failures = 0
        for cand in picked:
            if not args.write and fetched >= args.preview:
                break
            jsonld = fetch_jsonld(cand.rid, cand.name)
            if jsonld is None:
                failures += 1
            fetched += 1 if jsonld else 0
            note = render_note(cand, level, jsonld)
            if args.write:
                base = slugify(html.unescape((jsonld or {}).get("name") or cand.name)) or f"recipe-{cand.rid}"
                fname = f"{base}.md"
                n = 2
                while fname in used_filenames or (out_dir / fname).exists():
                    fname = f"{base} ({n}).md"
                    n += 1
                used_filenames.add(fname)
                (out_dir / fname).write_text(note, encoding="utf-8")
                written.append((level, fname))
                print(f"   wrote {fname}  (rid {cand.rid}, staples: {', '.join(cand.staples) or '-'})")
            else:
                print(f"   - rid {cand.rid} {cand.name}  "
                      f"[{cand.n_ingredients} ingr, {cand.steps} steps, "
                      f"{cand.servings} -> 7 servings, staples: {', '.join(cand.staples) or '-'}]")
                print(note)
                print("-" * 78)
            time.sleep(args.delay)
        if args.write:
            print(f"   wrote {len([w for w in written if w[0] == level])} notes, "
                  f"{failures} fetch failures")
    if args.write:
        print(f"\nDone: {len(written)} notes in {out_dir}")
    else:
        print("Dry run complete. Add --write to render every picked recipe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
