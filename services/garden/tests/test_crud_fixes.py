"""The CRUD and partial-edit fixes from the Phase 0-8 audit.

Each of these was a 422/500 that a real button produced: "Activate" on a plan,
an edit of one pantry field, clearing a sensor from a binding, a care rule with a
cadence that cannot work, a location carrying a Skylight chore, and a PNG
thumbnail saved as `.jpg`.
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden.main import app  # noqa: E402
from scripts.build_plants_seed import build_seed  # noqa: E402

PARENT = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "mother",
    "X-User-Role": "mother",
    "X-Family-Manage": "true",
}

#: A 1x1 PNG, and the JPEG the journal also accepts.
TINY_PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
).decode()
TINY_JPEG = base64.b64encode(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300ffd9")).decode()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    seed = tmp_path / "plants-seed.db"
    build_seed(seed)
    import services.garden.main as main_module

    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path / 'garden.db'}")
    monkeypatch.setenv("PLANT_SEED_PATH", str(seed))
    monkeypatch.setenv("GARDEN_THUMB_DIR", str(tmp_path / "thumbs"))
    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    main_module.app.state.engine = engine
    return TestClient(app)


@pytest.fixture()
def session(client):
    import services.garden.main as main_module

    with Session(main_module.app.state.engine) as db:
        yield db


def _plant(client, **extra) -> dict:
    body = {"nickname": "Monty", "species_ref": "monstera_deliciosa", "category": "houseplant", **extra}
    resp = client.post("/plants", headers=PARENT, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- plans and goals

def test_activate_sends_only_a_status_and_is_accepted(client):
    plan = client.post(
        "/plans", headers=PARENT, json={"name": "2027 Garden", "season": "spring_summer", "family_size": 4}
    ).json()

    resp = client.patch(f"/plans/{plan['id']}", headers=PARENT, json={"status": "active"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    # The other columns are untouched, not blanked.
    assert resp.json()["name"] == "2027 Garden"
    assert resp.json()["season"] == "spring_summer"


def test_one_pantry_field_can_be_edited_alone(client):
    goal = client.post(
        "/pantry-goals",
        headers=PARENT,
        json={"item": "Marinara sauce", "unit": "quarts", "method": "water_bath_canning", "target_qty": 40},
    ).json()

    resp = client.patch(f"/pantry-goals/{goal['id']}", headers=PARENT, json={"target_qty": 60})

    assert resp.status_code == 200, resp.text
    assert resp.json()["target_qty"] == 60
    assert resp.json()["item"] == "Marinara sauce"


def test_a_null_on_a_required_goal_column_is_ignored_not_stored(client):
    goal = client.post(
        "/pantry-goals",
        headers=PARENT,
        json={"item": "Salsa", "unit": "pints", "method": "water_bath_canning"},
    ).json()

    resp = client.patch(f"/pantry-goals/{goal['id']}", headers=PARENT, json={"item": None, "target_qty": 12})

    assert resp.status_code == 200, resp.text
    assert resp.json()["item"] == "Salsa"
    assert resp.json()["target_qty"] == 12


def test_a_location_can_carry_a_skylight_chore(client):
    bed = client.post("/locations", headers=PARENT, json={"name": "North Bed", "kind": "raised_bed"}).json()

    resp = client.patch(
        f"/locations/{bed['id']}", headers=PARENT, json={"skylight_chore_id": "chore-7"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["skylight_chore_id"] == "chore-7"


# --------------------------------------------------------------------------- bindings

def test_clearing_one_sensor_leaves_the_others(client):
    plant = _plant(client)
    created = client.put(
        f"/bindings/plant/{plant['id']}",
        headers=PARENT,
        json={"moisture_entity_id": "sensor.m", "valve_entity_id": "valve.v"},
    )
    assert created.status_code == 200, created.text

    cleared = client.put(
        f"/bindings/plant/{plant['id']}", headers=PARENT, json={"moisture_entity_id": ""}
    )

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["moisture_entity_id"] is None
    # Empty string means "not bound"; an explicit null means "clear it".
    assert cleared.json()["valve_entity_id"] == "valve.v"


def test_an_explicit_null_clears_a_binding_field(client):
    plant = _plant(client)
    client.put(f"/bindings/plant/{plant['id']}", headers=PARENT, json={"valve_entity_id": "valve.v"})

    resp = client.put(f"/bindings/plant/{plant['id']}", headers=PARENT, json={"valve_entity_id": None})

    assert resp.status_code == 200, resp.text
    assert resp.json()["valve_entity_id"] is None


# --------------------------------------------------------------------------- care rules

def test_a_cadence_that_cannot_work_is_refused(client):
    plant = _plant(client)

    zero = client.post(f"/plants/{plant['id']}/care-rules", headers=PARENT, json={"action": "water", "interval_days": 0})
    below_one = client.post(
        f"/plants/{plant['id']}/care-rules", headers=PARENT,
        json={"action": "water", "interval_days": 7, "winter_multiplier": 0.5},
    )

    assert zero.status_code == 422
    assert below_one.status_code == 422


def test_a_care_rule_defaults_to_rain_skip(client):
    plant = _plant(client)

    created = client.post(
        f"/plants/{plant['id']}/care-rules", headers=PARENT, json={"action": "fertilize", "interval_days": 30}
    ).json()

    rules = client.get(f"/plants/{plant['id']}/care-rules", headers=PARENT).json()
    rule = next(row for row in rules["rules"] if row["id"] == created["id"])
    assert rule["rain_skip_enabled"] is True


def test_changing_the_interval_moves_the_next_date(client):
    plant = _plant(client)
    created = client.post(
        f"/plants/{plant['id']}/care-rules", headers=PARENT,
        json={"action": "water", "interval_days": 30},
    ).json()

    resp = client.patch(
        f"/plants/{plant['id']}/care-rules/{created['id']}", headers=PARENT, json={"interval_days": 2}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["next_due_at"] != created["next_due_at"]


def test_a_care_rule_cannot_be_moved_to_another_plant(client):
    first = _plant(client, nickname="Monty")
    second = _plant(client, nickname="Fernie")
    created = client.post(
        f"/plants/{first['id']}/care-rules", headers=PARENT, json={"action": "water", "interval_days": 7}
    ).json()

    resp = client.patch(
        f"/plants/{first['id']}/care-rules/{created['id']}",
        headers=PARENT,
        json={"plant_id": second["id"]},
    )

    assert resp.status_code == 400


# --------------------------------------------------------------------------- thumbnails

def test_a_png_thumbnail_is_stored_as_png(client, session):
    from services.garden.main import _thumbs_dir

    plant = _plant(client)

    photo = client.post(
        f"/plants/{plant['id']}/photos", headers=PARENT, json={"thumb_base64": TINY_PNG}
    ).json()

    assert photo["thumb_path"].endswith(".png")
    served = client.get(f"/plants/{plant['id']}/photos/{photo['id']}/thumb", headers=PARENT)
    assert served.headers["content-type"].startswith("image/png")
    assert (_thumbs_dir() / photo["thumb_path"]).is_file()


def test_a_jpeg_thumbnail_stays_jpeg(client):
    plant = _plant(client)

    photo = client.post(
        f"/plants/{plant['id']}/photos", headers=PARENT, json={"thumb_base64": TINY_JPEG}
    ).json()

    assert photo["thumb_path"].endswith(".jpg")
    served = client.get(f"/plants/{plant['id']}/photos/{photo['id']}/thumb", headers=PARENT)
    assert served.headers["content-type"].startswith("image/jpeg")


# --------------------------------------------------------------------------- moisture is about water

def test_a_wet_reading_never_holds_back_a_fertilizing(client, monkeypatch):
    """Soil moisture says nothing about feeding: it must only touch watering."""
    from datetime import UTC, datetime, timedelta

    from sqlmodel import select as _select

    from services import config
    from services.garden import homehub_hardware as hardware
    from services.garden import weather_care
    from services.garden.models import CareRule

    states = [
        {
            "entity_id": "sensor.m", "state": "78",
            "attributes": {"device_class": "moisture", "friendly_name": "Soil"},
        }
    ]

    async def call(method, path, *, body=None):
        return (200, states) if path == "/api/states" else (200, {"tags": []})

    async def no_rain(_profile=None):
        return {"active": False, "known": True, "rain_in": 0.0, "threshold_in": 0.25}

    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test")
    monkeypatch.setattr(hardware, "_call", call)
    monkeypatch.setattr(weather_care, "rain_state", no_rain)
    hardware.invalidate()

    plant = _plant(client)
    client.put(
        f"/bindings/plant/{plant['id']}",
        headers=PARENT,
        json={"moisture_entity_id": "sensor.m", "moisture_override_enabled": True},
    )

    # A feeding that is due today, with the soil clearly moist.
    body = client.get("/today", headers=PARENT).json()
    assert body["held_by_moisture"] == [] or all(
        row["action"] == "water" for row in body["held_by_moisture"]
    )

    due = client.post(
        f"/plants/{plant['id']}/care-rules", headers=PARENT,
        json={"action": "fertilize", "interval_days": 30},
    ).json()
    from sqlmodel import Session

    import services.garden.main as main_module

    with Session(main_module.app.state.engine) as db:
        rule = db.exec(_select(CareRule).where(CareRule.id == due["id"])).first()
        rule.next_due_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        db.add(rule)
        db.commit()

    today = client.get("/today", headers=PARENT).json()

    # The fertilizing is still due: a wet pot does not postpone feeding.
    assert any(
        row["plant_id"] == plant["id"] and row["action"] == "fertilize" for row in today["all"]
    )
    assert all(row["action"] != "fertilize" for row in today["held_by_moisture"])
