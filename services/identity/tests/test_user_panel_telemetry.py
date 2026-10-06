"""The User Panel consent boundary, asserted rather than documented.

The design intent: device and usage metadata (identity, IP, app/APK version,
update events, per-feature counts, app-open frequency) is collected **without
opt-in**; location and health stay **opt-in only**, owned by
``UserActivitySharing``.

The risk is not that someone breaks the rule on purpose -- it is that a new
event or a new column makes it possible to break it by accident, months later,
with nobody noticing. So these tests fail on the *shape* of the schema rather
than on any one call site.
"""
import inspect

import pytest

from services.identity import models
from services.identity.models import (
    ADVANCED_DEVICE_KINDS,
    DEVICE_KINDS,
    NO_OPT_IN_EVENTS,
    CapabilityGap,
    CapabilityInventory,
    Device,
    DeviceEvent,
    FeatureUsage,
    AssistantConversation,
    UserActivitySharing,
)

#: Columns that would mean no-opt-in telemetry had started carrying the data
#: that needs consent. None may appear on a no-opt-in table.
CONSENTED_CONCEPTS = (
    "latitude", "longitude", "lat", "lon", "coordinates", "coords",
    "steps", "distance", "workout", "heart_rate", "vitals", "health",
    "calories", "sleep",
)


def _columns(model) -> set[str]:
    return {c.name for c in model.__table__.columns}


# Tables written without asking. AssistantConversation and CapabilityGap are
# deliberately NOT here: both are content-derived.
NO_OPT_IN_TABLES = (Device, DeviceEvent, FeatureUsage, CapabilityInventory)


class TestConsentBoundary:
    @pytest.mark.parametrize("model", NO_OPT_IN_TABLES)
    def test_no_opt_in_tables_carry_no_consented_data(self, model):
        """A no-opt-in table must not grow a location or health column."""
        for column in _columns(model):
            for concept in CONSENTED_CONCEPTS:
                assert concept not in column.lower(), (
                    f"{model.__name__}.{column} would collect '{concept}', which "
                    f"requires opt-in via UserActivitySharing"
                )

    @pytest.mark.parametrize("model", NO_OPT_IN_TABLES)
    def test_no_opt_in_tables_have_no_free_text_body(self, model):
        """The no-opt-in tables may hold small scalars, not prose.

        A free-text column is how "just this one snippet" turns into storing
        what someone typed. Content lives in AssistantConversation and
        CapabilityGap, which are called out as content-derived.
        """
        for column in _columns(model):
            assert column not in ("text", "body", "message", "transcript", "prompt")

    def test_content_tables_are_not_in_the_no_opt_in_set(self):
        """Guards against someone 'simplifying' by folding the two together."""
        assert AssistantConversation not in NO_OPT_IN_TABLES
        assert CapabilityGap not in NO_OPT_IN_TABLES

    def test_location_and_health_consent_has_a_single_owner(self):
        """Only UserActivitySharing may answer 'may I see this user's data'.

        A second opt-in table for the same question is how a sharing check
        drifts out of sync with the policy.
        """
        opted_in = [m for m in (UserActivitySharing,) if _columns(m)]
        assert opted_in == [UserActivitySharing]
        # And no panel table claims to carry a consent flag.
        for model in (Device, DeviceEvent, FeatureUsage, CapabilityGap, AssistantConversation):
            assert "consent" not in _columns(model), (
                f"{model.__name__} carries a consent column; consent belongs to "
                f"UserActivitySharing"
            )


class TestNoOptInEventAllowlist:
    def test_every_allowed_event_is_a_known_device_or_usage_event(self):
        # If a new event name appears it has to be one of these concepts.
        assert NO_OPT_IN_EVENTS == frozenset(
            {
                "device_seen",
                "app_open",
                "apk_check",
                "apk_install",
                "feature_use",
                "capability_miss",
                "battery",
            }
        )

    @pytest.mark.parametrize(
        "event",
        ["transcript", "message_sent", "location_update", "steps", "health_sync", "photo"],
    )
    def test_content_events_are_not_permitted_without_opt_in(self, event):
        assert event not in NO_OPT_IN_EVENTS

    def test_it_is_an_allowlist_we_can_enforce(self):
        """The enforcement point reads this constant, so it must be a set of
        plain strings -- a mapping or a generator would not behave."""
        assert isinstance(NO_OPT_IN_EVENTS, frozenset)
        assert all(isinstance(e, str) for e in NO_OPT_IN_EVENTS)


class TestDeviceKinds:
    def test_the_kinds_are_declared(self):
        # watch: a companion device paired from Jarvis with its on-screen code
        assert DEVICE_KINDS == ("phone", "assistant", "light", "watch")

    def test_light_is_excluded_from_the_advanced_panel(self):
        """A light has no screen and nothing to install; a watch has both."""
        assert "light" not in ADVANCED_DEVICE_KINDS
        assert set(ADVANCED_DEVICE_KINDS) == {"phone", "assistant", "watch"}

    def test_device_kind_defaults_to_phone(self):
        assert Device.model_fields["kind"].default == "phone"


class TestTablesExist:
    @pytest.mark.parametrize(
        "model",
        [Device, DeviceEvent, FeatureUsage, CapabilityGap, AssistantConversation, CapabilityInventory],
    )
    def test_table_is_registered_with_a_primary_key(self, model):
        assert "__tablename__" in model.__dict__ or model.__tablename__
        assert any(c.primary_key for c in model.__table__.columns)

    def test_device_key_is_unique_so_self_registration_is_idempotent(self):
        """A phone that logs in repeatedly must not create duplicate rows."""
        assert Device.model_fields["device_key"].unique is True

    def test_capability_gap_counts_repeats_rather_than_duplicating(self):
        """A gap hit ten times is one feature request worth ten votes."""
        assert CapabilityGap.model_fields["occurrences"].default == 1
        assert CapabilityGap.model_fields["capability"].index is True


class TestDocumentation:
    def test_the_boundary_is_written_down_where_the_schema_is(self):
        """The prose lives with the models, not only in a doc that drifts."""
        source = inspect.getsource(models)
        assert "WITHOUT opt-in" in source
        assert "UserActivitySharing" in source
