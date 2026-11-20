"""Jarvis Garden service — Species-Smart Houseplants MVP (Phase 1).

Port 8012, container `sharedllm_garden`.  Stores household garden state in
`/data/garden.db` (SQLite, `GARDEN_DATABASE_URL` / `GARDEN_DB_PATH` override)
and reads the read-only botanical seed at `/data/plants-seed.db`
(`PLANT_SEED_PATH` override; absent seed degrades gracefully).

Auth: `X-Internal-Secret` required on all routes except `GET /health`.
User/role headers forwarded by the Gateway: `X-User-Id`, `X-User-Role`,
`X-Is-Admin`, `X-Family-Manage`.

Phase 1 permissions ("open care, guarded structure"):
  * Everyone (child, teen, adult_child, mother, father, admin): view garden,
    log watering/care/snooze ("Still damp").
  * Adults & admins (adult_child, mother, father, admin): add/edit/archive
    locations, plants, and care rules.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import asyncio
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, SQLModel, create_engine, select

from services.common.secrets import secret_matches
from services.common.sqlite import add_missing_columns, apply_sqlite_pragmas
from services.config import INTERNAL_SECRET
from services.garden import plant_seed
from services.garden.cadence import (
    auto_seed_care_rules,
    compute_interval,
    heat_factor,
    nudge_calibration,
    placement_fit,
)
from services.garden.models import (
    CareLog,
    CareLogIn,
    CareRule,
    CareRuleIn,
    CustomSpecies,
    CustomSpeciesIn,
    GardenPlan,
    GardenPlanIn,
    GardenPlanOut,
    GardenProfile,
    GardenProfileIn,
    GardenProfileOut,
    HarvestLog,
    HarvestLogIn,
    HarvestLogOut,
    Location,
    LocationIn,
    LocationOut,
    PantryGoal,
    PantryGoalIn,
    PantryGoalOut,
    PlanCell,
    PlanCellIn,
    PlanCellOut,
    Plant,
    PlantIn,
    PlantOut,
    utcnow,
)
from services.garden.achievements import evaluate as evaluate_achievements
from services.garden.notices import collect_due_tasks, build_digest, digest_loop, send_digest_to_member
from services.garden.skylight_sync import mirror_care_log
from services.garden.species_lookup import (
    handle_species_custom,
    handle_species_custom_delete,
    handle_species_lookup,
    resolve_species,
)
from services.garden.weather_care import (
    build_weather_summary,
    dispatch_weather_notices,
    handle_weather_webhook,
    weather_poller,
    _is_outdoor_uncovered as _is_outdoor_uncovered_location,
)
from services.common import weather_client as weather
from services.garden.planner import (
    auto_arrange_bed,
    check_plan,
    crop_family,
    default_frost_dates,
    detect_zone_for_location,
    mmdd_sort_key,
    planting_milestones,
    pantry_goal_backcalc,
    season_length_days,
    simple_sizing,
    sqft_for_plants,
)
from services.shared.info_endpoint import info_router

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
log = logging.getLogger("garden")

DEFAULT_DATABASE_URL = "sqlite:////data/garden.db"
DEFAULT_SEED_PATH = "/app/plants-seed.db"

ADULT_ROLES = frozenset({"adult_child", "mother", "father", "admin"})
ALL_ROLES = frozenset({"child", "teen", "adult_child", "mother", "father", "admin"})


# --------------------------------------------------------------------------- database


def _database_url() -> str:
    return os.getenv("GARDEN_DATABASE_URL") or os.getenv("GARDEN_DB_PATH") or DEFAULT_DATABASE_URL


def _engine():
    url = _database_url()
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
        # Ensure the database directory exists so the service starts cleanly
        # on a fresh host without any manual directory creation.
        try:
            from urllib.parse import urlparse
            path = urlparse(url).path
            # sqlite:////data/garden.db -> /data/garden.db
            if path.startswith("/"):
                parent = os.path.dirname(path)
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
        except Exception:
            pass
    engine = create_engine(url, pool_pre_ping=True, **kwargs)
    apply_sqlite_pragmas(engine)
    return engine


_SEED: plant_seed.PlantSeed | None = None


def _seed() -> plant_seed.PlantSeed:
    global _SEED
    if _SEED is None:
        path = os.getenv("PLANT_SEED_PATH") or DEFAULT_SEED_PATH
        _SEED = plant_seed.PlantSeed(path)
    return _SEED


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = _db()
    SQLModel.metadata.create_all(engine)
    added = add_missing_columns(engine, SQLModel.metadata)
    if added:
        log.info("[Garden] added missing column(s): %s", ", ".join(added))
    log.info("[Garden] ready db=%s seed=%s", _database_url(), _seed().path)
    # Start the quiet morning digest loop (disabled under pytest).
    digest_task = asyncio.create_task(digest_loop(_db, _family_list))
    # Start the weather-smart care poller (disabled under pytest).
    weather_task = asyncio.create_task(weather_poller(_db, _family_list, _resolve_seed_get))
    try:
        yield
    finally:
        digest_task.cancel()
        weather_task.cancel()


def _resolve_seed_get(key: str) -> dict | None:
    """Resolve a species key via the read-only botanical seed (for weather_care)."""
    return _seed().get(key)


app = FastAPI(title="Jarvis Garden Service", lifespan=lifespan)
app.state.engine = None


def _db():
    if app.state.engine is None:
        app.state.engine = _engine()
    return app.state.engine


def get_session() -> Iterator[Session]:
    with Session(_db()) as session:
        yield session


# --------------------------------------------------------------------------- auth


def _verify_internal_secret(header_secret: str | None) -> bool:
    return secret_matches(header_secret, INTERNAL_SECRET)


@app.middleware("http")
async def require_internal_secret_middleware(request: Request, call_next):
    """Only the gateway may call this service. /health is exempt."""
    if request.url.path.rstrip("/") in ("/health", ""):
        return await call_next(request)
    secret = request.headers.get("X-Internal-Secret")
    if not _verify_internal_secret(secret):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


app.include_router(info_router)


def _user(request: Request) -> str:
    """The acting username, forwarded by the gateway after authentication."""
    user = (request.headers.get("X-User-Id") or "").strip().lower()
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _role(request: Request) -> str:
    return (request.headers.get("X-User-Role") or "").strip().lower()


def _is_admin(request: Request) -> bool:
    return (request.headers.get("X-Is-Admin") or "").lower() in ("1", "true", "yes")


def _family_manage(request: Request) -> bool:
    return (request.headers.get("X-Family-Manage") or "").lower() in ("1", "true", "yes")


def _require_admin_or_adult(request: Request) -> None:
    """403 unless the caller is an adult or admin (adult_child/mother/father/admin)."""
    role = _role(request)
    if role not in ADULT_ROLES and not _is_admin(request):
        raise HTTPException(
            status_code=403,
            detail="Adults and admins only: add/edit/archive locations, plants, and care rules.",
        )


PARENT_ROLES = frozenset({"mother", "father", "admin"})


def _require_parent_or_admin(request: Request) -> None:
    """403 unless the caller is a parent or admin (mother/father/admin only — never adult_child/teen/child)."""
    role = _role(request)
    if role not in PARENT_ROLES and not _is_admin(request):
        raise HTTPException(
            status_code=403,
            detail="Parents and admins only: assign plants/locations, toggle Skylight sync, manage garden assignments.",
        )


def _is_minor_role(role: str) -> bool:
    return role in ("child", "teen")


async def _family_list() -> list[str]:
    """Every family member's username, fetched from identity via telemetry's
    common notify helper. Degrades gracefully to an empty list."""
    from services.common.notify import family
    try:
        return await family()
    except Exception as e:
        log.warning("[Garden] could not list family members: %s", e)
        return []


# --------------------------------------------------------------------------- helpers


def _plant_out(row: Plant, species: dict | None = None) -> dict:
    data = row.model_dump()
    if species:
        data["species"] = {
            "common_name": species.get("common_name"),
            "scientific_name": species.get("scientific_name"),
            "family": species.get("family"),
            "light_level": species.get("light_level"),
            "watering_method": species.get("watering_method"),
            "dryness_cue": species.get("dryness_cue"),
            "water_quality": species.get("water_quality"),
            "humidity_band": species.get("humidity_band"),
            "misting_rule": species.get("misting_rule"),
            "soil_mix": species.get("soil_mix"),
            "temp_min_f": species.get("temp_min_f"),
            "temp_max_f": species.get("temp_max_f"),
            "cold_draft_sensitive": species.get("cold_draft_sensitive"),
            "toxicity_level": species.get("toxicity_level"),
            "toxicity_notes": species.get("toxicity_notes"),
            "common_pests": species.get("common_pests"),
            "symptoms": species.get("symptoms"),
            "grooming_propagation": species.get("grooming_propagation"),
            "fertilizing": species.get("fertilizing"),
            "repot_interval_months": species.get("repot_interval_months"),
            "description": species.get("description"),
            "preservation_methods": species.get("preservation_methods"),
            "usda_acidity_class": species.get("usda_acidity_class"),
        }
    return data


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


# --------------------------------------------------------------------------- health


@app.get("/health")
def health():
    return {"status": "ok", "service": "garden", "seed_present": _seed().present}


# --------------------------------------------------------------------------- species (read-only from botanical seed)


@app.get("/species/search")
def species_search(
    request: Request,
    q: str = Query("", description="Free-text search over names/descriptions"),
    category: str | None = Query(None, description="houseplant | herb | vegetable | fruit | flower"),
    usda_zone: int | None = Query(None, description="USDA hardiness zone filter"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search the botanical seed and custom_species. Falls back gracefully when the seed is absent."""
    _user(request)
    seed = _seed()
    results: list[dict] = []
    if q:
        results = seed.search(q or "", limit, category=category, usda_zone=usda_zone) if seed.present else []
    # When there's no query but a category/zone filter, list by category instead.
    if not q and (category or usda_zone is not None):
        results = seed.list_by_category(category or "houseplant", limit=limit, usda_zone=usda_zone) if seed.present else []
    # Merge in custom_species matches (Phase 3: unified resolution).
    custom_results: list[dict] = []
    try:
        with Session(_db()) as session:
            custom_rows = session.exec(select(CustomSpecies).limit(limit)).all()
            for row in custom_rows:
                sp = {
                    "key": row.key,
                    "category": row.category,
                    "common_name": row.common_name,
                    "scientific_name": row.scientific_name,
                    "family": row.family,
                    "description": row.description,
                    "watering_method": row.watering_method,
                    "dryness_cue": row.dryness_cue,
                    "water_quality": row.water_quality,
                    "light_level": row.light_level,
                    "toxicity_level": row.toxicity_level,
                    "frost_tolerance": row.frost_tolerance,
                    "source": row.source,
                    "custom": True,
                }
                # Apply the same filters as seed results.
                if category and sp["category"] != category:
                    continue
                if q and q.lower() not in (sp["common_name"] or "").lower() and q.lower() not in (sp["scientific_name"] or "").lower():
                    continue
                custom_results.append(sp)
    except Exception as e:
        log.info("[Garden] custom_species not ready: %s", e)
    results.extend(custom_results)
    return {
        "query": q,
        "category": category,
        "usda_zone": usda_zone,
        "count": len(results),
        "results": results,
        "seed_present": seed.present,
    }


@app.get("/species/{key}")
def species_get(key: str = PathParam(..., description="Botanical seed key or custom species key")):
    """Get one species, resolving across custom_species and the botanical seed."""
    _require_auth = None  # species read is open to anyone who passed the internal secret
    with Session(_db()) as session:
        species = resolve_species(session, key)
    if species is None:
        raise HTTPException(status_code=404, detail=f"Species {key!r} not found")
    return {"key": key, "present": True, "species": species}


@app.get("/species")
def species_list(
    request: Request,
    category: str | None = Query(None),
    usda_zone: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """List species in a category (or all when no category given), including custom_species."""
    _user(request)
    seed = _seed()
    results: list[dict] = []
    if category:
        results = seed.list_by_category(category, limit=limit, usda_zone=usda_zone) if seed.present else []
    else:
        for cat in ("houseplant", "herb", "vegetable", "fruit", "flower"):
            results.extend(seed.list_by_category(cat, limit=limit, usda_zone=usda_zone) if seed.present else [])
    # Merge in custom_species (Phase 3: unified resolution).
    try:
        with Session(_db()) as session:
            custom_rows = session.exec(select(CustomSpecies).limit(limit)).all()
            for row in custom_rows:
                sp = {
                    "key": row.key,
                    "category": row.category,
                    "common_name": row.common_name,
                    "scientific_name": row.scientific_name,
                    "family": row.family,
                    "description": row.description,
                    "watering_method": row.watering_method,
                    "dryness_cue": row.dryness_cue,
                    "water_quality": row.water_quality,
                    "light_level": row.light_level,
                    "toxicity_level": row.toxicity_level,
                    "frost_tolerance": row.frost_tolerance,
                    "source": row.source,
                    "custom": True,
                }
                if category and sp["category"] != category:
                    continue
                results.append(sp)
    except Exception as e:
        log.info("[Garden] custom_species not ready: %s", e)
    return {
        "category": category,
        "usda_zone": usda_zone,
        "count": len(results),
        "results": results,
        "seed_present": seed.present,
    }


# --------------------------------------------------------------------------- species lookup (3-tier unknown/rare species, Phase 3)


@app.post("/species/lookup", status_code=201)
async def species_lookup(
    request: Request,
    session: Session = Depends(get_session),
):
    """Look up an unknown/rare species (3-tier: seed -> Wikipedia + LLM draft -> custom_species).

    Accepts ``{"name": "...", "category": "..."}``.  Returns a draft for user
    review — never auto-saves unreviewed AI drafts.  Adults and admins only.
    """
    _require_admin_or_adult(request)
    body = await request.json()
    from services.garden.species_lookup import handle_species_lookup

    return await handle_species_lookup(session, body, _user(request), _is_admin(request))


@app.post("/species/custom", status_code=201)
async def species_custom(
    request: Request,
    session: Session = Depends(get_session),
):
    """Save a user-reviewed custom species permanently (adults and admins only)."""
    _require_admin_or_adult(request)
    body = await request.json()
    from services.garden.species_lookup import handle_species_custom, CustomSpeciesIn

    validated = CustomSpeciesIn(**body)
    return await handle_species_custom(session, validated, _user(request), _is_admin(request))


@app.delete("/species/custom/{key}")
async def species_custom_delete(
    key: str = PathParam(..., description="Custom species key"),
    request: Request = None,
    session: Session = Depends(get_session),
):
    """Delete a custom species (adults and admins only)."""
    _require_admin_or_adult(request)
    from services.garden.species_lookup import handle_species_custom_delete

    return await handle_species_custom_delete(session, key, _user(request), _is_admin(request))


# --------------------------------------------------------------------------- internal weather webhook (Phase 3)


@app.post("/hooks/weather")
async def hooks_weather(request: Request):
    """Internal webhook for weather:events (X-Internal-Secret authenticated).

    Evaluates rain-skip + frost/heat/wind alerts and sends urgent garden
    notices via Telemetry when household plants are at risk.
    """
    secret = request.headers.get("X-Internal-Secret")
    if not _verify_internal_secret(secret):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await handle_weather_webhook(_db, _family_list, _resolve_seed_get)


# --------------------------------------------------------------------------- profile


@app.get("/profile")
def get_profile(request: Request, session: Session = Depends(get_session)):
    _user(request)
    profile = session.get(GardenProfile, "household")
    if profile is None:
        profile = GardenProfile(user_id="household")
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return GardenProfileOut.model_validate(profile).model_dump()


@app.put("/profile")
def put_profile(
    body: GardenProfileIn, request: Request, session: Session = Depends(get_session)
):
    _require_admin_or_adult(request)
    profile = session.get(GardenProfile, "household")
    if profile is None:
        profile = GardenProfile(user_id="household")
        session.add(profile)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(profile, key, value)
    profile.updated_at = utcnow()
    session.commit()
    session.refresh(profile)
    return GardenProfileOut.model_validate(profile).model_dump()


# --------------------------------------------------------------------------- locations


@app.get("/locations")
def list_locations(request: Request, session: Session = Depends(get_session)):
    _user(request)
    rows = session.exec(select(Location).order_by(Location.name)).all()
    return [LocationOut.model_validate(r).model_dump() for r in rows]


@app.post("/locations", status_code=201)
def create_location(
    body: LocationIn, request: Request, session: Session = Depends(get_session)
):
    _require_admin_or_adult(request)
    row = Location(**body.model_dump(exclude_unset=True))
    session.add(row)
    session.commit()
    session.refresh(row)
    return LocationOut.model_validate(row).model_dump()


@app.patch("/locations/{loc_id}")
def patch_location(
    loc_id: str,
    body: LocationIn,
    request: Request,
    session: Session = Depends(get_session),
):
    row = session.get(Location, loc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Location not found")
    # Assignment / Skylight sync changes are parent+ only.
    if "assigned_to" in body.model_fields_set or "skylight_sync_enabled" in body.model_fields_set:
        _require_parent_or_admin(request)
    else:
        _require_admin_or_adult(request)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return LocationOut.model_validate(row).model_dump()


@app.delete("/locations/{loc_id}", status_code=204)
def delete_location(
    loc_id: str, request: Request, session: Session = Depends(get_session)
):
    _require_admin_or_adult(request)
    row = session.get(Location, loc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Location not found")
    # Refuse to delete a location that still has plants.
    plants = session.exec(select(Plant).where(Plant.location_id == loc_id)).all()
    if plants:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete location: {len(plants)} plant(s) still assigned. Move or archive them first.",
        )
    session.delete(row)
    session.commit()
    return JSONResponse(status_code=204, content=None)


# --------------------------------------------------------------------------- location assignment (parent+ only)


class LocationAssignmentIn(SQLModel):
    assigned_to: str | None = None
    skylight_sync_enabled: bool | None = None


@app.patch("/locations/{loc_id}/assign")
def assign_location(
    loc_id: str,
    body: LocationAssignmentIn,
    request: Request,
    session: Session = Depends(get_session),
):
    """Assign a location to a family member (parent+ only).

    Uses ``model_fields_set`` so ``{"assigned_to": null}`` actually unassigns
    (distinguishes an explicit null from an omitted field).
    """
    _require_parent_or_admin(request)
    row = session.get(Location, loc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Location not found")
    fields_set = body.model_fields_set
    if "assigned_to" in fields_set:
        row.assigned_to = body.assigned_to  # None = unassign
    if "skylight_sync_enabled" in fields_set:
        row.skylight_sync_enabled = body.skylight_sync_enabled
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return LocationOut.model_validate(row).model_dump()


# --------------------------------------------------------------------------- plant assignment (parent+ only)


class PlantAssignmentIn(SQLModel):
    assigned_to: str | None = None
    skylight_sync_enabled: bool | None = None


@app.patch("/plants/{plant_id}/assign")
def assign_plant(
    plant_id: str,
    body: PlantAssignmentIn,
    request: Request,
    session: Session = Depends(get_session),
):
    """Assign a plant to a family member (parent+ only).

    Uses ``model_fields_set`` so ``{"assigned_to": null}`` actually unassigns.
    """
    _require_parent_or_admin(request)
    row = session.get(Plant, plant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    fields_set = body.model_fields_set
    if "assigned_to" in fields_set:
        row.assigned_to = body.assigned_to  # None = unassign
    if "skylight_sync_enabled" in fields_set:
        row.skylight_sync_enabled = body.skylight_sync_enabled
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    species = resolve_species(session, row.species_ref)
    return _plant_out(row, species)


# --------------------------------------------------------------------------- plants


@app.get("/plants")
def list_plants(request: Request, session: Session = Depends(get_session)):
    _user(request)
    rows = session.exec(select(Plant).where(Plant.status == "active").order_by(Plant.updated_at.desc())).all()
    out = []
    for row in rows:
        species = resolve_species(session, row.species_ref)
        out.append(_plant_out(row, species))
    return out


@app.post("/plants", status_code=201)
def create_plant(
    body: PlantIn, request: Request, session: Session = Depends(get_session)
):
    _require_admin_or_adult(request)
    seed = _seed()
    species = seed.get(body.species_ref) if seed.present else None
    if species is None:
        # Graceful degradation when the seed is absent: still allow creating
        # a plant with just a species_ref (care cues will be unavailable).
        species = {}

    # Auto-enable rain_skip_enabled for outdoor uncovered plants so rain-skip
    # applies to them (Phase 3).  Indoor rooms, greenhouses, and covered pots
    # never rain-skip.
    rain_skip_enabled = False
    location = None
    if body.location_id:
        location = session.get(Location, body.location_id)
    if location and _is_outdoor_uncovered_location(location):
        rain_skip_enabled = True

    row = Plant(**body.model_dump(exclude_unset=True))
    session.add(row)
    session.commit()
    session.refresh(row)

    # Auto-seed species-appropriate care rules (no rules if seed absent).
    for rule in auto_seed_care_rules(species):
        # Outdoor uncovered watering rules get rain_skip_enabled = True.
        if location and _is_outdoor_uncovered_location(location) and rule["action"] == "water":
            rule["rain_skip_enabled"] = True
        session.add(
            CareRule(
                plant_id=row.id,
                action=rule["action"],
                interval_days=rule["interval_days"],
                winter_multiplier=rule["winter_multiplier"],
                rain_skip_enabled=rule["rain_skip_enabled"],
                next_due_at=_compute_next_due(rule, species, row),
            )
        )
    session.commit()

    species_full = resolve_species(session, row.species_ref)
    return _plant_out(row, species_full)


def _is_winter() -> bool:
    """Northern hemisphere winter: Nov-Feb (inclusive)."""
    month = datetime.now(UTC).month
    return month in (11, 12, 1, 2)


def _compute_next_due(rule: dict, species: dict, plant: Plant | None = None) -> str | None:
    """First due date for a care rule using compute_interval().

    Uses the plant's pot_material, pot_size_in, soil_mix, window_direction,
    distance_from_window, calibration_factor, and actual winter check
    (Nov-Feb in northern hemisphere, not multiplying by winter_multiplier
    year-round).
    """
    interval = rule.get("interval_days")
    if interval is None:
        return None
    winter_mult = rule.get("winter_multiplier") or 1.0
    season_winter = _is_winter()

    cal = plant.calibration_factor if plant else 1.0
    result = compute_interval(
        base_interval_days=interval,
        winter_multiplier=winter_mult,
        pot_material=plant.pot_material if plant else None,
        pot_size_in=plant.pot_size_in if plant else None,
        soil_mix=plant.soil_mix if plant else None,
        window_direction=plant.window_direction if plant else None,
        distance_from_window=plant.distance_from_window if plant else None,
        season_winter=season_winter,
        calibration_factor=cal,
    )
    due = datetime.now(UTC) + timedelta(days=result.next_due_offset_days)
    return due.isoformat()


@app.patch("/plants/{plant_id}")
def patch_plant(
    plant_id: str,
    body: PlantIn,
    request: Request,
    session: Session = Depends(get_session),
):
    row = session.get(Plant, plant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    # Assignment / Skylight sync changes are parent+ only.
    if "assigned_to" in body.model_fields_set or "skylight_sync_enabled" in body.model_fields_set:
        _require_parent_or_admin(request)
    else:
        _require_admin_or_adult(request)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    species = resolve_species(session, row.species_ref)
    return _plant_out(row, species)


@app.delete("/plants/{plant_id}", status_code=204)
def delete_plant(
    plant_id: str, request: Request, session: Session = Depends(get_session)
):
    _require_admin_or_adult(request)
    row = session.get(Plant, plant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    session.delete(row)
    session.commit()
    return JSONResponse(status_code=204, content=None)


# --------------------------------------------------------------------------- today


@app.get("/today")
async def today(
    request: Request,
    assigned_to: str | None = Query(None, description="Filter to plants assigned to this member"),
    mine: bool = Query(False, description="Filter to plants assigned to the calling user (or unassigned)"),
    session: Session = Depends(get_session),
):
    """Due and overdue care tasks grouped by room/category, with species cues,
    plus a weather summary (rain-skip, frost/heat/wind alerts) when relevant."""
    user = _user(request)
    seed = _seed()
    now = datetime.now(UTC)

    # Build filter: assigned_to explicit member takes priority over mine.
    effective_assigned_to: str | None = None
    if assigned_to is not None:
        effective_assigned_to = assigned_to
    elif mine:
        effective_assigned_to = "__MINE__"  # sentinel: assigned to user OR unassigned

    rules = session.exec(
        select(CareRule).where(CareRule.paused == False)  # noqa: E712
    ).all()

    due_tasks = []
    for rule in rules:
        plant = session.get(Plant, rule.plant_id)
        if plant is None or plant.status != "active":
            continue

        # Effective assignment: plant.assigned_to, else location.assigned_to.
        location = session.get(Location, plant.location_id) if plant.location_id else None
        plant_assigned = plant.assigned_to or (location.assigned_to if location else None)

        # Apply assignment filter.
        if effective_assigned_to == "__MINE__":
            if plant_assigned is not None and plant_assigned != user:
                continue
        elif effective_assigned_to is not None and plant_assigned != effective_assigned_to:
            continue

        next_due = rule.next_due_at
        if next_due:
            try:
                due_dt = datetime.fromisoformat(next_due)
            except ValueError:
                due_dt = None
        else:
            due_dt = None
        # A rule is due when next_due_at is in the past OR its snooze has
        # expired (snoozed_until <= now).
        if due_dt is None:
            continue
        if rule.snoozed_until:
            try:
                snooze_dt = datetime.fromisoformat(rule.snoozed_until)
            except ValueError:
                snooze_dt = None
            if snooze_dt and snooze_dt > now:
                continue  # still snoozed, skip
        if due_dt > now:
            continue
        species = resolve_species(session, plant.species_ref) if seed.present else None
        # Location already fetched above for assignment inheritance.

        # Build the species-specific care cue.
        cue_parts = []
        if species:
            wm = species.get("watering_method")
            dc = species.get("dryness_cue")
            wq = species.get("water_quality")
            if wm:
                cue_parts.append(wm.replace("_", " "))
            if dc:
                cue_parts.append(f"{dc.replace('_', ' ')}")
            if wq:
                cue_parts.append(wq.replace("_", " "))
        cue = " · ".join(cue_parts) if cue_parts else "Check plant"

        # Placement warning.
        placement = None
        if species:
            pf = placement_fit(
                species,
                window_direction=plant.window_direction,
                distance_from_window=plant.distance_from_window,
                has_drainage=bool(plant.has_drainage),
            )
            if pf.warning:
                placement = {"warning": pf.warning, "severity": pf.severity}

        due_tasks.append(
            {
                "plant_id": plant.id,
                "nickname": plant.nickname,
                "common_name": species.get("common_name") if species else plant.species_ref,
                "scientific_name": species.get("scientific_name") if species else None,
                "category": plant.category,
                "location_name": location.name if location else None,
                "location_id": plant.location_id,
                "action": rule.action,
                "next_due_at": rule.next_due_at,
                "overdue": due_dt < now if due_dt else False,
                "care_cue": cue,
                "watering_method": species.get("watering_method") if species else None,
                "dryness_cue": species.get("dryness_cue") if species else None,
                "water_quality": species.get("water_quality") if species else None,
                "toxicity_level": species.get("toxicity_level") if species else None,
                "toxicity_notes": species.get("toxicity_notes") if species else None,
                "has_drainage": plant.has_drainage,
                "placement_warning": placement,
                "calibration_factor": rule.calibration_factor,
                "assigned_to": plant_assigned,
            }
        )

    # Group by location.
    by_room: dict[str, list[dict]] = {}
    for task in due_tasks:
        room = task["location_name"] or "Unplaced"
        by_room.setdefault(room, []).append(task)

    # Weather summary (Phase 3): None when weather is unavailable or calm.
    weather_summary = None
    try:
        weather_summary = await build_weather_summary(session, _resolve_seed_get)
    except Exception as e:
        log.info("[Garden] weather summary unavailable: %s", e)

    # Phase 4: ready-to-harvest herbs/produce summary.
    _now = datetime.now(UTC)
    _ready = []
    for _p in session.exec(
        select(Plant).where(
            Plant.category.in_(("herb", "vegetable", "fruit")), Plant.status == "active"
        )
    ).all():
        _species = resolve_species(session, _p.species_ref) or {}
        _aliases_raw = _species.get("culinary_aliases") or ""
        _aliases = [a.strip() for a in _aliases_raw.split(",") if a.strip()]
        _in_cooldown = False
        _cooldown_until = None
        if _p.category == "herb" and _p.last_harvested_at and _p.regrowth_cooldown_days:
            try:
                _h = datetime.fromisoformat(_p.last_harvested_at)
                _cooldown_until = _h + timedelta(days=_p.regrowth_cooldown_days)
                if _now < _cooldown_until:
                    _in_cooldown = True
            except (ValueError, TypeError):
                pass
        _is_ready = False
        if _p.category == "herb":
            _is_ready = not _in_cooldown
        elif _p.category in ("vegetable", "fruit"):
            if _p.status == "harvesting":
                _is_ready = True
            else:
                try:
                    if _p.expected_harvest_start:
                        _s = datetime.fromisoformat(_p.expected_harvest_start)
                        if _p.expected_harvest_end:
                            _e = datetime.fromisoformat(_p.expected_harvest_end)
                            if _s <= _now <= _e:
                                _is_ready = True
                        elif _now >= _s:
                            _is_ready = True
                except (ValueError, TypeError):
                    pass
                if not _is_ready and _p.last_harvested_at:
                    try:
                        if _now - datetime.fromisoformat(_p.last_harvested_at) <= timedelta(days=14):
                            _is_ready = True
                    except (ValueError, TypeError):
                        pass
        if _is_ready:
            _ready.append(
                {
                    "plant_id": _p.id,
                    "nickname": _p.nickname,
                    "common_name": _species.get("common_name") or _p.species_ref,
                    "category": _p.category,
                    "species_ref": _p.species_ref,
                    "culinary_aliases": _aliases,
                    "in_cooldown": _in_cooldown,
                    "cooldown_until": _cooldown_until.isoformat() if _cooldown_until else None,
                    "pinching_bolting_notes": _species.get("pinching_bolting_notes"),
                    "herb_preservation_tips": _species.get("herb_preservation_tips"),
                }
            )

    return {
        "date": _today_iso(),
        "due_count": len(due_tasks),
        "overdue_count": sum(1 for t in due_tasks if t["overdue"]),
        "by_room": by_room,
        "all": due_tasks,
        "weather": weather_summary,
        "ready_to_harvest": _ready,
    }


# --------------------------------------------------------------------------- streaks


@app.get("/streaks")
def get_streaks(
    request: Request,
    session: Session = Depends(get_session),
):
    """Care streaks derived from care_logs: current streak (consecutive days
    with at least one care log) and longest streak per user, plus earned
    garden achievements and points."""
    user = _user(request)
    now = datetime.now(UTC).date()

    # All care logs for this user, ordered by date.
    logs = session.exec(
        select(CareLog)
        .where(CareLog.performed_by == user)
        .order_by(CareLog.created_at.desc())
    ).all()

    # Extract distinct dates with at least one log.
    logged_dates: set = set()
    for log in logs:
        try:
            d = datetime.fromisoformat(log.created_at).date()
            logged_dates.add(d)
        except (ValueError, TypeError):
            continue

    if not logged_dates:
        streaks = {
            "user_id": user,
            "current_streak": 0,
            "longest_streak": 0,
            "total_logs": 0,
        }
    else:
        # Current streak: count backwards from today.
        current_streak = 0
        d = now
        while d in logged_dates:
            current_streak += 1
            d -= timedelta(days=1)

        # Longest streak: find max consecutive run.
        sorted_dates = sorted(logged_dates, reverse=True)
        longest_streak = 1
        run = 1
        for i in range(1, len(sorted_dates)):
            if sorted_dates[i - 1] - sorted_dates[i] == timedelta(days=1):
                run += 1
                longest_streak = max(longest_streak, run)
            else:
                run = 1

        streaks = {
            "user_id": user,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "total_logs": len(logs),
        }

    # Count active plants for this user.
    plants_total = len(
        session.exec(select(Plant).where(Plant.status == "active")).all()
    )

    # Count harvests for this user (Phase 4: first_harvest achievement).
    harvests_total = len(
        session.exec(select(HarvestLog).where(HarvestLog.harvested_by == user)).all()
    )

    # Evaluate garden achievements.
    ach = evaluate_achievements(
        session,
        user,
        plants_total=plants_total,
        harvests_total=harvests_total,
        snoozes_total=len(
            session.exec(select(CareLog).where(CareLog.performed_by == user, CareLog.action == "snooze")).all()
        ),
        room_water_total=len(
            session.exec(select(CareLog).where(CareLog.performed_by == user, CareLog.notes.like("%batch water room%"))).all()
        ),
        pantry_batches_total=0,  # Phase 6: pantry
    )

    return {**streaks, **ach}


# --------------------------------------------------------------------------- notices (quiet morning digest test)


@app.post("/notices/test")
async def test_notice(
    request: Request,
    session: Session = Depends(get_session),
):
    """Send a test morning digest to the calling user (parent/admin only).

    Dispatches a real notice via Telemetry. Test digests omit the ``once``
    key so they never suppress that day's real morning digest.
    """
    _require_parent_or_admin(request)
    user = _user(request)
    # Build a minimal digest: just the caller's due tasks.
    from services.common import notify
    from services.garden.notices import collect_due_tasks, build_digest
    tasks = collect_due_tasks(session, user)
    digest = build_digest(user, tasks)
    if digest is None:
        payload = notify.notice(
            user=user,
            category="garden",
            title="Garden Morning Digest (test)",
            body=f"{user}, everything is caught up — no plants need care today.",
            kind="garden_digest_test",
        )
    else:
        payload = notify.notice(
            user=user,
            category="garden",
            title=digest["title"],
            body=digest["body"],
            actions=digest["actions"],
            kind="garden_digest_test",
        )
    # Dispatch via Telemetry (actually await it — no fire-and-forget).
    try:
        from services.common import notify as _notify
        await _notify.send(payload)
    except Exception as e:
        return {"ok": False, "user_id": user, "error": str(e)}
    return {
        "ok": True,
        "user_id": user,
        "dispatched": True,
        "payload": payload,
    }


# --------------------------------------------------------------------------- care log


@app.post("/plants/{plant_id}/care")
def log_care(
    plant_id: str,
    body: CareLogIn,
    request: Request,
    session: Session = Depends(get_session),
):
    """Log a care action: advances next_due_at and writes care_logs."""
    user = _user(request)
    plant = session.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    if plant.status != "active":
        raise HTTPException(status_code=409, detail="Plant is not active")

    species = resolve_species(session, plant.species_ref)

    # Write the care log.
    log_row = CareLog(
        plant_id=plant_id,
        action=body.action,
        performed_by=body.performed_by or user,
        source=body.source,
        notes=body.notes,
        sensor_snapshot_json=json.dumps(body.sensor_snapshot) if body.sensor_snapshot else None,
    )
    session.add(log_row)
    # Mirror to Skylight if the plant has opt-in sync enabled.
    mirror_care_log(session, plant_id=plant_id, action=body.action, performed_by=log_row.performed_by)

    # Advance the matching care rule's next_due_at using compute_interval.
    rule = session.exec(
        select(CareRule).where(CareRule.plant_id == plant_id, CareRule.action == body.action)
    ).first()
    if rule:
        result = compute_interval(
            base_interval_days=rule.interval_days or 7.0,
            winter_multiplier=rule.winter_multiplier or 1.0,
            pot_material=plant.pot_material,
            pot_size_in=plant.pot_size_in,
            soil_mix=plant.soil_mix,
            window_direction=plant.window_direction,
            distance_from_window=plant.distance_from_window,
            season_winter=_is_winter(),
            calibration_factor=rule.calibration_factor or 1.0,
        )
        next_due = datetime.now(UTC) + timedelta(days=result.next_due_offset_days)
        rule.next_due_at = next_due.isoformat()
        rule.updated_at = utcnow()
        # Clear snooze when care is actually logged.
        rule.snoozed_until = None
        session.add(rule)

    plant.updated_at = utcnow()
    session.commit()
    session.refresh(log_row)
    if rule:
        session.refresh(rule)

    return {
        "ok": True,
        "action": body.action,
        "plant_id": plant_id,
        "logged_by": log_row.performed_by,
        "next_due_at": rule.next_due_at if rule else None,
    }


@app.post("/plants/{plant_id}/snooze")
def snooze_plant(
    plant_id: str,
    request: Request,
    still_damp: bool = Query(True, description="\"Still damp\" — nudges calibration factor upward"),
    postpone_days: float = Query(2.0, ge=0.5, le=14.0, description="Days to postpone the next due date"),
    session: Session = Depends(get_session),
):
    """Snooze a plant's watering. "Still damp" nudges calibration_factor upward
    (self-calibrating) and postpones next_due_at to now + postpone_days."""
    user = _user(request)
    plant = session.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")

    species = resolve_species(session, plant.species_ref)

    # Snooze the water care rule: postpone next_due_at to now + postpone_days,
    # nudge calibration_factor upward if still_damp.
    rules = session.exec(select(CareRule).where(CareRule.plant_id == plant_id)).all()
    water_rule = None
    for rule in rules:
        if rule.action == "water":
            water_rule = rule
            # Self-calibrating: "Still damp" nudges calibration_factor up.
            new_cal = nudge_calibration(rule.calibration_factor or 1.0, still_damp=still_damp)
            rule.calibration_factor = new_cal
            next_due = datetime.now(UTC) + timedelta(days=postpone_days)
            rule.next_due_at = next_due.isoformat()
            rule.snoozed_until = next_due.isoformat()
            rule.updated_at = utcnow()
            session.add(rule)
            break

    # Log the snooze as a care entry.
    session.add(
        CareLog(
            plant_id=plant_id,
            action="snooze",
            performed_by=user,
            source="manual",
            notes=f"still_damp={still_damp}, postpone={postpone_days}d",
        )
    )
    plant.updated_at = utcnow()
    session.commit()

    return {
        "ok": True,
        "plant_id": plant_id,
        "still_damp": still_damp,
        "postpone_days": postpone_days,
        "calibration_factor": water_rule.calibration_factor if water_rule else plant.calibration_factor,
        "next_due_at": water_rule.next_due_at if water_rule else None,
    }


# --------------------------------------------------------------------------- location batch watering


@app.post("/locations/{loc_id}/water-all")
def water_all(
    loc_id: str,
    request: Request,
    due_only: bool = Query(True, description="Only water due/overdue plants"),
    session: Session = Depends(get_session),
):
    """Batch-water all (due) plants in a room."""
    user = _user(request)
    location = session.get(Location, loc_id)
    if location is None:
        raise HTTPException(status_code=404, detail="Location not found")

    now = datetime.now(UTC)
    plants = session.exec(
        select(Plant).where(Plant.location_id == loc_id, Plant.status == "active")
    ).all()

    watered = []
    for plant in plants:
        species = resolve_species(session, plant.species_ref)
        rule = session.exec(
            select(CareRule).where(CareRule.plant_id == plant.id, CareRule.action == "water")
        ).first()
        if rule:
            # Skip plants that aren't due yet when due_only=True.
            if due_only and rule.next_due_at:
                try:
                    due_dt = datetime.fromisoformat(rule.next_due_at)
                except ValueError:
                    due_dt = None
                if due_dt and due_dt > now:
                    continue
            result = compute_interval(
                base_interval_days=rule.interval_days or 7.0,
                winter_multiplier=rule.winter_multiplier or 1.0,
                pot_material=plant.pot_material,
                pot_size_in=plant.pot_size_in,
                soil_mix=plant.soil_mix,
                window_direction=plant.window_direction,
                distance_from_window=plant.distance_from_window,
                season_winter=_is_winter(),
                calibration_factor=rule.calibration_factor or 1.0,
            )
            next_due = now + timedelta(days=result.next_due_offset_days)
            rule.next_due_at = next_due.isoformat()
            rule.updated_at = utcnow()
            # Clear any snooze when watering.
            rule.snoozed_until = None
            session.add(rule)
        session.add(
            CareLog(
                plant_id=plant.id,
                location_id=loc_id,
                action="water",
                performed_by=user,
                source="manual",
                notes=f"batch water room: {location.name}",
            )
        )
        plant.updated_at = utcnow()
        common_name = species.get("common_name") if species else plant.species_ref
        watered.append(
            {
                "plant_id": plant.id,
                "nickname": plant.nickname,
                "common_name": common_name,
                "next_due_at": rule.next_due_at if rule else None,
            }
        )

    session.commit()
    return {
        "ok": True,
        "location_id": loc_id,
        "location_name": location.name,
        "watered_count": len(watered),
        "watered": watered,
    }


# --------------------------------------------------------------------------- care logs


@app.get("/plants/{plant_id}/logs")
def get_plant_logs(
    plant_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
):
    _user(request)
    plant = session.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    rows = (
        session.exec(
            select(CareLog)
            .where(CareLog.plant_id == plant_id)
            .order_by(CareLog.created_at.desc())
            .limit(limit)
        )
        .all()
    )
    return {
        "plant_id": plant_id,
        "count": len(rows),
        "logs": [
            {
                "id": r.id,
                "action": r.action,
                "performed_by": r.performed_by,
                "source": r.source,
                "notes": r.notes,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------- harvest log (Phase 4)


@app.post("/plants/{plant_id}/harvest", status_code=201)
async def log_harvest(
    plant_id: str,
    body: HarvestLogIn,
    request: Request,
    session: Session = Depends(get_session),
):
    """Log a harvest (open to all family roles).

    Writes a HarvestLog, updates plant.last_harvested_at and
    regrowth_cooldown_days, and pushes a yield metric to weather_client
    best-effort (never fails the request if weather is unavailable).
    """
    user = _user(request)
    plant = session.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    if plant.status != "active":
        raise HTTPException(status_code=409, detail="Plant is not active")

    # Validate disposition.
    valid_dispositions = {"fresh", "canned", "frozen", "dried", "fermented", "shared"}
    if body.disposition not in valid_dispositions:
        raise HTTPException(
            status_code=422,
            detail=f"disposition must be one of: {', '.join(sorted(valid_dispositions))}",
        )

    # Resolve species for harvest yield metric name.
    species = resolve_species(session, plant.species_ref) or {}
    species_key = plant.species_ref

    row = HarvestLog(
        plant_id=plant_id,
        harvested_at=body.harvested_at or utcnow(),
        harvested_by=body.harvested_by or user,
        quantity=body.quantity,
        unit=body.unit,
        disposition=body.disposition,
        notes=body.notes,
    )
    session.add(row)

    # Update plant harvest tracking.
    plant.last_harvested_at = row.harvested_at
    # Derive regrowth cooldown from species (herbs) if not already set.
    if plant.regrowth_cooldown_days is None and species.get("regrowth_cadence"):
        plant.regrowth_cooldown_days = (
            5 if species["regrowth_cadence"] == "cut_and_come_again" else 7
        )
    plant.updated_at = utcnow()

    # Best-effort push harvest yield metric (never fails the request).
    try:
        await weather.push_metric(
            f"garden_harvest_{species_key}",
            body.quantity,
            unit=body.unit,
            label=body.disposition,
        )
    except Exception as e:
        log.info("[Garden] harvest metric push best-effort failed: %s", e)

    session.commit()
    session.refresh(row)
    session.refresh(plant)

    return {
        "ok": True,
        "harvest_id": row.id,
        "plant_id": plant_id,
        "harvested_at": row.harvested_at,
        "harvested_by": row.harvested_by,
        "quantity": row.quantity,
        "unit": row.unit,
        "disposition": row.disposition,
        "plant": HarvestLogOut.model_validate(row).model_dump(),
    }


@app.get("/harvests")
def list_harvests(
    request: Request,
    plant_id: str | None = Query(None, description="Filter to a specific plant"),
    days: int | None = Query(None, ge=1, le=365, description="Lookback window in days"),
    disposition: str | None = Query(None, description="Filter by disposition"),
    session: Session = Depends(get_session),
):
    """Harvest log history with optional filters + per-crop seasonal yield totals."""
    user = _user(request)
    query = select(HarvestLog)
    if plant_id:
        query = query.where(HarvestLog.plant_id == plant_id)
    if days is not None:
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)
        query = query.where(HarvestLog.harvested_at >= cutoff.isoformat())
    if disposition:
        query = query.where(HarvestLog.disposition == disposition)
    query = query.order_by(HarvestLog.harvested_at.desc())
    rows = session.exec(query).all()

    # Per-crop seasonal yield totals.
    totals: dict[str, dict] = {}
    for row in rows:
        key = row.plant_id
        if key not in totals:
            p = session.get(Plant, key)
            species = resolve_species(session, p.species_ref) if p else None
            totals[key] = {
                "plant_id": key,
                "nickname": p.nickname if p else None,
                "common_name": (species or {}).get("common_name") if species else None,
                "category": p.category if p else None,
                "species_ref": p.species_ref if p else None,
                "total_quantity": 0.0,
                "unit": row.unit,
                "count": 0,
            }
        totals[key]["total_quantity"] += row.quantity
        totals[key]["count"] += 1

    return {
        "count": len(rows),
        "filters": {
            "plant_id": plant_id,
            "days": days,
            "disposition": disposition,
        },
        "harvests": [
            {
                "id": r.id,
                "plant_id": r.plant_id,
                "harvested_at": r.harvested_at,
                "harvested_by": r.harvested_by,
                "quantity": r.quantity,
                "unit": r.unit,
                "disposition": r.disposition,
                "notes": r.notes,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "totals": list(totals.values()),
    }


@app.delete("/harvests/{harvest_id}", status_code=204)
def delete_harvest(
    harvest_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """Delete a harvest log (adult/admin only, or the user who logged it)."""
    user = _user(request)
    role = _role(request)
    is_admin = _is_admin(request)
    row = session.get(HarvestLog, harvest_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Harvest not found")
    if row.harvested_by != user and role not in ADULT_ROLES and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only the person who logged the harvest, or adults/admins, may delete it.",
        )
    session.delete(row)
    session.commit()
    return JSONResponse(status_code=204, content=None)


@app.get("/harvest/ready")
def harvest_ready(
    request: Request,
    session: Session = Depends(get_session),
):
    """Currently harvest-ready herbs, vegetables, and fruits.

    - Active herb plants not in regrowth cooldown
      (now >= last_harvested_at + regrowth_cooldown_days)
    - Active vegetable/fruit plants in status "harvesting" or inside
      [expected_harvest_start, expected_harvest_end] or harvested
      within the last 14 days.
    """
    from datetime import UTC, datetime, timedelta

    user = _user(request)
    now = datetime.now(UTC)
    plants = session.exec(
        select(Plant).where(Plant.category.in_(("herb", "vegetable", "fruit")), Plant.status == "active")
    ).all()

    ready = []
    for plant in plants:
        species = resolve_species(session, plant.species_ref) or {}
        culinary_aliases_raw = species.get("culinary_aliases") or ""
        culinary_aliases = [a.strip() for a in culinary_aliases_raw.split(",") if a.strip()]

        in_cooldown = False
        cooldown_until = None
        if plant.category == "herb" and plant.last_harvested_at and plant.regrowth_cooldown_days:
            try:
                harvested_dt = datetime.fromisoformat(plant.last_harvested_at)
                cooldown_until = harvested_dt + timedelta(days=plant.regrowth_cooldown_days)
                if now < cooldown_until:
                    in_cooldown = True
            except (ValueError, TypeError):
                pass

        is_ready = False
        if plant.category == "herb":
            is_ready = not in_cooldown
        elif plant.category in ("vegetable", "fruit"):
            # Ready if in harvesting status, inside harvest window, or harvested recently.
            if plant.status == "harvesting":
                is_ready = True
            else:
                try:
                    if plant.expected_harvest_start:
                        start = datetime.fromisoformat(plant.expected_harvest_start)
                        if plant.expected_harvest_end:
                            end = datetime.fromisoformat(plant.expected_harvest_end)
                            if start <= now <= end:
                                is_ready = True
                        elif now >= start:
                            is_ready = True
                except (ValueError, TypeError):
                    pass
                if not is_ready and plant.last_harvested_at:
                    try:
                        harvested_dt = datetime.fromisoformat(plant.last_harvested_at)
                        if now - harvested_dt <= timedelta(days=14):
                            is_ready = True
                    except (ValueError, TypeError):
                        pass

        if is_ready:
            ready.append(
                {
                    "plant_id": plant.id,
                    "nickname": plant.nickname,
                    "common_name": species.get("common_name") or plant.species_ref,
                    "category": plant.category,
                    "species_ref": plant.species_ref,
                    "culinary_aliases": culinary_aliases,
                    "in_cooldown": in_cooldown,
                    "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                    "pinching_bolting_notes": species.get("pinching_bolting_notes"),
                    "herb_preservation_tips": species.get("herb_preservation_tips"),
                }
            )

    return {"count": len(ready), "ready": ready}


# --------------------------------------------------------------------------- care rules (admin+)


@app.get("/plants/{plant_id}/care-rules")
def list_care_rules(
    plant_id: str, request: Request, session: Session = Depends(get_session)
):
    _user(request)
    plant = session.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    rows = session.exec(select(CareRule).where(CareRule.plant_id == plant_id)).all()
    return {
        "plant_id": plant_id,
        "rules": [
            {
                "id": r.id,
                "action": r.action,
                "interval_days": r.interval_days,
                "winter_multiplier": r.winter_multiplier,
                "calibration_factor": r.calibration_factor,
                "rain_skip_enabled": r.rain_skip_enabled,
                "next_due_at": r.next_due_at,
                "snoozed_until": r.snoozed_until,
                "paused": r.paused,
            }
            for r in rows
        ],
    }


@app.post("/plants/{plant_id}/care-rules", status_code=201)
def create_care_rule(
    plant_id: str,
    body: CareRuleIn,
    request: Request,
    session: Session = Depends(get_session),
):
    _require_admin_or_adult(request)
    plant = session.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status_code=404, detail="Plant not found")
    row = CareRule(
        plant_id=plant_id,
        action=body.action,
        interval_days=body.interval_days,
        winter_multiplier=body.winter_multiplier,
        rain_skip_enabled=body.rain_skip_enabled,
        next_due_at=_compute_next_due(
            {"interval_days": body.interval_days, "winter_multiplier": body.winter_multiplier or 1.0},
            {},
        ),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {
        "id": row.id,
        "action": row.action,
        "interval_days": row.interval_days,
        "winter_multiplier": row.winter_multiplier,
        "next_due_at": row.next_due_at,
    }


@app.patch("/plants/{plant_id}/care-rules/{rule_id}")
def patch_care_rule(
    plant_id: str,
    rule_id: str,
    body: CareRuleIn,
    request: Request,
    session: Session = Depends(get_session),
):
    _require_admin_or_adult(request)
    rule = session.get(CareRule, rule_id)
    if rule is None or rule.plant_id != plant_id:
        raise HTTPException(status_code=404, detail="Care rule not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, key, value)
    rule.updated_at = utcnow()
    session.commit()
    session.refresh(rule)
    return {"id": rule.id, "action": rule.action, "next_due_at": rule.next_due_at}


@app.delete("/plants/{plant_id}/care-rules/{rule_id}", status_code=204)
def delete_care_rule(
    plant_id: str, rule_id: str, request: Request, session: Session = Depends(get_session)
):
    _require_admin_or_adult(request)
    rule = session.get(CareRule, rule_id)
    if rule is None or rule.plant_id != plant_id:
        raise HTTPException(status_code=404, detail="Care rule not found")
    session.delete(rule)
    session.commit()
    return JSONResponse(status_code=204, content=None)


# ========================= Phase 5: Garden Planner =========================

@app.post("/profile/detect-zone")
async def profile_detect_zone(request: Request, session: Session = Depends(get_session)):
    """Auto-detect USDA zone + frost dates from household lat/lon (weather config).

    Degrades gracefully with a clear message when geo/weather is unconfigured.
    Never hardcodes coordinates.
    """
    _user(request)
    profile = session.exec(select(GardenProfile)).first()
    if not profile:
        # Auto-initialize default profile row if none exists yet.
        profile = GardenProfile(
            user_id="household",
            family_size=7,
            zone_source="manual",
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)

    zone = None
    source = "manual"
    lat = None
    lon = None
    min_temp_f = None
    try:
        forecast = await weather.forecast()
        location = (forecast or {}).get("location") or {}
        lat = location.get("latitude")
        lon = location.get("longitude")
        if lat is not None and lon is not None:
            zone, min_temp_f = await detect_zone_for_location(float(lat), float(lon))
            if zone:
                source = "auto"
    except Exception as e:
        log.info("[Garden] zone auto-detect unavailable: %s", e)

    if not zone:
        if lat is None or lon is None:
            message = "Geo/weather not configured; set zone manually in Garden Profile."
        else:
            message = (
                "Could not read historical winter temperatures; "
                "set zone manually in Garden Profile."
            )
        return {
            "zone": profile.hardiness_zone,
            "zone_source": profile.zone_source or "manual",
            "last_spring_frost_mmdd": profile.last_spring_frost_mmdd,
            "first_fall_frost_mmdd": profile.first_fall_frost_mmdd,
            "latitude": lat,
            "longitude": lon,
            "min_temp_f": min_temp_f,
            "message": message,
        }

    spring_mmdd, fall_mmdd = default_frost_dates(zone)

    # Auto-detect was explicitly requested by the user (tap on Auto-detect),
    # so persist the detected zone and the frost dates it implies.
    profile.hardiness_zone = zone
    profile.zone_source = "auto"
    profile.last_spring_frost_mmdd = spring_mmdd or profile.last_spring_frost_mmdd
    profile.first_fall_frost_mmdd = fall_mmdd or profile.first_fall_frost_mmdd
    profile.updated_at = utcnow()
    session.add(profile)
    session.commit()
    session.refresh(profile)

    return {
        "zone": zone,
        "zone_source": source,
        "latitude": lat,
        "longitude": lon,
        "min_temp_f": min_temp_f,
        "last_spring_frost_mmdd": spring_mmdd,
        "first_fall_frost_mmdd": fall_mmdd,
        "season_length_days": season_length_days(zone) if zone else None,
        "message": "Zone detected from the household's historical winter minimum.",
    }


@app.get("/pantry-goals")
def list_pantry_goals(
    request: Request,
    season_year: int | None = Query(None, description="Filter by calendar year"),
    session: Session = Depends(get_session),
):
    """List pantry preservation goals."""
    _user(request)
    query = select(PantryGoal)
    if season_year is not None:
        query = query.where(PantryGoal.season_year == season_year)
    query = query.order_by(PantryGoal.season_year.desc(), PantryGoal.created_at.desc())
    rows = session.exec(query).all()
    return {
        "count": len(rows),
        "goals": [
            {
                "id": r.id,
                "item": r.item,
                "crop_refs_json": r.crop_refs_json,
                "target_qty": r.target_qty,
                "unit": r.unit,
                "method": r.method,
                "season_year": r.season_year,
                "notes": r.notes,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ],
    }


@app.post("/pantry-goals", status_code=201)
def create_pantry_goal(
    body: PantryGoalIn,
    request: Request,
    session: Session = Depends(get_session),
):
    """Create a pantry preservation goal."""
    _require_admin_or_adult(request)
    row = PantryGoal(
        item=body.item,
        crop_refs_json=body.crop_refs_json or "[]",
        target_qty=body.target_qty,
        unit=body.unit,
        method=body.method,
        season_year=body.season_year or datetime.now(UTC).year,
        notes=body.notes,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return PantryGoalOut.model_validate(row).model_dump()


@app.patch("/pantry-goals/{goal_id}")
def patch_pantry_goal(
    goal_id: str,
    body: PantryGoalIn,
    request: Request,
    session: Session = Depends(get_session),
):
    """Update a pantry goal."""
    _require_admin_or_adult(request)
    row = session.get(PantryGoal, goal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pantry goal not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return PantryGoalOut.model_validate(row).model_dump()


@app.delete("/pantry-goals/{goal_id}", status_code=204)
def delete_pantry_goal(
    goal_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """Delete a pantry goal."""
    _require_admin_or_adult(request)
    row = session.get(PantryGoal, goal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pantry goal not found")
    session.delete(row)
    session.commit()
    return JSONResponse(status_code=204, content=None)


@app.get("/pantry-goals/calculate")
def calculate_pantry_goal(
    request: Request,
    item: str = Query(..., description="Preserved item name"),
    crop_refs: str = Query("", description="Comma-separated species_refs"),
    target_qty: float = Query(..., ge=0, description="Target quantity"),
    unit: str = Query("quarts", description="Output unit"),
    method: str = Query("water_bath_canning", description="Preservation method"),
    session: Session = Depends(get_session),
):
    """Back-calculate raw lbs, plant counts, and sq ft from a pantry goal."""
    _user(request)
    crop_list = [c.strip() for c in crop_refs.split(",") if c.strip()] if crop_refs else []
    goal_in = PantryGoalIn(
        item=item,
        crop_refs_json=json.dumps(crop_list),
        target_qty=target_qty,
        unit=unit,
        method=method,
    )
    return pantry_goal_backcalc(session, goal_in, crop_list)


@app.get("/plans")
def list_plans(
    request: Request,
    status: str | None = Query(None, description="Filter by plan status"),
    season: str | None = Query(None, description="Filter by season"),
    session: Session = Depends(get_session),
):
    """List seasonal garden plans."""
    _user(request)
    query = select(GardenPlan)
    if status:
        query = query.where(GardenPlan.status == status)
    if season:
        query = query.where(GardenPlan.season == season)
    query = query.order_by(GardenPlan.year.desc(), GardenPlan.created_at.desc())
    rows = session.exec(query).all()
    return {
        "count": len(rows),
        "plans": [
            {
                "id": r.id,
                "name": r.name,
                "season": r.season,
                "year": r.year,
                "family_size": r.family_size,
                "mode": r.mode,
                "status": r.status,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ],
    }


@app.post("/plans", status_code=201)
def create_plan(
    body: GardenPlanIn,
    request: Request,
    session: Session = Depends(get_session),
):
    """Create a seasonal garden plan."""
    _require_admin_or_adult(request)
    row = GardenPlan(
        name=body.name,
        season=body.season,
        year=body.year or datetime.now(UTC).year,
        family_size=body.family_size or 7,
        mode=body.mode or "fresh_only",
        status=body.status or "draft",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return GardenPlanOut.model_validate(row).model_dump()


@app.get("/plans/{plan_id}")
def get_plan(
    plan_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """Get a plan with its cells."""
    _user(request)
    plan = session.get(GardenPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    cells = session.exec(select(PlanCell).where(PlanCell.plan_id == plan_id)).all()
    return {
        "plan": GardenPlanOut.model_validate(plan).model_dump(),
        "cells": [
            {
                "id": c.id,
                "plan_id": c.plan_id,
                "location_id": c.location_id,
                "col": c.col,
                "row": c.row,
                "species_ref": c.species_ref,
                "plant_id": c.plant_id,
                "crop_family": c.crop_family,
                "quantity": c.quantity,
                "notes": c.notes,
            }
            for c in cells
        ],
    }


@app.patch("/plans/{plan_id}")
def patch_plan(
    plan_id: str,
    body: GardenPlanIn,
    request: Request,
    session: Session = Depends(get_session),
):
    """Update a plan."""
    _require_admin_or_adult(request)
    plan = session.get(GardenPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(plan, key, value)
    plan.updated_at = utcnow()
    session.commit()
    session.refresh(plan)
    return GardenPlanOut.model_validate(plan).model_dump()


@app.delete("/plans/{plan_id}", status_code=204)
def delete_plan(
    plan_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """Delete a plan and its cells."""
    _require_admin_or_adult(request)
    plan = session.get(GardenPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    cells = session.exec(select(PlanCell).where(PlanCell.plan_id == plan_id)).all()
    for cell in cells:
        session.delete(cell)
    session.delete(plan)
    session.commit()
    return JSONResponse(status_code=204, content=None)


@app.get("/plans/{plan_id}/beds/{bed_id}/cells")
def list_plan_cells(
    plan_id: str,
    bed_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """List plan cells for a bed within a plan."""
    _user(request)
    cells = session.exec(
        select(PlanCell).where(PlanCell.plan_id == plan_id, PlanCell.location_id == bed_id)
    ).all()
    return {
        "plan_id": plan_id,
        "bed_id": bed_id,
        "cells": [
            {
                "id": c.id,
                "col": c.col,
                "row": c.row,
                "species_ref": c.species_ref,
                "plant_id": c.plant_id,
                "crop_family": c.crop_family,
                "quantity": c.quantity,
                "notes": c.notes,
            }
            for c in cells
        ],
    }


@app.put("/plans/{plan_id}/beds/{bed_id}/cells")
def upsert_plan_cells(
    plan_id: str,
    bed_id: str,
    body: list[PlanCellIn],
    request: Request,
    session: Session = Depends(get_session),
):
    """Upsert plan cells for a bed (replace all cells for that bed in the plan)."""
    _require_admin_or_adult(request)
    # Remove existing cells for this bed in this plan
    existing = session.exec(
        select(PlanCell).where(PlanCell.plan_id == plan_id, PlanCell.location_id == bed_id)
    ).all()
    for e in existing:
        session.delete(e)

    # Insert new cells
    for cell_in in body:
        cell = PlanCell(
            plan_id=plan_id,
            location_id=bed_id,
            col=cell_in.col,
            row=cell_in.row,
            species_ref=cell_in.species_ref,
            plant_id=cell_in.plant_id,
            crop_family=cell_in.crop_family,
            quantity=cell_in.quantity,
            notes=cell_in.notes,
        )
        session.add(cell)

    session.commit()
    return {"plan_id": plan_id, "bed_id": bed_id, "cells_upserted": len(body)}


class AutoArrangeRequest(BaseModel):
    """Optional body for the auto-arrange endpoint."""

    species_refs: list[str] | None = None
    apply: bool = True


@app.post("/plans/{plan_id}/beds/{bed_id}/auto-arrange")
def auto_arrange_bed_endpoint(
    plan_id: str,
    bed_id: str,
    request: Request,
    body: AutoArrangeRequest | None = None,
    session: Session = Depends(get_session),
):
    """Auto-arrange crops in a bed: tall crops north, companions paired, antagonists separated.

    Accepts optional ``{"species_refs": [...], "apply": true}``.  When
    ``apply`` is true (the default) the arranged cells are persisted and the
    updated checks are returned alongside the placements.
    """
    _require_admin_or_adult(request)
    plan = session.get(GardenPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    existing = session.exec(
        select(PlanCell).where(PlanCell.plan_id == plan_id, PlanCell.location_id == bed_id)
    ).all()

    refs = list(body.species_refs) if body and body.species_refs else [
        c.species_ref for c in existing if c.species_ref
    ]
    if not refs:
        return {
            "plan_id": plan_id,
            "bed_id": bed_id,
            "placements": [],
            "checks": [],
            "warning_count": 0,
            "applied": False,
            "message": "No crops in bed",
        }

    placements = auto_arrange_bed(
        session, existing, bed_id, refs, plan=plan
    )
    applied = bool(body.apply) if body else True

    if applied:
        for cell in existing:
            session.delete(cell)
        for placement in placements:
            if placement.get("col", -1) < 0:
                continue
            session.add(PlanCell(
                plan_id=plan_id,
                location_id=bed_id,
                col=int(placement["col"]),
                row=int(placement["row"]),
                species_ref=placement.get("species_ref"),
                crop_family=placement.get("crop_family"),
                quantity=float(placement.get("quantity") or 1.0),
            ))
        session.commit()

    cells = session.exec(select(PlanCell).where(PlanCell.plan_id == plan_id)).all()
    checks = check_plan(session, plan, cells)

    return {
        "plan_id": plan_id,
        "bed_id": bed_id,
        "placements": placements,
        "checks": checks,
        "warning_count": len([c for c in checks if c.get("kind") != "companion"]),
        "applied": applied,
    }


@app.get("/plans/{plan_id}/checks")
def plan_checks(
    plan_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """Run companion/antagonist/rotation checks across all beds in a plan."""
    _user(request)
    plan = session.get(GardenPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    cells = session.exec(select(PlanCell).where(PlanCell.plan_id == plan_id)).all()
    warnings = check_plan(session, plan, cells)

    return {
        "plan_id": plan_id,
        "checks": warnings,
        "warning_count": len([w for w in warnings if w.get("kind") != "companion"]),
    }


@app.get("/plans/{plan_id}/timeline")
def plan_timeline(
    plan_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    """Seasonal planting timeline with dated milestones per crop."""
    _user(request)
    plan = session.get(GardenPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    profile = session.exec(select(GardenProfile)).first()
    spring_mmdd = profile.last_spring_frost_mmdd if profile else None
    fall_mmdd = profile.first_fall_frost_mmdd if profile else None

    cells = session.exec(select(PlanCell).where(PlanCell.plan_id == plan_id)).all()
    timeline: list[dict] = []
    for cell in cells:
        species = resolve_species(session, cell.species_ref) if cell.species_ref else None
        if not species:
            continue
        milestones = planting_milestones(species, spring_mmdd, fall_mmdd)
        timeline.append({
            "cell_id": cell.id,
            "col": cell.col,
            "row": cell.row,
            "species_ref": cell.species_ref,
            "common_name": species.get("common_name", cell.species_ref),
            "category": species.get("category", ""),
            "milestones": milestones,
        })

    # Sort by first milestone date
    timeline.sort(
        key=lambda t: mmdd_sort_key(
            t["milestones"][0]["mmdd"] if t["milestones"] else None
        )
    )
    return {
        "plan_id": plan_id,
        "plan_name": plan.name,
        "season": plan.season,
        "year": plan.year,
        "spring_frost_mmdd": spring_mmdd,
        "first_fall_frost_mmdd": fall_mmdd,
        "timeline": timeline,
    }


@app.get("/planner/sizing")
def planner_sizing(
    request: Request,
    mode: str = Query("fresh_only", description="fresh_only | fresh_plus_preserving | pantry_goals"),
    family_size: int | None = Query(None, ge=1, description="Override family size"),
    species_refs: str = Query("", description="Comma-separated species_refs"),
    session: Session = Depends(get_session),
):
    """Simple mode sizing calculator: plant counts & sq ft per crop."""
    _user(request)
    profile = session.exec(select(GardenProfile)).first()
    fs = family_size or (profile.family_size if profile else 7)
    refs = [c.strip() for c in species_refs.split(",") if c.strip()] if species_refs else []
    results = simple_sizing(session, fs, mode, refs)
    return {
        "mode": mode,
        "family_size": fs,
        "crops": results,
        "total_sqft": round(sum(c["sqft"] for c in results), 1),
        "total_plants": sum(c["plant_count"] for c in results),
    }
