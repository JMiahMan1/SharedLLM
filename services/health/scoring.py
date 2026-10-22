"""Pure health arithmetic.

Everything here is a function of values the caller supplies, with no database,
clock or network access. That is deliberate: scores that interpret a person's
day are the part most likely to be wrong in a way nobody notices, so they are
testable in isolation and the API layer only chooses the inputs.

Two rules run through the whole module:

* **Missing data is not zero.** A readiness score built from no signals is
  ``None`` with the reason attached, never a confident 50.
* **Kids are not small adults.** Any calorie target for a minor is a wide
  maintenance *guidance* range; deficits and weight-loss framing are refused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# --- generic helpers -------------------------------------------------------


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def median(values: list[float]) -> float | None:
    """Median of the finite values, or None when there is nothing to average."""
    clean = sorted(v for v in values if v is not None and v == v)
    if not clean:
        return None
    middle = len(clean) // 2
    if len(clean) % 2:
        return clean[middle]
    return (clean[middle - 1] + clean[middle]) / 2


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear-interpolated percentile, or None on an empty sample."""
    clean = sorted(v for v in values if v is not None and v == v)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = clamp(fraction, 0, 1) * (len(clean) - 1)
    low = int(position)
    high = min(low + 1, len(clean) - 1)
    weight = position - low
    return clean[low] * (1 - weight) + clean[high] * weight


# --- canonical units -------------------------------------------------------

#: Canonical unit per metric. Ingestion converts into these before storing, so
#: history never mixes pounds with kilograms or hours with minutes.
METRIC_UNITS: dict[str, str] = {
    "weight": "kg",
    "body_fat": "%",
    "resting_hr": "bpm",
    "hrv": "ms",
    "spo2": "%",
    "water": "ml",
    "active_minutes": "min",
    "sleep_duration": "min",
    "sleep_efficiency": "%",
    "steps": "count",
}

#: Accepted aliases per metric -> (factor into the canonical unit, canonical
#: unit). An unlisted unit is rejected by the API with the accepted set, not
#: guessed at.
UNIT_ALIASES: dict[str, dict[str, tuple[float, str]]] = {
    "weight": {
        "kg": (1.0, "kg"),
        "kilogram": (1.0, "kg"),
        "kilograms": (1.0, "kg"),
        "lb": (0.45359237, "kg"),
        "lbs": (0.45359237, "kg"),
        "pound": (0.45359237, "kg"),
        "pounds": (0.45359237, "kg"),
    },
    "body_fat": {"%": (1.0, "%"), "percent": (1.0, "%"), "pct": (1.0, "%")},
    "resting_hr": {"bpm": (1.0, "bpm"), "beats/min": (1.0, "bpm")},
    "hrv": {"ms": (1.0, "ms"), "milliseconds": (1.0, "ms")},
    "spo2": {"%": (1.0, "%"), "percent": (1.0, "%"), "pct": (1.0, "%")},
    "water": {
        "ml": (1.0, "ml"),
        "milliliter": (1.0, "ml"),
        "milliliters": (1.0, "ml"),
        "l": (1000.0, "ml"),
        "liter": (1000.0, "ml"),
        "liters": (1000.0, "ml"),
        "oz": (29.5735, "ml"),
        "ounce": (29.5735, "ml"),
        "ounces": (29.5735, "ml"),
        "cup": (236.588, "ml"),
        "cups": (236.588, "ml"),
    },
    "active_minutes": {
        "min": (1.0, "min"),
        "minute": (1.0, "min"),
        "minutes": (1.0, "min"),
        "h": (60.0, "min"),
        "hr": (60.0, "min"),
        "hour": (60.0, "min"),
        "hours": (60.0, "min"),
    },
    "sleep_duration": {
        "min": (1.0, "min"),
        "minute": (1.0, "min"),
        "minutes": (1.0, "min"),
        "h": (60.0, "min"),
        "hr": (60.0, "min"),
        "hour": (60.0, "min"),
        "hours": (60.0, "min"),
    },
    "sleep_efficiency": {"%": (1.0, "%"), "percent": (1.0, "%"), "pct": (1.0, "%")},
    "steps": {"count": (1.0, "count"), "steps": (1.0, "count")},
}

#: Sanity bounds. A reading outside them is a bad unit or a bad device, and is
#: rejected rather than charted as a scary spike.
METRIC_BOUNDS: dict[str, tuple[float, float]] = {
    "weight": (1.0, 500.0),
    "body_fat": (1.0, 75.0),
    "resting_hr": (20.0, 250.0),
    "hrv": (0.0, 500.0),
    "spo2": (50.0, 100.0),
    "water": (0.0, 10000.0),
    "active_minutes": (0.0, 1440.0),
    "sleep_duration": (0.0, 1440.0),
    "sleep_efficiency": (0.0, 100.0),
    "steps": (0.0, 200000.0),
}


def convert_unit(metric: str, value: float, unit: str) -> tuple[float, str]:
    """Convert a reading to the metric's canonical unit.

    Raises ``ValueError`` naming the accepted units when the unit is unknown,
    so an integration that sends ``stone`` gets a fixable message instead of a
    number interpreted as kilograms.
    """
    aliases = UNIT_ALIASES.get(metric)
    if aliases is None:
        raise ValueError(f"unknown metric {metric!r}")
    key = (unit or "").strip().lower()
    if key not in aliases:
        accepted = ", ".join(sorted(aliases))
        raise ValueError(f"unit {unit!r} is not valid for {metric}; use one of: {accepted}")
    factor, canonical = aliases[key]
    return float(value) * factor, canonical


def validate_metric_value(metric: str, value: float) -> float:
    """Return the value if it is finite and inside the metric's sane bounds."""
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("value must be a finite number")
    bounds = METRIC_BOUNDS.get(metric)
    if bounds and not (bounds[0] <= value <= bounds[1]):
        raise ValueError(
            f"{metric} must be between {bounds[0]:g} and {bounds[1]:g}"
        )
    return value


# --- sleep -----------------------------------------------------------------

SLEEP_BANDS = (
    (85, "rested", "A full night. Keep the same bedtime tonight."),
    (70, "steady", "Close to your usual night. Nothing to fix."),
    (55, "short", "Shorter than your body asked for. An earlier bedtime helps."),
    (0, "low", "A rough night. Go gently today and protect tonight's sleep."),
)


@dataclass
class Score:
    """A score with the evidence behind it made explicit."""

    score: int | None
    label: str
    detail: str
    #: How much of the intended evidence was present: low | medium | high.
    confidence: str
    #: What was missing, in plain language, when confidence is not high.
    missing: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)


def sleep_score(
    duration_minutes: float | None,
    need_minutes: float | None,
    efficiency: float | None = None,
    quality: int | None = None,
) -> Score | None:
    """A 0-100 sleep score from duration, efficiency and how it felt.

    Duration is the only required signal. Efficiency and quality add detail
    when a device or a person supplies them, and the score is renormalised over
    the evidence that exists rather than filled in with invented values.
    """
    if duration_minutes is None or need_minutes is None or need_minutes <= 0:
        return None

    missing: list[str] = []
    points = 0.0
    possible = 0.0

    ratio = duration_minutes / need_minutes
    # Under-sleep costs more than over-sleep: 8 h against a 7 h need is a
    # better night than 5 h against a 7 h need, and the score should say so.
    if ratio < 1.0:
        duration_points = 50.0 * clamp(ratio / 0.9, 0.0, 1.0)
    else:
        duration_points = 50.0 * clamp(1.0 - (ratio - 1.1) * 0.8, 0.0, 1.0)
    points += duration_points
    possible += 50.0

    if efficiency is not None:
        # 85%+ is a full 30; 60% or below earns nothing.
        points += 30.0 * clamp((efficiency - 60.0) / 25.0, 0.0, 1.0)
        possible += 30.0
    else:
        missing.append("sleep efficiency")

    if quality is not None:
        points += 20.0 * clamp((quality - 1) / 4.0, 0.0, 1.0)
        possible += 20.0
    else:
        missing.append("how rested you felt")

    raw = 100.0 * points / possible if possible else 0.0
    score = int(round(clamp(raw, 0.0, 100.0)))
    band = next((b for b in SLEEP_BANDS if score >= b[0]), SLEEP_BANDS[-1])
    confidence = "high" if len(missing) == 0 else ("medium" if len(missing) == 1 else "low")
    hours = duration_minutes / 60.0
    detail = f"{hours:.1f} h against your {need_minutes / 60.0:.1f} h need."
    return Score(
        score=score,
        label=band[1],
        detail=f"{band[2]} {detail}",
        confidence=confidence,
        missing=missing,
        components={
            "duration": round(duration_points, 1),
            "duration_possible": 50.0,
            **({"efficiency": round(30.0 * clamp((efficiency - 60.0) / 25.0, 0.0, 1.0), 1)} if efficiency is not None else {}),
            **({"quality": round(20.0 * clamp((quality - 1) / 4.0, 0.0, 1.0), 1)} if quality is not None else {}),
        },
    )


# --- readiness -------------------------------------------------------------

READINESS_BANDS = (
    (80, "ready", "Green light. A normal day or a harder session fits."),
    (60, "steady", "An ordinary day. Move, but do not chase a personal best."),
    (40, "easy", "Take the easier option today; a walk beats a hard workout."),
    (0, "recover", "Recovery is the training today. Keep it gentle."),
)


def readiness(
    sleep: Score | None,
    resting_hr: float | None = None,
    baseline_resting_hr: float | None = None,
    energy: int | None = None,
    mood: int | None = None,
    soreness: int | None = None,
    checkin_stress: int | None = None,
) -> Score | None:
    """Interpret the morning signals as one score and one sentence.

    Sleep carries the most weight, then a resting heart rate away from the
    person's own baseline, then the check-in. Every component is optional and
    the result is normalised over what exists. With no sleep and no check-in,
    the answer is ``None`` -- there is nothing to interpret.
    """
    components: dict[str, float] = {}
    weights: dict[str, float] = {}
    missing: list[str] = []

    if sleep is not None and sleep.score is not None:
        components["sleep"] = float(sleep.score)
        weights["sleep"] = 0.5
    else:
        missing.append("last night's sleep")

    if resting_hr is not None and baseline_resting_hr and baseline_resting_hr > 0:
        # At or below baseline is a full 100; each bpm above costs 2 points,
        # so 10 bpm above the person's normal is a clear recovery signal.
        components["resting_hr"] = clamp(
            100.0 - (resting_hr - baseline_resting_hr) * 2.0, 0.0, 100.0
        )
        weights["resting_hr"] = 0.25
    elif resting_hr is not None:
        missing.append("a resting-heart-rate baseline (needs about a week of readings)")

    if energy is not None:
        components["energy"] = clamp((energy - 1) / 4.0 * 100.0, 0.0, 100.0)
        weights["energy"] = 0.15

    if mood is not None:
        components["mood"] = clamp((mood - 1) / 4.0 * 100.0, 0.0, 100.0)
        weights["mood"] = 0.05

    if soreness is not None:
        # Soreness is inverted: 5 (very sore) is a low score.
        components["soreness"] = clamp((5 - soreness) / 4.0 * 100.0, 0.0, 100.0)
        weights["soreness"] = 0.05

    if checkin_stress is not None:
        components["stress"] = clamp((5 - checkin_stress) / 4.0 * 100.0, 0.0, 100.0)
        weights["stress"] = 0.05

    if not weights:
        return None

    total_weight = sum(weights.values())
    raw = sum(components[key] * weights[key] for key in weights) / total_weight
    score = int(round(clamp(raw, 0.0, 100.0)))
    band = next((b for b in READINESS_BANDS if score >= b[0]), READINESS_BANDS[-1])
    confidence = "high" if total_weight >= 0.9 else ("medium" if total_weight >= 0.55 else "low")

    reasons: list[str] = []
    if sleep is not None and sleep.score is not None:
        reasons.append(f"Sleep {sleep.score}/100 ({sleep.label}).")
    if "resting_hr" in components:
        delta = (resting_hr or 0) - (baseline_resting_hr or 0)
        if delta >= 3:
            reasons.append(f"Resting heart rate is {delta:.0f} bpm above your baseline.")
        elif delta <= -3:
            reasons.append(f"Resting heart rate is {abs(delta):.0f} bpm below your baseline.")
        else:
            reasons.append("Resting heart rate is at your baseline.")
    if energy is not None:
        reasons.append(f"You reported energy {energy}/5.")
    if soreness is not None and soreness >= 4:
        reasons.append("You reported significant soreness.")

    return Score(
        score=score,
        label=band[1],
        detail=f"{band[2]} {' '.join(reasons)}".strip(),
        confidence=confidence,
        missing=missing if confidence != "high" else [],
        components={k: round(v, 1) for k, v in components.items()},
    )


# --- calorie and macro targets --------------------------------------------

ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

#: Floors used by mainstream guidance. They are a safety rail, not a target:
#: an adult's deficit range is never allowed below these.
CALORIE_FLOOR = {"female": 1200.0, "male": 1500.0}


@dataclass
class CalorieTarget:
    """An adult range or a minor's guidance range, with its provenance."""

    min: int | None
    max: int | None
    bmr: int | None
    tdee: int | None
    kind: str  # adult_range | teen_guidance | unavailable
    goal: str
    note: str
    missing: list[str] = field(default_factory=list)
    #: False for minors and when the profile is not complete enough.
    deficit_allowed: bool = False


def mifflin_st_jeor(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    """Basal metabolic rate. ``sex`` must be female or male."""
    if sex == "male":
        offset = 5.0
    elif sex == "female":
        offset = -161.0
    else:
        # Explicit midpoint rather than silently pretending one sex.
        offset = -78.0
    return 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age + offset


def calorie_target(
    *,
    birth_year: int | None,
    height_cm: float | None,
    weight_kg: float | None,
    sex: str | None,
    activity_level: str = "moderate",
    goal: str = "maintain",
    kid_mode: bool = False,
    today: date | None = None,
) -> CalorieTarget:
    """Daily calorie range, adult-only for any deficit.

    Returns ``kind="unavailable"`` and names every missing field when the maths
    cannot be done honestly. Never invents a weight, a height or an age.
    """
    today = today or date.today()
    missing: list[str] = []
    if not birth_year:
        missing.append("birth year")
    if not height_cm:
        missing.append("height")
    if not weight_kg:
        missing.append("weight")
    if sex not in ("female", "male"):
        missing.append("sex")

    age = today.year - birth_year if birth_year else None
    if age is not None and not (5 <= age <= 120):
        missing.append("a valid birth year")
        age = None

    if missing or age is None or height_cm is None or weight_kg is None or sex is None:
        return CalorieTarget(
            min=None,
            max=None,
            bmr=None,
            tdee=None,
            kind="unavailable",
            goal=goal,
            note="Add the missing profile fields to see a calorie range.",
            missing=missing,
        )

    bmr = mifflin_st_jeor(sex, weight_kg, height_cm, age)
    factor = ACTIVITY_FACTORS.get(activity_level, ACTIVITY_FACTORS["moderate"])
    tdee = bmr * factor
    is_minor = kid_mode or age < 18

    if is_minor:
        # A wide maintenance band, no deficit, and no weight-loss language.
        low = max(bmr * 1.1, tdee * 0.9)
        high = max(low, tdee * 1.1)
        return CalorieTarget(
            min=int(round(low)),
            max=int(round(high)),
            bmr=int(round(bmr)),
            tdee=int(round(tdee)),
            kind="teen_guidance",
            goal="balance",
            note="A guidance range for growing bodies. No deficit is applied.",
            missing=[],
            deficit_allowed=False,
        )

    if goal == "lose":
        low = max(tdee - 500.0, CALORIE_FLOOR.get(sex, 1200.0))
        high = max(tdee - 250.0, low)
        note = "A gentle deficit: about 0.25-0.5 kg a week, never below the safety floor."
        deficit = True
    elif goal == "gain":
        low = tdee + 200.0
        high = tdee + 400.0
        note = "A small surplus to support training, with room to adjust."
        deficit = False
    else:
        low = tdee - 100.0
        high = tdee + 100.0
        note = "A maintenance range around your estimated daily energy use."
        deficit = False

    return CalorieTarget(
        min=int(round(low)),
        max=int(round(high)),
        bmr=int(round(bmr)),
        tdee=int(round(tdee)),
        kind="adult_range",
        goal=goal,
        note=note,
        missing=[],
        deficit_allowed=deficit,
    )


@dataclass
class MacroTarget:
    protein_g: int
    carbs_g: int
    fat_g: int


def macro_targets(calories: int, split: tuple[float, float, float] = (0.25, 0.45, 0.30)) -> MacroTarget:
    """Grams of protein, carbohydrate and fat for a calorie target.

    Defaults follow the common 25/45/30 balance; the values are guides, not
    prescriptions, and the UI labels them as such.
    """
    protein, carbs, fat = split
    return MacroTarget(
        protein_g=int(round(calories * protein / 4.0)),
        carbs_g=int(round(calories * carbs / 4.0)),
        fat_g=int(round(calories * fat / 9.0)),
    )


# --- food density ----------------------------------------------------------

DENSITY_BANDS = (
    (100.0, "green", "Light: fills you up for the energy."),
    (300.0, "yellow", "Moderate: a normal part of a meal."),
    (float("inf"), "orange", "Dense: satisfying, and easy to over-serve."),
)


def food_density(calories_per_100g: float | None) -> dict[str, str] | None:
    """Noom-style density guide. A guide, never a verdict, and only when the
    per-100g energy is actually known."""
    if calories_per_100g is None or calories_per_100g < 0:
        return None
    band = next((b for b in DENSITY_BANDS if calories_per_100g <= b[0]), DENSITY_BANDS[-1])
    return {"band": band[1], "label": band[2], "calories_per_100g": round(calories_per_100g, 1)}


# --- water -----------------------------------------------------------------


def water_progress(total_ml: float | None, goal_ml: float | None) -> dict:
    """Water total and percentage, distinguishing "none logged" from zero."""
    has_data = total_ml is not None
    total = max(0.0, total_ml or 0.0)
    goal = goal_ml if goal_ml and goal_ml > 0 else None
    percent = min(100, round(total / goal * 100)) if goal else None
    return {
        "ml": round(total),
        "goal_ml": goal,
        "percent": percent,
        "logged": has_data,
    }
