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
    #: Legacy column, no longer read: it used to hold the roster count copied
    #: from Identity (or a fallback of 7) on the first read, which went stale.
    #: ``migrate_family_size`` moves an explicit value into
    #: ``family_size_override`` once and sets this to 0. SQLite cannot drop or
    #: relax a NOT NULL column in place, so it stays, always 0.
    family_size: int = Field(default=0)
    #: A family size a person set on purpose. ``None`` = ask Identity's roster.
    family_size_override: int | None = Field(default=None)
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
    skylight_chore_id: str | None = Field(default=None, max_length=128)
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
    #: The household member the Skylight chore is assigned to (Identity username).
    skylight_assignee: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=2000)
    calibration_factor: float = Field(default=1.0)  # self-calibrating snooze adjustment
    last_harvested_at: str | None = Field(default=None)  # when last harvested (herbs/veg/fruit)
    regrowth_cooldown_days: int | None = Field(default=None)  # days to wait before next harvest (herbs)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class HarvestLog(SQLModel, table=True):
    """A harvest record: how much was picked, by whom, and what was done with it."""

    __tablename__ = "harvest_logs"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    plant_id: str = Field(max_length=128)
    harvested_at: str = Field(default_factory=utcnow)
    harvested_by: str | None = Field(default=None, max_length=128)
    quantity: float = Field(default=0.0)
    unit: str = Field(max_length=32)  # oz | lb | bunch | leaf | pint | quart | piece
    disposition: str = Field(max_length=32)  # fresh | canned | frozen | dried | fermented | shared
    notes: str | None = Field(default=None, max_length=1000)
    created_at: str = Field(default_factory=utcnow)


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


class CustomSpecies(SQLModel, table=True):
    """A species created manually or via live botanical lookup + Jarvis AI draft.

    Shares the same schema as the read-only botanical seed (plants-seed.db), so
    it can be resolved right alongside it. Tagged with ``source``
    (``manual | wiki_ai_draft``) and review metadata.
    """

    __tablename__ = "custom_species"

    key: str = Field(primary_key=True, max_length=120)  # e.g. "thai_holy_basil"
    #: Set on every save; without it editing an existing species was an UPDATE
    #: against a column the table did not have.
    updated_at: str = Field(default_factory=utcnow)
    category: str = Field(max_length=32)  # houseplant | herb | vegetable | fruit | flower
    common_name: str = Field(max_length=120)
    scientific_name: str | None = Field(default=None, max_length=120)
    family: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    # --- watering ---
    watering_interval_days: float | None = Field(default=None)
    winter_multiplier: float = Field(default=1.0)
    watering_method: str | None = Field(default=None)  # top_drain | bottom_water | soak_and_dry | evenly_moist | bark_soak | air_bath
    dryness_cue: str | None = Field(default=None)  # surface_damp | top_1in_dry | top_2in_dry | mostly_dry | bone_dry
    water_quality: str | None = Field(default=None)  # tap_ok | filtered_or_rain_preferred
    # --- environment ---
    light_level: str | None = Field(default=None)  # low | medium | bright_indirect | bright_direct | full_sun
    temp_min_f: float | None = Field(default=None)
    temp_max_f: float | None = Field(default=None)
    cold_draft_sensitive: bool = Field(default=False)
    humidity_band: str | None = Field(default=None)  # low_tolerant | average | high_humidity
    misting_rule: str | None = Field(default=None)  # beneficial | optional | avoid_misting
    soil_mix: str | None = Field(default=None)  # standard_potting | chunky_aroid | cactus_succulent | orchid_bark | african_violet | moisture_retentive
    # --- lifecycle ---
    fertilizing: str | None = Field(default=None, max_length=200)
    repot_interval_months: float | None = Field(default=None)
    grooming_propagation: str | None = Field(default=None, max_length=500)
    common_pests: str | None = Field(default=None, max_length=500)
    symptoms: str | None = Field(default=None, max_length=500)
    # --- outdoor / agronomy ---
    frost_tolerance: str | None = Field(default=None)  # tender | half_hardy | hardy
    usda_zone_min: int | None = Field(default=None)
    usda_zone_max: int | None = Field(default=None)
    days_to_maturity: int | None = Field(default=None)
    tall_trellis: int = Field(default=0)
    # --- safety & preservation ---
    toxicity_level: str | None = Field(default=None)  # non_toxic | mild_irritant | toxic_pets | toxic_all
    toxicity_notes: str | None = Field(default=None, max_length=500)
    preservation_methods: str | None = Field(default=None, max_length=200)
    usda_acidity_class: str | None = Field(default=None)  # high_acid | low_acid
    # --- provenance ---
    source: str = Field(default="manual")  # manual | wiki_ai_draft
    reviewed_by: str | None = Field(default=None, max_length=128)
    reviewed_at: str | None = Field(default=None, max_length=32)
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
    #: Fields the plant's own page edits (PATCH used to ignore them silently).
    notes: str | None = None
    status: str | None = None  # active | archived
    expected_harvest_start: str | None = None
    expected_harvest_end: str | None = None
    last_repotted_date: str | None = None
    planting_method: str | None = None
    regrowth_cooldown_days: int | None = None


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
    """Create/update request for garden profile.

    ``family_size`` is an explicit override; sending ``null`` clears it so the
    planner follows Identity's household roster again.
    """

    family_size: int | None = Field(default=None, ge=1)
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
    skylight_assignee: str | None
    #: The earliest due date among this plant's active care rules, so a card can
    #: say when care is next rather than a fixed placeholder.
    next_due_at: str | None = None
    notes: str | None
    calibration_factor: float
    last_harvested_at: str | None
    regrowth_cooldown_days: int | None
    created_at: str
    updated_at: str
    species: dict | None = None


class LocationOut(SQLModel):
    id: str
    name: str
    kind: str
    #: ``homehub`` for an indoor room (Home Hub's area registry is the only
    #: list of rooms); ``garden`` for the beds and stations Garden owns.
    source: str = "garden"
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
    skylight_chore_id: str | None = None
    created_at: str
    updated_at: str


class GardenProfileOut(SQLModel):
    user_id: str
    #: The size the planner uses: the override, else Identity's roster, else
    #: ``None`` when Identity could not be asked (``family_size_source`` says which).
    family_size: int | None = None
    family_size_override: int | None = None
    #: ``override`` | ``identity`` | ``unavailable``
    family_size_source: str = "identity"
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


class CustomSpeciesIn(SQLModel):
    """Create/update request for a custom species. Validates enums + safety."""

    key: str | None = None
    category: str | None = None
    common_name: str | None = None
    scientific_name: str | None = None
    family: str | None = None
    description: str | None = None
    watering_interval_days: float | None = None
    winter_multiplier: float | None = None
    watering_method: str | None = None
    dryness_cue: str | None = None
    water_quality: str | None = None
    light_level: str | None = None
    temp_min_f: float | None = None
    temp_max_f: float | None = None
    cold_draft_sensitive: bool | None = None
    humidity_band: str | None = None
    misting_rule: str | None = None
    soil_mix: str | None = None
    fertilizing: str | None = None
    repot_interval_months: float | None = None
    grooming_propagation: str | None = None
    common_pests: str | None = None
    symptoms: str | None = None
    frost_tolerance: str | None = None
    usda_zone_min: int | None = None
    usda_zone_max: int | None = None
    days_to_maturity: int | None = None
    tall_trellis: int | None = None
    toxicity_level: str | None = None
    toxicity_notes: str | None = None
    preservation_methods: str | None = None
    usda_acidity_class: str | None = None
    source: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None


class HarvestLogIn(SQLModel):
    """Create/update request for a harvest log entry."""

    plant_id: str | None = None  # Comes from URL path in POST /plants/{plant_id}/harvest
    harvested_at: str | None = None
    harvested_by: str | None = None
    quantity: float = Field(default=1.0, ge=0)
    unit: str = Field(default="count", max_length=32)  # oz | lb | bunch | pint | quart | piece | count
    disposition: str = Field(default="fresh", max_length=32)  # fresh | canned | frozen | dried | fermented | shared
    notes: str | None = Field(default=None, max_length=1000)


class HarvestLogOut(SQLModel):
    id: str
    plant_id: str
    harvested_at: str
    harvested_by: str | None
    quantity: float
    unit: str
    disposition: str
    notes: str | None
    created_at: str


# ========================= Phase 5: Garden Planner Models =========================

class PantryGoal(SQLModel, table=True):
    """Yearly preservation target that back-calculates required plant counts and sq ft."""

    __tablename__ = "pantry_goals"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    item: str = Field(max_length=120)  # e.g., "Marinara sauce", "Dill pickles"
    crop_refs_json: str = Field(max_length=500)  # JSON list of species_refs
    target_qty: float = Field(default=0.0, ge=0)
    unit: str = Field(max_length=32)  # quarts | pints | lbs | pieces
    method: str = Field(max_length=32)  # water_bath_canning | pressure_canning | freezing | dehydrating | fermenting | cold_storage | oil_herb_cubes
    season_year: int = Field(default=0)  # calendar year
    notes: str | None = Field(default=None, max_length=1000)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class PantryGoalIn(SQLModel):
    item: str
    crop_refs_json: str | None = None
    target_qty: float = Field(default=0.0, ge=0)
    unit: str = Field(max_length=32)
    method: str = Field(max_length=32)
    season_year: int | None = None
    notes: str | None = Field(default=None, max_length=1000)


class PantryGoalPatch(SQLModel):
    """A partial pantry-goal edit (the same reason as ``GardenPlanPatch``)."""

    item: str | None = None
    crop_refs_json: str | None = None
    target_qty: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=32)
    method: str | None = Field(default=None, max_length=32)
    season_year: int | None = None
    notes: str | None = Field(default=None, max_length=1000)


class PantryGoalOut(SQLModel):
    id: str
    item: str
    crop_refs_json: str | None
    target_qty: float
    unit: str
    method: str
    season_year: int
    notes: str | None
    created_at: str
    updated_at: str


class PantryBatch(SQLModel, table=True):
    """One preserved batch put up in the pantry (Phase 6).

    A batch is what is actually on the shelf: *"12 quarts marinara, water-bath
    canned, basement shelf, use by Oct 2027"*. ``goal_id`` links it to the
    ``pantry_goals`` target it counts towards (optional: not everything put up
    belongs to a goal).
    """

    __tablename__ = "pantry_batches"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    goal_id: str | None = Field(default=None, max_length=128)  # pantry_goals.id
    label: str = Field(max_length=120)  # e.g., "Marinara sauce", "Dill pickles"
    method: str = Field(max_length=32)  # see preservation_combos.json "methods"
    batch_date: str = Field(default="", max_length=10)  # ISO YYYY-MM-DD, defaults to today
    qty_initial: float = Field(default=0.0, ge=0)
    qty_remaining: float = Field(default=0.0, ge=0)
    unit: str = Field(max_length=32)  # quarts | pints | half_pints | lbs | bags | jars | bunches
    storage_location: str | None = Field(default=None, max_length=120)
    use_by_date: str | None = Field(default=None, max_length=10)  # ISO YYYY-MM-DD
    logged_by: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=1000)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class PantryBatchIn(SQLModel):
    label: str = Field(max_length=120)
    goal_id: str | None = Field(default=None, max_length=128)
    method: str = Field(max_length=32)
    batch_date: str | None = Field(default=None, max_length=10)
    qty_initial: float = Field(default=0.0, ge=0)
    qty_remaining: float | None = Field(default=None, ge=0)
    unit: str = Field(max_length=32)
    storage_location: str | None = Field(default=None, max_length=120)
    use_by_date: str | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=1000)


class PantryBatchOut(SQLModel):
    id: str
    goal_id: str | None
    label: str
    method: str
    batch_date: str
    qty_initial: float
    qty_remaining: float
    unit: str
    storage_location: str | None
    use_by_date: str | None
    logged_by: str | None
    notes: str | None
    created_at: str
    updated_at: str


class PantryBatchUseIn(SQLModel):
    """Use some of a batch. Everyone may do this, children included."""

    qty_used: float = Field(default=1.0, ge=0)


class PantryBatchPatch(SQLModel):
    """Partial edit of a batch: only the fields the caller sends change."""

    goal_id: str | None = Field(default=None, max_length=128)
    label: str | None = Field(default=None, max_length=120)
    method: str | None = Field(default=None, max_length=32)
    batch_date: str | None = Field(default=None, max_length=10)
    qty_initial: float | None = Field(default=None, ge=0)
    qty_remaining: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=32)
    storage_location: str | None = Field(default=None, max_length=120)
    use_by_date: str | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=1000)


class PlantPhoto(SQLModel, table=True):
    """One photo on a plant's timeline (Phase 7).

    Full-resolution images live in Nextcloud under ``Garden/Photos/<plant_id>/``
    (Execution owns that account); this row keeps the metadata, the link, and a
    small local thumbnail the UI can load instantly. ``kind`` says what the photo
    is: a ``progress`` shot for the growth journal, an ``id`` from snap-to-add, or
    a ``diagnosis`` from the Plant Doctor, whose structured result is kept in
    ``ai_summary_json``.
    """

    __tablename__ = "plant_photos"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    plant_id: str = Field(max_length=128, index=True)
    nextcloud_path: str | None = Field(default=None, max_length=400)
    thumb_path: str | None = Field(default=None, max_length=200)  # file name under /data/thumbs/
    taken_at: str = Field(default_factory=utcnow)
    taken_by: str | None = Field(default=None, max_length=128)
    kind: str = Field(default="progress", max_length=32)  # progress | id | diagnosis
    ai_summary_json: str | None = Field(default=None, max_length=8000)
    notes: str | None = Field(default=None, max_length=1000)
    created_at: str = Field(default_factory=utcnow)


class PlantPhotoIn(SQLModel):
    kind: str = Field(default="progress", max_length=32)
    notes: str | None = Field(default=None, max_length=1000)
    taken_at: str | None = Field(default=None, max_length=40)
    nextcloud_path: str | None = Field(default=None, max_length=400)
    #: A small JPEG/PNG made by the caller (Gateway, through Execution's Pillow).
    thumb_base64: str | None = None
    ai_summary_json: str | None = Field(default=None, max_length=8000)


class PlantPhotoOut(SQLModel):
    id: str
    plant_id: str
    nextcloud_path: str | None
    thumb_path: str | None
    taken_at: str
    taken_by: str | None
    kind: str
    ai_summary_json: str | None
    notes: str | None
    created_at: str


class GardenPlan(SQLModel, table=True):
    """Seasonal bed plan (e.g., "Spring/Summer 2027")."""

    __tablename__ = "garden_plans"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    name: str = Field(max_length=120)  # e.g., "Spring/Summer 2027"
    season: str = Field(max_length=32)  # spring_summer | fall | winter
    year: int = Field(default=0)
    #: The household size this plan was made for (an explicit value, else the
    #: roster from Identity at the time); a plan keeps the size it was drawn for.
    family_size: int = Field(ge=1)
    mode: str = Field(max_length=32)  # fresh_only | fresh_plus_preserving | pantry_goals
    status: str = Field(max_length=32, default="draft")  # draft | active | archived
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class GardenPlanIn(SQLModel):
    name: str
    season: str
    year: int | None = None
    family_size: int | None = Field(default=None, ge=1)
    mode: str | None = None
    status: str | None = None


class GardenPlanPatch(SQLModel):
    """A partial plan edit: "Activate" sends only ``{status: "active"}``."""

    name: str | None = None
    season: str | None = None
    year: int | None = None
    family_size: int | None = Field(default=None, ge=1)
    mode: str | None = None
    status: str | None = None


class GardenPlanOut(SQLModel):
    id: str
    name: str
    season: str
    year: int
    family_size: int
    mode: str
    status: str
    created_at: str
    updated_at: str


class PlanCell(SQLModel, table=True):
    """One square-foot cell assignment in a seasonal bed plan."""

    __tablename__ = "plan_cells"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    plan_id: str = Field(max_length=128)
    location_id: str = Field(max_length=128)  # bed
    col: int = Field(default=0, ge=0)
    row: int = Field(default=0, ge=0)
    species_ref: str | None = Field(default=None, max_length=120)
    plant_id: str | None = Field(default=None, max_length=128)  # linked once planted
    crop_family: str | None = Field(default=None, max_length=80)  # for rotation checks
    quantity: float = Field(default=1.0, ge=0)
    notes: str | None = Field(default=None, max_length=500)
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class PlanCellIn(SQLModel):
    plan_id: str | None = None
    location_id: str | None = None
    col: int = Field(default=0, ge=0)
    row: int = Field(default=0, ge=0)
    species_ref: str | None = None
    plant_id: str | None = None
    crop_family: str | None = None
    quantity: float = Field(default=1.0, ge=0)
    notes: str | None = Field(default=None, max_length=500)


class PlanCellOut(SQLModel):
    id: str
    plan_id: str
    location_id: str
    col: int
    row: int
    species_ref: str | None
    plant_id: str | None
    crop_family: str | None
    quantity: float
    notes: str | None
    created_at: str
    updated_at: str


# --------------------------------------------------------------------------- Phase 8: Home Hub hardware

#: ``valve_mode`` values. ``notify_only`` is the only default there will ever be:
#: auto-watering is opt-in, per bed, and still capped below.
VALVE_MODES = ("notify_only", "confirm", "auto")

#: The caps. A request outside them is refused, and a run is clamped again.
MAX_RUN_MINUTES_LIMIT = 30
MAX_DAILY_RUNS_LIMIT = 4


class HomeHubBinding(SQLModel, table=True):
    """One plant's or bed's garden hardware, held as Home Hub ids only.

    Home Hub owns entity ids, friendly names, areas and live states. Garden keeps
    the ids it is bound to plus its own thresholds and opt-in automation flags,
    so a rename in Home Hub never has to be copied here and a card never reads a
    stale name.
    """

    __tablename__ = "garden_homehub_bindings"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    #: ``plant`` or ``location`` -- one target per binding.
    target_kind: str = Field(max_length=16)
    target_id: str = Field(max_length=128, index=True)

    moisture_entity_id: str | None = Field(default=None, max_length=128)
    temperature_entity_id: str | None = Field(default=None, max_length=128)
    humidity_entity_id: str | None = Field(default=None, max_length=128)
    lux_entity_id: str | None = Field(default=None, max_length=128)

    grow_light_entity_id: str | None = Field(default=None, max_length=128)
    grow_light_on_time: str | None = Field(default=None, max_length=5)  # HH:MM
    grow_light_off_time: str | None = Field(default=None, max_length=5)  # HH:MM
    grow_light_enabled: bool = Field(default=False)

    valve_entity_id: str | None = Field(default=None, max_length=128)
    #: ``notify_only`` (default) | ``confirm`` | ``auto`` (explicit opt-in).
    valve_mode: str = Field(default="notify_only", max_length=16)

    #: Opt-in: let a live moisture reading override the schedule cadence.
    moisture_override_enabled: bool = Field(default=False)
    #: Below this, the plant is treated as needing water now.
    moisture_threshold_pct: float = Field(default=30.0)

    max_run_minutes: int = Field(default=5)
    daily_max_runs: int = Field(default=2)
    rain_interlock: bool = Field(default=True)

    #: Set while a valve is open, so the watchdog can close it even after a
    #: restart. Cleared by every successful close.
    active_run_started_at: str | None = Field(default=None)
    active_run_ends_at: str | None = Field(default=None)

    nfc_tag_id: str | None = Field(default=None, max_length=128, index=True)

    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)


class HomeHubBindingIn(SQLModel):
    """Create/update a binding for a plant or a bed (partial: send what changed)."""

    moisture_entity_id: str | None = None
    temperature_entity_id: str | None = None
    humidity_entity_id: str | None = None
    lux_entity_id: str | None = None
    grow_light_entity_id: str | None = None
    grow_light_on_time: str | None = None
    grow_light_off_time: str | None = None
    grow_light_enabled: bool | None = None
    valve_entity_id: str | None = None
    valve_mode: str | None = None
    moisture_override_enabled: bool | None = None
    moisture_threshold_pct: float | None = None
    max_run_minutes: int | None = None
    daily_max_runs: int | None = None
    rain_interlock: bool | None = None
    nfc_tag_id: str | None = None


class ValveRunIn(SQLModel):
    """A manual valve run: minutes are clamped to the binding's own cap."""

    minutes: int | None = None
    #: A parent may water through the rain interlock on purpose.
    override_rain: bool = False


class HomeHubBindingOut(SQLModel):
    id: str
    target_kind: str
    target_id: str
    target_name: str | None = None
    moisture_entity_id: str | None
    temperature_entity_id: str | None
    humidity_entity_id: str | None
    lux_entity_id: str | None
    grow_light_entity_id: str | None
    grow_light_on_time: str | None
    grow_light_off_time: str | None
    grow_light_enabled: bool
    valve_entity_id: str | None
    valve_mode: str
    moisture_override_enabled: bool
    moisture_threshold_pct: float
    max_run_minutes: int
    daily_max_runs: int
    rain_interlock: bool
    active_run_started_at: str | None
    active_run_ends_at: str | None
    nfc_tag_id: str | None
    #: Live readings from Home Hub (all ``None`` when unbound or unavailable).
    readings: dict[str, Any] = Field(default_factory=dict)
    #: How many guarded runs this target has had today (UTC).
    runs_today: int = 0
    created_at: str
    updated_at: str


class ValveRun(SQLModel, table=True):
    """One guarded irrigation run -- the record the daily cap is counted from.

    A bed's run waters every plant in it, so counting care rows would count a
    single run once per plant (and never at all for an empty bed). One row per
    run is what ``daily_max_runs`` means, whatever is planted where.
    """

    __tablename__ = "garden_valve_runs"

    id: str = Field(primary_key=True, default_factory=lambda: str(uuid4()))
    binding_id: str = Field(max_length=64, index=True)
    valve_entity_id: str | None = Field(default=None, max_length=128)
    performed_by: str | None = Field(default=None, max_length=128)
    minutes: int = Field(default=5)
    #: ``manual`` (someone tapped Run) or ``auto`` (the opt-in dry-soil trigger).
    trigger: str = Field(default="manual", max_length=16)
    started_at: str = Field(default_factory=utcnow)
    ends_at: str | None = Field(default=None)
    closed_at: str | None = Field(default=None)
