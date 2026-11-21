"""Unit tests for the adaptive cadence engine (services/garden/cadence.py).

Pure logic — no database, no network.  Covers pot/soil/window/season
factors, winter_multiplier >= 1.0, calibration factor bounds, self-calibrating
snooze nudges, misting guardrails, and placement-fit warnings.
"""
from services.garden.cadence import (
    CALIBRATION_MAX,
    CALIBRATION_MIN,
    auto_seed_care_rules,
    compute_interval,
    nudge_calibration,
    placement_fit,
)


def test_compute_interval_base_only():
    # Neutral factors: terracotta=1.0, medium pot=1.0, standard soil=1.0,
    # ne window=1.0, 2-5ft distance=1.0 -> interval == base.
    result = compute_interval(
        base_interval_days=7.0,
        winter_multiplier=2.0,
        season_winter=True,
        pot_material="terracotta",
        pot_size_in=8,
        soil_mix="standard_potting",
        window_direction="ne",
        distance_from_window=3.0,
    )
    assert result.interval_days == 7.0
    assert result.winter_days == 14.0
    assert result.next_due_offset_days == 14.0


def test_terracotta_dries_faster_than_plastic():
    terracotta = compute_interval(
        base_interval_days=7.0, winter_multiplier=1.0, pot_material="terracotta"
    )
    plastic = compute_interval(
        base_interval_days=7.0, winter_multiplier=1.0, pot_material="plastic"
    )
    assert terracotta.interval_days < plastic.interval_days


def test_large_pot_holds_moisture_longer():
    small = compute_interval(
        base_interval_days=7.0, winter_multiplier=1.0, pot_size_in=4
    )
    large = compute_interval(
        base_interval_days=7.0, winter_multiplier=1.0, pot_size_in=12
    )
    assert small.interval_days < large.interval_days


def test_moisture_retentive_soil_dries_slower():
    standard = compute_interval(
        base_interval_days=7.0, winter_multiplier=1.0, soil_mix="standard_potting"
    )
    retentive = compute_interval(
        base_interval_days=7.0, winter_multiplier=1.0, soil_mix="moisture_retentive"
    )
    assert retentive.interval_days > standard.interval_days


def test_cactus_soil_dries_faster():
    cactus = compute_interval(
        base_interval_days=7.0, winter_multiplier=1.0, soil_mix="cactus_succulent"
    )
    standard = compute_interval(
        base_interval_days=7.0, winter_multiplier=1.0, soil_mix="standard_potting"
    )
    assert cactus.interval_days < standard.interval_days


def test_south_window_dries_faster_than_north():
    # South gets more sun -> soil dries faster -> water MORE often -> smaller interval.
    # North gets less sun -> soil dries slower -> water LESS often -> larger interval.
    south = compute_interval(
        base_interval_days=7.0,
        winter_multiplier=1.0,
        window_direction="s",
        distance_from_window=1.0,
    )
    north = compute_interval(
        base_interval_days=7.0,
        winter_multiplier=1.0,
        window_direction="n",
        distance_from_window=1.0,
    )
    assert south.interval_days < north.interval_days


def test_winter_multiplier_slows_watering():
    summer = compute_interval(
        base_interval_days=7.0,
        winter_multiplier=1.0,
        season_winter=False,
        pot_material="terracotta",
        pot_size_in=8,
        soil_mix="standard_potting",
        window_direction="ne",
        distance_from_window=3.0,
    )
    winter = compute_interval(
        base_interval_days=7.0,
        winter_multiplier=2.5,
        season_winter=True,
        pot_material="terracotta",
        pot_size_in=8,
        soil_mix="standard_potting",
        window_direction="ne",
        distance_from_window=3.0,
    )
    assert winter.winter_days > summer.interval_days
    assert winter.winter_days == 7.0 * 2.5


def test_winter_multiplier_must_be_ge_1():
    try:
        compute_interval(base_interval_days=7.0, winter_multiplier=0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("winter_multiplier < 1.0 should raise ValueError")


def test_base_interval_must_be_positive():
    try:
        compute_interval(base_interval_days=0, winter_multiplier=1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("base_interval_days <= 0 should raise ValueError")


def test_calibration_clamped_to_bounds():
    # Above max.
    result = nudge_calibration(CALIBRATION_MAX, still_damp=True)
    assert result == CALIBRATION_MAX
    # Below min.
    result = nudge_calibration(CALIBRATION_MIN, still_damp=False)
    assert result == CALIBRATION_MIN


def test_still_damp_nudges_calibration_up():
    before = 1.0
    after = nudge_calibration(before, still_damp=True)
    assert after > before
    assert after <= CALIBRATION_MAX


def test_no_still_damp_no_nudge():
    before = 1.0
    after = nudge_calibration(before, still_damp=False)
    assert after == before


def test_auto_seed_always_includes_water():
    species = {"watering_interval_days": 7, "winter_multiplier": 2.0}
    rules = auto_seed_care_rules(species)
    actions = {r["action"] for r in rules}
    assert "water" in actions


def test_auto_seed_no_mist_when_avoid_misting():
    species = {
        "watering_interval_days": 7,
        "winter_multiplier": 2.0,
        "misting_rule": "avoid_misting",
    }
    rules = auto_seed_care_rules(species)
    actions = {r["action"] for r in rules}
    assert "mist" not in actions


def test_auto_seed_mist_when_beneficial():
    species = {
        "watering_interval_days": 7,
        "winter_multiplier": 2.0,
        "misting_rule": "beneficial",
    }
    rules = auto_seed_care_rules(species)
    actions = {r["action"] for r in rules}
    assert "mist" in actions


def test_auto_seed_includes_rotate_and_repot():
    species = {"watering_interval_days": 7, "winter_multiplier": 2.0}
    rules = auto_seed_care_rules(species)
    actions = {r["action"] for r in rules}
    assert "rotate" in actions
    assert "repot" in actions
    assert "check_pests" in actions


def test_placement_no_drainage_is_critical():
    pf = placement_fit(
        {"light_level": "medium", "watering_interval_days": 7},
        window_direction="e",
        distance_from_window=3.0,
        has_drainage=False,
    )
    assert pf.severity == "critical"
    assert "drainage" in pf.warning.lower()


def test_placement_direct_sun_low_light_warns():
    pf = placement_fit(
        {"light_level": "low", "watering_interval_days": 7},
        window_direction="s",
        distance_from_window=2.0,
        has_drainage=True,
    )
    assert pf.severity == "warn"


def test_placement_bright_direct_north_window_warns():
    pf = placement_fit(
        {"light_level": "bright_direct", "watering_interval_days": 7},
        window_direction="n",
        distance_from_window=1.0,
        has_drainage=True,
    )
    assert pf.severity == "warn"


def test_placement_no_warning_when_fit():
    pf = placement_fit(
        {"light_level": "medium", "watering_interval_days": 7},
        window_direction="e",
        distance_from_window=3.0,
        has_drainage=True,
    )
    assert pf.warning is None
    assert pf.severity == "info"


# --------------------------------------------------------------------------- review fixes

def test_soil_drying_factors_apply_to_watering_only():
    from services.garden.cadence import compute_interval as _compute

    kwargs = dict(
        base_interval_days=30.0, winter_multiplier=1.0, pot_material="plastic", pot_size_in=16,
        soil_mix="moisture_retentive", window_direction="n", distance_from_window=12, calibration_factor=1.5,
    )

    assert _compute(**kwargs, action="water").interval_days > 30.0
    for action in ("fertilize", "rotate", "repot", "check_pests"):
        result = _compute(**kwargs, action=action)
        assert result.interval_days == 30.0
        assert set(result.factors.values()) == {1.0}


def test_no_window_is_neutral_not_north():
    from services.garden.cadence import compute_interval as _compute

    outdoor = _compute(base_interval_days=4.0, winter_multiplier=1.0, pot_size_in=8, window_direction=None)

    assert outdoor.factors["window"] == 1.0


def test_winter_follows_the_hemisphere():
    from datetime import date

    from services.garden.clock import is_winter

    july, january = date(2026, 7, 15), date(2026, 1, 15)
    assert is_winter(39.7, january) is True and is_winter(39.7, july) is False
    assert is_winter(-33.9, july) is True and is_winter(-33.9, january) is False
    assert is_winter(1.3, january) is False  # tropics
    assert is_winter(None, january) is None
