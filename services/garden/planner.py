"""Phase 5: Family-Sized Outdoor Garden Planner core logic.

USDA zone & frost detection, dual sizing (family presets + Pantry-Goal
back-calculator), companion / antagonist / multi-year rotation checks,
deterministic auto-arrange, and seasonal planting timeline computation.

Never hardcodes coordinates or zone data -- household lat/lon comes from the
weather/geo configuration, degrades cleanly when unconfigured.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from services.garden.cadence import compute_interval
from services.garden.models import (
    GardenPlan,
    GardenPlanIn,
    GardenPlanOut,
    Location,
    PantryGoal,
    PantryGoalIn,
    PantryGoalOut,
    PlanCell,
    PlanCellIn,
    PlanCellOut,
    Plant,
    utcnow,
)
from services.garden.species_lookup import resolve_species

log = logging.getLogger("garden.planner")

# ── USDA zone helpers ────────────────────────────────────────────────────────

USDA_ZONES = [f"{n}{l}" for n in range(1, 12) for l in ("a", "b")]


def zone_number(zone: str | int | float | None) -> int:
    """Extract the numeric part of a USDA zone like '7b' -> 7, or an int as-is.

    ``plants-seed.db`` stores ``usda_zone_min`` / ``usda_zone_max`` as integers
    (3..11) while households store strings like ``"7b"``; both must work.
    """
    if zone is None:
        return 0
    if isinstance(zone, (int, float)):
        return int(zone)
    digits = "".join(c for c in str(zone) if c.isdigit())
    return int(digits) if digits else 0


def zone_letter(zone: str | int | None) -> str:
    """Extract the letter part of a USDA zone string like '7b' -> 'b'."""
    if zone is None or isinstance(zone, (int, float)):
        return "a"
    return "".join(c for c in str(zone) if c.isalpha()).lower() or "a"


def zone_sort_key(zone: str | int | None) -> tuple[int, int]:
    """Sort key for USDA zones: (number, letter_index)."""
    return (zone_number(zone), 0 if zone_letter(zone) == "a" else 1)


def zone_in_range(
    zone: str | int | None,
    zone_min: str | int | None,
    zone_max: str | int | None,
) -> bool:
    """True when ``zone`` falls between ``zone_min`` and ``zone_max`` inclusive."""
    if zone is None or zone_min is None or zone_max is None:
        return True  # no constraint -> suitable
    z, zmin, zmax = zone_sort_key(zone), zone_sort_key(zone_min), zone_sort_key(zone_max)
    return zmin <= z <= zmax


def season_length_days(zone: str) -> int:
    """Approximate frost-free season length in days for a USDA zone."""
    # Rough growing-season lengths per USDA zone (conservative).
    n = zone_number(zone)
    table = {
        1: 50, 2: 70, 3: 90, 4: 110, 5: 130, 6: 150, 7: 170, 8: 200, 9: 220, 10: 250, 11: 300,
    }
    return table.get(n, 150)


# ── Frost dates ──────────────────────────────────────────────────────────────

def default_frost_dates(zone: str | None) -> tuple[str | None, str | None]:
    """Return (last_spring_frost_mmdd, first_fall_frost_mmdd) for a USDA zone.

    Conservative typical dates (month-day). Degrades to (None, None) when
    zone is unconfigured.
    """
    if not zone:
        return None, None
    n = zone_number(zone)
    spring = {
        1: "07-15", 2: "06-01", 3: "05-15", 4: "05-01", 5: "04-15", 6: "04-01",
        7: "03-15", 8: "03-01", 9: "02-01", 10: "01-15", 11: "12-15",
    }.get(n, "05-01")
    fall = {
        1: "07-15", 2: "08-01", 3: "09-15", 4: "10-01", 5: "10-15", 6: "10-15",
        7: "11-01", 8: "11-15", 9: "12-01", 10: "12-15", 11: "01-15",
    }.get(n, "10-01")
    return spring, fall


def zone_from_min_temp(min_f: float) -> str:
    """USDA hardiness zone from an extreme annual minimum temperature (°F).

    Zone 1a starts at -60°F; each full zone is 10°F and each half (a/b) 5°F.
    """
    if min_f < -60:
        return "1a"
    if min_f >= 50:
        return "11b"
    number = int((min_f + 60) // 10) + 1
    number = max(1, min(11, number))
    lower = -60 + 10 * (number - 1)
    letter = "b" if (min_f - lower) >= 5 else "a"
    return f"{number}{letter}"


async def detect_zone_for_location(
    latitude: float,
    longitude: float,
    years: int = 5,
) -> tuple[str | None, float | None]:
    """Detect the USDA zone from Open-Meteo's historical winter minima.

    Uses the real configured coordinates (never a hardcoded latitude map) and
    degrades to ``(None, None)`` when the archive service is unavailable.
    """
    import aiohttp

    from services.common.http import get_client

    end = date.today() - timedelta(days=1)
    try:
        start = date(end.year - years, end.month, end.day)
    except ValueError:  # 29 February
        start = date(end.year - years, 2, 28)
    params = {
        "latitude": round(float(latitude), 4),
        "longitude": round(float(longitude), 4),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_min",
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
    }
    try:
        async with get_client().get(
            "https://archive-api.open-meteo.com/v1/archive",
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                log.info("[Garden] zone archive answered HTTP %s", resp.status)
                return None, None
            raw = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001 - optional external service
        log.info("[Garden] zone archive unavailable: %s", exc)
        return None, None

    daily = (raw or {}).get("daily") or {}
    values = [v for v in (daily.get("temperature_2m_min") or []) if v is not None]
    if not values:
        return None, None
    min_f = float(min(values))
    return zone_from_min_temp(min_f), min_f


def frost_offset_weeks(species: dict, key: str) -> int | None:
    """Read a frost offset (weeks relative to spring/fall frost) from species.

    Positive = weeks before last spring frost; negative = after last spring frost.
    For fall: weeks before first fall frost.
    """
    val = species.get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_mmdd(mmdd: str | None) -> date | None:
    """Parse ``"MM-DD"`` (year-agnostic) into a 2024 date."""
    if not mmdd:
        return None
    try:
        text = str(mmdd).strip().replace("/", "-")
        m_str, d_str = text.split("-")[:2]
        return date(2024, int(m_str), int(d_str))
    except (ValueError, TypeError, AttributeError):
        return None


def add_weeks(mmdd: str | None, weeks: int) -> str | None:
    """Add ``weeks`` to an MM-DD date, returning MM-DD (year-agnostic)."""
    base = _parse_mmdd(mmdd)
    if base is None:
        return None
    return (base + timedelta(weeks=weeks)).strftime("%m-%d")


def add_days(mmdd: str | None, days: int) -> str | None:
    """Add ``days`` to an MM-DD date, returning MM-DD (year-agnostic)."""
    base = _parse_mmdd(mmdd)
    if base is None:
        return None
    return (base + timedelta(days=days)).strftime("%m-%d")


def mmdd_sort_key(md: str | None) -> tuple[int, int]:
    """Chronological sort key for an ``"MM-DD"`` string (never raises)."""
    base = _parse_mmdd(md)
    if base is None:
        return (99, 99)
    return (base.month, base.day)


# ── Pantry-Goal back-calculator ──────────────────────────────────────────────

# Preservation conversion ratios: lbs of raw produce per unit of preserved output.
# Fallback values when the species lacks conversion metadata.
PRESERVATION_RATIOS: dict[str, dict[str, float]] = {
    "water_bath_canning": {"quarts": 3.0, "pints": 1.5},
    "pressure_canning": {"quarts": 3.5, "pints": 1.75},
    "freezing": {"quarts": 2.5, "pints": 1.25, "lbs": 1.0},
    "dehydrating": {"quarts": 8.0, "pints": 4.0, "lbs": 0.25},
    "fermenting": {"quarts": 4.0, "pints": 2.0},
    "cold_storage": {"lbs": 1.0},
    "oil_herb_cubes": {"pints": 0.5, "quarts": 1.0},
}


def lbs_per_unit(method: str, unit: str) -> float:
    """Raw pounds of produce needed per ``unit`` of preserved output for ``method``."""
    table = PRESERVATION_RATIOS.get(method, {})
    return table.get(unit, 1.0)


def preservation_raw_lbs(
    method: str,
    unit: str,
    target_qty: float,
    species_conversion: float | None = None,
) -> float:
    """Raw lbs needed to put up ``target_qty`` ``unit``(s) via ``method``."""
    ratio = species_conversion if species_conversion is not None else lbs_per_unit(method, unit)
    return max(0.0, target_qty * ratio)


def _conversion_ratio_from_text(text: str | None, unit: str) -> float | None:
    """Pull a "N lbs ... per quart" ratio out of seed prose when present.

    ``preservation_conversion`` in the seed catalogue is human-readable prose
    (e.g. "≈ 3 lbs paste tomatoes per quart marinara").  Only a clear
    "N lbs ... per <unit>" statement is usable as a numeric ratio; anything
    else falls back to :data:`PRESERVATION_RATIOS` with a warning.
    """
    if not text:
        return None
    unit_singular = unit.rstrip("s").lower()
    pattern = re.compile(
        rf"(\d+(?:\.\d+)?)\s*(?:lb|lbs|pound|pounds)\b[^.;]*?\bper\s+{unit_singular}\b",
        re.IGNORECASE,
    )
    match = pattern.search(str(text))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def preservation_conversion_for(
    method: str,
    unit: str,
    species: dict | None,
) -> tuple[float, str]:
    """Return (raw lbs per preserved unit, source) for a species/method/unit.

    ``source`` is ``"seed"`` when the catalogue's prose gave a usable ratio and
    ``"fallback"`` when the built-in table was used.
    """
    ratio = _conversion_ratio_from_text((species or {}).get("preservation_conversion"), unit)
    if ratio is not None and ratio > 0:
        return ratio, "seed"
    log.warning(
        "[Garden] preservation conversion unavailable for %s/%s; using fallback ratio",
        method,
        unit,
    )
    return lbs_per_unit(method, unit), "fallback"


def plant_count_for_raw_lbs(
    raw_lbs: float,
    species: dict,
    fresh_plus_preserving: bool = False,
) -> int:
    """Number of plants needed to produce ``raw_lbs`` of harvest.

    ``yield_per_plant_lbs`` is the seed catalogue's authoritative figure
    (``yield_typical_lbs`` is the fallback).  ``fresh_plus_preserving`` no
    longer inflates the yield -- ``raw_lbs`` already includes preserving.
    """
    del fresh_plus_preserving  # kept for API compatibility
    yield_lbs = (
        species.get("yield_per_plant_lbs")
        or species.get("yield_typical_lbs")
        or 1.0
    )
    try:
        yield_lbs = float(yield_lbs)
    except (ValueError, TypeError):
        yield_lbs = 1.0
    if yield_lbs <= 0:
        yield_lbs = 1.0
    return max(1, math.ceil(raw_lbs / yield_lbs))


def sqft_for_plants(plant_count: int, species: dict) -> float:
    """Square feet needed for ``plant_count`` plants of ``species``."""
    ppsf = species.get("plants_per_sqft") or 1.0
    if ppsf <= 0:
        ppsf = 1.0
    return plant_count / ppsf


# ── Companion / Antagonist / Rotation ────────────────────────────────────────

def crop_family(species: dict | None) -> str | None:
    """Return the crop family for rotation checks."""
    if not species:
        return None
    return species.get("crop_family") or None


def companion_list(species: dict | None) -> list[str]:
    """Return list of companion species_refs."""
    if not species:
        return []
    raw = species.get("companions") or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


def antagonist_list(species: dict | None) -> list[str]:
    """Return list of antagonist species_refs."""
    if not species:
        return []
    raw = species.get("antagonists") or ""
    return [a.strip() for a in raw.split(",") if a.strip()]


def _norm_family(value: str | None) -> str:
    return (value or "").strip().lower()


def _ref_aliases(species: dict | None) -> set[str]:
    """Lower-case tokens a species can be matched by (key, common name, aliases).

    The seed catalogue lists companions by common name (``"basil"``) while
    plan cells store keys (``"basil_sweet"``), so both must be recognised.
    """
    if not species:
        return set()
    aliases = {str(species.get("key") or "").strip().lower()}
    text = " ".join(
        str(species.get(key) or "")
        for key in ("common_name", "culinary_aliases", "scientific_name")
    )
    for word in text.replace(",", " ").replace("(", " ").replace(")", " ").split():
        token = word.strip(".;:/").lower()
        if token:
            aliases.add(token)
    return aliases


def _matches_tokens(tokens: set[str], species: dict | None) -> bool:
    return bool(tokens & _ref_aliases(species))


def _prior_year_families(
    session: Session,
    plan: GardenPlan | None,
    location_id: str,
) -> dict[tuple[int, int], set[str]]:
    """Families planted in the same bed in the previous three years.

    Keys are ``(col, row)``; values are normalised crop-family names.  Only
    plans with ``0 < plan.year - prior.year <= 3`` count, so same-year cells
    never create a false rotation warning.
    """
    if plan is None or not plan.year:
        return {}
    prior_plans = session.exec(
        select(GardenPlan).where(
            GardenPlan.year < plan.year,
            GardenPlan.year >= plan.year - 3,
        )
    ).all()
    if not prior_plans:
        return {}
    years = {p.id: p.year for p in prior_plans}
    prior_cells = session.exec(
        select(PlanCell).where(
            PlanCell.location_id == location_id,
            PlanCell.plan_id.in_(list(years)),  # type: ignore[attr-defined]
        )
    ).all()
    history: dict[tuple[int, int], set[str]] = {}
    for pc in prior_cells:
        family = pc.crop_family
        if not family and pc.species_ref:
            family = crop_family(resolve_species(session, pc.species_ref))
        family = _norm_family(family)
        if not family:
            continue
        history.setdefault((pc.col, pc.row), set()).add(family)
    return history


def check_plan(
    session: Session,
    plan: GardenPlan,
    plan_cells: list[PlanCell],
) -> list[dict]:
    """Companion / antagonist / shading / rotation checks for a whole plan.

    Rotation warnings compare only against *prior years'* plans in the same
    bed (``0 < plan.year - prior.year <= 3``).  Companion bonuses are returned
    with ``kind="companion"`` and are excluded from the warning count by the
    caller.
    """
    results: list[dict] = []
    species_cache: dict[str, dict | None] = {}

    def species_of(ref: str | None) -> dict | None:
        if not ref:
            return None
        if ref not in species_cache:
            species_cache[ref] = resolve_species(session, ref)
        return species_cache[ref]

    by_bed: dict[str, list[PlanCell]] = {}
    for cell in plan_cells:
        by_bed.setdefault(cell.location_id, []).append(cell)

    history_by_bed: dict[str, dict[tuple[int, int], set[str]]] = {}

    for bed_id, cells in by_bed.items():
        history = _prior_year_families(session, plan, bed_id)
        history_by_bed[bed_id] = history
        for cell in cells:
            species = species_of(cell.species_ref)
            if not species:
                continue
            my_family = _norm_family(crop_family(species))
            comps = {c.strip().lower() for c in companion_list(species)}
            antags = {a.strip().lower() for a in antagonist_list(species)}
            tall = bool(species.get("tall_trellis"))

            # Multi-year rotation at this exact square-foot cell.
            if my_family:
                prior_here = history.get((cell.col, cell.row), set())
                if my_family in prior_here:
                    results.append({
                        "cell_id": cell.id,
                        "col": cell.col,
                        "row": cell.row,
                        "species_ref": cell.species_ref,
                        "bed_id": bed_id,
                        "kind": "rotation",
                        "message": (
                            f"Rotation warning: {cell.species_ref} ({my_family}) "
                            "was grown in this bed within the last 3 years"
                        ),
                    })

            for other in cells:
                if other.id == cell.id:
                    continue
                dx = abs(other.col - cell.col)
                dy = abs(other.row - cell.row)
                adjacent = dx + dy == 1
                other_ref = (other.species_ref or "").strip().lower()
                other_species = species_of(other.species_ref)
                if not other_species:
                    continue

                # Antagonists: check both directions.
                other_antags = {a.strip().lower() for a in antagonist_list(other_species)}
                if adjacent and (
                    other_ref in antags
                    or _matches_tokens(antags, other_species)
                    or _matches_tokens(other_antags, species)
                ):
                    results.append({
                        "cell_id": cell.id,
                        "col": cell.col,
                        "row": cell.row,
                        "species_ref": cell.species_ref,
                        "bed_id": bed_id,
                        "kind": "antagonist",
                        "message": (
                            f"Antagonist adjacent: {other.species_ref} conflicts "
                            f"with {cell.species_ref}"
                        ),
                    })

                # Companions: informational only.
                other_comps = {c.strip().lower() for c in companion_list(other_species)}
                if adjacent and (
                    other_ref in comps
                    or _matches_tokens(comps, other_species)
                    or _matches_tokens(other_comps, species)
                ):
                    results.append({
                        "cell_id": cell.id,
                        "col": cell.col,
                        "row": cell.row,
                        "species_ref": cell.species_ref,
                        "bed_id": bed_id,
                        "kind": "companion",
                        "message": (
                            f"Companion adjacent: {other.species_ref} pairs well "
                            f"with {cell.species_ref}"
                        ),
                    })

                # Shading: a tall/trelised crop south of a shorter crop.
                if tall and not other_species.get("tall_trellis"):
                    if other.row > cell.row and abs(other.col - cell.col) <= 1:
                        results.append({
                            "cell_id": cell.id,
                            "col": cell.col,
                            "row": cell.row,
                            "species_ref": cell.species_ref,
                            "bed_id": bed_id,
                            "kind": "shading",
                            "message": (
                                f"Shading warning: tall {cell.species_ref} sits south "
                                f"of shorter {other.species_ref}"
                            ),
                        })

    return results


def check_companions(
    session: Session,
    plan_cells: list[PlanCell],
    cell: PlanCell,
    species: dict | None,
) -> list[str]:
    """Backwards-compatible messages for one cell (same-year checks only)."""
    del session, species
    messages: list[str] = []
    for other in plan_cells:
        if other.id == cell.id or other.location_id != cell.location_id:
            continue
        if abs(other.col - cell.col) + abs(other.row - cell.row) != 1:
            continue
        # Messages only; the authoritative check is :func:`check_plan`.
        if other.species_ref:
            messages.append(f"Adjacent: {other.species_ref} next to {cell.species_ref}")
    return messages


# ── Auto-arrange ─────────────────────────────────────────────────────────────

def auto_arrange_bed(
    session: Session,
    plan_cells: list[PlanCell],
    bed_location_id: str,
    species_refs: list[str],
    tall_trellis_north: bool = True,
    plan: GardenPlan | None = None,
) -> list[dict]:
    """Deterministically place crops in a bed grid.

    Tall/trellised crops go to the north edge (high row index; low row index is
    south), companions are pulled next to each other, antagonists and crop
    families planted in the same bed within the previous three years are
    avoided where possible.  Returns ``{col, row, species_ref, ...}`` dicts.
    """
    del plan_cells  # existing cells inform species_refs; placement is recomputed
    species_map: dict[str, dict] = {}
    for ref in species_refs:
        sp = resolve_species(session, ref)
        if sp:
            species_map[ref] = sp

    def sort_key(ref: str) -> tuple[int, float, str]:
        sp = species_map.get(ref, {})
        is_tall = 0 if sp.get("tall_trellis") else 1  # tall first
        ppsf = float(sp.get("plants_per_sqft") or 1.0)
        return (is_tall, -ppsf, ref)

    ordered = sorted(species_refs, key=sort_key)

    from services.garden.models import Location

    bed = session.get(Location, bed_location_id)
    width = max(1, int(round(float(bed.width_ft or 4)))) if bed else 4
    length = max(1, int(round(float(bed.length_ft or 8)))) if bed else 8

    history = _prior_year_families(session, plan, bed_location_id)
    placements: list[dict] = []
    used: set[tuple[int, int]] = set()
    placed_species: list[tuple[int, int, str, dict]] = []

    def adjacent_refs(col: int, row: int) -> list[tuple[str, dict]]:
        found: list[tuple[str, dict]] = []
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            match = next(
                (
                    (placed_ref, placed_sp)
                    for placed_col, placed_row, placed_ref, placed_sp in placed_species
                    if placed_col == col + dc and placed_row == row + dr
                ),
                None,
            )
            if match is not None:
                found.append(match)
        return found

    for ref in ordered:
        sp = species_map.get(ref, {})
        ppsf = float(sp.get("plants_per_sqft") or 1.0)
        needed = max(1, math.ceil(1.0 / ppsf)) if ppsf < 1 else 1
        quantity = max(1.0, round(ppsf)) if ppsf >= 1 else 1.0
        tall = bool(sp.get("tall_trellis"))
        comps = {c.strip().lower() for c in companion_list(sp)}
        antags = {a.strip().lower() for a in antagonist_list(sp)}
        family = _norm_family(crop_family(sp))

        candidates: list[tuple[tuple, int, int]] = []
        for row in range(length):
            for col in range(width):
                if any((col + dc, row) in used or col + dc >= width for dc in range(needed)):
                    continue
                neighbors = adjacent_refs(col, row)
                comp_adj = sum(
                    1
                    for other_ref, other_sp in neighbors
                    if other_ref.lower() in comps
                    or _matches_tokens(comps, other_sp)
                    or _matches_tokens(
                        {c.strip().lower() for c in companion_list(other_sp)}, sp
                    )
                )
                antag_adj = sum(
                    1
                    for other_ref, other_sp in neighbors
                    if other_ref.lower() in antags
                    or _matches_tokens(antags, other_sp)
                    or _matches_tokens(
                        {a.strip().lower() for a in antagonist_list(other_sp)}, sp
                    )
                )
                rot_hit = 1 if family and family in history.get((col, row), set()) else 0
                north_priority = (length - 1 - row) if tall_trellis_north else row
                if tall:
                    score = (north_priority, rot_hit, antag_adj, -comp_adj, row, col)
                else:
                    score = (rot_hit, antag_adj, -comp_adj, row, col)
                candidates.append((score, col, row))

        if candidates:
            _, col, row = min(candidates, key=lambda c: c[0])
            for dc in range(needed):
                used.add((col + dc, row))
            placements.append({
                "col": col,
                "row": row,
                "species_ref": ref,
                "crop_family": sp.get("crop_family") or None,
                "quantity": quantity,
                "cells": needed,
            })
            placed_species.append((col, row, ref, sp))
        else:
            placements.append({
                "col": -1,
                "row": -1,
                "species_ref": ref,
                "crop_family": sp.get("crop_family") or None,
                "quantity": quantity,
                "cells": needed,
                "overflow": True,
            })

    return placements


# ── Seasonal Timeline ────────────────────────────────────────────────────────

def planting_milestones(
    species: dict,
    spring_frost_mmdd: str | None,
    fall_frost_mmdd: str | None,
) -> list[dict]:
    """Compute dated milestones for a species from frost dates + seed offsets.

    Seed offsets are positive weeks *before* the last spring frost and
    negative weeks *after* it, so they are subtracted from the frost date.
    Returns a chronologically sorted list of ``{stage, week_offset, mmdd}``.
    """
    del fall_frost_mmdd  # harvest dates are derived from outdoor planting + maturity
    milestones: list[dict] = []

    spring = _parse_mmdd(spring_frost_mmdd)
    if spring is None:
        return milestones

    def weeks_from_spring(mmdd: str | None) -> int | None:
        target = _parse_mmdd(mmdd)
        if target is None:
            return None
        return round((target - spring).days / 7)

    def add(stage: str, offset: int | None, mmdd: str | None) -> None:
        if mmdd:
            milestones.append({
                "stage": stage,
                "week_offset": offset,
                "mmdd": mmdd,
            })

    seed_off = frost_offset_weeks(species, "seed_start_weeks_before_last_frost")
    sow_off = frost_offset_weeks(species, "direct_sow_weeks_before_last_frost")
    transplant_off = frost_offset_weeks(species, "transplant_weeks_before_last_frost")

    try:
        dtm = int(species.get("days_to_maturity")) if species.get("days_to_maturity") is not None else None
    except (ValueError, TypeError):
        dtm = None
    try:
        harvest_window = (
            int(species.get("harvest_window_days"))
            if species.get("harvest_window_days") is not None
            else None
        )
    except (ValueError, TypeError):
        harvest_window = None

    outdoor_mmdd: str | None = None

    if seed_off is not None:
        seed_mmdd = add_weeks(spring_frost_mmdd, -seed_off)
        add("Start seeds indoors", seed_off, seed_mmdd)

    if sow_off is not None:
        sow_mmdd = add_weeks(spring_frost_mmdd, -sow_off)
        add("Direct sow", sow_off, sow_mmdd)
        outdoor_mmdd = sow_mmdd

    if transplant_off is not None:
        transplant_mmdd = add_weeks(spring_frost_mmdd, -transplant_off)
        add("Transplant", transplant_off, transplant_mmdd)
        # Harden off one week before transplant.
        add("Harden off", transplant_off + 1, add_weeks(transplant_mmdd, -1))
        outdoor_mmdd = transplant_mmdd

    if outdoor_mmdd and dtm is not None:
        harvest_mmdd = add_days(outdoor_mmdd, dtm)
        add("Expected harvest", weeks_from_spring(harvest_mmdd), harvest_mmdd)
        if harvest_window is not None and harvest_window > 0:
            window_end = add_days(harvest_mmdd, harvest_window)
            add("Harvest window ends", weeks_from_spring(window_end), window_end)

    milestones.sort(key=lambda m: mmdd_sort_key(m.get("mmdd")))
    return milestones


# ── Sizing calculator ────────────────────────────────────────────────────────

def simple_sizing(
    session: Session,
    family_size: int,
    mode: str,
    species_refs: list[str],
) -> list[dict]:
    """Simple mode: recommended plant counts & sq ft per crop for family_size."""
    results: list[dict] = []
    preserving = mode == "fresh_plus_preserving"

    for ref in species_refs:
        sp = resolve_species(session, ref)
        if not sp:
            continue
        # Base plants per person: 1-2 for fresh, 2-4 for preserving
        plants_per_person = 2.0 if preserving else 1.0
        plant_count = max(1, math.ceil(family_size * plants_per_person))
        sqft = sqft_for_plants(plant_count, sp)
        results.append({
            "species_ref": ref,
            "common_name": sp.get("common_name", ref),
            "category": sp.get("category", ""),
            "plant_count": plant_count,
            "sqft": round(sqft, 1),
            "plants_per_sqft": sp.get("plants_per_sqft", 1),
            "yield_per_plant_lbs": (
                sp.get("yield_per_plant_lbs") or sp.get("yield_typical_lbs") or 1.0
            ),
            "mode": mode,
        })
    return results


def pantry_goal_backcalc(
    session: Session,
    goal: PantryGoalIn,
    species_refs: list[str] | None = None,
) -> dict:
    """Back-calculate raw lbs, plant counts, and sq ft from a pantry goal."""
    species_refs = species_refs or []
    crop_refs: list[str] = []
    try:
        crop_refs = json.loads(goal.crop_refs_json or "[]")
    except (json.JSONDecodeError, TypeError):
        crop_refs = []
    if not crop_refs:
        crop_refs = [ref for ref in species_refs if ref]

    species_by_ref: dict[str, dict] = {}
    for ref in crop_refs:
        sp = resolve_species(session, ref) if ref else None
        if sp:
            species_by_ref[ref] = sp

    warnings: list[str] = []
    primary_species = species_by_ref.get(crop_refs[0]) if crop_refs else None
    if primary_species is not None:
        base_ratio, base_source = preservation_conversion_for(
            goal.method, goal.unit, primary_species
        )
    else:
        base_ratio, base_source = lbs_per_unit(goal.method, goal.unit), "fallback"
    if base_source == "fallback":
        warnings.append(
            f"No seed preservation conversion for {goal.item}; "
            "used built-in preservation ratios."
        )

    raw_lbs_needed = max(0.0, goal.target_qty * base_ratio)

    # Distribute across crops: primary crop gets 70%, others split remainder
    results: list[dict] = []
    total_sqft = 0.0
    total_plants = 0

    if crop_refs:
        primary = crop_refs[0]
        others = crop_refs[1:]
        primary_lbs = raw_lbs_needed * 0.7
        other_lbs = raw_lbs_needed * 0.3 / max(len(others), 1)

        for ref in crop_refs:
            sp = species_by_ref.get(ref)
            if not sp:
                continue
            lbs = primary_lbs if ref == primary else other_lbs
            count = plant_count_for_raw_lbs(lbs, sp)
            sqft = sqft_for_plants(count, sp)
            total_plants += count
            total_sqft += sqft
            _, conversion_source = preservation_conversion_for(goal.method, goal.unit, sp)
            if conversion_source == "fallback" and not warnings:
                warnings.append(
                    f"No seed preservation conversion for {ref}; "
                    "used built-in preservation ratios."
                )
            results.append({
                "species_ref": ref,
                "common_name": sp.get("common_name", ref),
                "raw_lbs_needed": round(lbs, 1),
                "plant_count": count,
                "sqft": round(sqft, 1),
                "conversion_source": conversion_source,
            })

    return {
        "item": goal.item,
        "method": goal.method,
        "unit": goal.unit,
        "target_qty": goal.target_qty,
        "total_raw_lbs_needed": round(raw_lbs_needed, 1),
        "total_plant_count": total_plants,
        "total_sqft": round(total_sqft, 1),
        "crops": results,
        "warnings": warnings,
    }
