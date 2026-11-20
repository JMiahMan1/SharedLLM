"""Adaptive watering cadence engine for Jarvis Garden.

Pure logic: given a species' base watering interval and the plant's exact
pot/soil/window/season context, compute the effective interval in days.
Self-calibrates when a user taps "Still damp", nudging the calibration
factor upward so Jarvis learns how fast that specific pot in that room
dries.

All factors are >= 1.0 for season (winter slows drying) and positive
multipliers elsewhere.  Nothing is hardcoded that should come from the
botanical seed: species base interval, winter_multiplier, watering_method,
dryness_cue, misting_rule all come from the seed record.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- multiplier tables -------------------------------------------------------

POT_MATERIAL_FACTOR: dict[str, float] = {
    # Terracotta breathes: soil dries fastest.
    "terracotta": 1.0,
    # Plastic holds moisture longest.
    "plastic": 1.35,
    # Ceramic glazed: between terracotta and plastic.
    "ceramic": 1.2,
    # Fabric pots air-prune and dry fastest of all.
    "fabric": 0.85,
}

POT_SIZE_FACTOR: dict[str, float] = {
    # Small pots dry faster; larger pots hold moisture longer.
    "small": 0.9,   # <= 6 in
    "medium": 1.0,  # 6-10 in
    "large": 1.15,  # 10-14 in
    "xlarge": 1.3,  # > 14 in
}

SOIL_MIX_FACTOR: dict[str, float] = {
    # Fast-draining mixes dry quicker.
    "cactus_succulent": 0.8,
    "orchid_bark": 0.85,
    "chunky_aroid": 0.9,
    "standard_potting": 1.0,
    "moisture_retentive": 1.2,
    "african_violet": 1.1,
}

WINDOW_DIRECTION_FACTOR: dict[str, float] = {
    # South gets most light -> soil dries fastest -> water MORE often (< 1.0 = shorter interval).
    # North gets least light -> soil dries slowest -> water LESS often (> 1.0 = longer interval).
    "s": 0.85,
    "sw": 0.90,
    "se": 0.90,
    "w": 0.95,
    "e": 0.95,
    "nw": 1.05,
    "n": 1.15,
    "ne": 1.0,
}

DISTANCE_FROM_WINDOW_FACTOR: dict[tuple[float, float], float] = {
    # Close to the window (< 2 ft): dries faster -> water MORE often (< 1.0).
    # Far from the window (> 10 ft): dries slower -> water LESS often (> 1.0).
    (0.0, 2.0): 0.90,
    (2.0, 5.0): 1.0,
    (5.0, 10.0): 1.05,
    (10.0, float("inf")): 1.15,
}

MISTING_RULE: dict[str, str] = {
    "beneficial": "Mist leaves 2-3x weekly in dry air.",
    "optional": "Misting is optional; harmless if you enjoy it.",
    "avoid_misting": "Do NOT mist — water sits in the crown/leaf axils and causes rot.",
}

# Sanity bounds for the self-calibrating factor.
CALIBRATION_MIN = 0.5
CALIBRATION_MAX = 2.0
CALIBRATION_NUDGE = 0.1  # how far "Still damp (+2d)" moves the factor


@dataclass(frozen=True)
class CadenceResult:
    """Result of computing an effective watering interval."""

    interval_days: float
    factors: dict[str, float]
    winter_days: float
    next_due_offset_days: float


@dataclass(frozen=True)
class PlacementFit:
    """Light/placement fit warning for a plant's spot."""

    warning: str | None
    severity: str  # info | warn | critical
    detail: str


def pot_size_bucket(diameter_in: float | None) -> str:
    """Map a pot diameter in inches to a size bucket."""
    if diameter_in is None:
        return "medium"
    if diameter_in <= 6:
        return "small"
    if diameter_in <= 10:
        return "medium"
    if diameter_in <= 14:
        return "large"
    return "xlarge"


def _window_factor(direction: str | None, distance_ft: float | None) -> float:
    """Combined window-direction × distance-from-window multiplier."""
    direction_key = (direction or "n").lower()
    direction_mult = WINDOW_DIRECTION_FACTOR.get(direction_key, 1.0)
    distance_mult = 1.0
    if distance_ft is not None:
        for (lo, hi), mult in DISTANCE_FROM_WINDOW_FACTOR.items():
            if lo <= distance_ft < hi:
                distance_mult = mult
                break
    return direction_mult * distance_mult


def compute_interval(
    *,
    base_interval_days: float,
    winter_multiplier: float,
    pot_material: str | None = None,
    pot_size_in: float | None = None,
    soil_mix: str | None = None,
    window_direction: str | None = None,
    distance_from_window: float | None = None,
    season_winter: bool = False,
    calibration_factor: float = 1.0,
) -> CadenceResult:
    """Compute the effective watering interval in days.

    ``base_interval_days`` comes from the botanical seed record
    (``watering_interval_days``).  ``winter_multiplier`` also comes from
    the seed and is >= 1.0 (winter_days = interval * multiplier).

    Every factor multiplies the base interval: a larger number means
    water less often because the pot holds moisture longer or the season
    slows drying.
    """
    if base_interval_days <= 0:
        raise ValueError("base_interval_days must be positive")
    if winter_multiplier < 1.0:
        raise ValueError("winter_multiplier must be >= 1.0")

    pot_mult = POT_MATERIAL_FACTOR.get((pot_material or "ceramic").lower(), 1.0)
    size_mult = POT_SIZE_FACTOR[pot_size_bucket(pot_size_in)]
    soil_mult = SOIL_MIX_FACTOR.get((soil_mix or "standard_potting").lower(), 1.0)
    window_mult = _window_factor(window_direction, distance_from_window)

    cal = max(CALIBRATION_MIN, min(CALIBRATION_MAX, calibration_factor or 1.0))

    active_interval = (
        base_interval_days
        * pot_mult
        * size_mult
        * soil_mult
        * window_mult
        * cal
    )

    season_mult = winter_multiplier if season_winter else 1.0
    winter_days = active_interval * season_mult

    return CadenceResult(
        interval_days=round(active_interval, 2),
        factors={
            "pot_material": pot_mult,
            "pot_size": size_mult,
            "soil_mix": soil_mult,
            "window": window_mult,
            "calibration": cal,
            "season": season_mult,
        },
        winter_days=round(winter_days, 2),
        next_due_offset_days=round(winter_days if season_winter else active_interval, 2),
    )


def nudge_calibration(
    current_factor: float,
    *,
    still_damp: bool,
    days_postponed: float = 2.0,
) -> float:
    """Self-calibrating snooze.

    When the user taps "Still damp (+2d)", the pot is holding moisture
    longer than Jarvis predicted, so nudge ``calibration_factor`` upward
    slightly.  Clamped to sane bounds so one tap can't run away.
    """
    factor = max(CALIBRATION_MIN, min(CALIBRATION_MAX, current_factor or 1.0))
    if still_damp:
        factor = round(min(CALIBRATION_MAX, factor + CALIBRATION_NUDGE), 2)
    return factor


def auto_seed_care_rules(species: dict) -> list[dict]:
    """Auto-seed species-appropriate care rules when adding a plant.

    Returns a list of care-rule dicts ready to insert.  Watering always
    gets a rule.  Fertilize is paused in winter (caller sets
    ``winter_multiplier`` / season).  Misting ONLY when the species'
    ``misting_rule == "beneficial"`` — never when "avoid_missing".
    """
    rules: list[dict] = []

    base_interval = species.get("watering_interval_days")
    if base_interval:
        rules.append(
            {
                "action": "water",
                "interval_days": float(base_interval),
                "winter_multiplier": species.get("winter_multiplier", 1.0),
                "rain_skip_enabled": False,  # indoor houseplants never rain-skip
            }
        )

    fertilizing = species.get("fertilizing") or ""
    if fertilizing.strip():
        rules.append(
            {
                "action": "fertilize",
                "interval_days": 30.0,
                "winter_multiplier": species.get("winter_multiplier", 1.0),
                "rain_skip_enabled": False,
            }
        )

    misting_rule = (species.get("misting_rule") or "optional").lower()
    if misting_rule == "beneficial":
        rules.append(
            {
                "action": "mist",
                "interval_days": 7.0,
                "winter_multiplier": 1.0,
                "rain_skip_enabled": False,
            }
        )
    # "avoid_misting" -> no mist rule at all (guardrail). "optional" -> no rule either.

    rules.append({"action": "rotate", "interval_days": 14.0, "winter_multiplier": 1.0, "rain_skip_enabled": False})
    # Use species repot_interval_months when present; default ~2 years.
    repot_months = species.get("repot_interval_months")
    repot_days = float(repot_months) * 30.0 if repot_months else 730.0
    rules.append({"action": "repot", "interval_days": repot_days, "winter_multiplier": 1.0, "rain_skip_enabled": False})
    rules.append({"action": "check_pests", "interval_days": 30.0, "winter_multiplier": 1.0, "rain_skip_enabled": False})

    return rules


def placement_fit(
    species: dict,
    *,
    window_direction: str | None = None,
    distance_from_window: float | None = None,
    has_drainage: bool = True,
) -> PlacementFit:
    """Compare species light needs against the plant's actual spot.

    Returns a PlacementFit with a warning (or None) and a severity.
    """
    species_light = (species.get("light_level") or "medium").lower()
    detail = f"Species prefers {species_light} light."

    # No drainage is the most serious warning.
    if not has_drainage:
        return PlacementFit(
            warning="No drainage: water sparingly and empty the saucer after watering.",
            severity="critical",
            detail=detail,
        )

    # Window direction vs light level.
    if window_direction:
        direction_key = window_direction.lower()
        southish = direction_key in ("s", "se", "sw")
        northish = direction_key in ("n", "nw", "ne")
        if species_light in ("bright_direct", "full_sun") and not southish:
            return PlacementFit(
                warning=f"{species_light.title()} plant may not get enough light from a {window_direction.upper()} window.",
                severity="warn",
                detail=detail,
            )
        if species_light == "low" and southish and distance_from_window is not None and distance_from_window < 3:
            return PlacementFit(
                warning="Low-light plant in direct southern sun may scorch; move back from the window.",
                severity="warn",
                detail=detail,
            )

    return PlacementFit(warning=None, severity="info", detail=detail)


# --- heatwave ----------------------------------------------------------------

HEATWAVE_HIGH_F = 90.0  # forecast daily high at or above triggers heat boost
HEATWAVE_HIGH_C = 32.2
HEAT_FACTOR = 0.75  # multiply outdoor/container watering interval by this in a heatwave


def heat_factor(temperature_f: float | None, dry: bool = True) -> float:
    """Heatwave boost: shorten outdoor/container watering intervals in a heatwave.

    Pure function, unit tested. Returns ``HEAT_FACTOR`` (0.75) when the forecast
    daily high is >= 90°F (32.2°C) and conditions are dry; otherwise 1.0.

    Callers multiply the effective interval by this factor so plants need water
    more often when it's hot and dry.  Degrades to 1.0 when temperature is
    unknown (weather unavailable) — never raises.
    """
    if temperature_f is None:
        return 1.0
    if dry and temperature_f >= HEATWAVE_HIGH_F:
        return HEAT_FACTOR
    return 1.0
