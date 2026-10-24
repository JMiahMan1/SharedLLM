"""Pure cycle arithmetic.

Cycle estimates are a calendar aid, not a hormone measurement and not
contraception. This module only receives dates that were actually logged; it
never assumes a 28-day cycle, invents a period, or turns a prediction into a
diagnosis. A person with one logged period gets a cycle day, not a confident
next-period date.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, timedelta

DISCLAIMER = "Estimates only, based on logged dates."
PHASE_LABELS = {
    "menstrual": "Period",
    "follicular": "Follicular",
    "ovulation": "Estimated ovulation",
    "luteal": "Luteal",
    "late": "Period expected",
    "unknown": "Not enough data",
}

#: Phase-aware ideas for a partner who was trusted with the phase summary.
#: They are deliberately practical and stay on what a partner can actually do;
#: no tip explains someone's feelings as hormones or asks a partner to act as
#: a clinician.
PARTNER_SUPPORT: dict[str, tuple[str, tuple[str, ...]]] = {
    "menstrual": (
        "During the period",
        (
            "Offer practical help without waiting to be asked: a meal, a chore, the school run.",
            "Keep the day low-pressure and let rest win; warmth and a hot drink often help.",
            "Feelings can ride the hormones. Listen first, don't take it personally, and don't rush to fix it.",
        ),
    ),
    "follicular": (
        "After the period",
        (
            "Energy is often rising: a good window for plans, outings or moving together.",
            "Back the goals your partner chooses instead of choosing them; ask how you can help.",
            "Small shared wins land well this week.",
        ),
    ),
    "ovulation": (
        "Around estimated ovulation",
        (
            "Many people feel most social and energetic now; make room for connection.",
            "Some notice twinges or discomfort. A gentler plan and a check-in go a long way.",
            "Keep affection warm and pressure-free.",
        ),
    ),
    "luteal": (
        "Before the next period",
        (
            "Energy and patience often dip. Take the lower-effort option and share the load.",
            "Protect wind-down time; sleep and routine matter more this week.",
            "If PMS shows up, be patient and ask what helps. Advice lands better when it is asked for.",
        ),
    ),
    "late": (
        "The period is later than usual",
        (
            "Don't comment on the calendar; your partner has probably noticed.",
            "Offer quiet support and room for however they feel. Uncertainty is tiring.",
            "If they are worried, or cycles stay irregular, encourage a clinician visit. The app does not diagnose.",
        ),
    ),
    "unknown": (
        "Being supportive",
        (
            "Ask what helps today; it can change through the month.",
            "Help with the practical load, not just advice.",
            "Keep this private to the people your partner chose to share it with.",
        ),
    ),
}


def partner_support(phase: str | None) -> dict:
    """Practical ideas for a trusted partner, keyed by the current phase.

    Unknown or missing phases fall back to general support rather than
    guessing; the returned ``phase`` always names which ideas are in use.
    """
    key = phase if phase in PARTNER_SUPPORT else "unknown"
    title, tips = PARTNER_SUPPORT[key]
    return {
        "phase": key,
        "title": title,
        "tips": list(tips),
    }


@dataclass(frozen=True)
class Period:
    start: date
    end: date | None = None


def _median_int(values: list[int]) -> int | None:
    clean = sorted(v for v in values if v > 0)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return clean[middle]
    return round((clean[middle - 1] + clean[middle]) / 2)


def _cycle_length(
    periods: list[Period], average_cycle_length: int | None
) -> tuple[int | None, str | None, list[int]]:
    starts = sorted({p.start for p in periods})
    gaps = [
        (later - earlier).days
        for earlier, later in itertools.pairwise(starts)
        if 15 <= (later - earlier).days <= 90
    ]
    if average_cycle_length:
        return int(average_cycle_length), "user_average", gaps
    observed = _median_int(gaps)
    return (observed, "observed", gaps) if observed else (None, None, gaps)


def _period_length(periods: list[Period], average_period_length: int | None) -> int | None:
    if average_period_length:
        return int(average_period_length)
    lengths = [
        (p.end - p.start).days + 1
        for p in periods
        if p.end is not None and 1 <= (p.end - p.start).days + 1 <= 20
    ]
    return _median_int(lengths)


def _empty() -> dict:
    return {
        "has_data": False,
        "phase": "unknown",
        "phase_label": PHASE_LABELS["unknown"],
        "cycle_day": None,
        "cycle_length_used": None,
        "cycle_length_source": None,
        "period_length_used": None,
        "last_period_start": None,
        "next_period_start": None,
        "days_until_next_period": None,
        "estimated_ovulation_day": None,
        "irregular": False,
        "variation_days": None,
        "confidence": "low",
        "detail": "Log a period start to see cycle estimates. Nothing is assumed before then.",
        "disclaimer": DISCLAIMER,
    }


def summarize(
    periods: list[Period],
    today: date,
    *,
    average_cycle_length: int | None = None,
    average_period_length: int | None = None,
) -> dict:
    """Estimate the current phase and next period from logged dates.

    The luteal phase is treated as roughly 14 days, so ovulation is estimated
    at ``cycle_length - 14``. That is a calendar estimate; the result says so
    and never labels a day as fertile. A cycle longer than the estimate is
    called "period expected", not a diagnosis.
    """
    usable = sorted((p for p in periods if p.start <= today), key=lambda p: p.start)
    if not usable:
        return _empty()

    latest = usable[-1]
    cycle_day = (today - latest.start).days + 1
    cycle_length, cycle_source, gaps = _cycle_length(usable, average_cycle_length)
    period_length = _period_length(usable, average_period_length)

    # Is today inside a logged period? An open-ended period uses the learned
    # period length when there is one; otherwise only its start day is certain.
    in_period = False
    for period in reversed(usable):
        end = period.end
        if end is None and period_length:
            end = period.start + timedelta(days=period_length - 1)
        if end is None:
            end = period.start
        if period.start <= today <= end:
            in_period = True
            break

    phase = "unknown"
    estimated_ovulation_day = None
    next_period_start = None
    days_until_next_period = None

    if in_period:
        phase = "menstrual"
    elif cycle_length:
        estimated_ovulation_day = max(1, cycle_length - 14)
        if cycle_day > cycle_length + 1:
            phase = "late"
        elif abs(cycle_day - estimated_ovulation_day) <= 1:
            phase = "ovulation"
        elif cycle_day < estimated_ovulation_day:
            phase = "follicular"
        else:
            phase = "luteal"

        next_period_start = latest.start + timedelta(days=cycle_length)
        days_until_next_period = (next_period_start - today).days

    variation_days = max(gaps) - min(gaps) if len(gaps) >= 2 else None
    irregular = variation_days is not None and variation_days > 7

    if cycle_source == "user_average":
        confidence = "medium"
    elif len(gaps) >= 3 and not irregular:
        confidence = "high"
    elif len(gaps) >= 2:
        confidence = "medium"
    else:
        confidence = "low"
    if irregular:
        confidence = "low"

    if phase == "menstrual":
        detail = f"Day {cycle_day} of your logged period."
    elif phase == "late":
        late_by = cycle_day - (cycle_length or cycle_day)
        detail = f"Your period is about {late_by} day{'s' if late_by != 1 else ''} later than the current estimate. Bodies vary; this is not a diagnosis."
    elif cycle_length:
        detail = f"Cycle day {cycle_day} of an estimated {cycle_length}-day cycle."
        if irregular:
            detail += " Your logged cycles vary by more than a week, so treat the date as a wide estimate."
    else:
        detail = f"Cycle day {cycle_day}. Log one more period start to estimate your cycle length."

    return {
        "has_data": True,
        "phase": phase,
        "phase_label": PHASE_LABELS[phase],
        "cycle_day": cycle_day,
        "cycle_length_used": cycle_length,
        "cycle_length_source": cycle_source,
        "period_length_used": period_length,
        "last_period_start": latest.start.isoformat(),
        "next_period_start": next_period_start.isoformat() if next_period_start else None,
        "days_until_next_period": days_until_next_period,
        "estimated_ovulation_day": estimated_ovulation_day,
        "irregular": irregular,
        "variation_days": variation_days,
        "confidence": confidence,
        "detail": detail,
        "disclaimer": DISCLAIMER,
    }
