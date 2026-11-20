"""A garden is sized by the household roster, not by a hardcoded seven.

The number is Identity's (``GET /api/internal/household``); Garden asks for it
and keeps only one fallback for the case where Identity cannot be reached.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

ROOT = Path(__file__).resolve().parents[3]

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden import household  # noqa: E402
from services.garden.main import app  # noqa: E402
from services.garden.models import GardenPlan, GardenProfile  # noqa: E402
from scripts.build_plants_seed import build_seed  # noqa: E402

ADMIN_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "mother",
    "X-User-Role": "mother",
    "X-Is-Admin": "true",
    "X-Family-Manage": "true",
}


@pytest.fixture()
def tmp_seed_path(tmp_path):
    seed_path = tmp_path / "plants-seed.db"
    build_seed(seed_path)
    return str(seed_path)


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'garden.db'}"


@pytest.fixture()
def client(tmp_seed_path, db_url, monkeypatch):
    import services.garden.main as main_module

    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", db_url)
    monkeypatch.setenv("PLANT_SEED_PATH", tmp_seed_path)
    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    main_module.app.state.engine = engine
    monkeypatch.setattr(household, "_cache", None)
    return TestClient(app)


@pytest.fixture()
def session(client):
    import services.garden.main as main_module

    with Session(main_module.app.state.engine) as db:
        yield db


@pytest.fixture()
def roster(monkeypatch):
    """Identity reports a five-person household."""
    async def five():
        return 5

    monkeypatch.setattr(household, "size", five)
    monkeypatch.setattr(household, "size_or_last_resort", five)
    return 5


def test_a_new_profile_takes_the_household_size(client, roster, session):
    body = client.get("/profile", headers=ADMIN_HEADERS).json()

    assert body["family_size"] == 5
    assert session.exec(select(GardenProfile)).first().family_size == 5


def test_detect_zone_also_takes_the_household_size(client, roster, session, monkeypatch):
    async def no_zone(*_a, **_k):
        return None, None

    import services.garden.main as main_module

    monkeypatch.setattr(main_module, "detect_zone_for_location", no_zone)

    client.post("/profile/detect-zone", headers=ADMIN_HEADERS)

    assert session.exec(select(GardenProfile)).first().family_size == 5


def test_an_explicit_size_still_wins(client, roster):
    client.put("/profile", headers=ADMIN_HEADERS, json={"family_size": 3})
    body = client.put("/profile", headers=ADMIN_HEADERS, json={"family_size": 9}).json()

    assert body["family_size"] == 9


def test_a_new_plan_takes_the_household_size(client, roster, session):
    resp = client.post(
        "/plans",
        headers=ADMIN_HEADERS,
        json={"name": "Spring 2027", "season": "spring_summer", "year": 2027},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["family_size"] == 5
    assert session.exec(select(GardenPlan)).first().family_size == 5


def test_planner_sizing_uses_the_roster_when_there_is_no_profile(client, roster, session):
    assert session.exec(select(GardenProfile)).first() is None

    body = client.get("/planner/sizing", headers=ADMIN_HEADERS).json()

    assert body["family_size"] == 5


def test_identity_away_falls_back_to_one_documented_number(client, monkeypatch):
    """Only one place knows the fallback; it is not repeated at each call site."""

    async def away():
        return None

    monkeypatch.setattr(household, "size", away)

    body = client.get("/profile", headers=ADMIN_HEADERS).json()

    assert body["family_size"] == household.LAST_RESORT
