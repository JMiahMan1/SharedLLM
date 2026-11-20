"""Phase 4 backend tests for Jarvis Garden harvest log & recipes bridge.

Covers: herb prune_pinch auto-seeding, POST /plants/{id}/harvest (all roles),
GET /harvests totals, regrowth cooldown, GET /today ready_to_harvest,
GET /streaks harvests_total (first_harvest achievement), DELETE /harvests/{id}.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden.main import app  # noqa: E402
from services.garden.models import (  # noqa: E402
    GardenProfile,
    HarvestLog,
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
TEEN_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "teen",
    "X-User-Role": "teen",
    "X-Is-Admin": "false",
    "X-Family-Manage": "false",
}


@pytest.fixture()
def tmp_seed_path(tmp_path):
    seed_path = tmp_path / "plants-seed.db"
    build_seed(seed_path)
    return str(seed_path)


@pytest.fixture()
def db_url(tmp_path):
    db_path = tmp_path / "garden.db"
    return f"sqlite:///{db_path}"


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

    return TestClient(app)


def _make_location(client, headers):
    resp = client.post("/locations", headers=headers, json={"name": "Kitchen", "kind": "herb_station"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _make_plant(client, headers, location_id, category="herb", species_ref="basil_sweet"):
    resp = client.post(
        "/plants",
        headers=headers,
        json={
            "category": category,
            "species_ref": species_ref,
            "location_id": location_id,
            "nickname": "Sir Basil",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --------------------------------------------------------------------------- cadence: herb prune_pinch


def test_herb_auto_seeds_prune_pinch(client):
    """Adding an herb plant auto-seeds a prune_pinch care rule."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.get(f"/plants/{plant_id}/care-rules", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    rules = resp.json()["rules"]
    actions = [r["action"] for r in rules]
    assert "prune_pinch" in actions, f"Expected prune_pinch rule, got {actions}"


def test_houseplant_no_prune_pinch(client):
    """Houseplants do NOT get a prune_pinch rule."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "houseplant", "monstera_deliciosa")
    resp = client.get(f"/plants/{plant_id}/care-rules", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    rules = resp.json()["rules"]
    actions = [r["action"] for r in rules]
    assert "prune_pinch" not in actions


# --------------------------------------------------------------------------- POST /plants/{id}/harvest


def test_harvest_child_role_allowed(client):
    """All family roles (including child) can log a harvest."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.post(
        f"/plants/{plant_id}/harvest",
        headers=KID_HEADERS,
        json={"quantity": 2.0, "unit": "bunch", "disposition": "fresh", "notes": "Snipped for pesto"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["quantity"] == 2.0
    assert data["unit"] == "bunch"
    assert data["disposition"] == "fresh"
    assert data["plant_id"] == plant_id


def test_harvest_teen_role_allowed(client):
    """Teen can log a harvest."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "vegetable", "tomato_roma")
    resp = client.post(
        f"/plants/{plant_id}/harvest",
        headers=TEEN_HEADERS,
        json={"quantity": 3.5, "unit": "lb", "disposition": "canned"},
    )
    assert resp.status_code == 201, resp.text


def test_harvest_updates_last_harvested_at(client):
    """Harvest updates plant.last_harvested_at."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    client.post(f"/plants/{plant_id}/harvest", headers=ADMIN_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"})
    resp = client.get("/plants", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    plants = resp.json()
    plant = next((p for p in plants if p["id"] == plant_id), None)
    assert plant is not None
    assert plant["last_harvested_at"] is not None


def test_harvest_invalid_disposition(client):
    """Invalid disposition returns 422."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.post(
        f"/plants/{plant_id}/harvest",
        headers=ADMIN_HEADERS,
        json={"quantity": 1.0, "unit": "bunch", "disposition": "invalid"},
    )
    assert resp.status_code == 422


def test_harvest_not_found(client):
    resp = client.post(
        "/plants/does-not-exist/harvest",
        headers=ADMIN_HEADERS,
        json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"},
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------- GET /harvests


def test_harvests_list(client):
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    client.post(f"/plants/{plant_id}/harvest", headers=ADMIN_HEADERS, json={"quantity": 2.0, "unit": "bunch", "disposition": "fresh"})
    client.post(f"/plants/{plant_id}/harvest", headers=ADMIN_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "dried"})
    resp = client.get("/harvests", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert len(data["harvests"]) == 2
    assert data["totals"][0]["count"] == 2
    assert data["totals"][0]["total_quantity"] == 3.0


def test_harvests_filter_by_plant(client):
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    plant2_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "parsley")
    client.post(f"/plants/{plant_id}/harvest", headers=ADMIN_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"})
    resp = client.get("/harvests", headers=ADMIN_HEADERS, params={"plant_id": plant2_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0


def test_harvests_filter_by_disposition(client):
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    client.post(f"/plants/{plant_id}/harvest", headers=ADMIN_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"})
    client.post(f"/plants/{plant_id}/harvest", headers=ADMIN_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "dried"})
    resp = client.get("/harvests", headers=ADMIN_HEADERS, params={"disposition": "dried"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["harvests"][0]["disposition"] == "dried"


# --------------------------------------------------------------------------- DELETE /harvests/{id}


def test_delete_harvest_owner(client):
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.post(f"/plants/{plant_id}/harvest", headers=KID_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"})
    harvest_id = resp.json()["harvest_id"]
    # Owner (child) can delete their own harvest.
    del_resp = client.delete(f"/harvests/{harvest_id}", headers=KID_HEADERS)
    assert del_resp.status_code == 204


def test_delete_harvest_admin(client):
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.post(f"/plants/{plant_id}/harvest", headers=KID_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"})
    harvest_id = resp.json()["harvest_id"]
    # Adult can delete anyone's harvest.
    del_resp = client.delete(f"/harvests/{harvest_id}", headers=ADMIN_HEADERS)
    assert del_resp.status_code == 204


def test_delete_harvest_not_owner_forbidden(client):
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.post(f"/plants/{plant_id}/harvest", headers=KID_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"})
    harvest_id = resp.json()["harvest_id"]
    # Non-owner minor cannot delete.
    del_resp = client.delete(f"/harvests/{harvest_id}", headers=TEEN_HEADERS)
    assert del_resp.status_code == 403


def test_delete_harvest_not_found(client):
    resp = client.delete("/harvests/does-not-exist", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- GET /harvest/ready


def test_harvest_ready_herb_not_in_cooldown(client):
    """Active herb not in cooldown is harvest-ready."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.get("/harvest/ready", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    ready_ids = [r["plant_id"] for r in data["ready"]]
    assert plant_id in ready_ids


def test_harvest_ready_herb_in_cooldown(client):
    """Herb in regrowth cooldown is NOT harvest-ready."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    # Harvest once to set last_harvested_at + cooldown.
    client.post(f"/plants/{plant_id}/harvest", headers=ADMIN_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"})
    resp = client.get("/harvest/ready", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    ready_ids = [r["plant_id"] for r in data["ready"]]
    assert plant_id not in ready_ids, f"Herb should be in cooldown, but is ready: {ready_ids}"


def test_harvest_ready_culinary_aliases(client):
    """Ready items include culinary_aliases."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.get("/harvest/ready", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    basil = next((r for r in data["ready"] if r["plant_id"] == plant_id), None)
    assert basil is not None
    assert "basil" in basil["culinary_aliases"]


# --------------------------------------------------------------------------- GET /today ready_to_harvest


def test_today_includes_ready_to_harvest(client):
    """GET /today includes ready_to_harvest summary."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    resp = client.get("/today", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "ready_to_harvest" in data
    assert isinstance(data["ready_to_harvest"], list)


# --------------------------------------------------------------------------- GET /streaks harvests_total


def test_streaks_harvests_total_unlocks_first_harvest(client):
    """Logging a harvest unlocks first_harvest achievement in /streaks."""
    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    # No harvests yet -> no first_harvest.
    resp = client.get("/streaks", headers=KID_HEADERS)
    assert resp.status_code == 200
    before = resp.json()
    earned_ids = {a["id"] for a in before.get("earned", [])}
    assert "first_harvest" not in earned_ids

    # Log a harvest.
    client.post(f"/plants/{plant_id}/harvest", headers=KID_HEADERS, json={"quantity": 1.0, "unit": "bunch", "disposition": "fresh"})

    # Now first_harvest should be earned.
    resp = client.get("/streaks", headers=KID_HEADERS)
    assert resp.status_code == 200
    after = resp.json()
    earned_ids = {a["id"] for a in after.get("earned", [])}
    assert "first_harvest" in earned_ids, f"first_harvest not earned after harvest, got {earned_ids}"


# --------------------------------------------------------------------------- models: HarvestLog persisted


def test_harvest_log_persisted_in_db(client, db_url, tmp_seed_path):
    """HarvestLog row is written to the database."""
    import services.garden.main as main_module
    from sqlmodel import Session, select

    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("GARDEN_DATABASE_URL", db_url)
    monkeypatch.setenv("PLANT_SEED_PATH", tmp_seed_path)
    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)

    loc_id = _make_location(client, ADMIN_HEADERS)
    plant_id = _make_plant(client, ADMIN_HEADERS, loc_id, "herb", "basil_sweet")
    client.post(f"/plants/{plant_id}/harvest", headers=ADMIN_HEADERS, json={"quantity": 2.0, "unit": "bunch", "disposition": "fresh", "notes": "test"})

    with Session(engine) as session:
        logs = session.exec(select(HarvestLog)).all()
        assert len(logs) == 1
        assert logs[0].quantity == 2.0
        assert logs[0].unit == "bunch"
        assert logs[0].disposition == "fresh"
        assert logs[0].notes == "test"
        assert logs[0].plant_id == plant_id
    monkeypatch.undo()
