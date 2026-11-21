"""Phase 8: Home Hub sensors, guarded valves, grow lights and NFC/QR tags.

Two rules run through every test here. A house with no garden hardware must see
**nothing** (``has_any_hardware`` is what hides the UI), and a valve must never
open outside its caps -- or, having opened, stay open past its timer.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from services import config
from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden import homehub_hardware as hardware  # noqa: E402
from services.garden import weather_care  # noqa: E402
from services.garden.main import app  # noqa: E402
from scripts.build_plants_seed import build_seed  # noqa: E402


#: Home Hub's horses: a soil probe, a bed valve, a grow light and an NFC tag.
HUB_STATES = [
    {
        "entity_id": "sensor.bed1_soil_moisture",
        "state": "22",
        "attributes": {"device_class": "moisture", "unit_of_measurement": "%", "friendly_name": "Bed 1 Soil Moisture"},
    },
    {
        "entity_id": "sensor.bed1_temperature",
        "state": "64.4",
        "attributes": {"device_class": "temperature", "unit_of_measurement": "°F", "friendly_name": "Bed 1 Temperature"},
    },
    {
        "entity_id": "sensor.bed1_humidity",
        "state": "51",
        "attributes": {"device_class": "humidity", "unit_of_measurement": "%", "friendly_name": "Bed 1 Humidity"},
    },
    {
        "entity_id": "sensor.bed1_light",
        "state": "18400",
        "attributes": {"device_class": "illuminance", "unit_of_measurement": "lx", "friendly_name": "Bed 1 Light"},
    },
    {
        "entity_id": "valve.bed1_drip",
        "state": "closed",
        "attributes": {"friendly_name": "Bed 1 Drip"},
    },
    {
        "entity_id": "light.grow_shelf",
        "state": "off",
        "attributes": {"friendly_name": "Grow Shelf"},
    },
    {
        "entity_id": "sensor.kitchen_temperature",
        "state": "71",
        "attributes": {"device_class": "temperature", "friendly_name": "Kitchen Temperature"},
    },
]

HUB_TAGS = [{"tag_id": "tag-bed1", "name": "Bed 1", "last_scanned": None}]


def _headers(user: str, role: str) -> dict[str, str]:
    return {
        "X-Internal-Secret": INTERNAL_SECRET,
        "X-User-Id": user,
        "X-User-Role": role,
        "X-Is-Admin": "true" if role == "admin" else "false",
        "X-Family-Manage": "true" if role in ("mother", "father") else "false",
    }


PARENT = _headers("mother", "mother")
ADULT = _headers("alex", "adult_child")
CHILD = _headers("jamie", "child")


#: The real function, kept before the ``hub`` fixture replaces it with a stub.
_REAL_RAIN_STATE = weather_care.rain_state


class Hub:
    """A fake Home Hub: states, tags and every service call it received."""

    def __init__(self, *, states_rows=None, tags_rows=None, close_works=True, open_works=True):
        self.states_rows = HUB_STATES if states_rows is None else states_rows
        self.tags_rows = HUB_TAGS if tags_rows is None else tags_rows
        self.close_works = close_works
        self.open_works = open_works
        self.calls: list[tuple[str, str, dict | None]] = []

    async def __call__(self, method, path, *, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path == "/api/states":
            return (200, self.states_rows)
        if method == "GET" and path == "/hub/tags":
            return (200, {"tags": self.tags_rows})
        if method == "POST" and "/api/services/" in path:
            if path.endswith("open_valve") or path.endswith("turn_on"):
                return (200 if self.open_works else 500, None)
            if path.endswith("close_valve") or path.endswith("turn_off"):
                return (200 if self.close_works else 500, None)
        return (404, None)

    def services(self) -> list[str]:
        return [path.split("/api/services/", 1)[1] for method, path, _ in self.calls if "/api/services/" in path]

    def bodies(self) -> list[dict | None]:
        return [body for _method, path, body in self.calls if "/api/services/" in path]


@pytest.fixture()
def seed_path(tmp_path):
    path = tmp_path / "plants-seed.db"
    build_seed(path)
    return str(path)


@pytest.fixture()
def client(seed_path, tmp_path, monkeypatch):
    import services.garden.main as main_module

    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path / 'garden.db'}")
    monkeypatch.setenv("PLANT_SEED_PATH", seed_path)
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


@pytest.fixture()
def hub(monkeypatch):
    """A hub that is there, with one of everything. Tests may replace it."""
    fake = Hub()
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test")
    monkeypatch.setattr(hardware, "_call", fake)
    hardware.invalidate()

    async def no_rain(_profile=None):
        return {"active": False, "known": True, "rain_in": 0.0, "threshold_in": 0.25}

    monkeypatch.setattr(weather_care, "rain_state", no_rain)
    return fake


@pytest.fixture()
def no_hub(monkeypatch):
    """The ordinary case: a house with no hub at all."""
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    hardware.invalidate()


def _bed(client, name="Bed 1", *, covered=False) -> dict:
    resp = client.post(
        "/locations",
        headers=PARENT,
        json={"name": name, "kind": "raised_bed", "covered": covered, "width_ft": 4, "length_ft": 8},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _plant(client, *, nickname="Monty", species_ref="tomato_roma", category="vegetable", location_id=None) -> dict:
    body = {"nickname": nickname, "species_ref": species_ref, "category": category}
    if location_id:
        body["location_id"] = location_id
    resp = client.post("/plants", headers=PARENT, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bind(client, kind, target, headers=PARENT, **fields):
    return client.put(f"/bindings/{kind}/{target}", headers=headers, json=fields)


# --------------------------------------------------------------------------- discovery

def test_no_hub_means_no_hardware_and_no_ui(client, no_hub):
    body = client.get("/hardware/discover", headers=CHILD).json()

    assert body["available"] is False
    assert body["has_any_hardware"] is False
    assert body["moisture_sensors"] == [] and body["valves"] == [] and body["tags"] == []


def test_an_unreachable_hub_is_not_an_error(client, no_hub, monkeypatch):
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test")

    async def down(method, path, *, body=None):
        return (0, None)

    monkeypatch.setattr(hardware, "_call", down)
    hardware.invalidate()

    resp = client.get("/hardware/discover", headers=CHILD)

    assert resp.status_code == 200
    assert resp.json()["has_any_hardware"] is False


def test_a_hub_with_only_other_devices_reports_no_garden_hardware(client, monkeypatch):
    bare = Hub(states_rows=[
        {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room"}},
        {"entity_id": "sensor.living_room_temperature", "state": "70", "attributes": {"device_class": "temperature"}},
    ], tags_rows=[])
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test")
    monkeypatch.setattr(hardware, "_call", bare)
    hardware.invalidate()

    body = client.get("/hardware/discover", headers=CHILD).json()

    assert body["available"] is True
    # The living-room light is a light, so it is a candidate; nothing else is.
    assert body["moisture_sensors"] == [] and body["valves"] == [] and body["tags"] == []


def test_every_garden_entity_is_found_and_classified(client, hub):
    body = client.get("/hardware/discover", headers=CHILD).json()

    assert body["has_any_hardware"] is True
    assert [row["entity_id"] for row in body["moisture_sensors"]] == ["sensor.bed1_soil_moisture"]
    assert [row["entity_id"] for row in body["valves"]] == ["valve.bed1_drip"]
    assert [row["entity_id"] for row in body["grow_lights"]] == ["light.grow_shelf"]
    assert [row["tag_id"] for row in body["tags"]] == ["tag-bed1"]
    assert len(body["temperature_sensors"]) == 2
    assert body["humidity_sensors"][0]["unit"] == "%"
    assert body["lux_sensors"][0]["entity_id"] == "sensor.bed1_light"


# --------------------------------------------------------------------------- bindings

def test_a_binding_defaults_to_the_safe_choice(client, hub):
    bed = _bed(client)

    resp = _bind(client, "location", bed["id"], moisture_entity_id="sensor.bed1_soil_moisture", valve_entity_id="valve.bed1_drip")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valve_mode"] == "notify_only"
    assert body["max_run_minutes"] == 5
    assert body["daily_max_runs"] == 2
    assert body["rain_interlock"] is True
    assert body["target_name"] == "Bed 1"
    assert body["readings"]["moisture_pct"] == 22.0
    assert body["runs_today"] == 0


@pytest.mark.parametrize("fields", [
    {"max_run_minutes": 0},
    {"max_run_minutes": 31},
    {"daily_max_runs": 0},
    {"daily_max_runs": 5},
    {"valve_mode": "always_on"},
    {"moisture_threshold_pct": 2},
    {"grow_light_on_time": "7am"},
])
def test_an_unsafe_binding_is_refused(client, hub, fields):
    bed = _bed(client)

    resp = _bind(client, "location", bed["id"], **fields)

    assert resp.status_code == 400, resp.text
    assert client.get("/bindings", headers=PARENT).json()["count"] == 0


def test_a_grow_light_schedule_needs_its_times(client, hub):
    bed = _bed(client)

    resp = _bind(client, "location", bed["id"], grow_light_entity_id="light.grow_shelf", grow_light_enabled=True)

    assert resp.status_code == 400
    assert "on time" in resp.json()["detail"]


def test_children_cannot_change_a_binding(client, hub):
    bed = _bed(client)

    assert _bind(client, "location", bed["id"], headers=CHILD, valve_entity_id="valve.bed1_drip").status_code == 403
    assert _bind(client, "location", bed["id"], headers=ADULT, valve_entity_id="valve.bed1_drip").status_code == 403
    assert _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip").status_code == 200


def test_a_binding_must_point_at_something_real(client, hub):
    assert _bind(client, "location", "nope", valve_entity_id="valve.bed1_drip").status_code == 404
    assert _bind(client, "greenhouse", "nope", valve_entity_id="valve.bed1_drip").status_code == 400


def test_one_tag_links_to_one_target(client, hub):
    first = _bed(client, "Bed 1")
    second = _bed(client, "Bed 2")
    assert _bind(client, "location", first["id"], nfc_tag_id="tag-bed1").status_code == 200

    resp = _bind(client, "location", second["id"], nfc_tag_id="tag-bed1")

    assert resp.status_code == 409
    assert "already linked" in resp.json()["detail"]


# --------------------------------------------------------------------------- moisture override

def _due_rule(session, plant_id: str, *, when: datetime) -> None:
    from sqlmodel import select as _select

    from services.garden.models import CareRule

    rule = session.exec(
        _select(CareRule).where(CareRule.plant_id == plant_id, CareRule.action == "water")
    ).first()
    assert rule is not None, "the plant should have a seeded watering rule"
    rule.next_due_at = when.isoformat()
    rule.snoozed_until = None
    session.add(rule)
    session.commit()


def test_a_dry_reading_makes_a_plant_due_now(client, hub, session):
    bed = _bed(client)
    plant = _plant(client, location_id=bed["id"])
    _due_rule(session, plant["id"], when=datetime.now(UTC) + timedelta(days=3))
    _bind(client, "plant", plant["id"], moisture_entity_id="sensor.bed1_soil_moisture", moisture_override_enabled=True)

    body = client.get("/today", headers=CHILD).json()

    task = next(t for t in body["all"] if t["plant_id"] == plant["id"])
    assert task["moisture_pct"] == 22.0
    assert task["moisture_reason"] == "Soil moisture is 22% (below 30%)"


def test_a_wet_reading_holds_off_a_due_plant(client, hub, session, monkeypatch):
    bed = _bed(client)
    plant = _plant(client, location_id=bed["id"])
    _due_rule(session, plant["id"], when=datetime.now(UTC) - timedelta(days=1))
    _bind(client, "plant", plant["id"], moisture_entity_id="sensor.bed1_soil_moisture", moisture_override_enabled=True)
    wet = [dict(row) for row in HUB_STATES]
    wet[0] = {**wet[0], "state": "78"}
    monkeypatch.setattr(hardware, "_call", Hub(states_rows=wet))
    hardware.invalidate()

    body = client.get("/today", headers=CHILD).json()

    assert all(t["plant_id"] != plant["id"] for t in body["all"])
    held = next(h for h in body["held_by_moisture"] if h["plant_id"] == plant["id"])
    assert held["reason"] == "Soil moisture is 78% — holding off"


def test_an_offline_sensor_falls_back_to_the_schedule(client, hub, session, monkeypatch):
    bed = _bed(client)
    plant = _plant(client, location_id=bed["id"])
    _due_rule(session, plant["id"], when=datetime.now(UTC) - timedelta(days=1))
    _bind(client, "plant", plant["id"], moisture_entity_id="sensor.bed1_soil_moisture", moisture_override_enabled=True)
    dead = [dict(row) for row in HUB_STATES]
    dead[0] = {**dead[0], "state": "unavailable"}
    monkeypatch.setattr(hardware, "_call", Hub(states_rows=dead))
    hardware.invalidate()

    task = next(
        t for t in client.get("/today", headers=CHILD).json()["all"] if t["plant_id"] == plant["id"]
    )

    assert task["moisture_pct"] is None
    assert task["moisture_offline"] is True


def test_the_override_is_opt_in(client, hub, session, monkeypatch):
    bed = _bed(client)
    plant = _plant(client, location_id=bed["id"])
    _due_rule(session, plant["id"], when=datetime.now(UTC) + timedelta(days=3))
    _bind(client, "plant", plant["id"], moisture_entity_id="sensor.bed1_soil_moisture")

    body = client.get("/today", headers=CHILD).json()

    assert all(t["plant_id"] != plant["id"] for t in body["all"])
    assert body["held_by_moisture"] == []


def test_the_plant_card_carries_the_live_moisture(client, hub):
    plant = _plant(client, nickname="Monty", species_ref="monstera_deliciosa", category="houseplant")
    assert client.get("/plants", headers=CHILD).json()[0]["live_moisture_pct"] is None
    _bind(client, "plant", plant["id"], moisture_entity_id="sensor.bed1_soil_moisture")

    row = client.get("/plants", headers=CHILD).json()[0]

    assert row["live_moisture_pct"] == 22.0


# --------------------------------------------------------------------------- guarded valve runs

def _binding_id(client, kind, target) -> str:
    rows = client.get("/bindings", headers=PARENT).json()["bindings"]
    return next(row["id"] for row in rows if row["target_kind"] == kind and row["target_id"] == target)


def test_a_guarded_run_opens_the_valve_and_logs_a_watering(client, hub, session):
    from sqlmodel import select as _select

    from services.garden.models import CareLog

    bed = _bed(client)
    plant = _plant(client, nickname="Monty", location_id=bed["id"])
    _bind(client, "plant", plant["id"], valve_entity_id="valve.bed1_drip", max_run_minutes=10)

    resp = client.post(f"/bindings/{_binding_id(client, 'plant', plant['id'])}/valve/run", headers=PARENT, json={"minutes": 3})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["minutes"] == 3
    assert body["runs_today"] == 1
    assert body["closes_at"] > body["opened_at"]
    assert hub.services() == ["valve/open_valve"]
    assert hub.bodies() == [{"entity_id": "valve.bed1_drip"}]
    logs = session.exec(_select(CareLog).where(CareLog.plant_id == plant["id"])).all()
    assert [(row.action, row.source) for row in logs] == [("water", "homehub_valve")]
    assert body["next_due_at"] is not None


def test_a_run_is_refused_beyond_the_bindings_own_cap(client, hub):
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip", max_run_minutes=5)

    resp = client.post(f"/bindings/{_binding_id(client, 'location', bed['id'])}/valve/run", headers=PARENT, json={"minutes": 30})

    assert resp.status_code == 400
    assert "capped at 5" in resp.json()["detail"]
    assert hub.services() == []


def test_the_daily_cap_is_counted_from_the_care_log(client, hub):
    bed = _bed(client)
    plant = _plant(client, location_id=bed["id"])
    _bind(client, "plant", plant["id"], valve_entity_id="valve.bed1_drip", daily_max_runs=1)
    binding_id = _binding_id(client, "plant", plant["id"])
    assert client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={"minutes": 2}).status_code == 200
    assert client.post(f"/bindings/{binding_id}/valve/stop", headers=PARENT).status_code == 200

    resp = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={"minutes": 2})

    assert resp.status_code == 409
    assert "Daily max runs reached (1 of 1 today)" in resp.json()["detail"]


def test_a_second_run_cannot_start_while_one_is_open(client, hub):
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])
    assert client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={}).status_code == 200

    resp = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"]


def test_the_rain_interlock_blocks_an_uncovered_bed(client, hub, monkeypatch):
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])

    async def raining(_profile=None):
        return {"active": True, "known": True, "rain_in": 0.6, "threshold_in": 0.25}

    monkeypatch.setattr(weather_care, "rain_state", raining)

    resp = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    assert resp.status_code == 409
    assert "rain interlock" in resp.json()["detail"]
    assert hub.services() == []
    # A parent may still choose to water through it.
    override = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={"override_rain": True})
    assert override.status_code == 200


def test_unknown_rain_does_not_water_blind(client, hub, monkeypatch):
    """No forecast: the interlock refuses and says so; a person may override."""
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])

    async def no_forecast(*_a, **_k):
        return None

    monkeypatch.setattr(weather_care, "rain_state", _REAL_RAIN_STATE)
    monkeypatch.setattr(weather_care, "_fetch_forecast", no_forecast)

    resp = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    assert resp.status_code == 409
    assert "cannot tell whether it rained" in resp.json()["detail"]
    assert hub.services() == []
    override = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={"override_rain": True})
    assert override.status_code == 200


def test_the_interlock_and_today_convert_rain_the_same_way(monkeypatch):
    """Millimetres from the weather data are converted by the data's units."""
    import asyncio

    metric = {"units": {"system": "metric"}, "hourly": [{"rain": 10.0}] + [{"rain": 0.0}] * 11}

    async def forecast():
        return metric

    async def day(_date):
        return {"rain": 2.54}  # millimetres, like the forecast

    monkeypatch.setattr(weather_care, "_fetch_forecast", forecast)
    monkeypatch.setattr(weather_care, "_fetch_day", day)

    rain_in = asyncio.run(weather_care._observed_and_forecast_rain_in())
    state = asyncio.run(_REAL_RAIN_STATE(None))

    assert round(rain_in, 3) == round((10.0 + 2.54) / 25.4, 3)
    assert state["known"] is True and state["rain_in"] == round(rain_in, 3)


def test_rain_cannot_reach_a_covered_bed(client, hub, monkeypatch):
    bed = _bed(client, "Greenhouse", covered=True)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")

    async def raining(_profile=None):
        return {"active": True, "known": True, "rain_in": 0.6, "threshold_in": 0.25}

    monkeypatch.setattr(weather_care, "rain_state", raining)

    resp = client.post(f"/bindings/{_binding_id(client, 'location', bed['id'])}/valve/run", headers=PARENT, json={})

    assert resp.status_code == 200


def test_only_parents_and_admins_may_touch_a_valve(client, hub):
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])

    assert client.post(f"/bindings/{binding_id}/valve/run", headers=CHILD, json={}).status_code == 403
    assert client.post(f"/bindings/{binding_id}/valve/run", headers=ADULT, json={}).status_code == 403
    assert client.post(f"/bindings/{binding_id}/valve/stop", headers=CHILD).status_code == 403
    assert hub.services() == []


def test_stop_closes_the_valve_and_clears_the_run(client, hub):
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])
    client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    resp = client.post(f"/bindings/{binding_id}/valve/stop", headers=PARENT)

    assert resp.status_code == 200
    assert hub.services()[-1] == "valve/close_valve"
    row = next(r for r in client.get("/bindings", headers=PARENT).json()["bindings"] if r["id"] == binding_id)
    assert row["active_run_started_at"] is None


# --------------------------------------------------------------------------- failsafe watchdog

def _rewind_timer(session, binding_id: str, *, minutes_ago: int = 2) -> None:
    from services.garden.models import HomeHubBinding

    session.expire_all()
    binding = session.get(HomeHubBinding, binding_id)
    binding.active_run_ends_at = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    session.add(binding)
    session.commit()


def test_the_watchdog_closes_a_valve_past_its_timer(client, hub, session):
    import asyncio

    import services.garden.main as main_module

    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])
    client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={"minutes": 1})
    _rewind_timer(session, binding_id)

    results = asyncio.run(hardware.check_valve_failsafe(lambda: main_module.app.state.engine))

    assert results == [{"binding_id": binding_id, "closed": True}]
    assert hub.services()[-1] == "valve/close_valve"
    session.expire_all()
    assert client.get("/bindings", headers=PARENT).json()["bindings"][0]["active_run_started_at"] is None


def test_a_valve_that_will_not_close_shouts_for_a_parent(client, hub, monkeypatch, session):
    import asyncio

    import services.garden.main as main_module

    # The same hub, but it will not confirm a close.
    deaf = Hub(close_works=False)
    monkeypatch.setattr(hardware, "_call", deaf)
    hardware.invalidate()
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])
    client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={"minutes": 1})
    _rewind_timer(session, binding_id)

    alerts: list[dict] = []

    async def capture(payload, **_kw):
        alerts.append(payload)

    monkeypatch.setattr(hardware.notify, "send", capture)

    async def parents():
        return ["mother", "father"]

    results = asyncio.run(
        hardware.check_valve_failsafe(lambda: main_module.app.state.engine, parents)
    )

    assert results == [{"binding_id": binding_id, "closed": False}]
    assert [alert["user"] for alert in alerts] == ["mother", "father"]
    assert alerts[0]["priority"] == 4
    assert "could not confirm" in alerts[0]["body"]
    # The run stays flagged, so the next tick tries again rather than forgetting it.
    session.expire_all()
    row = next(r for r in client.get("/bindings", headers=PARENT).json()["bindings"] if r["id"] == binding_id)
    assert row["active_run_started_at"] is not None


class ValveHub(Hub):
    """A hub whose valve really changes state, and can be made to misbehave."""

    def __init__(self, *, open_raises=None, open_status=200, stays_open=False, **kw):
        super().__init__(**kw)
        self.valve_state = "closed"
        self.open_raises = open_raises
        self.open_status = open_status
        self.stays_open = stays_open
        self.runs_seen_at_open: list[int] = []

    async def __call__(self, method, path, *, body=None):
        if method == "GET" and path == "/api/states":
            self.calls.append((method, path, body))
            rows = [dict(row) for row in self.states_rows]
            for row in rows:
                if row["entity_id"] == "valve.bed1_drip":
                    row["state"] = self.valve_state
            return (200, rows)
        if method == "POST" and path.endswith("open_valve"):
            self.calls.append((method, path, body))
            self.runs_seen_at_open.append(self._runs())
            # Whatever the hub answers, the valve did open (the dangerous case).
            self.valve_state = "open"
            if self.open_raises is not None:
                raise self.open_raises
            return (self.open_status, None)
        if method == "POST" and path.endswith("close_valve"):
            self.calls.append((method, path, body))
            if not self.stays_open:
                self.valve_state = "closed"
            return (200, None)
        return await super().__call__(method, path, body=body)

    @staticmethod
    def _runs() -> int:
        from sqlmodel import select as _select

        import services.garden.main as main_module
        from services.garden.models import ValveRun

        with Session(main_module.app.state.engine) as db:
            return len(db.exec(_select(ValveRun)).all())


def _valve_hub(monkeypatch, **kw) -> ValveHub:
    fake = ValveHub(**kw)
    monkeypatch.setattr(hardware, "_call", fake)
    monkeypatch.setattr(hardware, "CLOSE_CONFIRM_DELAYS_S", (0.0, 0.0))
    hardware.invalidate()
    return fake


def _run_rows(session) -> list:
    from sqlmodel import select as _select

    from services.garden.models import ValveRun

    session.expire_all()
    return session.exec(_select(ValveRun)).all()


def _binding_row(client, binding_id) -> dict:
    return next(r for r in client.get("/bindings", headers=PARENT).json()["bindings"] if r["id"] == binding_id)


def test_the_run_is_recorded_before_the_valve_is_opened(client, hub, monkeypatch):
    fake = _valve_hub(monkeypatch)
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")

    resp = client.post(f"/bindings/{_binding_id(client, 'location', bed['id'])}/valve/run", headers=PARENT, json={})

    assert resp.status_code == 200, resp.text
    assert fake.runs_seen_at_open == [1]


def test_an_open_that_times_out_is_closed_and_kept(client, hub, monkeypatch, session):
    fake = _valve_hub(monkeypatch, open_raises=TimeoutError("hub too slow"))
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])

    resp = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    assert resp.status_code == 502, resp.text
    assert "sent a close and the valve reports closed" in resp.json()["detail"]
    assert fake.services() == ["valve/open_valve", "valve/close_valve"]
    assert fake.valve_state == "closed"
    rows = _run_rows(session)
    assert len(rows) == 1 and rows[0].closed_at is not None
    assert _binding_row(client, binding_id)["active_run_started_at"] is None


def test_a_refused_open_that_opened_anyway_stays_with_the_watchdog(client, hub, monkeypatch, session):
    fake = _valve_hub(monkeypatch, open_status=500, stays_open=True)
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])

    resp = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    assert resp.status_code == 502
    assert "not confirmed: check the valve" in resp.json()["detail"]
    assert fake.services() == ["valve/open_valve", "valve/close_valve"]
    rows = _run_rows(session)
    assert len(rows) == 1 and rows[0].closed_at is None
    binding = _binding_row(client, binding_id)
    assert binding["active_run_started_at"] is not None
    # Due now, so the very next watchdog tick retries the close.
    assert binding["active_run_ends_at"] <= datetime.now(UTC).isoformat()


def test_a_close_the_valve_does_not_obey_is_not_closed(client, hub, monkeypatch):
    fake = _valve_hub(monkeypatch, stays_open=True)
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])
    assert client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={}).status_code == 200

    resp = client.post(f"/bindings/{binding_id}/valve/stop", headers=PARENT)

    assert resp.status_code == 502
    assert fake.valve_state == "open"
    assert _binding_row(client, binding_id)["active_run_started_at"] is not None


def test_an_open_valve_alert_with_no_family_goes_to_the_admins(client, hub, monkeypatch, session, caplog):
    import asyncio
    import logging

    import services.garden.main as main_module

    _valve_hub(monkeypatch, stays_open=True)
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])
    client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={"minutes": 1})
    _rewind_timer(session, binding_id)
    alerts: list[dict] = []

    async def capture(payload, **_kw):
        alerts.append(payload)
        return True

    monkeypatch.setattr(hardware.notify, "send", capture)

    async def identity_down():
        return []

    with caplog.at_level(logging.ERROR, logger="garden.homehub_hardware"):
        asyncio.run(hardware.check_valve_failsafe(lambda: main_module.app.state.engine, identity_down))

    assert [alert["user"] for alert in alerts] == ["@admins"]
    assert alerts[0]["category"] == "admin"
    assert any("no family list" in record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------- grow lights

def test_a_photoperiod_window_can_cross_midnight():
    from services.garden.models import HomeHubBinding

    binding = HomeHubBinding(
        target_kind="plant", target_id="p-1", grow_light_enabled=True,
        grow_light_entity_id="light.grow_shelf",
        grow_light_on_time="18:00", grow_light_off_time="06:00",
    )

    assert hardware.grow_light_window(binding, datetime(2026, 11, 20, 23, 0)) is True
    assert hardware.grow_light_window(binding, datetime(2026, 11, 20, 5, 59)) is True
    assert hardware.grow_light_window(binding, datetime(2026, 11, 20, 12, 0)) is False


def test_an_unscheduled_light_has_no_window():
    from services.garden.models import HomeHubBinding

    off = HomeHubBinding(target_kind="plant", target_id="p-1", grow_light_entity_id="light.x")
    no_entity = HomeHubBinding(
        target_kind="plant", target_id="p-1", grow_light_enabled=True,
        grow_light_on_time="06:00", grow_light_off_time="20:00",
    )

    assert hardware.grow_light_window(off, datetime(2026, 11, 20, 12, 0)) is None
    assert hardware.grow_light_window(no_entity, datetime(2026, 11, 20, 12, 0)) is None


def test_a_light_is_turned_on_inside_its_window(client, hub):
    import asyncio

    import services.garden.main as main_module

    bed = _bed(client)
    _bind(
        client, "location", bed["id"], grow_light_entity_id="light.grow_shelf",
        grow_light_enabled=True, grow_light_on_time="06:00", grow_light_off_time="20:00",
    )

    actions = asyncio.run(
        hardware.sync_grow_lights(
            lambda: main_module.app.state.engine, now=datetime(2026, 11, 20, 12, 0)
        )
    )

    assert [action["turned_on"] for action in actions] == [True]
    assert hub.services() == ["light/turn_on"]


def test_a_light_already_on_is_left_alone(client, hub, monkeypatch):
    import asyncio

    import services.garden.main as main_module

    bed = _bed(client)
    _bind(
        client, "location", bed["id"], grow_light_entity_id="light.grow_shelf",
        grow_light_enabled=True, grow_light_on_time="06:00", grow_light_off_time="20:00",
    )
    lit = [dict(row) for row in HUB_STATES]
    lit[5] = {**lit[5], "state": "on"}
    monkeypatch.setattr(hardware, "_call", Hub(states_rows=lit))
    hardware.invalidate()

    actions = asyncio.run(
        hardware.sync_grow_lights(
            lambda: main_module.app.state.engine, now=datetime(2026, 11, 20, 12, 0)
        )
    )

    assert actions == []


def test_a_hand_switched_light_is_left_until_the_window_changes(client, hub, monkeypatch):
    import asyncio

    import services.garden.main as main_module

    bed = _bed(client)
    _bind(
        client, "location", bed["id"], grow_light_entity_id="light.grow_shelf",
        grow_light_enabled=True, grow_light_on_time="06:00", grow_light_off_time="20:00",
    )

    def tick(hour, state):
        rows = [dict(row) for row in HUB_STATES]
        rows[5] = {**rows[5], "state": state}
        fake = Hub(states_rows=rows)
        monkeypatch.setattr(hardware, "_call", fake)
        hardware.invalidate()
        asyncio.run(hardware.sync_grow_lights(
            lambda: main_module.app.state.engine, now=datetime(2026, 11, 20, hour, 0)
        ))
        return fake.services()

    assert tick(12, "off") == ["light/turn_on"]  # first look: brought to the schedule
    assert tick(13, "off") == []  # someone switched it off by hand: left alone
    assert tick(21, "off") == []  # the window closed and it is already off
    assert tick(22, "on") == []  # switched on by hand at night: left alone
    assert tick(7, "on") == []  # the window opened and it is already on


def test_a_binding_must_name_the_right_kind_of_device(client, hub):
    bed = _bed(client)

    as_valve = client.put(f"/bindings/location/{bed['id']}", headers=PARENT,
                          json={"valve_entity_id": "light.grow_shelf"})
    as_moisture = client.put(f"/bindings/location/{bed['id']}", headers=PARENT,
                             json={"moisture_entity_id": "sensor.kitchen_temperature"})
    unknown = client.put(f"/bindings/location/{bed['id']}", headers=PARENT,
                         json={"grow_light_entity_id": "light.nowhere"})

    assert as_valve.status_code == 400 and "is not a valve" in as_valve.json()["detail"]
    assert as_moisture.status_code == 400
    assert unknown.status_code == 400


def test_binding_needs_the_hub_to_check_the_device(client, hub, monkeypatch):
    async def away(*_a, **_k):
        return (0, None)

    monkeypatch.setattr(hardware, "_call", away)
    hardware.invalidate()
    bed = _bed(client)

    resp = client.put(f"/bindings/location/{bed['id']}", headers=PARENT,
                      json={"valve_entity_id": "valve.bed1_drip"})

    assert resp.status_code == 503


# --------------------------------------------------------------------------- NFC / QR tags

def test_a_tag_scan_opens_the_bed_it_is_on(client, hub):
    bed = _bed(client)
    _plant(client, location_id=bed["id"])
    _bind(
        client, "location", bed["id"],
        nfc_tag_id="tag-bed1", moisture_entity_id="sensor.bed1_soil_moisture",
    )

    body = client.get("/tags/tag-bed1", headers=CHILD).json()

    assert body["matched"] is True
    assert body["target_kind"] == "location"
    assert body["target_name"] == "Bed 1"
    assert body["location"]["plant_count"] == 1
    assert body["readings"]["moisture_pct"] == 22.0
    assert body["actions"] == ["water", "photo"]


def test_a_tag_scan_on_a_plant_says_what_is_owed(client, hub, session):
    plant = _plant(client, nickname="Monty")
    _due_rule(session, plant["id"], when=datetime.now(UTC) - timedelta(days=2))
    _bind(client, "plant", plant["id"], nfc_tag_id="tag-monty")

    body = client.get("/tags/tag-monty", headers=CHILD).json()

    assert body["target_kind"] == "plant"
    assert body["plant"]["nickname"] == "Monty"
    assert body["due_today"] is True
    assert body["next_due_at"] is not None


def test_an_unknown_tag_says_so_plainly(client, hub):
    resp = client.get("/tags/tag-nope", headers=CHILD)

    assert resp.status_code == 404
    assert "not linked" in resp.json()["detail"]


def test_a_tag_scan_needs_a_person(client, hub):
    assert client.get("/tags/tag-bed1", headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 401


def test_removing_a_binding_forgets_its_tag(client, hub):
    bed = _bed(client)
    assert _bind(client, "location", bed["id"], nfc_tag_id="tag-bed1").status_code == 200

    assert client.delete(f"/bindings/location/{bed['id']}", headers=PARENT).status_code == 204

    assert client.get("/tags/tag-bed1", headers=CHILD).status_code == 404
    assert client.get("/bindings", headers=PARENT).json()["count"] == 0


def test_removing_a_binding_closes_an_open_valve_first(client, hub):
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])
    client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    resp = client.delete(f"/bindings/location/{bed['id']}", headers=PARENT)

    assert resp.status_code == 204
    assert hub.services()[-1] == "valve/close_valve"


def test_a_child_cannot_delete_a_binding(client, hub):
    bed = _bed(client)
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")

    assert client.delete(f"/bindings/location/{bed['id']}", headers=CHILD).status_code == 403


# --------------------------------------------------------------------------- one run, one count

def test_a_bed_run_counts_once_however_many_plants_it_waters(client, hub):
    bed = _bed(client)
    for index in range(3):
        _plant(client, nickname=f"Plant {index}", location_id=bed["id"])
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip", daily_max_runs=2)
    binding_id = _binding_id(client, "location", bed["id"])

    first = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    assert first.status_code == 200, first.text
    assert first.json()["runs_today"] == 1
    assert first.json()["watered_count"] == 3
    row = next(r for r in client.get("/bindings", headers=PARENT).json()["bindings"] if r["id"] == binding_id)
    assert row["runs_today"] == 1

    client.post(f"/bindings/{binding_id}/valve/stop", headers=PARENT)
    second = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})
    assert second.status_code == 200
    assert second.json()["runs_today"] == 2

    client.post(f"/bindings/{binding_id}/valve/stop", headers=PARENT)
    third = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})
    assert third.status_code == 409
    assert "Daily max runs reached (2 of 2 today)" in third.json()["detail"]


def test_an_empty_bed_still_counts_its_runs(client, hub):
    bed = _bed(client, "Bed with nothing in it yet")
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip", daily_max_runs=1)
    binding_id = _binding_id(client, "location", bed["id"])

    first = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    assert first.status_code == 200, first.text
    assert first.json()["watered_count"] == 0
    assert first.json()["runs_today"] == 1

    client.post(f"/bindings/{binding_id}/valve/stop", headers=PARENT)
    second = client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    assert second.status_code == 409
    assert "1 of 1 today" in second.json()["detail"]


def test_each_run_leaves_one_closed_row(client, hub, session):
    from sqlmodel import select as _select

    from services.garden.models import ValveRun

    bed = _bed(client)
    _plant(client, location_id=bed["id"])
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])

    client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={"minutes": 2})
    client.post(f"/bindings/{binding_id}/valve/stop", headers=PARENT)

    session.expire_all()
    runs = session.exec(_select(ValveRun).where(ValveRun.binding_id == binding_id)).all()
    assert len(runs) == 1
    assert runs[0].minutes == 2
    assert runs[0].trigger == "manual"
    assert runs[0].performed_by == "mother"
    assert runs[0].closed_at is not None


# --------------------------------------------------------------------------- opt-in auto watering

def _auto(client, boundary, *, daily_max_runs=2, plants=1):
    """A bed set to auto-water, with soil at 22% (below its 30% threshold)."""
    bed = _bed(client)
    for index in range(plants):
        _plant(client, nickname=f"Plant {index}", location_id=bed["id"])
    resp = _bind(
        client, "location", bed["id"],
        valve_entity_id="valve.bed1_drip",
        moisture_entity_id="sensor.bed1_soil_moisture",
        moisture_override_enabled=True,
        valve_mode="auto",
        daily_max_runs=daily_max_runs,
        max_run_minutes=6,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _run_auto(monkeypatch=None, bookkeeping=None):
    import asyncio

    import services.garden.main as main_module

    return asyncio.run(
        hardware.evaluate_auto_valves(
            lambda: main_module.app.state.engine,
            lambda: _parents(),
            bookkeeping,
        )
    )


async def _parents():
    return []


def test_auto_waters_a_dry_bed_and_logs_the_run(client, hub, session):
    from sqlmodel import select as _select

    from services.garden.models import CareLog, ValveRun

    import services.garden.main as main_module

    binding = _auto(client, None, plants=2)

    # The real callback the lifespan passes, so the care logs are the real ones.
    results = _run_auto(bookkeeping=main_module._auto_water_bookkeeping)

    assert results == [{"binding_id": binding["id"], "ran": True, "minutes": 6, "moisture_pct": 22.0}]
    assert hub.services() == ["valve/open_valve"]
    session.expire_all()
    runs = session.exec(_select(ValveRun).where(ValveRun.binding_id == binding["id"])).all()
    assert [(row.trigger, row.performed_by, row.minutes) for row in runs] == [("auto", "jarvis-auto", 6)]
    # Both plants in the bed got their care log, exactly like a tapped run.
    assert len(session.exec(_select(CareLog).where(CareLog.source == "homehub_valve")).all()) == 2
    row = next(r for r in client.get("/bindings", headers=PARENT).json()["bindings"] if r["id"] == binding["id"])
    assert row["runs_today"] == 1


def test_auto_does_nothing_for_notify_only(client, hub):
    bed = _bed(client)
    _bind(
        client, "location", bed["id"],
        valve_entity_id="valve.bed1_drip",
        moisture_entity_id="sensor.bed1_soil_moisture",
        moisture_override_enabled=True,
    )

    assert _run_auto() == []
    assert hub.services() == []


def test_auto_does_nothing_when_the_sensor_is_offline(client, hub, monkeypatch):
    _auto(client, None)
    dead = [dict(row) for row in HUB_STATES]
    dead[0] = {**dead[0], "state": "unavailable"}
    monkeypatch.setattr(hardware, "_call", Hub(states_rows=dead))
    hardware.invalidate()

    assert _run_auto(monkeypatch) == []
    assert hub.services() == []


def test_auto_does_nothing_when_the_soil_is_wet(client, hub, monkeypatch):
    _auto(client, None)
    wet = [dict(row) for row in HUB_STATES]
    wet[0] = {**wet[0], "state": "71"}
    monkeypatch.setattr(hardware, "_call", Hub(states_rows=wet))
    hardware.invalidate()

    assert _run_auto(monkeypatch) == []
    assert hub.services() == []


def test_auto_respects_the_daily_cap(client, hub):
    binding = _auto(client, None, daily_max_runs=1)
    first = _run_auto()
    assert first[0]["ran"] is True
    # Close it so the next evaluation is blocked by the cap, not by "already open".
    assert client.post(f"/bindings/{binding['id']}/valve/stop", headers=PARENT).status_code == 200

    second = _run_auto()

    assert second[0]["ran"] is False
    assert "Daily max runs reached" in second[0]["reason"]
    assert hub.services().count("valve/open_valve") == 1


def test_auto_respects_the_rain_interlock(client, hub, monkeypatch):
    _auto(client, None)

    async def raining(_profile=None):
        return {"active": True, "known": True, "rain_in": 0.7, "threshold_in": 0.25}

    monkeypatch.setattr(weather_care, "rain_state", raining)

    results = _run_auto()

    assert results[0]["ran"] is False
    assert "rain interlock" in results[0]["reason"]
    assert hub.services() == []


def test_the_auto_notice_names_what_was_watered(client, hub, monkeypatch):
    _auto(client, None)
    alerts: list[dict] = []

    async def capture(payload, **_kw):
        alerts.append(payload)

    monkeypatch.setattr(hardware.notify, "send", capture)

    async def parents():
        return ["mother"]

    import asyncio

    import services.garden.main as main_module

    asyncio.run(
        hardware.evaluate_auto_valves(
            lambda: main_module.app.state.engine, parents, None
        )
    )

    assert len(alerts) == 1
    assert alerts[0]["title"].startswith("Auto-watered")
    assert "soil 22%" in alerts[0]["body"]
    assert alerts[0]["audience"] == "parents"


# --------------------------------------------------------------------------- progressive disclosure

def test_ordinary_smart_bulbs_are_not_grow_lights(client, monkeypatch):
    """A house of smart bulbs must not sprout a hardware panel."""
    bulbs = Hub(
        states_rows=[
            {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
            {"entity_id": "light.bedroom", "state": "off", "attributes": {"friendly_name": "Bedroom"}},
            {"entity_id": "switch.lamp", "state": "off", "attributes": {"friendly_name": "Lamp"}},
        ],
        tags_rows=[],
    )
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test")
    monkeypatch.setattr(hardware, "_call", bulbs)
    hardware.invalidate()

    body = client.get("/hardware/discover", headers=CHILD).json()

    assert body["available"] is True
    assert body["grow_lights"] == []
    assert body["has_any_hardware"] is False


def test_a_light_named_for_growing_is_a_grow_light(client, hub):
    body = client.get("/hardware/discover", headers=CHILD).json()

    assert [row["entity_id"] for row in body["grow_lights"]] == ["light.grow_shelf"]


# --------------------------------------------------------------------------- deleting a target

def test_deleting_a_plant_stops_its_valve_and_takes_its_rows(client, hub, session):
    from sqlmodel import select as _select

    from services.garden.models import CareLog, CareRule, HomeHubBinding, ValveRun

    bed = _bed(client)
    plant = _plant(client, nickname="Monty", location_id=bed["id"])
    _bind(client, "plant", plant["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "plant", plant["id"])
    client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})

    resp = client.delete(f"/plants/{plant['id']}", headers=PARENT)

    assert resp.status_code == 204, resp.text
    # The valve was closed before anything was removed.
    assert hub.services()[-1] == "valve/close_valve"
    session.expire_all()
    assert session.exec(_select(HomeHubBinding).where(HomeHubBinding.target_id == plant["id"])).all() == []
    assert session.exec(_select(ValveRun).where(ValveRun.binding_id == binding_id)).all() == []
    assert session.exec(_select(CareRule).where(CareRule.plant_id == plant["id"])).all() == []
    assert session.exec(_select(CareLog).where(CareLog.plant_id == plant["id"])).all() == []


def test_deleting_a_bed_takes_its_binding_and_logs(client, hub, session):
    from sqlmodel import select as _select

    from services.garden.models import CareLog, HomeHubBinding

    bed = _bed(client, "Empty Bed")
    _bind(client, "location", bed["id"], valve_entity_id="valve.bed1_drip")
    binding_id = _binding_id(client, "location", bed["id"])
    client.post(f"/bindings/{binding_id}/valve/run", headers=PARENT, json={})
    client.delete(f"/locations/{bed['id']}", headers=PARENT)

    session.expire_all()
    assert session.exec(
        _select(HomeHubBinding).where(HomeHubBinding.target_id == bed["id"])
    ).all() == []
    assert session.exec(_select(CareLog).where(CareLog.location_id == bed["id"])).all() == []
