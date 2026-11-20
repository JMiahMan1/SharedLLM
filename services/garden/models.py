"""SQLModel tables for the Garden household store (`/data/garden.db`).

Phase 1: houseplants MVP. Tables are designed cleanly so later phases
(herbs, outdoor beds, harvest logs, pantry, Home Hub bindings) can add
columns or new tables without migration pain.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlmodel import Field, SQLModel


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class GardenProfile(SQLModel, table=True):
    """Household-level garden settings. One row only (user_id = 'household')."""

    __tablename__ = "garden_profile"

    user_id: str = Field(primary_key=True, default="household")
    family_size: int = Field(default=7)
    hardiness_zone: str | None = Field(default=None)
    zone_source: str = Field(default="manual")
    last_spring_frost_mmdd: str | None = Field(default=None)
    first_fall_frost_mmdd: str | None = Field(default=None)
    units: str = Field(default="imperial")
    preservation_prefs: str | None = Field(default=None)
    daily_digest_time: str | None = Field(default=None)
    yard_canvas_json: str | None = Field(default=None)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class Location(SQLModel, table=True):
    """An indoor room, herb station, or outdoor bed/structure."""

    __tablename__ = "locations"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    name: str = Field(max_length=120)
    kind: str = Field(max_length=32)  # indoor_room | herb_station | raised_bed | in_ground | container | trellis | greenhouse | orchard
    area_id: str | None = Field(default=None, max_length=80)  # Home Hub area, if any
    width_ft: float | None = Field(default=None)
    length_ft: float | None = Field(default=None)
    area_sqft: float | None = Field(default=None)
    sun_exposure: str | None = Field(default=None)  # full_sun | partial_shade | shade
    covered: bool = Field(default=False)
    map_x: float | None = Field(default=None)  # 2D yard-map coordinate
    map_y: float | None = Field(default=None)
    rotation_deg: float | None = Field(default=None)
    light_notes: str | None = Field(default=None)
    draft_notes: str | None = Field(default=None)
    assigned_to: str | None = Field(default=None)
    skylight_sync_enabled: bool = Field(default=False)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class Plant(SQLModel, table=True):
    """An individual houseplant, herb pot, or outdoor crop planting."""

    __tablename__ = "plants"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    category: str = Field(max_length=32)  # houseplant | herb | vegetable | fruit | flower
    species_ref: str = Field(max_length=120)  # key into plants-seed.db (or custom_species)
    location_id: str | None = Field(default=None, max_length=128)
    nickname: str | None = Field(default=None, max_length=120)
    quantity: int = Field(default=1)
    area_sqft_used: float | None = Field(default=None)
    pot_size_in: float | None = Field(default=None)
    pot_material: str | None = Field(default=None)  # terracotta | plastic | ceramic | fabric
    has_drainage: bool = Field(default=True)
    soil_mix: str | None = Field(default=None)  # standard_potting | chunky_aroid | cactus_succulent | orchid_bark | african_violet | moisture_retentive
    window_direction: str | None = Field(default=None)  # n | ne | e | se | s | sw | w | nw
    distance_from_window: float | None = Field(default=None)  # feet
    planted_date: str | None = Field(default=None)
    last_repotted_date: str | None = Field(default=None)
    planting_method: str | None = Field(default=None)
    expected_harvest_start: str | None = Field(default=None)
    expected_harvest_end: str | None = Field(default=None)
    status: str = Field(default="active")  # active | archived | dormant
    assigned_to: str | None = Field(default=None)
    skylight_sync_enabled: bool = Field(default=False)
    skylight_chore_id: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=2000)
    calibration_factor: float = Field(default=1.0)  # self-calibrating snooze adjustment
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class CareRule(SQLModel, table=True):
    """Per-plant recurring care action with an adaptive schedule."""

    __tablename__ = "care_rules"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    plant_id: str = Field(max_length=128)
    action: str = Field(max_length=32)  # water | fertilize | mist | clean_leaves | prune_pinch | rotate | repot | check_pests | weed | mulch | frost_protect
    interval_days: float | None = Field(default=None)
    winter_multiplier: float | None = Field(default=None)
    calibration_factor: float = Field(default=1.0)
    rain_skip_enabled: bool = Field(default=False)
    next_due_at: str | None = Field(default=None)
    snoozed_until: str | None = Field(default=None)
    paused: bool = Field(default=False)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class CareLog(SQLModel, table=True):
    """Timestamped care history for a plant or location."""

    __tablename__ = "care_logs"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    plant_id: str | None = Field(default=None, max_length=128)
    location_id: str | None = Field(default=None, max_length=128)
    action: str = Field(max_length=32)
    performed_by: str | None = Field(default=None, max_length=128)
    source: str = Field(default="manual")  # manual | voice | push_action | nfc_tag | rain_auto_skip | homehub_valve
    notes: str | None = Field(default=None, max_length=1000)
    sensor_snapshot_json: str | None = Field(default=None)
    created_at: str = Field(default_factory=utcnow)


class CareRuleIn(SQLModel):
    """Create/update request for a care rule. All fields optional for PATCH."""

    plant_id: str | None = None
    action: str | None = None
    interval_days: float | None = None
    winter_multiplier: float | None = None
    rain_skip_enabled: bool | None = None
    paused: bool | None = None


class CareLogIn(SQLModel):
    """Log a care action."""

    action: str
    performed_by: str | None = None
    source: str = "manual"
    notes: str | None = None
    sensor_snapshot: dict[str, Any] | None = None


class PlantIn(SQLModel):
    """Create/update request for a plant (fast-add). All fields optional for PATCH."""

    category: str | None = None
    species_ref: str | None = None
    location_id: str | None = None
    nickname: str | None = None
    quantity: int | None = None
    area_sqft_used: float | None = None
    pot_size_in: float | None = None
    pot_material: str | None = None
    has_drainage: bool | None = None
    soil_mix: str | None = None
    window_direction: str | None = None
    distance_from_window: float | None = None
    planted_date: str | None = None
    assigned_to: str | None = None
    skylight_sync_enabled: bool | None = None


class LocationIn(SQLModel):
    """Create/update request for a location. All fields optional for PATCH."""

    name: str | None = None
    kind: str | None = None
    area_id: str | None = None
    width_ft: float | None = None
    length_ft: float | None = None
    area_sqft: float | None = None
    sun_exposure: str | None = None
    covered: bool | None = None
    map_x: float | None = None
    map_y: float | None = None
    rotation_deg: float | None = None
    light_notes: str | None = None
    draft_notes: str | None = None
    assigned_to: str | None = None
    skylight_sync_enabled: bool | None = None
    skylight_chore_id: str | None = None


class GardenProfileIn(SQLModel):
    """Create/update request for garden profile."""

    family_size: int | None = None
    hardiness_zone: str | None = None
    zone_source: str | None = None
    last_spring_frost_mmdd: str | None = None
    first_fall_frost_mmdd: str | None = None
    units: str | None = None
    preservation_prefs: str | None = None
    daily_digest_time: str | None = None
    yard_canvas_json: str | None = None


class PlantOut(SQLModel):
    id: str
    category: str
    species_ref: str
    location_id: str | None
    nickname: str | None
    quantity: int
    area_sqft_used: float | None
    pot_size_in: float | None
    pot_material: str | None
    has_drainage: bool
    soil_mix: str | None
    window_direction: str | None
    distance_from_window: float | None
    planted_date: str | None
    last_repotted_date: str | None
    planting_method: str | None
    expected_harvest_start: str | None
    expected_harvest_end: str | None
    status: str
    assigned_to: str | None
    skylight_sync_enabled: bool
    skylight_chore_id: str | None
    notes: str | None
    calibration_factor: float
    created_at: str
    updated_at: str
    species: dict | None = None


class LocationOut(SQLModel):
    id: str
    name: str
    kind: str
    area_id: str | None
    width_ft: float | None
    length_ft: float | None
    area_sqft: float | None
    sun_exposure: str | None
    covered: bool
    map_x: float | None
    map_y: float | None
    rotation_deg: float | None
    light_notes: str | None
    draft_notes: str | None
    assigned_to: str | None
    skylight_sync_enabled: bool
    created_at: str
    updated_at: str


class GardenProfileOut(SQLModel):
    user_id: str
    family_size: int
    hardiness_zone: str | None
    zone_source: str
    last_spring_frost_mmdd: str | None
    first_fall_frost_mmdd: str | None
    units: str
    preservation_prefs: str | None
    daily_digest_time: str | None
    yard_canvas_json: str | None
    created_at: str
    updated_at: str
