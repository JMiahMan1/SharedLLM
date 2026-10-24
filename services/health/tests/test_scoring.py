"""Pure scoring tests: the arithmetic that interprets a day."""
from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest

from services.health import scoring


class TestUnits:
    def test_pounds_convert_to_kilograms(self):
        value, unit = scoring.convert_unit("weight", 154.0, "lb")
        assert unit == "kg"
        assert value == pytest.approx(69.853, abs=0.001)

    def test_hours_convert_to_minutes(self):
        assert scoring.convert_unit("sleep_duration", 7.5, "hours") == (450.0, "min")

    def test_ounces_convert_to_millilitres(self):
        value, unit = scoring.convert_unit("water", 8, "oz")
        assert unit == "ml"
        assert value == pytest.approx(236.588, abs=0.01)

    def test_unknown_unit_names_the_accepted_set(self):
        with pytest.raises(ValueError, match="use one of"):
            scoring.convert_unit("weight", 10, "stone")

    def test_unknown_metric_is_rejected(self):
        with pytest.raises(ValueError, match="unknown metric"):
            scoring.convert_unit("vibes", 1, "unit")


class TestSleepScore:
    def test_full_night_at_need_scores_high(self):
        score = scoring.sleep_score(480, 480)
        assert score is not None
        assert score.score == 100
        assert score.confidence == "low"  # duration only
        assert "sleep efficiency" in score.missing

    def test_short_night_scores_lower_than_a_full_one(self):
        full = scoring.sleep_score(480, 480)
        short = scoring.sleep_score(300, 480)
        assert full and short
        assert short.score < full.score
        assert short.label in ("short", "low")

    def test_oversleep_is_not_rewarded(self):
        on_time = scoring.sleep_score(480, 480)
        long = scoring.sleep_score(660, 480)
        assert on_time and long
        assert long.score < on_time.score

    def test_efficiency_and_quality_raise_confidence(self):
        score = scoring.sleep_score(450, 480, efficiency=92, quality=4)
        assert score is not None
        assert score.confidence == "high"
        assert score.missing == []

    def test_no_duration_means_no_score(self):
        assert scoring.sleep_score(None, 480) is None

    def test_no_need_means_no_score(self):
        assert scoring.sleep_score(480, None) is None


class TestReadiness:
    def test_no_signals_is_none_not_fifty(self):
        assert scoring.readiness(None) is None

    def test_sleep_alone_still_produces_a_score(self):
        sleep = scoring.sleep_score(480, 480)
        ready = scoring.readiness(sleep)
        assert ready is not None
        assert ready.score == 100
        assert ready.confidence == "low"
        assert "a resting-heart-rate baseline" not in ready.missing

    def test_resting_hr_above_baseline_lowers_the_score(self):
        sleep = scoring.sleep_score(480, 480)
        at_baseline = scoring.readiness(sleep, resting_hr=60, baseline_resting_hr=60)
        elevated = scoring.readiness(sleep, resting_hr=72, baseline_resting_hr=60)
        assert at_baseline and elevated
        assert elevated.score < at_baseline.score
        assert "above your baseline" in elevated.detail

    def test_soreness_inverts(self):
        good = scoring.readiness(None, energy=5, soreness=1)
        sore = scoring.readiness(None, energy=5, soreness=5)
        assert good and sore
        assert sore.score < good.score

    def test_band_language_is_forgiving(self):
        sleep = scoring.sleep_score(180, 480)
        ready = scoring.readiness(sleep, energy=1, soreness=5)
        assert ready is not None
        assert ready.label == "recover"
        assert "Recovery is the training" in ready.detail


class TestCalorieTarget:
    PROFILE: ClassVar[dict] = dict(
        birth_year=1985,
        height_cm=180.0,
        weight_kg=80.0,
        sex="male",
        activity_level="moderate",
        goal="maintain",
        today=date(2026, 10, 22),
    )

    def test_maintain_range_brackets_tdee(self):
        target = scoring.calorie_target(**self.PROFILE)
        assert target.kind == "adult_range"
        assert target.tdee and target.min and target.max
        assert target.min < target.tdee < target.max
        assert target.deficit_allowed is False

    def test_adult_deficit_has_a_floor(self):
        target = scoring.calorie_target(**{**self.PROFILE, "goal": "lose"})
        assert target.deficit_allowed is True
        assert target.min >= 1500

    def test_minor_gets_guidance_and_no_deficit(self):
        target = scoring.calorie_target(**{**self.PROFILE, "birth_year": 2012, "goal": "lose"})
        assert target.kind == "teen_guidance"
        assert target.deficit_allowed is False
        assert target.goal == "balance"
        assert "No deficit" in target.note

    def test_missing_fields_are_named_not_guessed(self):
        target = scoring.calorie_target(
            birth_year=None, height_cm=None, weight_kg=None, sex=None
        )
        assert target.kind == "unavailable"
        assert set(target.missing) == {"birth year", "height", "weight", "sex"}
        assert target.min is None

    def test_unspecified_sex_is_not_treated_as_male(self):
        target = scoring.calorie_target(**{**self.PROFILE, "sex": "unspecified"})
        assert target.kind == "unavailable"
        assert "sex" in target.missing


class TestMacrosAndDensity:
    def test_macro_grams_add_up_to_the_calorie_target(self):
        macros = scoring.macro_targets(2000)
        total = macros.protein_g * 4 + macros.carbs_g * 4 + macros.fat_g * 9
        assert total == pytest.approx(2000, abs=4)

    def test_density_bands_are_guides(self):
        assert scoring.food_density(50)["band"] == "green"
        assert scoring.food_density(200)["band"] == "yellow"
        assert scoring.food_density(400)["band"] == "orange"
        assert scoring.food_density(None) is None


class TestWater:
    def test_none_is_not_zero(self):
        progress = scoring.water_progress(None, 2000)
        assert progress["logged"] is False
        assert progress["ml"] == 0

    def test_percentage_is_capped(self):
        progress = scoring.water_progress(2500, 2000)
        assert progress["percent"] == 100
        assert progress["logged"] is True

    def test_missing_goal_leaves_percentage_unknown(self):
        assert scoring.water_progress(500, None)["percent"] is None
