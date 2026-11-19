"""API tests for the Garden service (services/garden/main.py).

Uses a temporary SQLite database and a temporary seed database built from the
real catalog JSON files.  Tests run fast and exercise the full endpoint surface
plus role-based permissions.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden.main import app  # noqa: E402
from services.garden.models import (  # noqa: E402
    CareLog,
    CareRule,
    GardenProfile,
    Location,
    Plant,
)
from scripts.build_plants_seed import build_seed  # noqa: E402

ADMIN_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "mother",
    "X-User-Role": "mother",
    "X-Is-Admin": "true",
    "X-Family-Manage": "true",
}
KID_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "child",
    "X-User-Role": "child",
    "X-Is-Admin": "false",
    "X-Family-Manage": "false",
}


@pytest.fixture()
def tmp_seed_path(tmp_path):
    """Build a temporary botanical seed from the real catalog JSON."""
    seed_path = tmp_path / "plants-seed.db"
    build_seed(seed_path)
    return str(seed_path)


@pytest.fixture()
def db_url(tmp_path):
    """A temporary garden database URL."""
    db_path = tmp_path / "garden.db"
    return f"sqlite:///{db_path}"


@pytest.fixture()
def client(tmp_seed_path, db_url, monkeypatch):
    """TestClient with the garden service pointed at temp DB and seed."""
    import services.garden.main as main_module
    from sqlmodel import SQLModel

    # Reset cached engine/seed so each test gets a fresh DB at its own URL.
    main_module.app.state.engine = None
    main_module._SEED = None

    monkeypatch.setenv("GARDEN_DATABASE_URL", db_url)
    monkeypatch.setenv("PLANT_SEED_PATH", tmp_seed_path)

    # Create tables explicitly so they exist before lifespan runs.
    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    # Store the engine so lifespan's _db() reuses it (same DB file).
    main_module.app.state.engine = engine

    return TestClient(app)


def _headers(role="mother", admin=True, family_manage=True):
    return {
        "X-Internal-Secret": "test-secret",
        "X-User-Id": role,
        "X-User-Role": role,
        "X-Is-Admin": "true" if admin else "false",
        "X-Family-Manage": "true" if family_manage else "false",
    }


# --------------------------------------------------------------------------- health


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "garden"


def test_health_without_internal_secret(client):
    resp = client.get("/health", headers={"X-Internal-Secret": "wrong"})
    # /health is exempt from the internal-secret check.
    assert resp.status_code == 200


# --------------------------------------------------------------------------- species


def test_species_search(client):
    resp = client.get("/species/search", headers=ADMIN_HEADERS, params={"q": "basil", "category": "herb"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] > 0
    assert all(r["category"] == "herb" for r in data["results"])
    assert data["seed_present"] is True


def test_species_search_no_seed_degrades(monkeypatch, tmp_path, db_url):
    import services.garden.main as main_module

    main_module.DEFAULT_DATABASE_URL = db_url
    main_module.DEFAULT_SEED_PATH = str(tmp_path / "nonexistent.db")
    main_module.app.state.engine = None
    main_module._SEED = None
    c = TestClient(app)
    resp = c.get("/species/search", headers=ADMIN_HEADERS, params={"q": "basil"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["seed_present"] is False


def test_species_get(client):
    resp = client.get("/species/basil_sweet", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "basil_sweet"
    assert data["species"]["common_name"] == "Sweet Basil"


def test_species_get_not_found(client):
    resp = client.get("/species/no_such_plant", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


def test_species_list_category(client):
    resp = client.get("/species", headers=ADMIN_HEADERS, params={"category": "houseplant", "limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 5
    assert all(r["category"] == "houseplant" for r in data["results"])


# --------------------------------------------------------------------------- profile


def test_profile_get_creates_default(client):
    resp = client.get("/profile", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "household"
    assert data["family_size"] == 7


def test_profile_put_admin(client):
    resp = client.put("/profile", headers=ADMIN_HEADERS, json={"family_size": 4})
    assert resp.status_code == 200
    assert resp.json()["family_size"] == 4


def test_profile_put_forbidden_for_kid(client):
    resp = client.put("/profile", headers=KID_HEADERS, json={"family_size": 4})
    assert resp.status_code == 403


# --------------------------------------------------------------------------- locations


def test_location_crud(client):
    # Create.
    resp = client.post(
        "/locations", headers=ADMIN_HEADERS,
        json={"name": "Living Room", "kind": "indoor_room", "light_notes": "bright east window"},
    )
    assert resp.status_code == 201
    loc = resp.json()
    assert loc["name"] == "Living Room"
    loc_id = loc["id"]

    # List.
    resp = client.get("/locations", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    # Patch.
    resp = client.patch(
        f"/locations/{loc_id}", headers=ADMIN_HEADERS,
        json={"light_notes": "very bright east window"},
    )
    assert resp.status_code == 200
    assert resp.json()["light_notes"] == "very bright east window"

    # Delete (no plants assigned yet).
    resp = client.delete(f"/locations/{loc_id}", headers=ADMIN_HEADERS)
    assert resp.status_code == 204


def test_location_create_forbidden_for_kid(client):
    resp = client.post(
        "/locations", headers=KID_HEADERS,
        json={"name": "Kids Room", "kind": "indoor_room"},
    )
    assert resp.status_code == 403


def test_location_delete_refused_with_plants(client):
    # Create a location.
    resp = client.post(
        "/locations", headers=ADMIN_HEADERS,
        json={"name": "Bedroom", "kind": "indoor_room"},
    )
    loc_id = resp.json()["id"]

    # Create a plant in it.
    resp = client.post(
        "/plants", headers=ADMIN_HEADERS,
        json={"category": "houseplant", "species_ref": "sansevieria_trifasciata", "location_id": loc_id},
    )
    assert resp.status_code == 201

    # Delete should be refused.
    resp = client.delete(f"/locations/{loc_id}", headers=ADMIN_HEADERS)
    assert resp.status_code == 409


# --------------------------------------------------------------------------- plants


def test_plant_crud(client):
    # Create a location first.
    resp = client.post(
        "/locations", headers=ADMIN_HEADERS,
        json={"name": "Living Room", "kind": "indoor_room"},
    )
    loc_id = resp.json()["id"]

    # Create a plant.
    resp = client.post(
        "/plants", headers=ADMIN_HEADERS,
        json={
            "category": "houseplant",
            "species_ref": "sansevieria_trifasciata",
            "location_id": loc_id,
            "nickname": "Monty",
            "pot_size_in": 8,
            "pot_material": "terracotta",
            "has_drainage": True,
            "window_direction": "e",
            "distance_from_window": 3.0,
        },
    )
    assert resp.status_code == 201
    plant = resp.json()
    assert plant["nickname"] == "Monty"
    assert plant["species"]["common_name"] == "Snake Plant (Mother-in-Law's Tongue)"
    plant_id = plant["id"]

    # List plants.
    resp = client.get("/plants", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    # Patch plant.
    resp = client.patch(
        f"/plants/{plant_id}", headers=ADMIN_HEADERS,
        json={"nickname": "Monty Jr"},
    )
    assert resp.status_code == 200
    assert resp.json()["nickname"] == "Monty Jr"

    # Delete plant.
    resp = client.delete(f"/plants/{plant_id}", headers=ADMIN_HEADERS)
    assert resp.status_code == 204


def test_plant_create_forbidden_for_kid(client):
    resp = client.post(
        "/plants", headers=KID_HEADERS,
        json={"category": "houseplant", "species_ref": "sansevieria_trifasciata"},
    )
    assert resp.status_code == 403


def test_plant_create_unknown_species(client):
    # Graceful degradation: unknown species still creates a plant (201)
    # when the seed is absent or the key isn't found.
    resp = client.post(
        "/plants", headers=ADMIN_HEADERS,
        json={"category": "houseplant", "species_ref": "no_such_plant"},
    )
    assert resp.status_code == 201


def test_plant_auto_seeds_care_rules(client):
    resp = client.post(
        "/locations", headers=ADMIN_HEADERS,
        json={"name": "Living Room", "kind": "indoor_room"},
    )
    loc_id = resp.json()["id"]
    resp = client.post(
        "/plants", headers=ADMIN_HEADERS,
        json={
            "category": "houseplant",
            "species_ref": "sansevieria_trifasciata",
            "location_id": loc_id,
        },
    )
    plant_id = resp.json()["id"]

    resp = client.get(f"/plants/{plant_id}/care-rules", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    rules = resp.json()["rules"]
    actions = {r["action"] for r in rules}
    assert "water" in actions
    assert "rotate" in actions


# --------------------------------------------------------------------------- today


def test_today_endpoint(client):
    resp = client.get("/today", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "date" in data
    assert "due_count" in data
    assert "by_room" in data
    assert "all" in data


# --------------------------------------------------------------------------- care log


def test_log_care(client):
    resp = client.post(
        "/locations", headers=ADMIN_HEADERS,
        json={"name": "Living Room", "kind": "indoor_room"},
    )
    loc_id = resp.json()["id"]
    resp = client.post(
        "/plants", headers=ADMIN_HEADERS,
        json={
            "category": "houseplant",
            "species_ref": "sansevieria_trifasciata",
            "location_id": loc_id,
            "nickname": "Monty",
        },
    )
    plant_id = resp.json()["id"]

    resp = client.post(
        f"/plants/{plant_id}/care", headers=KID_HEADERS,
        json={"action": "water", "performed_by": "child", "source": "manual", "notes": "All good"},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "water"
    assert resp.json()["logged_by"] == "child"

    # Kids can log care.
    resp = client.get(f"/plants/{plant_id}/logs", headers=KID_HEADERS)
    assert resp.status_code == 200
    logs = resp.json()["logs"]
    assert len(logs) >= 1
    assert logs[0]["action"] == "water"


def test_log_care_nonexistent_plant(client):
    resp = client.post(
        "/plants/no-such-id/care", headers=ADMIN_HEADERS,
        json={"action": "water"},
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- snooze


def test_snooze_still_damp(client):
    resp = client.post(
        "/locations", headers=ADMIN_HEADERS,
        json={"name": "Living Room", "kind": "indoor_room"},
    )
    loc_id = resp.json()["id"]
    resp = client.post(
        "/plants", headers=ADMIN_HEADERS,
        json={
            "category": "houseplant",
            "species_ref": "sansevieria_trifasciata",
            "location_id": loc_id,
            "nickname": "Monty",
        },
    )
    plant_id = resp.json()["id"]

    resp = client.post(
        f"/plants/{plant_id}/snooze",
        headers=KID_HEADERS,
        params={"still_damp": "true", "postpone_days": 2},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["still_damp"] is True
    assert data["calibration_factor"] > 1.0  # nudged upward


# --------------------------------------------------------------------------- water-all


def test_water_all(client):
    resp = client.post(
        "/locations", headers=ADMIN_HEADERS,
        json={"name": "Living Room", "kind": "indoor_room"},
    )
    loc_id = resp.json()["id"]
    resp = client.post(
        "/plants", headers=ADMIN_HEADERS,
        json={
            "category": "houseplant",
            "species_ref": "sansevieria_trifasciata",
            "location_id": loc_id,
            "nickname": "Monty",
        },
    )
    plant_id = resp.json()["id"]

    resp = client.post(f"/locations/{loc_id}/water-all", headers=ADMIN_HEADERS, params={"due_only": "false"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["watered_count"] == 1
    assert data["location_name"] == "Living Room"


# --------------------------------------------------------------------------- auth enforcement


def test_missing_internal_secret_rejected(client):
    resp = client.get("/plants", headers={"X-User-Id": "mother"})
    assert resp.status_code == 403


def test_kid_can_log_care_but_not_create_plant(client):
    # Kid can log care (needs a plant first, created by admin).
    resp = client.post(
        "/locations", headers=ADMIN_HEADERS,
        json={"name": "Living Room", "kind": "indoor_room"},
    )
    loc_id = resp.json()["id"]
    resp = client.post(
        "/plants", headers=ADMIN_HEADERS,
        json={
            "category": "houseplant",
            "species_ref": "sansevieria_trifasciata",
            "location_id": loc_id,
        },
    )
    plant_id = resp.json()["id"]

    # Kid can log care.
    resp = client.post(
        f"/plants/{plant_id}/care", headers=KID_HEADERS,
        json={"action": "water"},
    )
    assert resp.status_code == 200

    # Kid cannot create a plant.
    resp = client.post(
        "/plants", headers=KID_HEADERS,
        json={"category": "houseplant", "species_ref": "sansevieria_trifasciata"},
    )
    assert resp.status_code == 403
