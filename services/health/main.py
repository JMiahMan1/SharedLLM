"""Jarvis Health service.

The store and the arithmetic behind the Health hub: profiles, open metric
ingestion, sleep, morning check-ins and nutrition. Activity (steps, workouts,
distance) stays in **geo**, where it already lives and is already fused across
devices; this service never copies those numbers. The summary deliberately
returns ``activity: null`` and the UI composes the two sources, so there is one
answer per question.

Design commitments:

* Readings keep their ``source``. A watch, Health Connect, a scale and a hand
  entry are distinguishable forever.
* Ingestion is idempotent when a source supplies an ``external_id``.
* Missing data is reported as missing, never as a zero or a guessed score.
* Nutrition targets come from the profile's own maths (Mifflin-St Jeor);
  minors never get a deficit range.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import aiohttp
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, SQLModel, create_engine, select

from services.config import INTERNAL_SECRET
from services.health import challenges, cycle, food_seed, scoring, weekly
from services.health.models import (
    ChallengeMember,
    Checkin,
    CycleLog,
    CyclePeriod,
    CycleSettings,
    FamilyChallenge,
    FoodEntry,
    HealthProfile,
    HealthShare,
    MetricSample,
    OffProduct,
    SleepSession,
    utcnow,
)
from services.shared.info_endpoint import info_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
log = logging.getLogger("health")

_START_TIME = time.time()
DEFAULT_DATABASE_URL = "sqlite:////data/health.db"
METRICS = tuple(scoring.METRIC_UNITS)
MEALS = ("breakfast", "lunch", "dinner", "snack")
CYCLE_FLOWS = ("spotting", "light", "medium", "heavy")
CYCLE_SYMPTOMS = (
    "cramps",
    "headache",
    "bloating",
    "acne",
    "breast_tenderness",
    "fatigue",
    "cravings",
    "backache",
    "nausea",
    "insomnia",
    "mood_swing",
)
OVULATION_TESTS = ("negative", "positive", "inconclusive")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
SOURCES_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OFF_BASE = "https://world.openfoodfacts.org"
OFF_HEADERS = {"User-Agent": "Jarvis-OS/1.0 (family health hub; self-hosted)"}
DEFAULT_SLEEP_NEED = 480
DEFAULT_WATER_GOAL = 2000


# --- database --------------------------------------------------------------


def _database_url() -> str:
    return os.getenv("HEALTH_DATABASE_URL") or DEFAULT_DATABASE_URL


def _engine():
    url = _database_url()
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return create_engine(url, pool_pre_ping=True, **kwargs)


FDC_SEED_FILENAME = "fdc-seed.db"
_SEED_CACHE: dict[str, food_seed.FoodSeed] = {}


def _seed_path() -> str:
    """Where the USDA seed lives: FDC_SEED_PATH, else beside the database."""
    explicit = os.getenv("FDC_SEED_PATH")
    if explicit:
        return explicit
    url = _database_url()
    if url.startswith("sqlite:///"):
        folder = os.path.dirname(url[len("sqlite:///") :]) or "."
        return os.path.join(folder, FDC_SEED_FILENAME)
    return f"/data/{FDC_SEED_FILENAME}"


def _seed() -> food_seed.FoodSeed:
    path = _seed_path()
    seed = _SEED_CACHE.get(path)
    if seed is None:
        seed = food_seed.FoodSeed(path)
        _SEED_CACHE[path] = seed
    return seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = _db()
    SQLModel.metadata.create_all(engine)
    # Sex is male or female; a profile stored under the removed "other" option
    # is reset to "not set" so its owner picks one instead of keeping a value
    # the UI can no longer show.
    with Session(engine) as session:
        legacy = session.exec(select(HealthProfile).where(HealthProfile.sex == "other")).all()
        for profile in legacy:
            profile.sex = None
        if legacy:
            session.commit()
            log.info("[Health] reset %d profile(s) from sex=other to not set", len(legacy))
    log.info("[Health] ready db=%s", _database_url())
    yield


app = FastAPI(title="Jarvis Health Service", lifespan=lifespan)
app.state.engine = None


def _db():
    if app.state.engine is None:
        app.state.engine = _engine()
    return app.state.engine


def get_session() -> Iterator[Session]:
    with Session(_db()) as session:
        yield session


# --- service-to-service guard ---------------------------------------------


def _verify_internal_secret(header_secret: str | None, query_secret: str | None = None) -> bool:
    if not INTERNAL_SECRET:
        return True
    return header_secret == INTERNAL_SECRET or query_secret == INTERNAL_SECRET


@app.middleware("http")
async def require_internal_secret_middleware(request: Request, call_next):
    """Only the gateway may call this service.

    The service also listens on its own container port, so a per-route check
    would be one forgotten decorator away from exposing a family's health data.
    Only ``/health`` is exempt (container healthcheck, carries no data).
    """
    if request.url.path.rstrip("/") in ("/health", ""):
        return await call_next(request)
    secret = request.headers.get("X-Internal-Secret") or request.query_params.get("x_internal_secret")
    if not _verify_internal_secret(secret):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


app.include_router(info_router)


def _user(request: Request) -> str:
    """The acting username, forwarded by the gateway after authentication.

    No fallback identity: an unauthenticated request has no health data, and
    defaulting to the admin would be exactly the privacy bug this avoids.
    """
    user = (request.headers.get("X-User-Id") or "").strip().lower()
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _admin(request: Request) -> bool:
    return (request.headers.get("X-User-Is-Admin") or "").lower() in ("1", "true", "yes")


# --- validation helpers ----------------------------------------------------


def _day(value: str | None, *, field: str = "day") -> str:
    if not value:
        raise HTTPException(status_code=422, detail=f"{field} is required (YYYY-MM-DD)")
    if not DAY_RE.match(value):
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} is not a real date") from exc
    return value


def _instant(value: str | None, *, field: str = "recorded_at") -> str:
    """Normalise an ISO-8601 instant. A date-only value anchors at local noon.

    A bare date carries no timezone, so noon is the one anchor that cannot slip
    into the previous or next day; it is only accepted for readings (weight,
    body fat) where the exact hour is irrelevant.
    """
    if not value:
        return datetime.now(UTC).isoformat()
    text = value.strip()
    if DAY_RE.match(text):
        return f"{text}T12:00:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        # A bare instant came from a person or a device on this network, not
        # from UTC. Anchor it to the household's timezone so a 22:30 bedtime
        # does not render as 15:30 in the UI.
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    now = datetime.now(UTC)
    if parsed > now + timedelta(days=1):
        raise HTTPException(status_code=422, detail=f"{field} is in the future")
    if parsed.year < 1900:
        raise HTTPException(status_code=422, detail=f"{field} is implausibly old")
    return parsed.isoformat()


def _metric(metric: str) -> str:
    if metric not in scoring.METRIC_UNITS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown metric {metric!r}; valid metrics: {', '.join(METRICS)}",
        )
    return metric


def _source(value: str | None) -> str:
    source = (value or "manual").strip().lower()
    if not SOURCES_RE.match(source):
        raise HTTPException(
            status_code=422,
            detail="source must be a short lowercase slug (a-z, 0-9, _ or -)",
        )
    return source


def _finite(value: float | None, name: str, low: float = 0.0) -> float:
    if value is None:
        raise HTTPException(status_code=422, detail=f"{name} is required")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise HTTPException(status_code=422, detail=f"{name} must be a finite number")
    if number < low:
        raise HTTPException(status_code=422, detail=f"{name} must be at least {low:g}")
    return number


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _sample_out(sample: MetricSample) -> dict:
    return {
        "id": sample.id,
        "user_id": sample.user_id,
        "metric": sample.metric,
        "value": sample.value,
        "unit": sample.unit,
        "recorded_at": sample.recorded_at,
        "source": sample.source,
        "external_id": sample.external_id,
        "metadata": json.loads(sample.metadata_json or "{}"),
    }


def _sleep_out(session: SleepSession) -> dict:
    return {
        "id": session.id,
        "user_id": session.user_id,
        "night": session.night,
        "bedtime": session.bedtime,
        "wake_time": session.wake_time,
        "duration_minutes": session.duration_minutes,
        "efficiency": session.efficiency,
        "quality": session.quality,
        "stages": json.loads(session.stages_json or "{}"),
        "source": session.source,
        "external_id": session.external_id,
        "note": session.note,
    }


def _checkin_out(checkin: Checkin) -> dict:
    return {
        "id": checkin.id,
        "user_id": checkin.user_id,
        "day": checkin.day,
        "energy": checkin.energy,
        "mood": checkin.mood,
        "soreness": checkin.soreness,
        "stress": checkin.stress,
        "note": checkin.note,
        "updated_at": checkin.updated_at,
    }


def _food_out(entry: FoodEntry) -> dict:
    return {
        "id": entry.id,
        "user_id": entry.user_id,
        "day": entry.day,
        "meal": entry.meal,
        "name": entry.name,
        "brand": entry.brand,
        "calories": entry.calories,
        "protein_g": entry.protein_g,
        "carbs_g": entry.carbs_g,
        "fat_g": entry.fat_g,
        "fiber_g": entry.fiber_g,
        "sugar_g": entry.sugar_g,
        "sodium_mg": entry.sodium_mg,
        "servings": entry.servings,
        "serving_grams": entry.serving_grams,
        "barcode": entry.barcode,
        "recipe_path": entry.recipe_path,
        "source": entry.source,
        "external_id": entry.external_id,
        "created_at": entry.created_at,
        "density": scoring.food_density(
            entry.calories / entry.serving_grams * 100.0
            if entry.serving_grams and entry.serving_grams > 0
            else None
        ),
    }


# --- request models --------------------------------------------------------


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    birth_year: int | None = None
    height_cm: float | None = None
    sex: str | None = None
    activity_level: str | None = None
    goal: str | None = None
    units: str | None = None
    kid_mode: bool | None = None
    sleep_need_minutes: int | None = None
    water_goal_ml: int | None = None
    weekly_active_minutes: int | None = None

    @field_validator("birth_year")
    @classmethod
    def _birth_year(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not (1900 <= value <= date.today().year):
            raise ValueError("birth_year must be between 1900 and this year")
        return value

    @field_validator("height_cm")
    @classmethod
    def _height(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not (50 <= value <= 250):
            raise ValueError("height_cm must be between 50 and 250")
        return value

    @field_validator("sex")
    @classmethod
    def _sex(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("female", "male", "unspecified"):
            raise ValueError("sex must be female, male or unspecified")
        return value

    @field_validator("activity_level")
    @classmethod
    def _activity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in scoring.ACTIVITY_FACTORS:
            raise ValueError(f"activity_level must be one of {', '.join(scoring.ACTIVITY_FACTORS)}")
        return value

    @field_validator("goal")
    @classmethod
    def _goal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("lose", "maintain", "gain", "balance"):
            raise ValueError("goal must be lose, maintain, gain or balance")
        return value

    @field_validator("units")
    @classmethod
    def _units(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("imperial", "metric"):
            raise ValueError("units must be imperial or metric")
        return value

    @field_validator("sleep_need_minutes")
    @classmethod
    def _sleep_need(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not (240 <= value <= 840):
            raise ValueError("sleep_need_minutes must be between 240 and 840")
        return value

    @field_validator("water_goal_ml")
    @classmethod
    def _water_goal(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not (250 <= value <= 10000):
            raise ValueError("water_goal_ml must be between 250 and 10000")
        return value

    @field_validator("weekly_active_minutes")
    @classmethod
    def _weekly_active(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not (0 <= value <= 2000):
            raise ValueError("weekly_active_minutes must be between 0 and 2000")
        return value


class MetricIn(BaseModel):
    metric: str
    value: float
    unit: str
    recorded_at: str | None = None
    source: str = "manual"
    external_id: str | None = Field(default=None, max_length=128)
    metadata: dict = Field(default_factory=dict)


class MetricsBatch(BaseModel):
    samples: list[MetricIn] = Field(min_length=1, max_length=500)


class SleepIn(BaseModel):
    night: str
    bedtime: str | None = None
    wake_time: str | None = None
    duration_minutes: int | None = None
    efficiency: float | None = None
    quality: int | None = None
    stages: dict = Field(default_factory=dict)
    source: str = "manual"
    external_id: str | None = Field(default=None, max_length=128)
    note: str = Field(default="", max_length=2000)


class CheckinIn(BaseModel):
    energy: int | None = Field(default=None, ge=1, le=5)
    mood: int | None = Field(default=None, ge=1, le=5)
    soreness: int | None = Field(default=None, ge=1, le=5)
    stress: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=2000)


class FoodIn(BaseModel):
    day: str
    meal: str = "snack"
    name: str = Field(min_length=1, max_length=200)
    brand: str = Field(default="", max_length=120)
    calories: float = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0
    fiber_g: float | None = None
    sugar_g: float | None = None
    sodium_mg: float | None = None
    servings: float = 1
    serving_grams: float | None = None
    barcode: str | None = Field(default=None, max_length=40)
    recipe_path: str | None = Field(default=None, max_length=500)
    source: str = "manual"
    external_id: str | None = Field(default=None, max_length=128)

    @field_validator("meal")
    @classmethod
    def _meal(cls, value: str) -> str:
        if value not in MEALS:
            raise ValueError(f"meal must be one of {', '.join(MEALS)}")
        return value

    @field_validator("servings")
    @classmethod
    def _servings(cls, value: float) -> float:
        if not (0 < value <= 100):
            raise ValueError("servings must be greater than 0 and at most 100")
        return value


class ShareIn(BaseModel):
    viewer: str = Field(min_length=1, max_length=80)
    scope: str
    enabled: bool = True

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: str) -> str:
        if value not in ("sleep", "nutrition", "cycle"):
            raise ValueError("scope must be sleep, nutrition or cycle")
        return value


class CycleSettingsUpdate(BaseModel):
    enabled: bool | None = None
    average_cycle_length: int | None = Field(default=None, ge=15, le=90)
    average_period_length: int | None = Field(default=None, ge=1, le=20)
    guardian_username: str | None = Field(default=None, max_length=80)


class CyclePeriodIn(BaseModel):
    start_day: str
    end_day: str | None = None
    flow: str = "medium"
    note: str = Field(default="", max_length=2000)
    source: str = "manual"
    external_id: str | None = Field(default=None, max_length=128)

    @field_validator("flow")
    @classmethod
    def _flow(cls, value: str) -> str:
        if value not in CYCLE_FLOWS:
            raise ValueError(f"flow must be one of {', '.join(CYCLE_FLOWS)}")
        return value


class CycleLogIn(BaseModel):
    symptoms: list[str] = Field(default_factory=list, max_length=40)
    mood: int | None = Field(default=None, ge=1, le=5)
    energy: int | None = Field(default=None, ge=1, le=5)
    bbt_c: float | None = Field(default=None, ge=34, le=42)
    ovulation_test: str | None = None
    note: str = Field(default="", max_length=2000)

    @field_validator("symptoms")
    @classmethod
    def _symptoms(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - set(CYCLE_SYMPTOMS))
        if unknown:
            raise ValueError(
                f"unknown symptoms: {', '.join(unknown)}; valid symptoms: {', '.join(CYCLE_SYMPTOMS)}"
            )
        return list(dict.fromkeys(values))

    @field_validator("ovulation_test")
    @classmethod
    def _ovulation_test(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in OVULATION_TESTS:
            raise ValueError(f"ovulation_test must be one of {', '.join(OVULATION_TESTS)}")
        return value


# --- profile ---------------------------------------------------------------


def _profile_out(profile: HealthProfile | None, user: str) -> dict:
    if profile is None:
        return {
            "user_id": user,
            "exists": False,
            "display_name": "",
            "birth_year": None,
            "height_cm": None,
            "sex": None,
            "activity_level": "moderate",
            "goal": "maintain",
            "units": "imperial",
            "kid_mode": False,
            "sleep_need_minutes": None,
            "water_goal_ml": None,
            "weekly_active_minutes": None,
        }
    data = profile.model_dump()
    data["exists"] = True
    return data


@app.get("/profile")
def get_profile(request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    return _profile_out(session.get(HealthProfile, user), user)


@app.put("/profile")
def put_profile(body: ProfileUpdate, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    profile = session.get(HealthProfile, user)
    if profile is None:
        profile = HealthProfile(user_id=user)
        session.add(profile)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, key, value)
    profile.updated_at = utcnow()
    session.commit()
    session.refresh(profile)
    return _profile_out(profile, user)


# --- cycle tracking --------------------------------------------------------


def _age_years(profile: HealthProfile | None) -> int | None:
    if profile is None or not profile.birth_year:
        return None
    return date.today().year - profile.birth_year


def _cycle_applicable(profile: HealthProfile | None) -> bool:
    """Cycle controls are shown only for a profile explicitly marked female.

    Male, unspecified and not-yet-set profiles all get ``applicable: false``:
    the tracker appears once Female is chosen. Existing data is never deleted
    just because the profile changed; it is simply not surfaced.
    """
    return profile is not None and profile.sex == "female"


def _cycle_hidden_reason(profile: HealthProfile | None) -> str:
    """Why the cycle panel is absent, in language the person can act on."""
    if profile is not None and profile.sex == "male":
        return "Cycle tracking is not shown for a male profile."
    return "Choose Female in your profile to use cycle tracking."


def _cycle_is_minor(profile: HealthProfile | None) -> bool:
    """Minor check for cycle sharing.

    A known birth year decides it. When the birth year is missing, ``kid_mode``
    is the signal: the family app marks children and teens there, so a person
    who is not in kid mode is treated as an adult and may choose who sees the
    phase summary (a wife sharing with her husband, for example). If neither
    signal exists, the person stays guardian-only.
    """
    age = _age_years(profile)
    if age is not None:
        return age < 18
    return bool(profile.kid_mode) if profile is not None else True


def _cycle_settings(session: Session, user: str) -> CycleSettings | None:
    return session.get(CycleSettings, user)


def _cycle_period_out(row: CyclePeriod) -> dict:
    return {
        "id": row.id,
        "start_day": row.start_day,
        "end_day": row.end_day,
        "flow": row.flow,
        "note": row.note,
        "source": row.source,
        "external_id": row.external_id,
        "created_at": row.created_at,
    }


def _cycle_log_out(row: CycleLog) -> dict:
    try:
        symptoms = json.loads(row.symptoms_json or "[]")
    except (TypeError, ValueError):
        symptoms = []
    return {
        "id": row.id,
        "day": row.day,
        "symptoms": symptoms if isinstance(symptoms, list) else [],
        "mood": row.mood,
        "energy": row.energy,
        "bbt_c": row.bbt_c,
        "ovulation_test": row.ovulation_test,
        "note": row.note,
        "updated_at": row.updated_at,
    }


def _cycle_periods(session: Session, user: str) -> list[CyclePeriod]:
    return list(
        session.exec(
            select(CyclePeriod)
            .where(CyclePeriod.user_id == user)
            .order_by(CyclePeriod.start_day.desc())
        ).all()
    )


def _cycle_summary(
    session: Session,
    user: str,
    day: str,
    settings: CycleSettings | None = None,
) -> dict | None:
    """Owner-only phase estimate. ``None`` when tracking is off or not applicable."""
    profile = session.get(HealthProfile, user)
    if not _cycle_applicable(profile):
        return None
    settings = settings if settings is not None else _cycle_settings(session, user)
    if settings is None or not settings.enabled:
        return None
    rows = _cycle_periods(session, user)
    periods = [
        cycle.Period(date.fromisoformat(row.start_day), date.fromisoformat(row.end_day) if row.end_day else None)
        for row in rows
    ]
    return cycle.summarize(
        periods,
        date.fromisoformat(day),
        average_cycle_length=settings.average_cycle_length,
        average_period_length=settings.average_period_length,
    )


def _drop_cycle_shares(session: Session, user: str, *, keep: str | None = None) -> None:
    rows = session.exec(
        select(HealthShare).where(HealthShare.owner == user, HealthShare.scope == "cycle")
    ).all()
    for row in rows:
        if keep is None or row.viewer != keep:
            session.delete(row)


@app.get("/cycle")
def get_cycle(
    request: Request,
    day: str | None = None,
    session: Session = Depends(get_session),
):
    """The cycle panel in one call: settings, current estimate, recent logs.

    A non-female profile gets ``applicable: false`` and no cycle data at all.
    The route still exists so the UI can make a clean decision without a 404.
    """
    user = _user(request)
    day = _day(day or date.today().isoformat())
    profile = session.get(HealthProfile, user)
    if not _cycle_applicable(profile):
        return {
            "applicable": False,
            "enabled": False,
            "is_minor": False,
            "average_cycle_length": None,
            "average_period_length": None,
            "guardian_username": None,
            "summary": None,
            "periods": [],
            "logs": [],
            "reason": _cycle_hidden_reason(profile),
        }
    settings = _cycle_settings(session, user)
    enabled = bool(settings and settings.enabled)
    periods = _cycle_periods(session, user) if enabled else []
    logs = (
        session.exec(
            select(CycleLog)
            .where(CycleLog.user_id == user)
            .order_by(CycleLog.day.desc())
            .limit(120)
        ).all()
        if enabled
        else []
    )
    return {
        "applicable": True,
        "enabled": enabled,
        "is_minor": _cycle_is_minor(profile),
        "age_known": _age_years(profile) is not None,
        "average_cycle_length": settings.average_cycle_length if settings else None,
        "average_period_length": settings.average_period_length if settings else None,
        "guardian_username": settings.guardian_username if settings else None,
        "summary": _cycle_summary(session, user, day, settings),
        "periods": [_cycle_period_out(row) for row in periods],
        "logs": [_cycle_log_out(row) for row in logs],
    }


@app.put("/cycle/settings")
def put_cycle_settings(
    body: CycleSettingsUpdate,
    request: Request,
    session: Session = Depends(get_session),
):
    user = _user(request)
    profile = session.get(HealthProfile, user)
    changes = body.model_dump(exclude_unset=True)
    enabling = changes.get("enabled") is True

    if enabling and not _cycle_applicable(profile):
        raise HTTPException(
            status_code=422,
            detail=_cycle_hidden_reason(profile),
        )

    if "guardian_username" in changes:
        guardian = (changes["guardian_username"] or "").strip().lower() or None
        if guardian:
            if not USERNAME_RE.match(guardian):
                raise HTTPException(status_code=422, detail="guardian_username is not a valid username")
            if guardian == user:
                raise HTTPException(status_code=422, detail="You cannot be your own guardian")
        changes["guardian_username"] = guardian

    settings = _cycle_settings(session, user)
    if settings is None:
        settings = CycleSettings(user_id=user)
        session.add(settings)
    for key, value in changes.items():
        setattr(settings, key, value)
    settings.updated_at = utcnow()

    if changes.get("enabled") is False:
        _drop_cycle_shares(session, user)
    if "guardian_username" in changes:
        _drop_cycle_shares(session, user, keep=settings.guardian_username)

    session.commit()
    session.refresh(settings)
    return get_cycle(request, session=session)


@app.post("/cycle/periods")
def add_cycle_period(
    body: CyclePeriodIn,
    request: Request,
    session: Session = Depends(get_session),
):
    user = _user(request)
    profile = session.get(HealthProfile, user)
    if not _cycle_applicable(profile):
        raise HTTPException(status_code=422, detail=_cycle_hidden_reason(profile))
    settings = _cycle_settings(session, user)
    if settings is None or not settings.enabled:
        raise HTTPException(status_code=422, detail="Enable cycle tracking before logging a period.")

    start = _day(body.start_day, field="start_day")
    today = date.today().isoformat()
    if start > today:
        raise HTTPException(status_code=422, detail="start_day cannot be in the future")
    end = _day(body.end_day, field="end_day") if body.end_day else None
    if end:
        if end < start:
            raise HTTPException(status_code=422, detail="end_day must be on or after start_day")
        if end > today:
            raise HTTPException(status_code=422, detail="end_day cannot be in the future")
        if (date.fromisoformat(end) - date.fromisoformat(start)).days > 60:
            raise HTTPException(status_code=422, detail="A logged period cannot be longer than 60 days")
    source = _source(body.source)

    row = session.exec(
        select(CyclePeriod).where(CyclePeriod.user_id == user, CyclePeriod.start_day == start)
    ).first()
    if row is None:
        row = CyclePeriod(user_id=user, start_day=start)
        session.add(row)
    row.end_day = end
    row.flow = body.flow
    row.note = body.note
    row.source = source
    row.external_id = body.external_id
    session.commit()
    session.refresh(row)
    return {"period": _cycle_period_out(row), "summary": _cycle_summary(session, user, today)}


@app.get("/cycle/periods")
def list_cycle_periods(
    request: Request,
    days: int = Query(default=365, ge=1, le=3650),
    session: Session = Depends(get_session),
):
    user = _user(request)
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = session.exec(
        select(CyclePeriod)
        .where(CyclePeriod.user_id == user, CyclePeriod.start_day >= since)
        .order_by(CyclePeriod.start_day.desc())
    ).all()
    return {"periods": [_cycle_period_out(row) for row in rows]}


@app.delete("/cycle/periods/{period_id}")
def delete_cycle_period(
    period_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    user = _user(request)
    row = session.get(CyclePeriod, period_id)
    if row is None or row.user_id != user:
        raise HTTPException(status_code=404, detail="Period not found")
    session.delete(row)
    session.commit()
    return {"deleted": period_id}


@app.get("/cycle/logs")
def list_cycle_logs(
    request: Request,
    days: int = Query(default=90, ge=1, le=3650),
    session: Session = Depends(get_session),
):
    user = _user(request)
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = session.exec(
        select(CycleLog)
        .where(CycleLog.user_id == user, CycleLog.day >= since)
        .order_by(CycleLog.day.desc())
    ).all()
    return {"logs": [_cycle_log_out(row) for row in rows]}


@app.get("/cycle/logs/{day}")
def get_cycle_log(
    day: str,
    request: Request,
    session: Session = Depends(get_session),
):
    user = _user(request)
    day = _day(day)
    row = session.exec(
        select(CycleLog).where(CycleLog.user_id == user, CycleLog.day == day)
    ).first()
    return {"day": day, "log": _cycle_log_out(row) if row else None}


@app.put("/cycle/logs/{day}")
def put_cycle_log(
    day: str,
    body: CycleLogIn,
    request: Request,
    session: Session = Depends(get_session),
):
    user = _user(request)
    day = _day(day)
    if day > date.today().isoformat():
        raise HTTPException(status_code=422, detail="You cannot log symptoms for a future day")
    profile = session.get(HealthProfile, user)
    if not _cycle_applicable(profile):
        raise HTTPException(status_code=422, detail=_cycle_hidden_reason(profile))
    settings = _cycle_settings(session, user)
    if settings is None or not settings.enabled:
        raise HTTPException(status_code=422, detail="Enable cycle tracking before logging symptoms.")

    row = session.exec(
        select(CycleLog).where(CycleLog.user_id == user, CycleLog.day == day)
    ).first()
    if row is None:
        row = CycleLog(user_id=user, day=day)
        session.add(row)
    row.symptoms_json = json.dumps(body.symptoms)
    row.mood = body.mood
    row.energy = body.energy
    row.bbt_c = body.bbt_c
    row.ovulation_test = body.ovulation_test
    row.note = body.note
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return {"log": _cycle_log_out(row)}


@app.delete("/cycle/logs/{day}")
def delete_cycle_log(
    day: str,
    request: Request,
    session: Session = Depends(get_session),
):
    user = _user(request)
    day = _day(day)
    row = session.exec(
        select(CycleLog).where(CycleLog.user_id == user, CycleLog.day == day)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Log not found")
    session.delete(row)
    session.commit()
    return {"deleted": day}


@app.get("/metrics/catalog")
def metric_catalog():
    return {
        "metrics": [
            {
                "key": metric,
                "unit": unit,
                "min": scoring.METRIC_BOUNDS.get(metric, (None, None))[0],
                "max": scoring.METRIC_BOUNDS.get(metric, (None, None))[1],
                "units": sorted(scoring.UNIT_ALIASES.get(metric, {})),
            }
            for metric, unit in scoring.METRIC_UNITS.items()
        ]
    }


@app.post("/metrics")
def add_metrics(body: MetricsBatch, request: Request, session: Session = Depends(get_session)):
    """Accept a batch of readings from any source.

    This is the open door for future devices: Health Connect, a watch export,
    a scale or a script all post the same shape. Repeated ``external_id``s are
    counted as duplicates instead of inflating history.
    """
    user = _user(request)
    accepted: list[dict] = []
    duplicates = 0

    for sample in body.samples:
        metric = _metric(sample.metric)
        try:
            value, unit = scoring.convert_unit(metric, _finite(sample.value, "value", -1e9), sample.unit)
            scoring.validate_metric_value(metric, value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        source = _source(sample.source)
        recorded_at = _instant(sample.recorded_at)
        if sample.external_id:
            existing = session.exec(
                select(MetricSample).where(
                    MetricSample.user_id == user,
                    MetricSample.source == source,
                    MetricSample.external_id == sample.external_id,
                )
            ).first()
            if existing is not None:
                duplicates += 1
                continue
        row = MetricSample(
            user_id=user,
            metric=metric,
            value=value,
            unit=unit,
            recorded_at=recorded_at,
            source=source,
            external_id=sample.external_id,
            metadata_json=json.dumps(sample.metadata or {}),
        )
        session.add(row)
        accepted.append(row)

    session.commit()
    for row in accepted:
        session.refresh(row)
    return {
        "accepted": len(accepted),
        "duplicates": duplicates,
        "samples": [_sample_out(row) for row in accepted],
    }


@app.get("/metrics/{metric}")
def get_metrics(
    metric: str,
    request: Request,
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
):
    user = _user(request)
    metric = _metric(metric)
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = session.exec(
        select(MetricSample)
        .where(
            MetricSample.user_id == user,
            MetricSample.metric == metric,
            MetricSample.recorded_at >= since,
        )
        .order_by(MetricSample.recorded_at.desc())
    ).all()
    samples = [_sample_out(row) for row in rows]
    return {
        "user_id": user,
        "metric": metric,
        "unit": scoring.METRIC_UNITS[metric],
        "days": days,
        "samples": samples,
        "latest": samples[0] if samples else None,
    }


@app.delete("/metrics/{sample_id}")
def delete_metric(sample_id: int, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    row = session.get(MetricSample, sample_id)
    if row is None or row.user_id != user:
        raise HTTPException(status_code=404, detail="Reading not found")
    session.delete(row)
    session.commit()
    return {"deleted": sample_id}


def _latest_metric(session: Session, user: str, metric: str, on_or_before: str | None = None) -> MetricSample | None:
    query = select(MetricSample).where(MetricSample.user_id == user, MetricSample.metric == metric)
    if on_or_before:
        # ISO strings sort by their date prefix, whatever offset they carry, so
        # "before tomorrow" includes every reading whose local day is <= today.
        next_day = (date.fromisoformat(on_or_before) + timedelta(days=1)).isoformat()
        query = query.where(MetricSample.recorded_at < next_day)
    return session.exec(query.order_by(MetricSample.recorded_at.desc()).limit(1)).first()


def _metric_series(session: Session, user: str, metric: str, since: str) -> list[MetricSample]:
    return session.exec(
        select(MetricSample)
        .where(
            MetricSample.user_id == user,
            MetricSample.metric == metric,
            MetricSample.recorded_at >= since,
        )
        .order_by(MetricSample.recorded_at.asc())
    ).all()


# --- sleep -----------------------------------------------------------------


def _clock(value: str, night: str) -> datetime:
    """A bedtime/wake time, with a bare ``HH:MM`` anchored to the night.

    A bare evening time belongs to the previous calendar day (a 22:30 bedtime
    for a night ending on the 21st is the 20th at 22:30); a bare morning time
    belongs to the wake date.
    """
    text = value.strip()
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", text):
        parsed = datetime.fromisoformat(f"{night}T{text}").replace(
            tzinfo=datetime.now().astimezone().tzinfo
        )
        if parsed.hour >= 12:
            parsed -= timedelta(days=1)
        return parsed
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _sleep_times(body: SleepIn, night: str) -> tuple[str | None, str | None, int]:
    """Bedtime, wake time and duration for a night, validated; the duration
    comes from the times when it is not given."""
    duration = body.duration_minutes
    bedtime: str | None = None
    wake_time: str | None = None
    if body.bedtime:
        bedtime = _clock(body.bedtime, night).isoformat()
    if body.wake_time:
        wake_time = _clock(body.wake_time, night).isoformat()
    if duration is None and bedtime and wake_time:
        bed = datetime.fromisoformat(bedtime)
        wake = datetime.fromisoformat(wake_time)
        if wake <= bed:
            wake += timedelta(days=1)
        duration = round((wake - bed).total_seconds() / 60.0)
    if duration is None:
        raise HTTPException(status_code=422, detail="Provide duration_minutes, or both bedtime and wake_time")
    if not (0 < duration <= 1440):
        raise HTTPException(status_code=422, detail="duration_minutes must be between 1 and 1440")
    if body.efficiency is not None and not (0 <= body.efficiency <= 100):
        raise HTTPException(status_code=422, detail="efficiency must be between 0 and 100")
    if body.quality is not None and not (1 <= body.quality <= 5):
        raise HTTPException(status_code=422, detail="quality must be between 1 and 5")
    return bedtime, wake_time, duration


@app.post("/sleep")
def add_sleep(body: SleepIn, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    night = _day(body.night, field="night")
    bedtime, wake_time, duration = _sleep_times(body, night)
    source = _source(body.source)

    if body.external_id:
        existing = session.exec(
            select(SleepSession).where(
                SleepSession.user_id == user,
                SleepSession.source == source,
                SleepSession.external_id == body.external_id,
            )
        ).first()
        if existing is not None:
            return {**_sleep_out(existing), "duplicate": True}

    row = SleepSession(
        user_id=user,
        night=night,
        bedtime=_instant(bedtime, field="bedtime") if bedtime else None,
        wake_time=_instant(wake_time, field="wake_time") if wake_time else None,
        duration_minutes=duration,
        efficiency=body.efficiency,
        quality=body.quality,
        stages_json=json.dumps(body.stages or {}),
        source=source,
        external_id=body.external_id,
        note=body.note,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**_sleep_out(row), "duplicate": False}


@app.put("/sleep/{sleep_id}")
def update_sleep(sleep_id: int, body: SleepIn, request: Request, session: Session = Depends(get_session)):
    """Correct a saved night in place (an estimate a person fixed becomes theirs)."""
    user = _user(request)
    row = session.get(SleepSession, sleep_id)
    if row is None or row.user_id != user:
        raise HTTPException(status_code=404, detail="Sleep session not found")
    night = _day(body.night, field="night")
    bedtime, wake_time, duration = _sleep_times(body, night)
    row.night = night
    row.bedtime = _instant(bedtime, field="bedtime") if bedtime else None
    row.wake_time = _instant(wake_time, field="wake_time") if wake_time else None
    row.duration_minutes = duration
    row.efficiency = body.efficiency
    row.quality = body.quality
    row.source = _source(body.source)
    row.note = body.note
    session.add(row)
    session.commit()
    session.refresh(row)
    return _sleep_out(row)


@app.get("/sleep")
def get_sleep(
    request: Request,
    days: int = Query(14, ge=1, le=365),
    session: Session = Depends(get_session),
):
    user = _user(request)
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = session.exec(
        select(SleepSession)
        .where(SleepSession.user_id == user, SleepSession.night >= since)
        .order_by(SleepSession.night.desc())
    ).all()
    return {"user_id": user, "sessions": [_sleep_out(row) for row in rows]}


@app.delete("/sleep/{sleep_id}")
def delete_sleep(sleep_id: int, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    row = session.get(SleepSession, sleep_id)
    if row is None or row.user_id != user:
        raise HTTPException(status_code=404, detail="Sleep session not found")
    session.delete(row)
    session.commit()
    return {"deleted": sleep_id}


# Saved from the device-use estimate: below a hand log, which is below a
# wearable or other imported reading.
USAGE_ESTIMATE_SOURCE = "usage_estimate"


def _sleep_rank(row: SleepSession) -> int:
    if row.source == USAGE_ESTIMATE_SOURCE:
        return 0
    return 1 if row.source == "manual" else 2


def _best_night(rows: list[SleepSession]) -> SleepSession | None:
    """From one night's rows (newest first): a device session wins over a hand
    log, a hand log over a device-use estimate, and the newest wins a tie."""
    best: SleepSession | None = None
    for row in rows:
        if best is None or _sleep_rank(row) > _sleep_rank(best):
            best = row
    return best


def _sleep_for_night(session: Session, user: str, day: str) -> SleepSession | None:
    """The night that ended on ``day`` (see ``_best_night``)."""
    rows = session.exec(
        select(SleepSession)
        .where(SleepSession.user_id == user, SleepSession.night == day)
        .order_by(SleepSession.created_at.desc())
    ).all()
    return _best_night(list(rows))


# --- check-ins -------------------------------------------------------------


@app.get("/checkins")
def list_checkins(
    request: Request,
    days: int = Query(14, ge=1, le=365),
    session: Session = Depends(get_session),
):
    user = _user(request)
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = session.exec(
        select(Checkin)
        .where(Checkin.user_id == user, Checkin.day >= since)
        .order_by(Checkin.day.desc())
    ).all()
    return {"user_id": user, "checkins": [_checkin_out(row) for row in rows]}


@app.get("/checkins/{day}")
def get_checkin(day: str, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    day = _day(day)
    row = session.exec(
        select(Checkin).where(Checkin.user_id == user, Checkin.day == day)
    ).first()
    return {"checkin": _checkin_out(row) if row else None}


@app.put("/checkins/{day}")
def put_checkin(day: str, body: CheckinIn, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    day = _day(day)
    if all(value is None for value in (body.energy, body.mood, body.soreness, body.stress)) and not body.note:
        raise HTTPException(status_code=422, detail="Give at least one check-in answer")
    row = session.exec(
        select(Checkin).where(Checkin.user_id == user, Checkin.day == day)
    ).first()
    if row is None:
        row = Checkin(user_id=user, day=day)
        session.add(row)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    session.commit()
    session.refresh(row)
    return _checkin_out(row)


# --- nutrition -------------------------------------------------------------


@app.post("/food")
def add_food(body: FoodIn, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    day = _day(body.day)
    if body.calories < 0 or body.protein_g < 0 or body.carbs_g < 0 or body.fat_g < 0:
        raise HTTPException(status_code=422, detail="Nutrition values cannot be negative")
    source = _source(body.source)
    if body.external_id:
        existing = session.exec(
            select(FoodEntry).where(
                FoodEntry.user_id == user,
                FoodEntry.source == source,
                FoodEntry.external_id == body.external_id,
            )
        ).first()
        if existing is not None:
            return {**_food_out(existing), "duplicate": True}
    row = FoodEntry(
        user_id=user,
        day=day,
        meal=body.meal,
        name=body.name.strip(),
        brand=body.brand.strip(),
        calories=max(0.0, body.calories),
        protein_g=max(0.0, body.protein_g),
        carbs_g=max(0.0, body.carbs_g),
        fat_g=max(0.0, body.fat_g),
        fiber_g=body.fiber_g,
        sugar_g=body.sugar_g,
        sodium_mg=body.sodium_mg,
        servings=body.servings,
        serving_grams=body.serving_grams,
        barcode=body.barcode,
        recipe_path=body.recipe_path,
        source=source,
        external_id=body.external_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return {**_food_out(row), "duplicate": False}


@app.get("/food")
def list_food(
    request: Request,
    day: str | None = None,
    days: int = Query(0, ge=0, le=365),
    session: Session = Depends(get_session),
):
    user = _user(request)
    query = select(FoodEntry).where(FoodEntry.user_id == user)
    if day:
        query = query.where(FoodEntry.day == _day(day))
    elif days:
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        query = query.where(FoodEntry.day >= since)
    rows = session.exec(query.order_by(FoodEntry.created_at.desc())).all()
    return {"user_id": user, "entries": [_food_out(row) for row in rows]}


@app.delete("/food/{entry_id}")
def delete_food(entry_id: int, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    row = session.get(FoodEntry, entry_id)
    if row is None or row.user_id != user:
        raise HTTPException(status_code=404, detail="Food entry not found")
    session.delete(row)
    session.commit()
    return {"deleted": entry_id}


def _nutrition_day(session: Session, user: str, day: str, profile: HealthProfile | None) -> dict:
    rows = session.exec(
        select(FoodEntry)
        .where(FoodEntry.user_id == user, FoodEntry.day == day)
        .order_by(FoodEntry.created_at.asc())
    ).all()
    totals = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0, "fiber_g": 0.0}
    meals: dict[str, dict] = {meal: {"entries": [], "calories": 0.0} for meal in MEALS}
    for row in rows:
        factor = max(0.0, row.servings)
        totals["calories"] += row.calories * factor
        totals["protein_g"] += row.protein_g * factor
        totals["carbs_g"] += row.carbs_g * factor
        totals["fat_g"] += row.fat_g * factor
        totals["fiber_g"] += (row.fiber_g or 0.0) * factor
        meals.setdefault(row.meal, {"entries": [], "calories": 0.0})
        meals[row.meal]["entries"].append(_food_out(row))
        meals[row.meal]["calories"] += row.calories * factor

    weight = _latest_metric(session, user, "weight", day)
    target = scoring.calorie_target(
        birth_year=profile.birth_year if profile else None,
        height_cm=profile.height_cm if profile else None,
        weight_kg=weight.value if weight else None,
        sex=profile.sex if profile else None,
        activity_level=profile.activity_level if profile else "moderate",
        goal=profile.goal if profile else "maintain",
        kid_mode=profile.kid_mode if profile else False,
    )
    # Macro targets use the midpoint of the range when one exists.
    macro_calories = round((target.min + target.max) / 2) if target.min and target.max else None
    macros = scoring.macro_targets(macro_calories) if macro_calories else None

    return {
        "day": day,
        "entries_count": len(rows),
        "logged": len(rows) > 0,
        "totals": {key: round(value, 1) for key, value in totals.items()},
        "meals": meals,
        "target": {
            "kind": target.kind,
            "min": target.min,
            "max": target.max,
            "bmr": target.bmr,
            "tdee": target.tdee,
            "goal": target.goal,
            "note": target.note,
            "missing": target.missing,
            "deficit_allowed": target.deficit_allowed,
            "protein_g": macros.protein_g if macros else None,
            "carbs_g": macros.carbs_g if macros else None,
            "fat_g": macros.fat_g if macros else None,
        },
    }


@app.get("/nutrition/day")
def nutrition_day(
    request: Request,
    day: str | None = None,
    session: Session = Depends(get_session),
):
    user = _user(request)
    day = _day(day or date.today().isoformat())
    return {"user_id": user, **_nutrition_day(session, user, day, session.get(HealthProfile, user))}


def _off_product(product: dict) -> dict | None:
    """One OpenFoodFacts product -> the result shape the UI logs from.

    The v2 endpoint is deliberate: it normalizes nutrient keys per 100 g
    (``*_100g``), while the v3 payload reports ``source_per`` (not always
    100 g) and no longer carries ``serving_quantity``, which would silently
    degrade serving weights.
    """
    nutriments = product.get("nutriments") or {}
    calories = _num(nutriments.get("energy-kcal_100g"))
    if calories is None:
        energy_kj = _num(nutriments.get("energy_100g"))
        calories = energy_kj / 4.184 if energy_kj is not None else None
    name = (product.get("product_name") or "").strip()
    if not name:
        return None
    serving_grams = _num(product.get("serving_quantity"))
    sodium = _num(nutriments.get("sodium_100g"))
    return {
        "code": product.get("code") or product.get("_id"),
        "name": name,
        "brand": (product.get("brands") or "").strip(),
        "image": product.get("image_front_small_url") or product.get("image_url"),
        "serving_size": product.get("serving_size"),
        "serving_grams": serving_grams,
        "source": "openfoodfacts",
        "per_100g": {
            "calories": calories,
            "protein_g": _num(nutriments.get("proteins_100g")),
            "carbs_g": _num(nutriments.get("carbohydrates_100g")),
            "fat_g": _num(nutriments.get("fat_100g")),
            "fiber_g": _num(nutriments.get("fiber_100g")),
            "sugar_g": _num(nutriments.get("sugars_100g")),
            "sodium_mg": sodium * 1000.0 if sodium is not None else None,
        },
    }


async def _off_get(path: str, params: dict | None = None) -> dict:
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client,
            client.get(f"{OFF_BASE}{path}", params=params, headers=OFF_HEADERS) as resp,
        ):
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"OpenFoodFacts returned {resp.status}")
            return await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        log.warning("[Health] OpenFoodFacts lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail="OpenFoodFacts is unreachable; try again or add the food by hand.") from exc


async def _off_search(query: str, limit: int) -> list[dict]:
    """OpenFoodFacts branded search. OFF allows roughly ten searches a minute
    per IP, so this runs for a submitted search only, never for type-ahead."""
    data = await _off_get(
        "/api/v2/search",
        {
            "search_terms": query,
            "fields": "code,product_name,brands,serving_size,serving_quantity,image_front_small_url,nutriments",
            "page_size": str(limit),
        },
    )
    return [item for item in (_off_product(p) for p in data.get("products", [])) if item]


@app.get("/food/search")
async def search_food(q: str = Query(min_length=1, max_length=120), limit: int = Query(10, ge=1, le=25)):
    """Search foods for logging: the USDA seed first, then OpenFoodFacts.

    The seed answers generic whole foods and ships household portions; OFF adds
    branded products. A missing seed or an unreachable OFF degrades to the
    other source instead of failing the search.
    """
    seed = _seed()
    seed_hits = seed.search(q, limit)
    remaining = limit - len(seed_hits)
    off_hits: list[dict] = []
    offline: list[str] = []
    if remaining > 0:
        try:
            off_hits = await _off_search(q, remaining)
        except HTTPException:
            if not seed_hits:
                raise
            offline.append("openfoodfacts")
            log.info("OpenFoodFacts search unavailable; answered %d USDA seed hits for %r", len(seed_hits), q)
    sources = (["usda_fdc"] if seed_hits else []) + (["openfoodfacts"] if off_hits else [])
    return {
        "query": q,
        "sources": sources,
        "offline_sources": offline,
        "seed": {"present": seed.present, "results": len(seed_hits)},
        "results": seed_hits + off_hits,
    }


@app.get("/food/barcode/{code}")
async def barcode_food(code: str, session: Session = Depends(get_session)):
    if not re.match(r"^[0-9A-Za-z-]{4,32}$", code):
        raise HTTPException(status_code=422, detail="Not a valid barcode")
    cached = session.get(OffProduct, code)
    if cached is not None:
        return json.loads(cached.payload_json)
    data = await _off_get(
        f"/api/v2/product/{code}.json",
        {"fields": "code,product_name,brands,serving_size,serving_quantity,image_front_small_url,nutriments"},
    )
    if data.get("status") != 1 or not data.get("product"):
        raise HTTPException(status_code=404, detail="That barcode is not in OpenFoodFacts")
    product = _off_product(data["product"])
    if product is None:
        raise HTTPException(status_code=404, detail="That barcode has no usable nutrition data")
    # Cache permanently: OFF rate-limits product reads and the family scans the
    # same packages repeatedly; a packaged food's label does not change.
    session.merge(OffProduct(code=code, payload_json=json.dumps(product), fetched_at=utcnow()))
    session.commit()
    return product


# --- family sharing --------------------------------------------------------


@app.get("/shares")
def list_shares(request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    rows = session.exec(select(HealthShare).where(HealthShare.owner == user)).all()
    return {
        "owner": user,
        "shares": [{"viewer": row.viewer, "scope": row.scope} for row in rows],
    }


@app.put("/shares")
def put_share(body: ShareIn, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    viewer = body.viewer.strip().lower()
    if viewer == user:
        raise HTTPException(status_code=422, detail="You already see your own data")
    if not USERNAME_RE.match(viewer):
        raise HTTPException(status_code=422, detail="viewer is not a valid username")

    if body.scope == "cycle" and body.enabled:
        profile = session.get(HealthProfile, user)
        if not _cycle_applicable(profile):
            raise HTTPException(status_code=422, detail=_cycle_hidden_reason(profile))
        settings = _cycle_settings(session, user)
        if settings is None or not settings.enabled:
            raise HTTPException(status_code=422, detail="Enable cycle tracking before sharing it.")
        if _cycle_is_minor(profile):
            guardian = (settings.guardian_username or "").strip().lower()
            if not guardian:
                raise HTTPException(
                    status_code=422,
                    detail="Set a mother or trusted adult before sharing cycle data.",
                )
            if viewer != guardian:
                raise HTTPException(
                    status_code=403,
                    detail="A minor can only share cycle data with their designated mother or trusted adult.",
                )

    row = session.exec(
        select(HealthShare).where(
            HealthShare.owner == user,
            HealthShare.viewer == viewer,
            HealthShare.scope == body.scope,
        )
    ).first()
    if body.enabled and row is None:
        session.add(HealthShare(owner=user, viewer=viewer, scope=body.scope))
    elif not body.enabled and row is not None:
        session.delete(row)
    session.commit()
    return {"owner": user, "viewer": viewer, "scope": body.scope, "enabled": body.enabled}


@app.get("/family")
def family_summary(
    request: Request,
    day: str | None = None,
    session: Session = Depends(get_session),
):
    """Only what each person explicitly shared, one scope at a time.

    This never returns a profile or a full day by accident: each owner's grants
    are read first, then only those scopes are projected into the answer.
    """
    viewer = _user(request)
    day = _day(day or date.today().isoformat())
    grants = session.exec(select(HealthShare).where(HealthShare.viewer == viewer)).all()
    by_owner: dict[str, set[str]] = {}
    for grant in grants:
        by_owner.setdefault(grant.owner, set()).add(grant.scope)

    people = []
    for owner, scopes in sorted(by_owner.items()):
        entry: dict[str, Any] = {"user_id": owner, "scopes": sorted(scopes)}
        if "sleep" in scopes:
            night = _sleep_for_night(session, owner, day)
            profile = session.get(HealthProfile, owner)
            need = (profile.sleep_need_minutes if profile else None) or DEFAULT_SLEEP_NEED
            score = scoring.sleep_score(
                night.duration_minutes if night else None,
                need,
                night.efficiency if night else None,
                night.quality if night else None,
            )
            entry["sleep"] = {
                "duration_minutes": night.duration_minutes if night else None,
                "score": score.score if score else None,
                "label": score.label if score else None,
                "logged": night is not None,
            }
        if "nutrition" in scopes:
            nutrition = _nutrition_day(session, owner, day, session.get(HealthProfile, owner))
            entry["nutrition"] = {
                "logged": nutrition["logged"],
                "calories": nutrition["totals"]["calories"],
            }
        if "cycle" in scopes:
            owner_profile = session.get(HealthProfile, owner)
            shared = _cycle_summary(session, owner, day)
            # Support ideas travel with the share the owner chose to make.
            support = cycle.partner_support(shared["phase"]) if shared else None
            entry["cycle"] = {
                "applicable": _cycle_applicable(owner_profile),
                "enabled": shared is not None,
                "phase": shared["phase"] if shared else None,
                "phase_label": shared["phase_label"] if shared else None,
                "cycle_day": shared["cycle_day"] if shared else None,
                "next_period_start": shared["next_period_start"] if shared else None,
                "days_until_next_period": shared["days_until_next_period"] if shared else None,
                "confidence": shared["confidence"] if shared else None,
                "detail": shared["detail"] if shared else None,
                "support": support,
                # Deliberately no flow, symptoms, BBT, notes or period history.
            }
        people.append(entry)
    return {"viewer": viewer, "day": day, "people": people}


# --- summary ---------------------------------------------------------------


@app.get("/summary")
def summary(
    request: Request,
    day: str | None = None,
    session: Session = Depends(get_session),
):
    """One call for the Today screen.

    Composes sleep, readiness, check-in, nutrition, water and recent metrics.
    Activity is **not** here: geo owns steps and workouts, and the UI already
    has that number under a shared query key. Duplicating it here would create
    two answers to one question.
    """
    user = _user(request)
    day = _day(day or date.today().isoformat())
    profile = session.get(HealthProfile, user)
    profile_out = _profile_out(profile, user)

    defaults: list[str] = []
    sleep_need = profile.sleep_need_minutes if profile else None
    if sleep_need is None:
        sleep_need = DEFAULT_SLEEP_NEED
        defaults.append("sleep_need_minutes")
    water_goal = profile.water_goal_ml if profile else None
    if water_goal is None:
        water_goal = DEFAULT_WATER_GOAL
        defaults.append("water_goal_ml")

    night = _sleep_for_night(session, user, day)
    sleep_hist = session.exec(
        select(SleepSession)
        .where(SleepSession.user_id == user, SleepSession.night <= day)
        .order_by(SleepSession.night.desc())
        .limit(14)
    ).all()
    sleep = scoring.sleep_score(
        night.duration_minutes if night else None,
        sleep_need,
        night.efficiency if night else None,
        night.quality if night else None,
    )

    checkin = session.exec(
        select(Checkin).where(Checkin.user_id == user, Checkin.day == day)
    ).first()

    since_30 = (date.fromisoformat(day) - timedelta(days=30)).isoformat()
    rhr_series = _metric_series(session, user, "resting_hr", since_30)
    baseline_rhr = scoring.median([row.value for row in rhr_series])
    latest_rhr = _latest_metric(session, user, "resting_hr", day)
    latest = {
        metric: _sample_out(sample)
        for metric in ("weight", "body_fat", "resting_hr", "hrv", "spo2")
        if (sample := _latest_metric(session, user, metric, day)) is not None
    }

    ready = scoring.readiness(
        sleep,
        resting_hr=latest_rhr.value if latest_rhr else None,
        baseline_resting_hr=baseline_rhr,
        energy=checkin.energy if checkin else None,
        mood=checkin.mood if checkin else None,
        soreness=checkin.soreness if checkin else None,
        checkin_stress=checkin.stress if checkin else None,
    )

    water_rows = session.exec(
        select(MetricSample).where(
            MetricSample.user_id == user,
            MetricSample.metric == "water",
            MetricSample.recorded_at >= day,
            MetricSample.recorded_at < (date.fromisoformat(day) + timedelta(days=1)).isoformat(),
        )
    ).all()
    water = scoring.water_progress(
        sum(row.value for row in water_rows) if water_rows else None,
        water_goal,
    )

    nutrition = _nutrition_day(session, user, day, profile)

    alerts: list[str] = []
    if nutrition["target"]["kind"] == "unavailable":
        alerts.append(
            "Add " + ", ".join(nutrition["target"]["missing"] or ["profile details"]) + " to see a calorie range."
        )
    if night is None:
        alerts.append("No sleep logged for last night.")
    if checkin is None:
        alerts.append("Morning check-in not done.")

    return {
        "user_id": user,
        "day": day,
        "profile": profile_out,
        "defaults_used": defaults,
        "sleep": {
            "last_night": _sleep_out(night) if night else None,
            "score": {
                "score": sleep.score,
                "label": sleep.label,
                "detail": sleep.detail,
                "confidence": sleep.confidence,
                "missing": sleep.missing,
                "components": sleep.components,
            }
            if sleep
            else None,
            "need_minutes": sleep_need,
            "history": [_sleep_out(row) for row in sleep_hist],
        },
        "readiness": {
            "score": ready.score,
            "label": ready.label,
            "detail": ready.detail,
            "confidence": ready.confidence,
            "missing": ready.missing,
            "components": ready.components,
        }
        if ready
        else None,
        "checkin": _checkin_out(checkin) if checkin else None,
        "nutrition": nutrition,
        "water": {**water, "goal_ml": water_goal},
        "metrics": {key: value for key, value in latest.items()},
        "resting_hr_baseline": round(baseline_rhr, 1) if baseline_rhr is not None else None,
        "activity": None,
        # Owner-only and absent unless the person turned tracking on; never
        # part of the family projection, which only sees the explicit grant.
        "cycle": _cycle_summary(session, user, day),
        "alerts": alerts,
    }


def _week_days(session: Session, user: str, first: date, count: int, sleep_need: int) -> list[dict]:
    """One record per day from ``first``: only what was logged, ``None`` otherwise."""
    start, stop = first.isoformat(), (first + timedelta(days=count)).isoformat()
    sleeps = session.exec(
        select(SleepSession)
        .where(SleepSession.user_id == user, SleepSession.night >= start, SleepSession.night < stop)
        .order_by(SleepSession.created_at.desc())
    ).all()
    checkins = {
        row.day: row
        for row in session.exec(
            select(Checkin).where(Checkin.user_id == user, Checkin.day >= start, Checkin.day < stop)
        ).all()
    }
    calories: dict[str, float] = {}
    for row in session.exec(
        select(FoodEntry).where(FoodEntry.user_id == user, FoodEntry.day >= start, FoodEntry.day < stop)
    ).all():
        calories[row.day] = calories.get(row.day, 0.0) + row.calories * max(0.0, row.servings)
    water: dict[str, float] = {}
    for row in session.exec(
        select(MetricSample).where(
            MetricSample.user_id == user,
            MetricSample.metric == "water",
            MetricSample.recorded_at >= start,
            MetricSample.recorded_at < stop,
        )
    ).all():
        water[row.recorded_at[:10]] = water.get(row.recorded_at[:10], 0.0) + row.value

    days = []
    for offset in range(count):
        day = (first + timedelta(days=offset)).isoformat()
        night = _best_night([row for row in sleeps if row.night == day])
        score = (
            scoring.sleep_score(night.duration_minutes, sleep_need, night.efficiency, night.quality) if night else None
        )
        checkin = checkins.get(day)
        days.append({
            "day": day,
            "sleep_minutes": night.duration_minutes if night else None,
            "sleep_score": score.score if score else None,
            "calories": round(calories[day], 1) if day in calories else None,
            "water_ml": round(water[day], 1) if day in water else None,
            "energy": checkin.energy if checkin else None,
            "mood": checkin.mood if checkin else None,
        })
    return days


@app.get("/weekly")
def weekly_digest(
    request: Request,
    end: str | None = None,
    session: Session = Depends(get_session),
):
    """The seven days ending on ``end`` (today by default) against the seven before."""
    user = _user(request)
    last = date.fromisoformat(_day(end or date.today().isoformat(), field="end"))
    profile = session.get(HealthProfile, user)
    sleep_need = (profile.sleep_need_minutes if profile else None) or DEFAULT_SLEEP_NEED
    days = _week_days(session, user, last - timedelta(days=13), 14, sleep_need)
    return {
        "user_id": user,
        **weekly.digest(days[7:], days[:7], kid_mode=bool(profile.kid_mode) if profile else False),
    }


# --- weekly family challenges ----------------------------------------------


class ChallengeIn(BaseModel):
    kind: str
    # Any day in the challenge's week; the client knows the local date.
    day: str
    target_days: int = Field(ge=1, le=7)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in challenges.KINDS:
            raise ValueError(f"kind must be one of {', '.join(challenges.KINDS)}")
        return value


def _may_create_challenge(request: Request, session: Session, user: str) -> bool:
    """Only an adult starts a family challenge; anyone may join one.

    The gateway says whether the caller's family role is a minor's
    (``X-User-Is-Minor``); anything but an explicit ``false`` -- including
    Identity being unreachable -- is treated as a minor. A profile in kid mode
    or with a birth year under 18 is a minor here too. Admins may always.
    """
    if _admin(request):
        return True
    if (request.headers.get("X-User-Is-Minor") or "").strip().lower() != "false":
        return False
    profile = session.get(HealthProfile, user)
    if profile is None:
        return True
    age = _age_years(profile)
    return not profile.kid_mode and (age is None or age >= 18)


def _challenge_member_days(session: Session, user: str, start: date) -> tuple[list[dict], dict]:
    """The week's logged records for one member, plus the goals they are measured against."""
    profile = session.get(HealthProfile, user)
    sleep_need = profile.sleep_need_minutes if profile else None
    water_goal = profile.water_goal_ml if profile else None
    goals = {
        "sleep_need_minutes": sleep_need or DEFAULT_SLEEP_NEED,
        "water_goal_ml": water_goal or DEFAULT_WATER_GOAL,
        # Said out loud, like the summary's ``defaults_used``.
        "guideline_goals": [
            name
            for name, value in (("sleep_need_minutes", sleep_need), ("water_goal_ml", water_goal))
            if value is None
        ],
    }
    days = _week_days(session, user, start, 7, goals["sleep_need_minutes"])
    stop = (start + timedelta(days=7)).isoformat()
    recipe_dinners = {
        row.day
        for row in session.exec(
            select(FoodEntry).where(
                FoodEntry.user_id == user,
                FoodEntry.meal == "dinner",
                FoodEntry.recipe_path.is_not(None),  # type: ignore[union-attr]
                FoodEntry.day >= start.isoformat(),
                FoodEntry.day < stop,
            )
        ).all()
        if row.recipe_path
    }
    for record in days:
        record["recipe_dinner"] = record["day"] in recipe_dinners
    return days, goals


def _challenge_out(session: Session, row: FamilyChallenge, viewer: str) -> dict:
    start = date.fromisoformat(row.week_start)
    members = session.exec(
        select(ChallengeMember).where(ChallengeMember.challenge_id == row.id).order_by(ChallengeMember.joined_at.asc())
    ).all()
    scored = []
    for member in members:
        days, goals = _challenge_member_days(session, member.user_id, start)
        result = challenges.progress(
            row.kind,
            days,
            water_goal_ml=goals["water_goal_ml"],
            sleep_need_minutes=goals["sleep_need_minutes"],
        )
        relevant = {"water_days": "water_goal_ml", "sleep_nights": "sleep_need_minutes"}.get(row.kind)
        scored.append({
            "user_id": member.user_id,
            **result,
            "guideline_goal": relevant in goals["guideline_goals"] if relevant else False,
        })
    meta = challenges.KINDS[row.kind]
    return {
        "id": row.id,
        "kind": row.kind,
        "title": meta["title"],
        "unit": meta["unit"],
        "detail": meta["detail"],
        "week_start": row.week_start,
        "week_end": (start + timedelta(days=6)).isoformat(),
        "target_days": row.target_days,
        "created_by": row.created_by,
        "joined": any(member.user_id == viewer for member in members),
        "leaderboard": challenges.leaderboard(scored, row.target_days),
    }


def _challenge_or_404(session: Session, challenge_id: int) -> FamilyChallenge:
    row = session.get(FamilyChallenge, challenge_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return row


@app.get("/challenges")
def list_challenges(
    request: Request,
    day: str | None = None,
    session: Session = Depends(get_session),
):
    """The family's challenges for the week ``day`` falls in, with live progress.

    Only members' progress is computed, from what they logged; a person who
    has not joined is never read.
    """
    user = _user(request)
    start = challenges.week_start(date.fromisoformat(_day(day or date.today().isoformat())))
    rows = session.exec(
        select(FamilyChallenge)
        .where(FamilyChallenge.week_start == start.isoformat())
        .order_by(FamilyChallenge.created_at.asc())
    ).all()
    taken = {row.kind for row in rows}
    return {
        "week_start": start.isoformat(),
        "week_end": (start + timedelta(days=6)).isoformat(),
        "can_create": _may_create_challenge(request, session, user),
        "kinds": [{"kind": kind, **meta, "taken": kind in taken} for kind, meta in challenges.KINDS.items()],
        "challenges": [_challenge_out(session, row, user) for row in rows],
    }


@app.post("/challenges")
def create_challenge(body: ChallengeIn, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    if not _may_create_challenge(request, session, user):
        raise HTTPException(status_code=403, detail="A parent or another adult starts family challenges.")
    start = challenges.week_start(date.fromisoformat(_day(body.day)))
    existing = session.exec(
        select(FamilyChallenge).where(
            FamilyChallenge.kind == body.kind, FamilyChallenge.week_start == start.isoformat()
        )
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="That challenge is already running this week.")
    row = FamilyChallenge(kind=body.kind, week_start=start.isoformat(), target_days=body.target_days, created_by=user)
    session.add(row)
    session.commit()
    session.refresh(row)
    # Starting a challenge is joining it.
    session.add(ChallengeMember(challenge_id=row.id, user_id=user))
    session.commit()
    return _challenge_out(session, row, user)


@app.post("/challenges/{challenge_id}/join")
def join_challenge(challenge_id: int, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    row = _challenge_or_404(session, challenge_id)
    member = session.exec(
        select(ChallengeMember).where(ChallengeMember.challenge_id == challenge_id, ChallengeMember.user_id == user)
    ).first()
    if member is None:
        session.add(ChallengeMember(challenge_id=challenge_id, user_id=user))
        session.commit()
    return _challenge_out(session, row, user)


@app.delete("/challenges/{challenge_id}/join")
def leave_challenge(challenge_id: int, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    row = _challenge_or_404(session, challenge_id)
    member = session.exec(
        select(ChallengeMember).where(ChallengeMember.challenge_id == challenge_id, ChallengeMember.user_id == user)
    ).first()
    if member is not None:
        session.delete(member)
        session.commit()
    return _challenge_out(session, row, user)


@app.delete("/challenges/{challenge_id}")
def delete_challenge(challenge_id: int, request: Request, session: Session = Depends(get_session)):
    user = _user(request)
    row = _challenge_or_404(session, challenge_id)
    if row.created_by != user and not _admin(request):
        raise HTTPException(status_code=403, detail="Only whoever started the challenge can end it.")
    for member in session.exec(select(ChallengeMember).where(ChallengeMember.challenge_id == challenge_id)).all():
        session.delete(member)
    session.delete(row)
    session.commit()
    return {"deleted": challenge_id}


# --- export ----------------------------------------------------------------


@app.get("/export")
def export_data(request: Request, session: Session = Depends(get_session)):
    """Everything this service holds for the caller, for a backup or a move."""
    user = _user(request)
    return {
        "exported_at": utcnow(),
        "user_id": user,
        "profile": _profile_out(session.get(HealthProfile, user), user),
        "metrics": [
            _sample_out(row)
            for row in session.exec(
                select(MetricSample).where(MetricSample.user_id == user).order_by(MetricSample.recorded_at.asc())
            ).all()
        ],
        "sleep": [
            _sleep_out(row)
            for row in session.exec(
                select(SleepSession).where(SleepSession.user_id == user).order_by(SleepSession.night.asc())
            ).all()
        ],
        "checkins": [
            _checkin_out(row)
            for row in session.exec(
                select(Checkin).where(Checkin.user_id == user).order_by(Checkin.day.asc())
            ).all()
        ],
        "food": [
            _food_out(row)
            for row in session.exec(
                select(FoodEntry).where(FoodEntry.user_id == user).order_by(FoodEntry.day.asc())
            ).all()
        ],
        "cycle": {
            "settings": (
                {
                    "enabled": settings.enabled,
                    "average_cycle_length": settings.average_cycle_length,
                    "average_period_length": settings.average_period_length,
                    "guardian_username": settings.guardian_username,
                }
                if (settings := _cycle_settings(session, user)) is not None
                else None
            ),
            "periods": [_cycle_period_out(row) for row in _cycle_periods(session, user)],
            "logs": [
                _cycle_log_out(row)
                for row in session.exec(
                    select(CycleLog).where(CycleLog.user_id == user).order_by(CycleLog.day.asc())
                ).all()
            ],
        },
        "shares": [
            {"viewer": row.viewer, "scope": row.scope}
            for row in session.exec(select(HealthShare).where(HealthShare.owner == user)).all()
        ],
        "challenges_joined": [
            {"challenge_id": row.challenge_id, "joined_at": row.joined_at}
            for row in session.exec(select(ChallengeMember).where(ChallengeMember.user_id == user)).all()
        ],
        "notes": {
            "activity": "Steps and workouts live in geo; request /api/geo/steps and /api/geo/workouts.",
            "scores": "Sleep and readiness scores are derived; the raw readings are included above.",
            "cycle": "Cycle data is included because this is the owner's own export.",
        },
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "health",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "database": "sqlite" if _database_url().startswith("sqlite") else "other",
    }
