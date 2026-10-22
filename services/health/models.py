"""Database models for the Health service.

The store is deliberately **open at the edges**: a reading always carries the
source that produced it (manual, Health Connect, a watch, a scale, a food
database). Nothing is silently merged into one anonymous number, so a person
can see where a value came from and a later device can be added without a
schema change.

Time is stored as ISO-8601 text, like the rest of the stack. ``day`` fields are
the user's *local* day (``YYYY-MM-DD``), supplied by the client that knows the
timezone -- deriving it on the server would put a bedtime after midnight on the
wrong day for anyone not in the server's timezone.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> str:
    """Current UTC instant as an ISO-8601 string with an explicit offset."""
    return datetime.now(timezone.utc).isoformat()


class HealthProfile(SQLModel, table=True):  # type: ignore
    """One person's health settings.

    ``user_id`` is the Identity username, not a numeric foreign key: the Health
    service keeps its own database and must stay usable when Identity is
    restarting. An unknown user is simply a profile that has not been set up.
    """

    __table_args__ = {"extend_existing": True}

    user_id: str = Field(primary_key=True)
    display_name: str = Field(default="")
    birth_year: int | None = None
    height_cm: float | None = None
    sex: str | None = None  # female | male | other | unspecified
    activity_level: str = Field(default="moderate")
    goal: str = Field(default="maintain")  # lose | maintain | gain | balance
    units: str = Field(default="imperial")  # imperial | metric
    kid_mode: bool = Field(default=False)
    # None means "not chosen yet"; the summary says a guideline default is in
    # use instead of pretending the person set it.
    sleep_need_minutes: int | None = None
    water_goal_ml: int | None = None
    weekly_active_minutes: int | None = None
    # Family sharing is opt-in per scope and defaults to off everywhere.
    share_sleep: bool = Field(default=False)
    share_nutrition: bool = Field(default=False)
    updated_at: str = Field(default_factory=utcnow)


class MetricSample(SQLModel, table=True):  # type: ignore
    """One scalar reading, e.g. a weight or a resting heart rate.

    ``external_id`` is the device's own identifier. When a source can replay a
    sync, the same reading is accepted once: the unique constraint plus an
    explicit lookup make ingestion idempotent. Manual entries have no external
    id and may legitimately repeat (two glasses of water are two samples).
    """

    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_metric_source_external"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    metric: str = Field(index=True)
    value: float
    unit: str
    recorded_at: str = Field(index=True)
    source: str = Field(default="manual", index=True)
    external_id: str | None = Field(default=None, index=True)
    metadata_json: str = Field(default="{}")
    created_at: str = Field(default_factory=utcnow)


class SleepSession(SQLModel, table=True):  # type: ignore
    """A night's sleep, from a device or entered by hand.

    ``night`` is the local date the person woke up, which is how people talk
    about "last night" and how the summary looks it up.
    """

    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_sleep_source_external"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    night: str = Field(index=True)
    bedtime: str | None = None
    wake_time: str | None = None
    duration_minutes: int
    efficiency: float | None = None  # percent, 0-100
    quality: int | None = None  # 1-5, manual feel
    stages_json: str = Field(default="{}")
    source: str = Field(default="manual", index=True)
    external_id: str | None = Field(default=None, index=True)
    note: str = Field(default="")
    created_at: str = Field(default_factory=utcnow)


class Checkin(SQLModel, table=True):  # type: ignore
    """The two-tap morning check-in that stands in for wearable data."""

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_checkin_user_day"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    day: str = Field(index=True)
    energy: int | None = None  # 1-5
    mood: int | None = None  # 1-5
    soreness: int | None = None  # 1-5 (5 = very sore)
    stress: int | None = None  # 1-5
    note: str = Field(default="")
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class FoodEntry(SQLModel, table=True):  # type: ignore
    """One food or drink logged for a meal.

    Nutrition values are stored **per serving**; ``servings`` is the multiplier.
    That keeps a recipe's per-serving macros intact and lets a person log half
    a portion without the UI doing silent arithmetic.
    """

    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    day: str = Field(index=True)
    meal: str = Field(default="snack", index=True)  # breakfast | lunch | dinner | snack
    name: str
    brand: str = Field(default="")
    calories: float = Field(default=0)
    protein_g: float = Field(default=0)
    carbs_g: float = Field(default=0)
    fat_g: float = Field(default=0)
    fiber_g: float | None = None
    sugar_g: float | None = None
    sodium_mg: float | None = None
    servings: float = Field(default=1)
    serving_grams: float | None = None
    barcode: str | None = None
    recipe_path: str | None = None
    source: str = Field(default="manual")
    external_id: str | None = None
    created_at: str = Field(default_factory=utcnow)


class HealthShare(SQLModel, table=True):  # type: ignore
    """A per-scope family sharing grant.

    Kept separate from Identity's activity sharing because health data has its
    own scopes and a stricter default. One row per (owner, viewer, scope);
    ``scope`` is ``sleep`` or ``nutrition``. An empty table means nobody shares
    anything, which is the intended default.
    """

    __table_args__ = (
        UniqueConstraint("owner", "viewer", "scope", name="uq_health_share"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    owner: str = Field(index=True)
    viewer: str = Field(index=True)
    scope: str = Field(index=True)
    created_at: str = Field(default_factory=utcnow)
