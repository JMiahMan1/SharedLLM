"""Rooms are Home Hub's areas: ``services/garden/hub_rooms.py`` and ``/locations``.

The hub's area registry is the house's only list of rooms. These tests pin that
down: a room is read from and written to the hub, a plant in one keeps its room
name on the Today list and in a digest, Garden's own rows are only the outdoor
places, and a hub that is away degrades instead of 500ing.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

ROOT = Path(__file__).resolve().parents[3]

from services import config  # noqa: E402
from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden import hub_rooms  # noqa: E402
from services.garden.main import app  # noqa: E402
from services.garden.models import CareRule, Location, Plant  # noqa: E402
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


class FakeHub:
    """Home Hub's area registry, in memory (services/homehub/entries_api.py)."""

    def __init__(self, areas: list[dict] | None = None, status: int = 200) -> None:
        self.areas = [dict(a) for a in areas or []]
        self.status = status
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _call(self, method: str, path: str, *, body: dict | None = None):
        self.calls.append((method, path, body))
        if self.status != 200:
            return (self.status, {"message": "hub trouble"})
        if method == "GET":
            return (200, {"areas": self.areas})
        if method == "POST":
            name = (body or {}).get("name", "")
            area = {"area_id": name.lower().replace(" ", "_"), "name": name}
            self.areas.append(area)
            return (200, area)
        if method == "PATCH":
            area_id = path.rsplit("/", 1)[-1]
            for area in self.areas:
                if area["area_id"] == area_id:
                    area.update(body or {})
                    return (200, area)
            return (404, {"message": "No such area"})
        if method == "DELETE":
            area_id = path.rsplit("/", 1)[-1]
            self.areas = [a for a in self.areas if a["area_id"] != area_id]
            return (200, {"status": "removed", "area_id": area_id})
        return (404, None)

    def methods(self) -> list[str]:
        return [method for method, _path, _body in self.calls]


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
    return TestClient(app)


@pytest.fixture()
def session(client):
    import services.garden.main as main_module

    with Session(main_module.app.state.engine) as db:
        yield db


@pytest.fixture()
def hub(monkeypatch):
    """A hub with one room, wired in as the service's client."""
    fake = FakeHub([{"area_id": "living_room", "name": "Living Room"}])
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test:8123")
    monkeypatch.setattr(hub_rooms, "_call", fake._call)
    monkeypatch.setattr(hub_rooms, "_cache", None)
    yield fake
    monkeypatch.setattr(hub_rooms, "_cache", None)


@pytest.fixture()
def no_hub(monkeypatch):
    """No hub configured: Garden's own rows are all there is."""
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(hub_rooms, "_cache", None)


def _due_water_rule(session: Session, plant_id: str) -> None:
    """Make the plant's watering due yesterday, so /today and water-all see it."""
    rule = session.exec(
        select(CareRule).where(CareRule.plant_id == plant_id, CareRule.action == "water")
    ).first()
    rule.next_due_at = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    session.add(rule)
    session.commit()


def _add_plant(client, location_id: str, nickname: str = "Monty") -> dict:
    resp = client.post(
        "/plants",
        headers=ADMIN_HEADERS,
        json={
            "category": "houseplant",
            "species_ref": "monstera_deliciosa",
            "nickname": nickname,
            "location_id": location_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- reading

def test_rooms_come_from_home_hub(client, hub):
    """``GET /locations`` is the hub's rooms plus the Garden's own beds."""
    client.post("/locations", headers=ADMIN_HEADERS, json={"name": "North Bed", "kind": "raised_bed"})

    places = client.get("/locations", headers=ADMIN_HEADERS).json()

    assert [(p["name"], p["kind"], p["source"]) for p in places] == [
        ("Living Room", "indoor_room", "homehub"),
        ("North Bed", "raised_bed", "garden"),
    ]
    room = places[0]
    # The id a plant stores for a room is the hub's area id.
    assert room["id"] == "living_room" and room["area_id"] == "living_room"


def test_no_room_rows_are_created_for_a_hub_room(client, hub, session):
    client.get("/locations", headers=ADMIN_HEADERS)
    assert session.exec(select(Location)).all() == []


# --------------------------------------------------------------------------- writing

def test_adding_a_room_adds_it_in_home_hub(client, hub, session):
    resp = client.post("/locations", headers=ADMIN_HEADERS, json={"name": "Loft Room", "kind": "indoor_room"})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == "loft_room" and body["source"] == "homehub"
    assert ("POST", "/hub/areas", {"name": "Loft Room"}) in hub.calls
    assert session.exec(select(Location)).all() == []


def test_adding_a_bed_stays_in_garden(client, hub):
    resp = client.post("/locations", headers=ADMIN_HEADERS, json={"name": "South Bed", "kind": "raised_bed"})

    assert resp.status_code == 201
    assert resp.json()["source"] == "garden"
    assert "POST" not in hub.methods()


def test_renaming_a_room_renames_it_in_home_hub(client, hub):
    resp = client.patch("/locations/living_room", headers=ADMIN_HEADERS, json={"name": "Den"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Den"
    assert ("PATCH", "/hub/areas/living_room", {"name": "Den"}) in hub.calls


def test_a_room_takes_no_garden_assignment(client, hub):
    resp = client.patch(
        "/locations/living_room", headers=ADMIN_HEADERS, json={"name": "Den", "assigned_to": "child"}
    )
    assert resp.status_code == 400
    assert "assign the plants" in resp.json()["detail"]

    resp = client.patch("/locations/living_room/assign", headers=ADMIN_HEADERS, json={"assigned_to": "child"})
    assert resp.status_code == 400


def test_removing_a_room_removes_it_from_home_hub(client, hub):
    resp = client.delete("/locations/living_room", headers=ADMIN_HEADERS)

    assert resp.status_code == 204
    assert ("DELETE", "/hub/areas/living_room", None) in hub.calls


def test_a_room_with_plants_is_not_removed(client, hub):
    _add_plant(client, "living_room")

    resp = client.delete("/locations/living_room", headers=ADMIN_HEADERS)

    assert resp.status_code == 409
    assert "1 plant(s)" in resp.json()["detail"]
    assert "DELETE" not in hub.methods()


# --------------------------------------------------------------------------- care paths

def test_today_names_a_room_from_home_hub(client, hub, session):
    plant = _add_plant(client, "living_room")
    _due_water_rule(session, plant["id"])

    today = client.get("/today", headers=ADMIN_HEADERS).json()

    assert "Living Room" in today["by_room"]
    assert today["by_room"]["Living Room"][0]["plant_id"] == plant["id"]


def test_water_all_works_in_a_room(client, hub, session):
    plant = _add_plant(client, "living_room")
    _due_water_rule(session, plant["id"])

    resp = client.post("/locations/living_room/water-all", headers=ADMIN_HEADERS)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["location_name"] == "Living Room"
    assert body["watered_count"] == 1


def test_a_hub_that_stops_answering_does_not_break_today(client, hub, session):
    """The plant keeps its room name while the hub is away: no 500."""
    plant = _add_plant(client, "living_room")
    _due_water_rule(session, plant["id"])
    hub.status = 500
    hub_rooms.invalidate()

    today = client.get("/today", headers=ADMIN_HEADERS)

    assert today.status_code == 200
    assert today.json()["by_room"]


# --------------------------------------------------------------------------- no hub

def test_without_a_hub_garden_keeps_its_own_room(client, no_hub):
    resp = client.post("/locations", headers=ADMIN_HEADERS, json={"name": "Sunroom", "kind": "indoor_room"})

    assert resp.status_code == 201
    assert resp.json()["source"] == "garden"
    places = client.get("/locations", headers=ADMIN_HEADERS).json()
    assert [p["name"] for p in places] == ["Sunroom"]


def test_a_garden_room_is_adopted_into_home_hub(client, hub, session):
    """A room the Garden made before the switch is moved to the hub, once."""
    legacy = Location(name="Living Room", kind="indoor_room")
    session.add(legacy)
    session.commit()
    session.refresh(legacy)
    plant = Plant(category="houseplant", species_ref="monstera_deliciosa", location_id=legacy.id)
    session.add(plant)
    session.commit()
    session.refresh(plant)

    places = client.get("/locations", headers=ADMIN_HEADERS).json()

    assert [p["id"] for p in places] == ["living_room"]
    session.expire_all()
    assert session.get(Plant, plant.id).location_id == "living_room"
    assert session.exec(select(Location)).all() == []
    assert "POST" not in hub.methods()  # matched by name: nothing added to the hub


def test_a_garden_only_room_is_created_in_home_hub(client, hub, session):
    session.add(Location(name="Fern Corner", kind="indoor_room"))
    session.commit()

    places = client.get("/locations", headers=ADMIN_HEADERS).json()

    assert "fern_corner" in [p["id"] for p in places]
    assert ("POST", "/hub/areas", {"name": "Fern Corner"}) in hub.calls


def test_children_cannot_add_a_room(client, hub):
    resp = client.post("/locations", headers=KID_HEADERS, json={"name": "Play Room", "kind": "indoor_room"})
    assert resp.status_code == 403
    assert "POST" not in hub.methods()


#: A parent who is not an admin: Home Hub's own area routes would refuse them.
PARENT_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "parent_one",
    "X-User-Role": "father",
    "X-Is-Admin": "false",
    "X-Family-Manage": "true",
}


def test_a_non_admin_cannot_rename_a_hub_room(client, hub):
    resp = client.patch("/locations/living_room", headers=PARENT_HEADERS, json={"name": "Den"})

    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"]
    assert "PATCH" not in hub.methods()


def test_a_non_admin_cannot_remove_a_hub_room(client, hub):
    resp = client.delete("/locations/living_room", headers=PARENT_HEADERS)

    assert resp.status_code == 403
    assert "DELETE" not in hub.methods()


def test_a_non_admin_cannot_add_a_hub_room(client, hub):
    resp = client.post("/locations", headers=PARENT_HEADERS, json={"name": "Loft", "kind": "indoor_room"})

    assert resp.status_code == 403
    assert "POST" not in hub.methods()


def test_a_non_admin_still_manages_garden_beds(client, hub):
    resp = client.post("/locations", headers=PARENT_HEADERS, json={"name": "East Bed", "kind": "raised_bed"})
    assert resp.status_code == 201, resp.text

    renamed = client.patch(f"/locations/{resp.json()['id']}", headers=PARENT_HEADERS, json={"name": "East Bed 2"})
    assert renamed.status_code == 200
    assert client.delete(f"/locations/{resp.json()['id']}", headers=PARENT_HEADERS).status_code == 204
