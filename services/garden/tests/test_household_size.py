"""A garden is sized by the household roster, never by a stored or invented number.

The number is Identity's (``GET /api/internal/household``). Garden stores only an
explicit override a person typed; reading the profile stores nothing, and with
Identity away and no override the planner answers 503 instead of guessing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

ROOT = Path(__file__).resolve().parents[3]

from scripts.build_plants_seed import build_seed  # noqa: E402
from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden import household  # noqa: E402
from services.garden.main import app, migrate_family_size  # noqa: E402
from services.garden.models import GardenPlan, GardenProfile  # noqa: E402

ADMIN_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "parent_one",
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
    members = {"count": 5}

    async def size():
        return members["count"]

    monkeypatch.setattr(household, "size", size)
    return members


@pytest.fixture()
def identity_away(monkeypatch):
    async def away():
        return None

    monkeypatch.setattr(household, "size", away)


def test_reading_the_profile_stores_no_family_size(client, roster, session):
    body = client.get("/profile", headers=ADMIN_HEADERS).json()

    assert body["family_size"] == 5
    assert body["family_size_source"] == "identity"
    assert body["family_size_override"] is None
    assert session.exec(select(GardenProfile)).first() is None


def test_a_roster_change_shows_at_once(client, roster):
    assert client.get("/profile", headers=ADMIN_HEADERS).json()["family_size"] == 5
    roster["count"] = 6
    assert client.get("/profile", headers=ADMIN_HEADERS).json()["family_size"] == 6


def test_detect_zone_stores_no_family_size(client, roster, session, monkeypatch):
    async def no_zone(*_a, **_k):
        return None, None

    import services.garden.main as main_module

    monkeypatch.setattr(main_module, "detect_zone_for_location", no_zone)

    client.post("/profile/detect-zone", headers=ADMIN_HEADERS)

    profile = session.exec(select(GardenProfile)).first()
    assert profile is None or profile.family_size_override is None


def test_an_explicit_size_wins_and_null_clears_it(client, roster, session):
    client.put("/profile", headers=ADMIN_HEADERS, json={"family_size": 3})
    body = client.put("/profile", headers=ADMIN_HEADERS, json={"family_size": 9}).json()

    assert (body["family_size"], body["family_size_source"]) == (9, "override")
    session.expire_all()
    assert session.exec(select(GardenProfile)).first().family_size_override == 9

    cleared = client.put("/profile", headers=ADMIN_HEADERS, json={"family_size": None}).json()
    assert (cleared["family_size"], cleared["family_size_source"]) == (5, "identity")


def test_an_edit_to_another_field_keeps_following_identity(client, roster, session):
    client.put("/profile", headers=ADMIN_HEADERS, json={"units": "metric"})
    roster["count"] = 4

    body = client.get("/profile", headers=ADMIN_HEADERS).json()

    assert body["family_size"] == 4
    session.expire_all()
    assert session.exec(select(GardenProfile)).first().family_size_override is None


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


def test_identity_away_is_said_not_guessed(client, identity_away):
    body = client.get("/profile", headers=ADMIN_HEADERS).json()

    assert body["family_size"] is None
    assert body["family_size_source"] == "unavailable"
    assert "Identity" in body["family_size_detail"]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    (
        ("GET", "/planner/sizing", None),
        ("POST", "/plans", {"name": "Fall", "season": "fall", "year": 2027}),
    ),
)
def test_identity_away_and_no_override_is_a_503(client, identity_away, method, path, payload):
    resp = client.request(method, path, headers=ADMIN_HEADERS, json=payload)

    assert resp.status_code == 503
    assert "Identity" in resp.json()["detail"]


def test_identity_away_with_an_override_still_plans(client, identity_away):
    client.put("/profile", headers=ADMIN_HEADERS, json={"family_size": 4})

    assert client.get("/planner/sizing", headers=ADMIN_HEADERS).json()["family_size"] == 4


def test_an_explicit_size_in_the_request_needs_no_identity(client, identity_away):
    body = client.get("/planner/sizing", headers=ADMIN_HEADERS, params={"family_size": 2}).json()

    assert body["family_size"] == 2


# --------------------------------------------------------------------------- migrating stored sizes

def _legacy_profile(session, *, family_size, edited):
    row = GardenProfile(user_id="household", family_size=family_size)
    if edited:
        row.updated_at = "2099-01-01T00:00:00+00:00"
    else:
        row.updated_at = row.created_at
    session.add(row)
    session.commit()


def test_a_copied_size_on_a_never_edited_profile_is_dropped(client, roster, session):
    import services.garden.main as main_module

    _legacy_profile(session, family_size=7, edited=False)

    assert migrate_family_size(main_module.app.state.engine) == "dropped"

    session.expire_all()
    row = session.get(GardenProfile, "household")
    assert (row.family_size, row.family_size_override) == (0, None)
    assert client.get("/profile", headers=ADMIN_HEADERS).json()["family_size"] == 5


def test_a_size_on_an_edited_profile_is_kept_as_an_override(client, roster, session):
    """Nothing recorded which fields an edit touched, so an edited size is kept."""
    import services.garden.main as main_module

    _legacy_profile(session, family_size=6, edited=True)

    assert migrate_family_size(main_module.app.state.engine) == "kept"

    session.expire_all()
    assert session.get(GardenProfile, "household").family_size_override == 6
    assert migrate_family_size(main_module.app.state.engine) is None  # once only
