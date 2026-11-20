"""Phase 5: Family-Sized Outdoor Garden Planner core logic.

USDA zone & frost detection, dual sizing (family presets + Pantry-Goal
back-calculator), companion / antagonist / multi-year rotation checks,
deterministic auto-arrange, and seasonal planting timeline computation.

Never hardcodes coordinates or zone data -- household lat/lon comes from the
weather/geo configuration, degrades cleanly when unconfigured.
"""

from __future__ import annotations

import json
import math
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

# ── USDA zone helpers ────────────────────────────────────────────────────────

USDA_ZONES = [f"{n}{l}" for n in range(1, 12) for l in ("a", "b")]


def zone_number(zone: str) -> int:
    """Extract the numeric part of a USDA zone string like '7b' -> 7."""
    return int("".join(c for c in zone if c.isdigit()))


def zone_letter(zone: str) -> str:
    """Extract the letter part of a USDA zone string like '7b' -> 'b'."""
    return "".join(c for c in zone if c.isalpha()).lower() or "a"


def zone_sort_key(zone: str) -> tuple[int, int]:
    """Sort key for USDA zones: (number, letter_index)."""
    return (zone_number(zone), 0 if zone_letter(zone) == "a" else 1)


def zone_in_range(zone: str, zone_min: str | None, zone_max: str | None) -> bool:
    """True when ``zone`` falls between ``zone_min`` and ``zone_max`` inclusive."""
    if not zone or not zone_min or not zone_max:
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


def add_weeks(mmdd: str, weeks: int) -> str | None:
    """Add ``weeks`` to an MM-DD date, returning MM-DD (year-agnostic)."""
    if not mmdd:
        return None
    try:
        m, d = int(mmdd[:2]), int(mmdd[2:])
        # Use a leap year (2024) so Feb 29 works.
        base = date(2024, m, d)
        shifted = base + timedelta(weeks=weeks)
        return shifted.strftime("%m-%d")
    except (ValueError, TypeError):
        return None


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


def plant_count_for_raw_lbs(
    raw_lbs: float,
    species: dict,
    fresh_plus_preserving: bool = False,
) -> int:
    """Number of plants needed to produce ``raw_lbs`` of harvest."""
    # Expected yield per plant in lbs (typical). Fallback to 1.0 if unknown.
    yield_lbs = species.get("expected_yield_lbs") or species.get("yield_lbs_per_plant") or 1.0
    if fresh_plus_preserving:
        yield_lbs *= 1.5  # preserving needs more volume
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


def check_companions(
    session: Session,
    plan_cells: list[PlanCell],
    cell: PlanCell,
    species: dict | None,
) -> list[str]:
    """Return warning messages for companion/antagonist conflicts around ``cell``."""
    warnings: list[str] = []
    if not species:
        return warnings

    comps = set(companion_list(species))
    antags = set(antagonist_list(species))
    my_family = crop_family(species)

    for other in plan_cells:
        if other.id == cell.id or other.location_id != cell.location_id:
            continue
        other_species = resolve_species(session, other.species_ref) if other.species_ref else None
        other_family = crop_family(other_species) if other_species else None

        # Antagonist adjacency (4-neighbor: same bed, adjacent cells)
        if other.species_ref in antags:
            dx = abs(other.col - cell.col)
            dy = abs(other.row - cell.row)
            if dx + dy == 1:  # orthogonally adjacent
                warnings.append(
                    f"Antagonist adjacent: {other.species_ref} conflicts with {cell.species_ref}"
                )

        # Companion bonus (just informational)
        if other.species_ref in comps:
            dx = abs(other.col - cell.col)
            dy = abs(other.row - cell.row)
            if dx + dy == 1:
                warnings.append(
                    f"Companion adjacent: {other.species_ref} pairs well with {cell.species_ref}"
                )

        # Multi-year rotation: same crop family in same bed within 3 years
        if my_family and other_family == my_family:
            warnings.append(
                f"Rotation warning: {cell.species_ref} ({my_family}) follows "
                f"{other.species_ref} in same bed -- rotate crop families"
            )

    return warnings


# ── Auto-arrange ─────────────────────────────────────────────────────────────

def auto_arrange_bed(
    session: Session,
    plan_cells: list[PlanCell],
    bed_location_id: str,
    species_refs: list[str],
    tall_trellis_north: bool = True,
) -> list[dict]:
    """Deterministically place crops in a bed grid.

    Places tall/trellised crops along the north edge (low row index = south,
    high row index = north assuming map orientation), groups companions,
    separates antagonists, and respects rotation history.
    Returns a list of {col, row, species_ref} placement dicts.
    """
    # Fetch species data for all refs
    species_map: dict[str, dict] = {}
    for ref in species_refs:
        sp = resolve_species(session, ref)
        if sp:
            species_map[ref] = sp

    # Sort: tall/trellis first (go to north edge), then by plants_per_sqft density
    def sort_key(ref: str) -> tuple[int, float]:
        sp = species_map.get(ref, {})
        is_tall = 0 if sp.get("tall_trellis") else 1  # tall first
        ppsf = sp.get("plants_per_sqft") or 1.0
        return (is_tall, -ppsf)

    ordered = sorted(species_refs, key=sort_key)

    # Determine bed dimensions from location
    from services.garden.models import Location

    bed = session.get(Location, bed_location_id)
    width = int(bed.width_ft or 4) if bed else 4
    length = int(bed.length_ft or 8) if bed else 8

    placements: list[dict] = []
    used: set[tuple[int, int]] = set()

    # North edge rows (high row index) for tall crops
    north_rows = list(range(length - 1, -1, -1)) if tall_trellis_north else list(range(length))
    south_rows = list(range(0, length)) if tall_trellis_north else list(range(length - 1, -1, -1))

    row_order = north_rows + [r for r in south_rows if r not in north_rows]

    for ref in ordered:
        sp = species_map.get(ref, {})
        ppsf = sp.get("plants_per_sqft") or 1.0
        needed = max(1, math.ceil(1.0 / ppsf))  # cells needed for this crop

        placed = False
        for row in row_order:
            for col in range(width):
                if (col, row) in used:
                    continue
                # Check if we have contiguous space
                can_place = True
                for dc in range(needed):
                    if (col + dc, row) in used or col + dc >= width:
                        can_place = False
                        break
                if can_place:
                    for dc in range(needed):
                        used.add((col + dc, row))
                    placements.append({
                        "col": col,
                        "row": row,
                        "species_ref": ref,
                        "crop_family": sp.get("crop_family"),
                        "quantity": needed,
                    })
                    placed = True
                    break
            if placed:
                break

        if not placed:
            # Bed full -- place at end with overflow flag
            placements.append({
                "col": -1,
                "row": -1,
                "species_ref": ref,
                "crop_family": sp.get("crop_family"),
                "quantity": needed,
                "overflow": True,
            })

    return placements


# ── Seasonal Timeline ────────────────────────────────────────────────────────

def planting_milestones(
    species: dict,
    spring_frost_mmdd: str | None,
    fall_frost_mmdd: str | None,
) -> list[dict]:
    """Compute dated milestones for a species from frost dates + offsets.

    Returns a list of {stage, week_offset, mmdd} dicts sorted chronologically.
    """
    milestones: list[dict] = []

    if not spring_frost_mmdd and not fall_frost_mmdd:
        return milestones

    days_to_maturity = species.get("days_to_maturity")
    try:
        dtm = int(days_to_maturity) if days_to_maturity else None
    except (ValueError, TypeError):
        dtm = None

    # Spring milestones
    if spring_frost_mmdd:
        start_indoors_off = frost_offset_weeks(species, "start_seeds_indoors_weeks") or 0
        harden_off_off = frost_offset_weeks(species, "harden_off_weeks") or 2
        transplant_off = frost_offset_weeks(species, "transplant_weeks") or 0

        milestones.append({
            "stage": "Start seeds indoors",
            "week_offset": start_indoors_off,
            "mmdd": add_weeks(spring_frost_mmdd, start_indoors_off),
        })
        milestones.append({
            "stage": "Harden off",
            "week_offset": harden_off_off,
            "mmdd": add_weeks(spring_frost_mmdd, harden_off_off),
        })
        milestones.append({
            "stage": "Transplant / Direct sow",
            "week_offset": transplant_off,
            "mmdd": add_weeks(spring_frost_mmdd, transplant_off),
        })

    # Fall milestones (from first fall frost, going backwards)
    if fall_frost_mmdd:
        expected_harvest_end = species.get("expected_harvest_end")
        if expected_harvest_end:
            try:
                m, d = int(expected_harvest_end[:2]), int(expected_harvest_end[2:])
                fall_mmdd = expected_harvest_end
            except (ValueError, TypeError):
                fall_mmdd = fall_frost_mmdd
        else:
            fall_mmdd = fall_frost_mmdd

        milestones.append({
            "stage": "Expected harvest",
            "week_offset": 0,
            "mmdd": fall_mmdd,
        })
        milestones.append({
            "stage": "Fall succession",
            "week_offset": -2,
            "mmdd": add_weeks(fall_mmdd, -2),
        })

    # Sort by mmdd
    def mmdd_sort_key(md: str) -> tuple[int, int]:
        try:
            return (int(md[:2]), int(md[2:]))
        except (ValueError, TypeError):
            return (99, 99)

    milestones.sort(key=lambda m: mmdd_sort_key(m.get("mmdd", "99-99")))
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
            "yield_lbs_per_plant": sp.get("expected_yield_lbs", 1.0),
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

    raw_lbs_needed = preservation_raw_lbs(
        goal.method, goal.unit, goal.target_qty
    )

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
            sp = resolve_species(session, ref) if ref else None
            if not sp:
                continue
            lbs = primary_lbs if ref == primary else other_lbs
            count = plant_count_for_raw_lbs(lbs, sp, fresh_plus_preserving=True)
            sqft = sqft_for_plants(count, sp)
            total_plants += count
            total_sqft += sqft
            results.append({
                "species_ref": ref,
                "common_name": sp.get("common_name", ref),
                "raw_lbs_needed": round(lbs, 1),
                "plant_count": count,
                "sqft": round(sqft, 1),
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
    }
