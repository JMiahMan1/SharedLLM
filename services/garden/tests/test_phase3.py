"""Phase 3 tests for the Garden service (services/garden/main.py, weather_care.py,
species_lookup.py, cadence.py).

Covers: rain-skip for uncovered outdoor beds only (never indoor/covered),
rain-skip dedup per episode, heatwave boost in cadence, frost alerts by
frost_tolerance, custom_species CRUD + enum/safety validation, adult/admin
permission guards, POST /species/lookup Tier 1 + Tier 2 draft flow, unified
species resolution in GET /species/search and create_plant, and weather
summary on GET /today.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden.main import app  # noqa: E402
from services.garden.models import (  # noqa: E402
    CareLog, CareRule, CustomSpecies, GardenProfile, Location, Plant,
)
from services.garden.cadence import heat_factor, compute_interval
from scripts.build_plants_seed import build_seed  # noqa: E402

PARENT_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "mother", "X-User-Role": "mother",
    "X-Is-Admin": "true", "X-Family-Manage": "true",
}
ADULT_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "adult_child", "X-User-Role": "adult_child",
    "X-Is-Admin": "false", "X-Family-Manage": "false",
}
TEEN_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "teen", "X-User-Role": "teen",
    "X-Is-Admin": "false", "X-Family-Manage": "false",
}
CHILD_HEADERS = {
    "X-Internal-Secret": INTERNAL_SECRET,
    "X-User-Id": "child", "X-User-Role": "child",
    "X-Is-Admin": "false", "X-Family-Manage": "false",
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
def garden_client(tmp_seed_path, db_url, monkeypatch):
    import services.garden.main as main_module
    from sqlmodel import SQLModel

    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", db_url)
    monkeypatch.setenv("PLANT_SEED_PATH", tmp_seed_path)

    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    main_module.app.state.engine = engine
    return TestClient(app), engine


# --------------------------------------------------------------------------- helpers


def _make_location(garden_client, headers, name="Living Room", kind="indoor_room", covered=False):
    client, _ = garden_client
    resp = client.post("/locations", headers=headers, json={"name": name, "kind": kind, "covered": covered})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_plant(garden_client, headers, species_ref="monstera_deliciosa", location_id=None,
                nickname=None, category="houseplant"):
    client, _ = garden_client
    body = {"category": category, "species_ref": species_ref}
    if location_id:
        body["location_id"] = location_id
    if nickname:
        body["nickname"] = nickname
    resp = client.post("/plants", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- heat_factor (cadence)


def test_heat_factor_returns_075_in_heatwave():
    assert heat_factor(95.0, dry=True) == 0.75
    assert heat_factor(90.0, dry=True) == 0.75


def test_heat_factor_returns_1_when_not_hot_or_not_dry():
    assert heat_factor(85.0, dry=True) == 1.0
    assert heat_factor(95.0, dry=False) == 1.0
    assert heat_factor(None, dry=True) == 1.0


# --------------------------------------------------------------------------- rain-skip


def test_rain_skip_applies_to_uncovered_outdoor_bed(garden_client, monkeypatch):
    client, engine = garden_client
    loc = _make_location(garden_client, PARENT_HEADERS, name="Bed 1", kind="raised_bed", covered=False)
    plant = _make_plant(garden_client, PARENT_HEADERS, species_ref="basil_sweet",
                        location_id=loc["id"], category="herb", nickname="Patio Basil")

    with Session(engine) as session:
        rule = session.exec(select(CareRule).where(CareRule.plant_id == plant["id"],
                                                   CareRule.action == "water")).first()
        assert rule is not None
        rule.rain_skip_enabled = True
        session.add(rule)
        session.commit()

    with patch("services.garden.weather_care._observed_and_forecast_rain_in", return_value=0.5):
        from services.garden import weather_care as wc
        with Session(engine) as session:
            profile = session.get(GardenProfile, "household")
            skipped = asyncio.run(wc.apply_rain_skip(session, profile))

    assert len(skipped) == 1
    assert skipped[0]["plant_id"] == plant["id"]
    assert skipped[0]["nickname"] == "Patio Basil"

    with Session(engine) as session:
        logs = session.exec(select(CareLog).where(CareLog.plant_id == plant["id"])).all()
        rain_logs = [l for l in logs if l.action == "rain_skip" and l.source == "rain_auto_skip"]
        assert len(rain_logs) == 1
        assert "0.50 in rain" in rain_logs[0].notes

        # Re-fetch rule in this session (detached from previous session).
        rule = session.exec(select(CareRule).where(CareRule.plant_id == plant["id"],
                                                   CareRule.action == "water")).first()
        next_due = datetime.fromisoformat(rule.next_due_at)
        assert next_due > datetime.now(UTC)


def test_rain_skip_never_skips_indoor_room(garden_client, monkeypatch):
    client, engine = garden_client
    loc = _make_location(garden_client, PARENT_HEADERS, name="Living Room", kind="indoor_room", covered=False)
    plant = _make_plant(garden_client, PARENT_HEADERS, species_ref="monstera_deliciosa",
                        location_id=loc["id"], category="houseplant", nickname="Monty")

    with Session(engine) as session:
        rule = session.exec(select(CareRule).where(CareRule.plant_id == plant["id"],
                                                   CareRule.action == "water")).first()
        rule.rain_skip_enabled = True
        session.add(rule)
        session.commit()

    with patch("services.garden.weather_care._observed_and_forecast_rain_in", return_value=0.5):
        from services.garden import weather_care as wc
        with Session(engine) as session:
            profile = session.get(GardenProfile, "household")
            skipped = asyncio.run(wc.apply_rain_skip(session, profile))

    assert skipped == []


def test_rain_skip_never_skips_covered_pot(garden_client, monkeypatch):
    client, engine = garden_client
    loc = _make_location(garden_client, PARENT_HEADERS, name="Porch", kind="container", covered=True)
    plant = _make_plant(garden_client, PARENT_HEADERS, species_ref="basil_sweet",
                        location_id=loc["id"], category="herb", nickname="Covered Basil")

    with Session(engine) as session:
        rule = session.exec(select(CareRule).where(CareRule.plant_id == plant["id"],
                                                   CareRule.action == "water")).first()
        rule.rain_skip_enabled = True
        session.add(rule)
        session.commit()

    with patch("services.garden.weather_care._observed_and_forecast_rain_in", return_value=0.5):
        from services.garden import weather_care as wc
        with Session(engine) as session:
            profile = session.get(GardenProfile, "household")
            skipped = asyncio.run(wc.apply_rain_skip(session, profile))

    assert skipped == []


def test_rain_skip_dedupes_within_episode(garden_client, monkeypatch):
    client, engine = garden_client
    loc = _make_location(garden_client, PARENT_HEADERS, name="Bed 1", kind="raised_bed", covered=False)
    plant = _make_plant(garden_client, PARENT_HEADERS, species_ref="basil_sweet",
                        location_id=loc["id"], category="herb", nickname="Patio Basil")

    with Session(engine) as session:
        rule = session.exec(select(CareRule).where(CareRule.plant_id == plant["id"],
                                                   CareRule.action == "water")).first()
        rule.rain_skip_enabled = True
        session.add(rule)
        session.commit()

    from services.garden import weather_care as wc

    with patch("services.garden.weather_care._observed_and_forecast_rain_in", return_value=0.5):
        with Session(engine) as session:
            profile = session.get(GardenProfile, "household")
            skipped1 = asyncio.run(wc.apply_rain_skip(session, profile))
    assert len(skipped1) == 1

    with patch("services.garden.weather_care._observed_and_forecast_rain_in", return_value=0.5):
        with Session(engine) as session:
            profile = session.get(GardenProfile, "household")
            skipped2 = asyncio.run(wc.apply_rain_skip(session, profile))
    assert skipped2 == []

    with Session(engine) as session:
        logs = session.exec(select(CareLog).where(CareLog.plant_id == plant["id"],
                                                  CareLog.action == "rain_skip",
                                                  CareLog.source == "rain_auto_skip")).all()
        assert len(logs) == 1


def test_rain_skip_only_when_threshold_met(garden_client, monkeypatch):
    client, engine = garden_client
    loc = _make_location(garden_client, PARENT_HEADERS, name="Bed 1", kind="raised_bed", covered=False)
    _make_plant(garden_client, PARENT_HEADERS, species_ref="basil_sweet",
                location_id=loc["id"], category="herb")

    with Session(engine) as session:
        rule = session.exec(select(CareRule).where(CareRule.action == "water")).first()
        rule.rain_skip_enabled = True
        session.add(rule)
        session.commit()

    from services.garden import weather_care as wc

    with patch("services.garden.weather_care._observed_and_forecast_rain_in", return_value=0.1):
        with Session(engine) as session:
            profile = session.get(GardenProfile, "household")
            skipped = asyncio.run(wc.apply_rain_skip(session, profile))
    assert skipped == []


# --------------------------------------------------------------------------- frost alerts


def test_frost_alerts_tender_flagged_hardy_safe(garden_client, monkeypatch):
    client, engine = garden_client
    loc = _make_location(garden_client, PARENT_HEADERS, name="Bed 1", kind="raised_bed", covered=False)

    tender = _make_plant(garden_client, PARENT_HEADERS, species_ref="basil_sweet",
                         location_id=loc["id"], category="herb", nickname="Patio Basil")
    hardy = _make_plant(garden_client, PARENT_HEADERS, species_ref="kale",
                        location_id=loc["id"], category="vegetable", nickname="Garden Kale")

    with Session(engine) as session:
        for p in [tender, hardy]:
            rule = session.exec(select(CareRule).where(CareRule.plant_id == p["id"],
                                                       CareRule.action == "water")).first()
            if rule:
                rule.rain_skip_enabled = True
                session.add(rule)
        session.commit()

    forecast = {
        "daily": [
            {"high": 55, "low": 40},
            {"high": 45, "low": 30},
        ],
    }
    from services.garden import weather_care as wc
    from services.garden import plant_seed
    ps = plant_seed.PlantSeed(__import__("os").getenv("PLANT_SEED_PATH") or "/data/plants-seed.db")
    with Session(engine) as session:
        alerts = asyncio.run(wc.check_frost_alerts(session, forecast, ps.get))

    assert len(alerts) == 1
    names = alerts[0]["names"]
    assert "Patio Basil" in names
    assert "Garden Kale" not in names
    assert alerts[0]["min_temp_f"] == 30.0


def test_frost_alerts_none_when_weather_unavailable(garden_client):
    client, engine = garden_client
    from services.garden import weather_care as wc
    with Session(engine) as session:
        alerts = asyncio.run(wc.check_frost_alerts(session, None, lambda k: None))
    assert alerts == []


# --------------------------------------------------------------------------- custom_species CRUD


def test_create_custom_species_valid(garden_client):
    client, _ = garden_client
    resp = client.post("/species/custom", headers=PARENT_HEADERS, json={
        "common_name": "Thai Holy Basil",
        "category": "herb",
        "scientific_name": "Ocimum tenuiflorum",
        "winter_multiplier": 1.5,
        "watering_method": "top_drain",
        "frost_tolerance": "tender",
        "toxicity_level": "non_toxic",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["key"] == "thai_holy_basil"
    assert data["source"] == "manual"


def test_create_custom_species_rejects_winter_multiplier_below_1(garden_client):
    client, _ = garden_client
    resp = client.post("/species/custom", headers=PARENT_HEADERS, json={
        "common_name": "Bad Plant",
        "category": "herb",
        "winter_multiplier": 0.5,
    })
    assert resp.status_code == 400, resp.text
    assert "winter_multiplier must be >= 1.0" in resp.json()["detail"]


def test_create_custom_species_rejects_low_acid_water_bath(garden_client):
    client, _ = garden_client
    resp = client.post("/species/custom", headers=PARENT_HEADERS, json={
        "common_name": "Green Beans",
        "category": "vegetable",
        "usda_acidity_class": "low_acid",
        "preservation_methods": "water_bath_canning",
    })
    assert resp.status_code == 400, resp.text
    assert "pressure canning" in resp.json()["detail"].lower()


def test_create_custom_species_403_for_teen(garden_client):
    client, _ = garden_client
    resp = client.post("/species/custom", headers=TEEN_HEADERS, json={
        "common_name": "Teen Plant",
        "category": "herb",
    })
    assert resp.status_code == 403, resp.text


def test_get_custom_species_in_search(garden_client):
    client, _ = garden_client
    client.post("/species/custom", headers=PARENT_HEADERS, json={
        "common_name": "My Rare Basil",
        "category": "herb",
    })
    resp = client.get("/species/search", headers=PARENT_HEADERS, params={"q": "Rare Basil"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    keys = [r["key"] for r in data["results"]]
    assert "my_rare_basil" in keys
    rare = [r for r in data["results"] if r.get("custom")]
    assert len(rare) >= 1


def test_delete_custom_species(garden_client):
    client, _ = garden_client
    client.post("/species/custom", headers=PARENT_HEADERS, json={
        "common_name": "To Delete",
        "category": "herb",
    })
    resp = client.delete("/species/custom/to_delete", headers=PARENT_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    resp = client.delete("/species/custom/to_delete", headers=PARENT_HEADERS)
    assert resp.status_code == 404, resp.text


def test_delete_custom_species_403_for_child(garden_client):
    client, _ = garden_client
    resp = client.delete("/species/custom/some_key", headers=CHILD_HEADERS)
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------------- species lookup (mocked Tier 2)


def test_species_lookup_tier1_seed_hit(garden_client):
    client, _ = garden_client
    resp = client.post("/species/lookup", headers=PARENT_HEADERS, json={
        "name": "basil_sweet",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["draft"] is False
    assert data["source"] == "seed"
    assert data["species"]["common_name"] == "Sweet Basil"


def test_species_lookup_tier2_draft(garden_client, monkeypatch):
    client, _ = garden_client
    import services.garden.species_lookup as sl

    async def fake_draft(name, category, wiki, warnings):
        return {
            "key": "rare_plant_x",
            "category": "houseplant",
            "common_name": "Rare Plant X",
            "scientific_name": "Rarus plantus",
            "watering_interval_days": 10.0,
            "winter_multiplier": 1.2,
            "watering_method": "top_drain",
            "frost_tolerance": "tender",
            "toxicity_level": "non_toxic",
            "source": "wiki_ai_draft",
        }

    async def fake_fetch(url):
        return {"extract": "A rare plant from nowhere."}

    monkeypatch.setattr(sl, "_draft_via_llm", fake_draft)
    monkeypatch.setattr(sl, "_fetch_json", fake_fetch)

    resp = client.post("/species/lookup", headers=PARENT_HEADERS, json={
        "name": "Rare Plant X",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["draft"] is True
    assert data["source"] == "wiki_ai_draft"
    assert data["species"]["common_name"] == "Rare Plant X"

    # Never auto-saved: not in custom_species yet.
    resp2 = client.get("/species/search", headers=PARENT_HEADERS, params={"q": "Rare Plant X"})
    assert resp2.status_code == 200
    keys = [r["key"] for r in resp2.json()["results"]]
    assert "rare_plant_x" not in keys


def test_species_lookup_403_for_child(garden_client):
    client, _ = garden_client
    resp = client.post("/species/lookup", headers=CHILD_HEADERS, json={
        "name": "Something",
    })
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------------- create_plant with custom species


def test_create_plant_with_custom_species(garden_client):
    client, engine = garden_client
    client.post("/species/custom", headers=PARENT_HEADERS, json={
        "common_name": "My Custom Fern",
        "category": "houseplant",
        "watering_interval_days": 14,
        "winter_multiplier": 1.0,
    })

    loc = _make_location(garden_client, PARENT_HEADERS, name="Fern Room", kind="indoor_room")
    resp = client.post("/plants", headers=PARENT_HEADERS, json={
        "category": "houseplant",
        "species_ref": "my_custom_fern",
        "location_id": loc["id"],
        "nickname": "Fernie",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["nickname"] == "Fernie"
    assert data["species"]["common_name"] == "My Custom Fern"

    # GET /plants resolves custom species.
    resp = client.get("/plants", headers=PARENT_HEADERS)
    assert resp.status_code == 200, resp.text
    plants = resp.json()
    fern = [p for p in plants if p["id"] == data["id"]]
    assert len(fern) == 1
    assert fern[0]["species"]["common_name"] == "My Custom Fern"


# --------------------------------------------------------------------------- weather summary on /today degrades gracefully


def test_today_weather_null_when_unavailable(garden_client, monkeypatch):
    client, _ = garden_client
    import services.garden.weather_care as wc
    monkeypatch.setattr(wc, "build_weather_summary", lambda *a, **k: None)

    resp = client.get("/today", headers=PARENT_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["weather"] is None


def test_hooks_weather_requires_secret(garden_client):
    client, _ = garden_client
    resp = client.post("/hooks/weather", headers={"X-Internal-Secret": "wrong"})
    assert resp.status_code == 403, resp.text


def test_hooks_weather_works_with_secret(garden_client, monkeypatch):
    client, _ = garden_client
    import services.garden.weather_care as wc
    monkeypatch.setattr(wc, "handle_weather_webhook",
                        lambda *a, **k: {"evaluated": True, "weather": None})
    resp = client.post("/hooks/weather", headers={"X-Internal-Secret": INTERNAL_SECRET})
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- regression tests (integration bugs)


def test_custom_species_preserves_watering_interval_days(garden_client):
    client, engine = garden_client
    resp = client.post("/species/custom", headers=PARENT_HEADERS, json={
        "common_name": "Thai Holy Basil",
        "category": "herb",
        "watering_interval_days": 10.5,
        "winter_multiplier": 1.5,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["ok"] is True

    # Verify it was saved with the watering interval preserved.
    with Session(engine) as session:
        row = session.exec(select(CustomSpecies).where(CustomSpecies.key == "thai_holy_basil")).first()
        assert row is not None
        assert row.watering_interval_days == 10.5
        assert row.winter_multiplier == 1.5


def test_weather_metric_25c_does_not_trigger_frost(garden_client, monkeypatch):
    """Metric forecast: 25°C (77°F) should NOT trigger a tender frost alert."""
    client, engine = garden_client
    import services.garden.weather_care as wc

    # Metric forecast: 25°C low = 77°F, well above tender threshold (36°F).
    forecast = {
        "units": "metric",
        "daily": [
            {"high": 28, "low": 20},   # today: 28°C / 20°C
            {"high": 25, "low": 25},   # tomorrow: 25°C / 25°C (77°F — not freezing)
        ],
        "hourly": [{"gust": 10} for _ in range(12)],  # 10 km/h gusts (~6 mph)
    }
    loc = _make_location(garden_client, PARENT_HEADERS, name="Bed 1", kind="raised_bed", covered=False)
    tender = _make_plant(garden_client, PARENT_HEADERS, species_ref="basil_sweet",
                         location_id=loc["id"], category="herb", nickname="Patio Basil")
    with Session(engine) as session:
        rule = session.exec(select(CareRule).where(CareRule.plant_id == tender["id"],
                                                   CareRule.action == "water")).first()
        if rule:
            rule.rain_skip_enabled = True
            session.add(rule)
        session.commit()

    from services.garden import plant_seed
    ps = plant_seed.PlantSeed(__import__("os").getenv("PLANT_SEED_PATH") or "/data/plants-seed.db")
    alerts = asyncio.run(wc.check_frost_alerts(session, forecast, ps.get, units="metric"))
    assert alerts == []


def test_weather_metric_1c_triggers_tender_frost(garden_client, monkeypatch):
    """Metric forecast: 1°C (33.8°F) SHOULD trigger a tender frost alert."""
    client, engine = garden_client
    import services.garden.weather_care as wc

    forecast = {
        "units": "metric",
        "daily": [
            {"high": 5, "low": 2},    # today
            {"high": 3, "low": 1},    # tomorrow: 1°C = 33.8°F <= 36°F tender threshold
        ],
        "hourly": [{"gust": 5} for _ in range(12)],
    }
    loc = _make_location(garden_client, PARENT_HEADERS, name="Bed 1", kind="raised_bed", covered=False)
    tender = _make_plant(garden_client, PARENT_HEADERS, species_ref="basil_sweet",
                         location_id=loc["id"], category="herb", nickname="Patio Basil")
    hardy = _make_plant(garden_client, PARENT_HEADERS, species_ref="kale",
                        location_id=loc["id"], category="vegetable", nickname="Garden Kale")
    with Session(engine) as session:
        for p in [tender, hardy]:
            rule = session.exec(select(CareRule).where(CareRule.plant_id == p["id"],
                                                       CareRule.action == "water")).first()
            if rule:
                rule.rain_skip_enabled = True
                session.add(rule)
        session.commit()

    from services.garden import plant_seed
    ps = plant_seed.PlantSeed(__import__("os").getenv("PLANT_SEED_PATH") or "/data/plants-seed.db")
    alerts = asyncio.run(wc.check_frost_alerts(session, forecast, ps.get, units="metric"))
    assert len(alerts) == 1
    assert "Patio Basil" in alerts[0]["names"]
    assert "Garden Kale" not in alerts[0]["names"]  # hardy, safe at 33.8°F


def test_weather_singular_gust_key(garden_client, monkeypatch):
    """weather.py emits 'gust' (singular) — wind advisory must pick it up."""
    client, engine = garden_client
    import services.garden.weather_care as wc

    forecast = {
        "units": "imperial",
        "daily": [{"high": 60, "low": 40}, {"high": 55, "low": 35}],
        "hourly": [{"gust": 40} for _ in range(12)],  # 40 mph >= 35 mph threshold
    }
    loc = _make_location(garden_client, PARENT_HEADERS, name="Bed 1", kind="raised_bed", covered=False)
    plant = _make_plant(garden_client, PARENT_HEADERS, species_ref="tomato_cherry",
                        location_id=loc["id"], category="vegetable", nickname="Trellis Tomato")
    with Session(engine) as session:
        rule = session.exec(select(CareRule).where(CareRule.plant_id == plant["id"],
                                                   CareRule.action == "water")).first()
        if rule:
            rule.rain_skip_enabled = True
            session.add(rule)
        session.commit()

    from services.garden import plant_seed
    ps = plant_seed.PlantSeed(__import__("os").getenv("PLANT_SEED_PATH") or "/data/plants-seed.db")
    wind = asyncio.run(wc.check_wind_advisory(session, forecast, ps.get, units="imperial"))
    assert wind is not None
    assert "Trellis Tomato" in wind["names"]
    assert wind["gusts_mph"] == 40.0


def test_weather_zero_f_not_dropped(garden_client, monkeypatch):
    """0.0°F forecast low must NOT be dropped by falsy `or` checks."""
    client, engine = garden_client
    import services.garden.weather_care as wc

    forecast = {
        "units": "imperial",
        "daily": [
            {"high": 20, "low": 10},   # today
            {"high": 15, "low": 0.0},  # tomorrow: exactly 0.0°F hard freeze
        ],
        "hourly": [{"gust": 5} for _ in range(12)],
    }
    loc = _make_location(garden_client, PARENT_HEADERS, name="Bed 1", kind="raised_bed", covered=False)
    hardy = _make_plant(garden_client, PARENT_HEADERS, species_ref="kale",
                        location_id=loc["id"], category="vegetable", nickname="Garden Kale")
    with Session(engine) as session:
        rule = session.exec(select(CareRule).where(CareRule.plant_id == hardy["id"],
                                                   CareRule.action == "water")).first()
        if rule:
            rule.rain_skip_enabled = True
            session.add(rule)
        session.commit()

    from services.garden import plant_seed
    ps = plant_seed.PlantSeed(__import__("os").getenv("PLANT_SEED_PATH") or "/data/plants-seed.db")
    alerts = asyncio.run(wc.check_frost_alerts(session, forecast, ps.get, units="imperial"))
    assert len(alerts) == 1
    assert alerts[0]["min_temp_f"] == 0.0  # 0.0°F <= 15°F hardy extreme freeze threshold


def test_draft_via_llm_parses_chat_completions(garden_client, monkeypatch):
    """_draft_via_llm() must parse OpenAI /v1/chat/completions response format."""
    import services.garden.species_lookup as sl
    from unittest.mock import patch

    captured = {}

    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def json(self, content_type=None):
            return self._data

    class FakeClient:
        def post(self, url, *, json=None, headers=None, timeout=None):
            import json as json_module
            captured["url"] = str(url)
            captured["json"] = json
            return FakeResp({
                "choices": [{
                    "message": {
                        "content": json_module.dumps({
                            "key": "test_plant",
                            "category": "houseplant",
                            "common_name": "Test Plant",
                            "watering_interval_days": 7.0,
                            "winter_multiplier": 1.0,
                            "watering_method": "top_drain",
                            "frost_tolerance": "hardy",
                            "toxicity_level": "non_toxic",
                            "source": "wiki_ai_draft",
                        })
                    }
                }]
            })

    with patch("services.common.http.get_client", return_value=FakeClient()):
        monkeypatch.setattr(sl, "_fetch_json", lambda url: None)  # no Wikipedia

        result = asyncio.run(sl._draft_via_llm("Test Plant", "houseplant", None, []))
    assert result is not None, f"Expected non-None result"
    assert result["common_name"] == "Test Plant"
    # Must have called /v1/chat/completions, not /api/llm/generate.
    assert "/v1/chat/completions" in captured["url"]
