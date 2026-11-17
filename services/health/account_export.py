"""The person's health data for the account export (docs/ACCOUNT_EXPORT.md).

Mounted under ``/internal/account``, behind the service's internal-secret
middleware like every other route. Every table is keyed by the username.
Sharing grants, cheers and challenge memberships name other people or this
server's ids, so they are exported for reading but never restored.
"""
from __future__ import annotations

from services.common.account_data import Table
from services.health.models import (
    ChallengeMember,
    Checkin,
    Cheer,
    CycleLog,
    CyclePeriod,
    CycleSettings,
    FastingSession,
    FoodEntry,
    HealthGoal,
    HealthPreferences,
    HealthProfile,
    HealthShare,
    MetricSample,
    RecipeMade,
    RecipeRating,
    SleepSession,
)


def specs() -> list[Table]:
    return [
        # Kid mode is a parent's switch: a file never turns it off.
        Table("profile", HealthProfile, owner="user_id", updated="updated_at", read_only=frozenset({"kid_mode"})),
        Table("preferences", HealthPreferences, owner="user_id", updated="updated_at"),
        Table("metrics", MetricSample, owner="user_id", natural_key=("source", "external_id")),
        Table("sleep", SleepSession, owner="user_id", natural_key=("source", "external_id")),
        Table("checkins", Checkin, owner="user_id", natural_key=("day",), updated="updated_at"),
        Table("food", FoodEntry, owner="user_id"),
        Table("fasting", FastingSession, owner="user_id"),
        Table("goals", HealthGoal, owner="user_id", natural_key=("week_start", "kind")),
        Table("cycle_settings", CycleSettings, owner="user_id", updated="updated_at", read_only=frozenset({"guardian_username"})),
        Table("cycle_periods", CyclePeriod, owner="user_id", natural_key=("start_day",)),
        Table("cycle_logs", CycleLog, owner="user_id", natural_key=("day",), updated="updated_at"),
        Table("recipe_ratings", RecipeRating, owner="user_id", natural_key=("recipe",), updated="updated_at"),
        Table("recipes_made", RecipeMade, owner="user_id", natural_key=("recipe", "day")),
        Table("shares_given", HealthShare, owner="owner", export_only=True),
        Table("cheers_sent", Cheer, owner="from_user", export_only=True),
        Table("cheers_received", Cheer, owner="to_user", export_only=True),
        Table("challenges_joined", ChallengeMember, owner="user_id", export_only=True),
    ]
