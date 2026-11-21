"""Harvest forecast, food-preservation advice and the Put-Up Pantry (Phase 6).

Everything here is **data-driven**: the preservation methods, the USDA safety
copy, the combo ideas and the supply lists live in ``preservation_combos.json``
next to this module. The botanical seed owns per-crop facts
(``days_to_maturity``, ``harvest_window_days``, ``preservation_methods``,
``usda_acidity_class``, ``blanching_minutes``, ``herb_preservation_tips``); this
module only joins them to what the household is actually growing.

Growing Degree Days
-------------------
The seed says how many days a crop needs, not how many heat units, so the GDD
model is self-consistent rather than invented: the requirement is
``days_to_maturity`` worth of GDD **at the rate the season is currently
running** (the mean daily GDD of the observed past plus the forecast). An
average season therefore reproduces ``planted_date + days_to_maturity`` exactly,
a hot spell pulls the date in and a cold spell pushes it out.

Base temperatures follow the crop's recorded frost tolerance: tender
(warm-season) crops accumulate above 50 °F, hardy (cool-season) crops above
40 °F, half-hardy between the two. With no weather at all the estimate is plain
``planted_date + days_to_maturity`` and every item says ``gdd_adjusted: false``.

Food safety
-----------
Canning advice never invents processing times or ratios: the advisory names the
tested USDA / NCHFP / Ball source and states whether the crop is high-acid
(water-bath safe) or low-acid (pressure canner required). The seed's
``usda_acidity_class`` column is the authority -- ``scripts/build_plants_seed.py``
asserts every known low-acid crop is recorded as such -- so nothing here repeats
that list.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from sqlmodel import Session, select

from services.garden.models import PantryBatch, PantryBatchOut, PantryGoal, Plant
from services.garden.species_lookup import resolve_species

log = logging.getLogger("garden.preservation")

CATALOG_PATH = Path(__file__).with_name("preservation_combos.json")

#: Crops the household eats; only these get a harvest forecast or preservation advice.
EDIBLE_CATEGORIES = frozenset({"vegetable", "fruit", "herb"})

#: Prep checklists start this many days before a crop is ready.
PREP_HORIZON_DAYS = 21

#: "Use first" reaches this far ahead of a batch's use-by date.
USE_FIRST_DAYS = 60

#: Volume units that convert cleanly between each other, in quarts.
VOLUME_IN_QUARTS = {"half_pints": 0.25, "pints": 0.5, "quarts": 1.0}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
#: Words that carry no crop identity ("fresh", "sweet", ...).
_TOKEN_STOP = frozenset({"the", "and", "with", "for", "sweet", "fresh", "dry", "dried", "common", "bush", "pole"})


@lru_cache(maxsize=1)
def catalog() -> dict:
    """The preservation catalog (methods, safety copy, supply categories, combos)."""
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def method_info(method: str) -> dict:
    """One method's label/storage/supplies; empty for an unknown method."""
    return (catalog().get("methods") or {}).get(method) or {}


def method_label(method: str) -> str:
    return method_info(method).get("label") or method.replace("_", " ")


def safety_for(acidity_class: str | None) -> dict:
    """The USDA safety callout for a crop's recorded acidity class."""
    safety = catalog().get("safety") or {}
    return safety.get(acidity_class or "") or safety.get("unknown") or {}


def _tokens(text: Any) -> set[str]:
    """Crop-identity words from a phrase (``"Roma (Plum) Tomato"`` -> roma, plum, tomato)."""
    if not text:
        return set()
    return {
        part
        for part in _TOKEN_SPLIT.split(str(text).lower())
        if len(part) >= 3 and part not in _TOKEN_STOP
    }


def crop_tokens(species: dict | None) -> set[str]:
    """Every word that names this crop: its key, common name and culinary aliases."""
    if not species:
        return set()
    aliases = species.get("culinary_aliases") or ""
    return (
        _tokens((species.get("key") or "").replace("_", " "))
        | _tokens(species.get("common_name"))
        | _tokens(str(aliases).replace(",", " "))
    )


def _same_word(a: str, b: str) -> bool:
    """Equal, or equal ignoring a trailing plural ``s``/``es`` (tomato/tomatoes)."""
    if a == b:
        return True
    for long, short in ((a, b), (b, a)):
        if long in (short + "s", short + "es") and len(long) >= 4:
            return True
    return False


def _matches(wanted: str, tokens: Iterable[str]) -> bool:
    """Does a catalog crop word (``"green bean"``) match the tokens a crop has?"""
    tokens = list(tokens)
    words = [word for word in _TOKEN_SPLIT.split((wanted or "").lower()) if word]
    if not words:
        return False
    return all(any(_same_word(word, token) for token in tokens) for word in words)


def _as_list(value: Any) -> list[str]:
    """A seed CSV column (``"freezing, dehydrating"``) as a clean list."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _num(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def gdd_base_f(frost_tolerance: str | None) -> int:
    """The GDD base temperature for a crop, from its recorded frost tolerance.

    Tender (warm-season) crops accumulate above 50 °F, hardy (cool-season)
    crops above 40 °F, half-hardy between them. A crop with no recorded
    tolerance is treated as tender, the common case for seed-catalog vegetables.
    """
    tolerance = (frost_tolerance or "").lower()
    if tolerance.startswith("tender"):
        return 50
    if tolerance.startswith("half_hardy"):
        return 45
    if tolerance.startswith("hardy"):
        return 40
    return 50


def _window_temps(daily: Any) -> list[tuple[date, float, float]]:
    """``(date, high, low)`` for every forecast day that carries both temperatures."""
    out: list[tuple[date, float, float]] = []
    for day in daily or []:
        when = _iso((day or {}).get("date"))
        high = _num((day or {}).get("high"))
        low = _num((day or {}).get("low"))
        if when and high is not None and low is not None:
            out.append((when, high, low))
    out.sort(key=lambda row: row[0])
    return out


def _daily_gdd(high: float, low: float, base: int) -> float:
    """GDD for one day: the day's mean above the base, never negative."""
    return max(0.0, ((high + low) / 2.0) - base)


def estimate_ready_date(
    planted: date,
    days_to_maturity: float,
    *,
    frost_tolerance: str | None,
    temps: list[tuple[date, float, float]],
    today: date,
) -> tuple[date, bool, int, float]:
    """Maturity date, GDD-adjusted when weather is available.

    The crop's heat requirement is fixed by what it has actually been growing
    in: ``days_to_maturity`` worth of GDD **at the rate of the season so far**
    (days already observed, gaps filled at that same rate). The remaining heat
    is then projected with the **forecast** rate, so a hot spell ahead pulls the
    date in and a cold one pushes it out -- while an average season reproduces
    ``planted_date + days_to_maturity`` exactly.

    Returns ``(ready, gdd_adjusted, gdd_base, daily_rate)``.
    """
    plain = planted + timedelta(days=round(days_to_maturity))
    base = gdd_base_f(frost_tolerance)
    if not temps or planted > today:
        return plain, False, base, 0.0

    values = [(when, _daily_gdd(high, low, base)) for when, high, low in temps]
    past = [value for when, value in values if when <= today]
    future = [value for when, value in values if when > today]
    rate_past = sum(past) / len(past) if past else (sum(future) / len(future) if future else 0.0)
    rate_ahead = sum(future) / len(future) if future else rate_past
    if rate_past <= 0 or rate_ahead <= 0:
        # At or below the base temperature nothing is growing: saying so by
        # keeping the plain estimate is honest; inventing a slow-down is not.
        return plain, False, base, rate_ahead

    observed = dict(values)
    accumulated = 0.0
    cursor = planted
    while cursor < today:
        accumulated += observed.get(cursor, rate_past)
        cursor += timedelta(days=1)
    required = days_to_maturity * rate_past
    remaining = required - accumulated
    if remaining <= 0:
        return today, True, base, rate_ahead
    return today + timedelta(days=math.ceil(remaining / rate_ahead)), True, base, rate_ahead


# --------------------------------------------------------------------------- harvest forecast


def compute_harvest_forecast(
    session: Session,
    *,
    days: int = 28,
    daily: Any = None,
    today: date | None = None,
) -> dict:
    """The rolling "Coming Ready Soon" window for every active edible plant.

    ``daily`` is the weather service's ``daily`` series (``{date, high, low}``);
    without it the forecast is plain ``planted_date + days_to_maturity`` and
    every item reports ``gdd_adjusted: false``.
    """
    today = today or date.today()
    temps = _window_temps(daily)
    plants = session.exec(select(Plant).where(Plant.status == "active")).all()

    items: list[dict] = []
    for plant in plants:
        if plant.category not in EDIBLE_CATEGORIES:
            continue
        species = resolve_species(session, plant.species_ref) or {}
        maturity = _num(species.get("days_to_maturity"))
        planted = _iso(plant.planted_date)
        expected = _iso(plant.expected_harvest_start)
        if not planted and not expected:
            continue
        if not maturity and not expected:
            continue

        if planted and maturity:
            ready, gdd_adjusted, base, rate = estimate_ready_date(
                planted,
                maturity,
                frost_tolerance=species.get("frost_tolerance"),
                temps=temps,
                today=today,
            )
            plain = planted + timedelta(days=round(maturity))
        else:
            ready = expected
            plain = expected
            gdd_adjusted = False
            base = gdd_base_f(species.get("frost_tolerance"))
            rate = 0.0

        days_until = (ready - today).days
        if days_until > days:
            continue
        window = _num(species.get("harvest_window_days")) or 0.0
        items.append(
            {
                "plant_id": plant.id,
                "nickname": plant.nickname,
                "common_name": species.get("common_name") or plant.species_ref,
                "category": plant.category,
                "species_ref": plant.species_ref,
                "planted_date": planted.isoformat() if planted else None,
                "estimated_ready_date": ready.isoformat(),
                "harvest_window_end": (ready + timedelta(days=round(window))).isoformat(),
                "days_until_ready": days_until,
                "ready_now": days_until <= 0,
                "gdd_adjusted": gdd_adjusted,
                "gdd_base_f": base,
                "gdd_daily_rate": round(rate, 2),
                "gdd_shift_days": (ready - plain).days if plain else 0,
                "days_to_maturity": maturity,
                "harvest_window_days": window or None,
                "culinary_aliases": _as_list(species.get("culinary_aliases")),
                "preservation_methods": _as_list(species.get("preservation_methods")),
                "usda_acidity_class": species.get("usda_acidity_class"),
                "blanching_minutes": _num(species.get("blanching_minutes")),
                "location_id": plant.location_id,
                "assigned_to": plant.assigned_to,
            }
        )

    items.sort(key=lambda item: (item["days_until_ready"], item["common_name"] or ""))
    return {
        "generated_on": today.isoformat(),
        "window_days": days,
        "weather_used": bool(temps),
        "gdd_adjusted_count": sum(1 for item in items if item["gdd_adjusted"]),
        "count": len(items),
        "items": items,
    }


# --------------------------------------------------------------------------- preservation advice


def method_key(recorded: str) -> str:
    """Map a seed ``preservation_methods`` word onto a catalog method key."""
    word = (recorded or "").strip().lower().replace(" ", "_")
    if word in (catalog().get("methods") or {}):
        return word
    return (catalog().get("method_aliases") or {}).get(word, word)


def _active_crops(session: Session) -> list[dict]:
    """One entry per species the household is actively growing to eat."""
    crops: dict[str, dict] = {}
    plants = session.exec(select(Plant).where(Plant.status == "active")).all()
    for plant in plants:
        if plant.category not in EDIBLE_CATEGORIES:
            continue
        species = resolve_species(session, plant.species_ref) or {}
        entry = crops.get(plant.species_ref)
        if entry is None:
            recorded = _as_list(species.get("preservation_methods"))
            blanching = _num(species.get("blanching_minutes"))
            entry = crops[plant.species_ref] = {
                "species_ref": plant.species_ref,
                "common_name": species.get("common_name") or plant.species_ref,
                "category": plant.category,
                "culinary_aliases": _as_list(species.get("culinary_aliases")),
                "usda_acidity_class": species.get("usda_acidity_class"),
                "safety": safety_for(species.get("usda_acidity_class")),
                "blanching_minutes": blanching,
                "herb_preservation_tips": (species.get("herb_preservation_tips") or "").strip() or None,
                "preservation_conversion": (species.get("preservation_conversion") or "").strip() or None,
                "plant_ids": [],
                "tokens": crop_tokens({**species, "key": plant.species_ref}),
                "methods": [
                    {
                        "method": method_key(raw),
                        "recorded_as": raw,
                        "label": method_label(method_key(raw)),
                        "storage": method_info(method_key(raw)).get("storage"),
                        "use_by_months": method_info(method_key(raw)).get("use_by_months"),
                        "blanching_minutes": blanching if method_key(raw) == "freezing" else None,
                        "requires_acidification": bool(
                            method_key(raw) == "water_bath_canning"
                            and species.get("usda_acidity_class") == "low_acid"
                        ),
                        "supplies": method_info(method_key(raw)).get("supplies") or [],
                    }
                    for raw in recorded
                ],
            }
        entry["plant_ids"].append(plant.id)
    for entry in crops.values():
        entry["plant_count"] = len(entry["plant_ids"])
    return sorted(crops.values(), key=lambda c: (c["category"], c["common_name"]))


def match_combos(crops: list[dict]) -> list[dict]:
    """Preservation combos the household can make now, with what is missing."""
    tokens: set[str] = set()
    for crop in crops:
        tokens |= crop["tokens"]

    def names(words: list[str]) -> list[str]:
        found: list[str] = []
        for crop in crops:
            if any(_matches(word, crop["tokens"]) for word in words):
                if crop["common_name"] not in found:
                    found.append(crop["common_name"])
        return found

    out: list[dict] = []
    for combo in catalog().get("combos") or []:
        required = list(combo.get("required") or [])
        optional = list(combo.get("optional") or [])
        matched_required = [word for word in required if _matches(word, tokens)]
        missing_required = [word for word in required if word not in matched_required]
        matched_optional = [word for word in optional if _matches(word, tokens)]
        missing_optional = [word for word in optional if word not in matched_optional]
        if not matched_required:
            continue
        info = method_info(combo.get("method") or "")
        out.append(
            {
                "key": combo.get("key"),
                "label": combo.get("label"),
                "method": combo.get("method"),
                "method_label": method_label(combo.get("method") or ""),
                "note": combo.get("note"),
                "required": required,
                "optional": optional,
                "matched_crops": names(matched_required),
                "missing_crops": missing_required,
                "optional_matched": names(matched_optional),
                "optional_missing": missing_optional,
                "ready": not missing_required,
                "matched_count": len(matched_required),
                "required_count": len(required),
                "safety": safety_for(info.get("acidity")),
                "supplies": list(combo.get("supplies") or []),
            }
        )
    out.sort(key=lambda combo: (not combo["ready"], -combo["matched_count"], combo["label"] or ""))
    return out


def _categorize(supplies: list[str]) -> list[dict]:
    """Group supplies under the catalog's categories, in catalog order."""
    categories = catalog().get("supply_categories") or []
    buckets: dict[str, dict] = {
        entry.get("key"): {"key": entry.get("key"), "label": entry.get("label"), "items": []}
        for entry in categories
    }
    buckets["other"] = {"key": "other", "label": "Other", "items": []}
    seen: set[str] = set()
    for item in supplies:
        if item in seen:
            continue
        seen.add(item)
        low = item.lower()
        placed = False
        for entry in categories:
            if any(word in low for word in entry.get("match") or []):
                buckets[entry.get("key")]["items"].append(item)
                placed = True
                break
        if not placed:
            buckets["other"]["items"].append(item)
    return [bucket for bucket in buckets.values() if bucket["items"]]


def build_prep_checklist(items: list[dict], *, horizon_days: int = PREP_HORIZON_DAYS) -> dict:
    """Supplies to buy now for what ripens within ``horizon_days``."""
    upcoming = [item for item in items if item.get("days_until_ready", 0) <= horizon_days]
    methods: list[str] = []
    supplies: list[str] = []
    for item in upcoming:
        for raw in item.get("preservation_methods") or []:
            key = method_key(raw)
            if key not in methods:
                methods.append(key)
            supplies.extend(method_info(key).get("supplies") or [])
    upcoming_crops = [
        {
            "common_name": item.get("common_name"),
            "tokens": _tokens(item.get("common_name"))
            | _tokens(" ".join(item.get("culinary_aliases") or [])),
        }
        for item in upcoming
    ]
    combos = match_combos(upcoming_crops)
    for combo in combos:
        supplies.extend(combo["supplies"])
    return {
        "horizon_days": horizon_days,
        "crops": [item.get("common_name") for item in upcoming],
        "methods": [{"method": key, "label": method_label(key)} for key in methods],
        "combos": [combo["label"] for combo in combos],
        "count": len(set(supplies)),
        "items": list(dict.fromkeys(supplies)),
        "categories": _categorize(supplies),
    }


def build_preservation_advice(
    session: Session,
    *,
    forecast: dict | None = None,
    today: date | None = None,
) -> dict:
    """Methods, USDA safety callouts, combo ideas and the prep checklist."""
    today = today or date.today()
    crops = _active_crops(session)
    if forecast is None:
        forecast = compute_harvest_forecast(session, today=today)
    return {
        "generated_on": today.isoformat(),
        "crop_count": len(crops),
        "plant_count": sum(crop["plant_count"] for crop in crops),
        "crops": crops,
        "combos": match_combos(crops),
        "prep_checklist": build_prep_checklist(forecast.get("items") or []),
        "safety_sources": sorted(
            {
                callout.get("source")
                for callout in (catalog().get("safety") or {}).values()
                if callout.get("source")
            }
        ),
    }


# --------------------------------------------------------------------------- put-up pantry


def convert_qty(qty: float, from_unit: str, to_unit: str) -> float | None:
    """``qty`` converted between units, or None when they do not convert cleanly.

    Volumes (quarts / pints / half-pints) convert exactly; anything else only
    counts against a goal in the same unit. None means "report the mismatch"
    rather than quietly dropping the jars.
    """
    if not from_unit or not to_unit:
        return None
    if from_unit == to_unit:
        return qty
    if from_unit in VOLUME_IN_QUARTS and to_unit in VOLUME_IN_QUARTS:
        return qty * VOLUME_IN_QUARTS[from_unit] / VOLUME_IN_QUARTS[to_unit]
    return None


def _add_months(when: date, months: int) -> date:
    month = when.month - 1 + months
    year = when.year + month // 12
    month = month % 12 + 1
    day = min(when.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                         31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def suggest_use_by(batch_date: str | None, method: str) -> str | None:
    """The catalog's shelf life for a method, counted from the batch date."""
    started = _iso(batch_date)
    months = method_info(method).get("use_by_months")
    if not started or not months:
        return None
    return _add_months(started, int(months)).isoformat()


def batch_flags(batch: PantryBatch, *, today: date, use_first_days: int = USE_FIRST_DAYS) -> dict:
    """Use-first / past-use-by flags for one batch."""
    use_by = _iso(batch.use_by_date)
    days_until = (use_by - today).days if use_by else None
    empty = batch.qty_remaining <= 0
    soon = days_until is not None and days_until <= use_first_days
    return {
        "days_until_use_by": days_until,
        "past_use_by": bool(days_until is not None and days_until < 0 and not empty),
        "use_first": bool(soon and not empty),
    }


def goal_progress(session: Session, *, season_year: int | None = None) -> list[dict]:
    """Per-goal progress: what was put up, what is left, and how far along it is."""
    query = select(PantryGoal)
    if season_year is not None:
        query = query.where(PantryGoal.season_year == season_year)
    goals = session.exec(query.order_by(PantryGoal.created_at)).all()
    out: list[dict] = []
    for goal in goals:
        batches = session.exec(select(PantryBatch).where(PantryBatch.goal_id == goal.id)).all()
        put_up = 0.0
        remaining = 0.0
        mismatch: list[str] = []
        for batch in batches:
            converted_initial = convert_qty(batch.qty_initial, batch.unit, goal.unit)
            converted_remaining = convert_qty(batch.qty_remaining, batch.unit, goal.unit)
            if converted_initial is None or converted_remaining is None:
                if batch.unit not in mismatch:
                    mismatch.append(batch.unit)
                continue
            put_up += converted_initial
            remaining += converted_remaining
        percent = 0
        if goal.target_qty > 0:
            percent = min(100, round(put_up / goal.target_qty * 100))
        out.append(
            {
                "goal_id": goal.id,
                "item": goal.item,
                "unit": goal.unit,
                "target_qty": goal.target_qty,
                "qty_put_up": round(put_up, 2),
                "qty_remaining": round(remaining, 2),
                "percent_complete": percent,
                "batch_count": len(batches),
                "unit_mismatch": bool(mismatch),
                "mismatched_units": mismatch,
                "season_year": goal.season_year,
            }
        )
    return out


def build_pantry_view(
    session: Session,
    *,
    goal_id: str | None = None,
    include_empty: bool = False,
    today: date | None = None,
) -> dict:
    """The pantry shelf: batches with use-first flags plus goal progress."""
    today = today or date.today()
    query = select(PantryBatch)
    if goal_id is not None:
        query = query.where(PantryBatch.goal_id == goal_id)
    if not include_empty:
        query = query.where(PantryBatch.qty_remaining > 0)
    batches = list(session.exec(query).all())

    # Only jars that still hold something can be "use first": an empty one would
    # wear the badge forever and point at nothing.
    oldest_by_label: dict[str, str] = {}
    for batch in sorted(batches, key=lambda b: (b.batch_date or "", b.created_at)):
        if (batch.qty_remaining or 0) <= 0:
            continue
        oldest_by_label.setdefault(batch.label.strip().lower(), batch.id)

    rows: list[dict] = []
    for batch in batches:
        flags = batch_flags(batch, today=today)
        oldest = oldest_by_label.get(batch.label.strip().lower()) == batch.id
        rows.append(
            {
                **PantryBatchOut.model_validate(batch).model_dump(),
                **flags,
                # The oldest jar of a label is used first even with a later date.
                "use_first": bool(flags["use_first"] or oldest),
                "oldest_in_label": oldest,
                "method_label": method_label(batch.method),
            }
        )
    # Soonest use-by first; no date after dated; then the oldest batch.
    rows.sort(
        key=lambda row: (
            not row["use_first"],
            row["days_until_use_by"] if row["days_until_use_by"] is not None else 10**6,
            row["batch_date"] or "",
        )
    )
    return {
        "generated_on": today.isoformat(),
        "include_empty": include_empty,
        "goal_id": goal_id,
        "count": len(rows),
        "use_first_count": sum(1 for row in rows if row["use_first"]),
        "batches": rows,
        "goal_progress": goal_progress(session),
    }




