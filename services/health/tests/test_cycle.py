"""Pure cycle arithmetic tests.

These cover the honest edge cases: no data, one logged period, an observed
cycle, an irregular history and a late period. The module must never invent a
28-day cycle or turn an estimate into a diagnosis.
"""
from __future__ import annotations

from datetime import date, timedelta

from services.health import cycle

TODAY = date(2026, 10, 22)


def _period(start: date, days: int | None = None) -> cycle.Period:
    return cycle.Period(start, start + timedelta(days=days - 1) if days else None)


class TestEmpty:
    def test_no_periods_is_not_a_prediction(self):
        result = cycle.summarize([], TODAY)
        assert result["has_data"] is False
        assert result["phase"] == "unknown"
        assert result["cycle_day"] is None
        assert result["next_period_start"] is None
        assert "nothing is assumed" in result["detail"].lower()
        assert "not contraception" in result["disclaimer"].lower()

    def test_a_future_period_is_ignored(self):
        result = cycle.summarize([_period(TODAY + timedelta(days=2))], TODAY)
        assert result["has_data"] is False


class TestOnePeriod:
    def test_open_period_covers_only_its_start_day_without_a_length(self):
        result = cycle.summarize([_period(TODAY)], TODAY)
        assert result["has_data"] is True
        assert result["cycle_day"] == 1
        assert result["phase"] == "menstrual"
        assert result["cycle_length_used"] is None
        assert result["next_period_start"] is None

    def test_one_old_period_gives_a_cycle_day_but_not_a_confident_phase(self):
        result = cycle.summarize([_period(TODAY - timedelta(days=10))], TODAY)
        assert result["has_data"] is True
        assert result["cycle_day"] == 11
        assert result["phase"] == "unknown"
        assert result["next_period_start"] is None
        assert "log one more" in result["detail"].lower()

    def test_a_logged_end_keeps_today_in_the_period(self):
        result = cycle.summarize([_period(TODAY - timedelta(days=2), days=5)], TODAY)
        assert result["phase"] == "menstrual"
        assert result["period_length_used"] == 5


class TestObservedCycle:
    def test_two_periods_learn_the_gap(self):
        result = cycle.summarize(
            [_period(TODAY - timedelta(days=32)), _period(TODAY - timedelta(days=4))],
            TODAY,
        )
        assert result["cycle_length_used"] == 28
        assert result["cycle_length_source"] == "observed"
        assert result["cycle_day"] == 5
        assert result["phase"] == "follicular"
        assert result["next_period_start"] == (TODAY - timedelta(days=4) + timedelta(days=28)).isoformat()
        assert result["days_until_next_period"] == 24

    def test_the_estimated_ovulation_day_is_labelled(self):
        result = cycle.summarize(
            [_period(TODAY - timedelta(days=18)), _period(TODAY - timedelta(days=46))],
            TODAY,
        )
        # cycle day 19 of a 28-day cycle: cycle_length - 14 = day 14, so this
        # is past the estimate and in the luteal phase.
        assert result["cycle_day"] == 19
        assert result["phase"] == "luteal"
        assert result["estimated_ovulation_day"] == 14

    def test_an_explicit_average_overrides_the_observation(self):
        result = cycle.summarize(
            [_period(TODAY - timedelta(days=20))],
            TODAY,
            average_cycle_length=30,
        )
        assert result["cycle_length_used"] == 30
        assert result["cycle_length_source"] == "user_average"
        assert result["cycle_day"] == 21
        assert result["phase"] == "luteal"

    def test_a_late_period_is_described_not_diagnosed(self):
        result = cycle.summarize(
            [_period(TODAY - timedelta(days=60))],
            TODAY,
            average_cycle_length=28,
        )
        assert result["phase"] == "late"
        assert result["days_until_next_period"] < 0
        assert "not a diagnosis" in result["detail"].lower()


class TestIrregular:
    def test_a_wide_spread_lowers_confidence_and_says_so(self):
        result = cycle.summarize(
            [
                _period(TODAY - timedelta(days=63)),
                _period(TODAY - timedelta(days=42)),
                _period(TODAY - timedelta(days=7)),
            ],
            TODAY,
        )
        assert result["irregular"] is True
        assert result["confidence"] == "low"
        assert "vary" in result["detail"].lower()

    def test_steady_cycles_earn_high_confidence(self):
        result = cycle.summarize(
            [
                _period(TODAY - timedelta(days=112)),
                _period(TODAY - timedelta(days=84)),
                _period(TODAY - timedelta(days=56)),
                _period(TODAY - timedelta(days=28)),
            ],
            TODAY,
        )
        assert result["irregular"] is False
        assert result["confidence"] == "high"

    def test_implausible_gaps_are_not_used(self):
        result = cycle.summarize(
            [
                _period(TODAY - timedelta(days=300)),
                _period(TODAY - timedelta(days=40)),
                _period(TODAY - timedelta(days=10)),
            ],
            TODAY,
        )
        # The 260-day gap is discarded; the 30-day gap remains.
        assert result["cycle_length_used"] == 30


class TestPartnerSupport:
    """Support ideas are general, short and never medical advice."""

    def test_every_phase_has_a_title_and_three_tips(self):
        for phase in cycle.PHASE_LABELS:
            support = cycle.partner_support(phase)
            assert support["phase"] == phase
            assert support["title"]
            assert len(support["tips"]) == 3
            assert all(tip.strip() for tip in support["tips"])
            assert "not medical advice" in support["disclaimer"]

    def test_unknown_phase_falls_back_to_the_neutral_advice(self):
        for value in (None, "", "nonsense"):
            support = cycle.partner_support(value)
            assert support["phase"] == "unknown"
            assert support["title"]
            assert len(support["tips"]) == 3
