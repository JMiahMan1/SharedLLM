"""Phase 6: harvest forecast (maturity + GDD), preservation advice with USDA
safety guardrails, combo ideas, prep checklists, and the Put-Up Pantry.

Uses the same temporary garden database + real plant seed as ``test_phase5.py``.
Weather is passed in as a plain ``daily`` series so the GDD maths is
deterministic; one test proves the route degrades cleanly with no weather.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden import preservation  # noqa: E402
from services.garden.main import app  # noqa: E402
from services.garden.models import PantryBatch, PantryGoal, Plant  # noqa: E402
from scripts.build_plants_seed import build_seed  # noqa: E402


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

TODAY = date(2026, 11, 20)


@pytest.fixture()
def tmp_seed_path(tmp_path):
    seed_path = tmp_path / "plants-seed.db"
    build_seed(seed_path)
    return str(seed_path)


@pytest.fixture()
def client(tmp_seed_path, tmp_path, monkeypatch):
    import services.garden.main as main_module

    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path / 'garden.db'}")
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


def _grow(session: Session, species_ref: str, *, category: str = "vegetable", planted: date | None = None) -> Plant:
    plant = Plant(
        category=category,
        species_ref=species_ref,
        nickname=species_ref.split("_")[0].title(),
        planted_date=(planted or (TODAY - timedelta(days=40))).isoformat(),
    )
    session.add(plant)
    session.commit()
    session.refresh(plant)
    return plant


def _temps(start: date, days: int, high: float, low: float) -> list[dict]:
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "high": high, "low": low}
        for i in range(days)
    ]


# --------------------------------------------------------------------------- harvest forecast

def test_forecast_without_weather_is_plain_maturity(session):
    _grow(session, "carrot", planted=TODAY - timedelta(days=50))

    out = preservation.compute_harvest_forecast(session, today=TODAY)

    assert out["weather_used"] is False
    assert out["count"] == 1
    item = out["items"][0]
    assert item["estimated_ready_date"] == (TODAY + timedelta(days=20)).isoformat()
    assert item["days_until_ready"] == 20
    assert item["gdd_adjusted"] is False


def test_a_hot_season_pulls_the_harvest_date_in(session):
    _grow(session, "tomato_roma", planted=TODAY - timedelta(days=40))
    # A moderate season so far (70/50 F) with a hot fortnight ahead (90/70 F).
    hot = _temps(TODAY - timedelta(days=41), 41, 70, 50) + _temps(TODAY + timedelta(days=1), 14, 90, 70)

    out = preservation.compute_harvest_forecast(session, days=90, daily=hot, today=TODAY)

    item = out["items"][0]
    assert out["weather_used"] is True
    assert item["gdd_adjusted"] is True
    assert item["gdd_shift_days"] < 0
    assert item["days_until_ready"] < 35  # plain would be 75 - 40 = 35


def test_a_cold_spell_pushes_the_harvest_date_out(session):
    _grow(session, "tomato_roma", planted=TODAY - timedelta(days=40))
    # A moderate season so far (70/50 F), then a cold fortnight ahead (58/48 F).
    cold = _temps(TODAY - timedelta(days=41), 41, 70, 50) + _temps(TODAY + timedelta(days=1), 14, 58, 48)

    out = preservation.compute_harvest_forecast(session, days=200, daily=cold, today=TODAY)

    item = out["items"][0]
    assert item["gdd_shift_days"] >= 0
    assert item["days_until_ready"] >= 35


def test_weather_entirely_below_the_base_keeps_the_plain_estimate(session):
    _grow(session, "carrot", planted=TODAY - timedelta(days=50))
    freezing = _temps(TODAY - timedelta(days=50), 60, 40, 30)

    out = preservation.compute_harvest_forecast(session, daily=freezing, today=TODAY)

    item = out["items"][0]
    assert item["gdd_adjusted"] is False
    assert item["estimated_ready_date"] == (TODAY + timedelta(days=20)).isoformat()


def test_hardy_crops_use_the_lower_base_temperature(session):
    _grow(session, "carrot", planted=TODAY - timedelta(days=50))  # hardy_annual
    _grow(session, "zucchini", planted=TODAY - timedelta(days=20))  # tender_annual

    out = preservation.compute_harvest_forecast(session, days=90, daily=_temps(TODAY, 5, 70, 50), today=TODAY)
    bases = {item["species_ref"]: item["gdd_base_f"] for item in out["items"]}

    assert bases == {"carrot": 40, "zucchini": 50}


def test_the_window_filters_far_off_crops(session):
    _grow(session, "garlic", planted=TODAY - timedelta(days=10))  # 240 days
    _grow(session, "carrot", planted=TODAY - timedelta(days=50))  # 20 days

    out = preservation.compute_harvest_forecast(session, days=28, today=TODAY)

    assert [item["species_ref"] for item in out["items"]] == ["carrot"]


def test_houseplants_are_not_harvested(session):
    _grow(session, "monstera_deliciosa", category="houseplant")

    assert preservation.compute_harvest_forecast(session, today=TODAY)["count"] == 0


def test_an_expected_harvest_start_is_used_without_maturity_data(session):
    plant = _grow(session, "basil_sweet", category="herb")
    plant.planted_date = None
    plant.expected_harvest_start = (TODAY + timedelta(days=5)).isoformat()
    session.add(plant)
    session.commit()

    item = preservation.compute_harvest_forecast(session, today=TODAY)["items"][0]

    assert item["estimated_ready_date"] == (TODAY + timedelta(days=5)).isoformat()
    assert item["gdd_adjusted"] is False


def test_forecast_items_carry_the_seed_facts_the_ui_needs(session):
    _grow(session, "green_bean_bush", planted=TODAY - timedelta(days=40))

    item = preservation.compute_harvest_forecast(session, today=TODAY)["items"][0]

    assert item["blanching_minutes"] == 3
    assert item["usda_acidity_class"] == "low_acid"
    assert "pressure_canning" in item["preservation_methods"]
    assert item["harvest_window_end"] > item["estimated_ready_date"]
    assert item["common_name"] == "Bush Green Bean"


# --------------------------------------------------------------------------- preservation advice

def _advice(session):
    return preservation.build_preservation_advice(session, today=TODAY)


def test_a_low_acid_crop_requires_a_pressure_canner(session):
    _grow(session, "green_bean_bush")

    crop = _advice(session)["crops"][0]

    assert crop["usda_acidity_class"] == "low_acid"
    assert crop["safety"]["level"] == "pressure_canner_required"
    assert "botulism" in crop["safety"]["message"]
    assert "Water-bath" not in crop["safety"]["title"]
    assert [m["method"] for m in crop["methods"]] == ["freezing", "pressure_canning", "dehydrating"]


def test_carrots_are_guarded_the_same_way(session):
    _grow(session, "carrot")

    crop = _advice(session)["crops"][0]

    assert crop["safety"]["level"] == "pressure_canner_required"
    assert any(m["method"] == "pressure_canning" for m in crop["methods"])


def test_a_high_acid_crop_is_water_bath_safe_and_needs_added_acid(session):
    _grow(session, "tomato_roma")

    crop = _advice(session)["crops"][0]

    assert crop["usda_acidity_class"] == "high_acid"
    assert crop["safety"]["level"] == "water_bath_safe"
    assert "citric acid" in crop["safety"]["message"]
    assert "USDA" in crop["safety"]["source"]


def test_freezing_carries_the_seeds_blanching_time(session):
    _grow(session, "green_bean_bush")

    freezing = next(m for m in _advice(session)["crops"][0]["methods"] if m["method"] == "freezing")

    assert freezing["blanching_minutes"] == 3
    assert "Freezer bags or containers" in freezing["supplies"]


def test_herb_preservation_tips_come_from_the_seed(session):
    _grow(session, "basil_sweet", category="herb")

    crop = _advice(session)["crops"][0]

    assert crop["herb_preservation_tips"] == "Freeze leaves in oil or dry hanging bundles."
    assert crop["safety"]["level"] == "verify"  # herbs carry no USDA acidity class


def test_one_species_grown_three_times_is_one_crop(session):
    _grow(session, "tomato_roma")
    _grow(session, "tomato_roma")
    _grow(session, "tomato_roma")

    advice = _advice(session)

    assert advice["crop_count"] == 1
    assert advice["plant_count"] == 3
    assert advice["crops"][0]["plant_count"] == 3


def test_tomato_basil_and_garlic_make_marinara(session):
    _grow(session, "tomato_roma")
    _grow(session, "basil_sweet", category="herb")
    _grow(session, "garlic")

    combos = {c["key"]: c for c in _advice(session)["combos"]}

    marinara = combos["marinara"]
    assert marinara["ready"] is True
    assert "Roma (Plum) Tomato" in marinara["matched_crops"]
    assert marinara["missing_crops"] == []
    assert marinara["safety"]["level"] == "water_bath_safe"


def test_salsa_reports_the_pepper_it_is_missing(session):
    _grow(session, "tomato_roma")

    combos = {c["key"]: c for c in _advice(session)["combos"]}

    assert combos["salsa"]["ready"] is False
    assert combos["salsa"]["missing_crops"] == ["pepper"]
    assert combos["salsa"]["matched_count"] == 1


def test_the_prep_checklist_is_categorized_and_deduplicated(session):
    _grow(session, "green_bean_bush", planted=TODAY - timedelta(days=45))

    checklist = _advice(session)["prep_checklist"]
    items = checklist["items"]

    assert checklist["count"] == len(set(items))
    assert len(items) == len(set(items))
    cats = {c["key"] for c in checklist["categories"]}
    assert {"jars_and_lids", "freezer"} <= cats
    assert "Bush Green Bean" in checklist["crops"]


def test_the_prep_checklist_is_empty_when_nothing_is_close(session):
    _grow(session, "garlic", planted=TODAY - timedelta(days=10))

    checklist = _advice(session)["prep_checklist"]

    assert checklist["items"] == []
    assert checklist["count"] == 0


# --------------------------------------------------------------------------- put-up pantry

def _goal(client, *, item="Marinara sauce", target=40.0, unit="quarts") -> dict:
    resp = client.post("/pantry-goals", headers=PARENT, json={
        "item": item, "crop_refs_json": '["tomato_roma"]',
        "target_qty": target, "unit": unit, "method": "water_bath_canning", "season_year": 2026,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _batch(client, *, qty=28.0, unit="quarts", goal_id=None, method="water_bath_canning",
           use_by=None, batch_date="2026-09-15", headers=None) -> dict:
    body = {
        "label": "Marinara sauce", "method": method, "batch_date": batch_date,
        "qty_initial": qty, "unit": unit, "storage_location": "Basement shelf",
    }
    if goal_id:
        body["goal_id"] = goal_id
    if use_by:
        body["use_by_date"] = use_by
    resp = client.post("/pantry-batches", headers=headers or PARENT, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_logging_a_batch_defaults_the_use_by_date_from_the_method(client):
    batch = _batch(client, batch_date="2026-09-15")

    assert batch["qty_remaining"] == 28.0
    assert batch["logged_by"] == "mother"
    assert batch["use_by_date"] == "2027-12-15"  # water-bath canning: +15 months
    assert batch["method_label"] == "Water-bath canning"


def test_frozen_batches_carry_a_shorter_shelf_life(client):
    batch = _batch(client, method="freezing", batch_date="2026-09-15")

    assert batch["use_by_date"] == "2027-07-15"  # freezing: +10 months


def test_a_child_may_not_log_a_batch(client):
    resp = client.post("/pantry-batches", headers=CHILD, json={
        "label": "Pickles", "method": "water_bath_canning", "qty_initial": 6, "unit": "quarts",
    })

    assert resp.status_code == 403


def test_an_adult_may_log_a_batch(client):
    batch = _batch(client, headers=ADULT)

    assert batch["logged_by"] == "alex"


def test_a_bad_date_is_refused_with_a_clear_message(client):
    resp = client.post("/pantry-batches", headers=PARENT, json={
        "label": "Pickles", "method": "water_bath_canning", "qty_initial": 6,
        "unit": "quarts", "batch_date": "2026-13-45",
    })

    assert resp.status_code == 400
    assert "batch_date" in resp.json()["detail"]


def test_everyone_may_take_a_jar_out_including_children(client):
    batch = _batch(client, qty=6.0)

    resp = client.post(f"/pantry-batches/{batch['id']}/use", headers=CHILD, json={"qty_used": 1})

    assert resp.status_code == 200, resp.text
    assert resp.json()["qty_remaining"] == 5.0
    assert resp.json()["used_by"] == "jamie"


def test_using_the_last_jar_clamps_at_zero(client):
    batch = _batch(client, qty=1.0)

    resp = client.post(f"/pantry-batches/{batch['id']}/use", headers=CHILD, json={"qty_used": 5})

    assert resp.json()["qty_remaining"] == 0.0


def test_use_defaults_to_one_when_no_body_is_sent(client):
    batch = _batch(client, qty=4.0)

    resp = client.post(f"/pantry-batches/{batch['id']}/use", headers=CHILD)

    assert resp.status_code == 200, resp.text
    assert resp.json()["qty_remaining"] == 3.0


def test_goal_progress_reports_twenty_eight_of_forty_quarts(client):
    goal = _goal(client)
    _batch(client, qty=28.0, goal_id=goal["id"])

    progress = client.get("/pantry-batches", headers=PARENT).json()["goal_progress"][0]

    assert progress["qty_put_up"] == 28.0
    assert progress["qty_remaining"] == 28.0
    assert progress["percent_complete"] == 70
    assert progress["batch_count"] == 1
    assert progress["unit_mismatch"] is False


def test_pints_count_against_a_quart_goal(client):
    goal = _goal(client, target=40.0, unit="quarts")
    _batch(client, qty=20.0, unit="pints", goal_id=goal["id"])

    progress = client.get("/pantry-batches", headers=PARENT).json()["goal_progress"][0]

    assert progress["qty_put_up"] == 10.0
    assert progress["percent_complete"] == 25


def test_a_goal_reports_units_it_cannot_convert(client):
    goal = _goal(client, target=40.0, unit="quarts")
    _batch(client, qty=12.0, unit="lbs", goal_id=goal["id"])

    progress = client.get("/pantry-batches", headers=PARENT).json()["goal_progress"][0]

    assert progress["unit_mismatch"] is True
    assert progress["mismatched_units"] == ["lbs"]
    assert progress["qty_put_up"] == 0.0


def test_soon_expiring_batches_are_flagged_use_first(client):
    soon = (date.today() + timedelta(days=10)).isoformat()
    later = (date.today() + timedelta(days=400)).isoformat()
    _batch(client, use_by=soon)
    _batch(client, use_by=later)

    view = client.get("/pantry-batches", headers=PARENT).json()

    assert view["use_first_count"] == 1
    assert view["batches"][0]["use_by_date"] == soon
    assert view["batches"][0]["use_first"] is True
    assert view["batches"][1]["use_first"] is False


def test_a_past_use_by_date_is_called_out(client):
    stale = (date.today() - timedelta(days=5)).isoformat()
    _batch(client, use_by=stale)

    row = client.get("/pantry-batches", headers=PARENT).json()["batches"][0]

    assert row["past_use_by"] is True
    assert row["days_until_use_by"] == -5


def test_empty_batches_are_hidden_unless_asked_for(client):
    batch = _batch(client, qty=2.0)
    client.post(f"/pantry-batches/{batch['id']}/use", headers=CHILD, json={"qty_used": 2})

    assert client.get("/pantry-batches", headers=PARENT).json()["count"] == 0
    included = client.get("/pantry-batches", headers=PARENT, params={"include_empty": "true"}).json()
    assert included["count"] == 1


def test_an_adult_may_edit_a_batch_but_a_child_may_not(client):
    batch = _batch(client, qty=6.0)

    child = client.patch(f"/pantry-batches/{batch['id']}", headers=CHILD, json={"label": "Nope"})
    assert child.status_code == 403

    adult = client.patch(f"/pantry-batches/{batch['id']}", headers=ADULT, json={
        "label": "Marinara sauce", "method": "water_bath_canning", "unit": "quarts",
        "qty_initial": 6.0, "storage_location": "Cold room",
    })
    assert adult.status_code == 200, adult.text
    assert adult.json()["storage_location"] == "Cold room"


def test_deleting_a_batch_is_204_and_adult_only(client):
    batch = _batch(client)

    assert client.delete(f"/pantry-batches/{batch['id']}", headers=CHILD).status_code == 403
    assert client.delete(f"/pantry-batches/{batch['id']}", headers=ADULT).status_code == 204
    assert client.get("/pantry-batches", headers=PARENT).json()["count"] == 0


def test_the_pantry_needs_an_authenticated_family_member(client):
    resp = client.get("/pantry-batches", headers={"X-Internal-Secret": INTERNAL_SECRET})

    assert resp.status_code == 401


# --------------------------------------------------------------------------- the routes

def test_the_forecast_route_degrades_cleanly_without_weather(client, monkeypatch):
    from services.common import weather_client

    async def no_weather(**_kw):
        return None

    monkeypatch.setattr(weather_client, "forecast", no_weather)
    planted = (date.today() - timedelta(days=50)).isoformat()
    created = client.post("/plants", headers=PARENT, json={
        "category": "vegetable", "species_ref": "carrot", "planted_date": planted,
    })
    assert created.status_code == 201, created.text

    resp = client.get("/harvest/forecast", headers=CHILD)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["weather_used"] is False
    assert body["count"] == 1
    assert body["items"][0]["days_until_ready"] == 20
    assert body["items"][0]["gdd_adjusted"] is False


def test_the_advice_route_returns_the_safety_sources(client, monkeypatch):
    from services.common import weather_client

    async def no_weather(**_kw):
        return None

    monkeypatch.setattr(weather_client, "forecast", no_weather)
    client.post("/plants", headers=PARENT, json={"category": "vegetable", "species_ref": "green_bean_bush"})

    resp = client.get("/preservation/advice", headers=CHILD)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["crops"][0]["safety"]["level"] == "pressure_canner_required"
    assert any("USDA" in source for source in body["safety_sources"])


def test_the_forecast_window_is_bounded(client):
    assert client.get("/harvest/forecast", headers=CHILD, params={"days": 0}).status_code == 422
    assert client.get("/harvest/forecast", headers=CHILD, params={"days": 999}).status_code == 422


def test_the_oldest_batch_of_a_label_is_flagged_even_with_a_late_date(client):
    far = (date.today() + timedelta(days=400)).isoformat()
    _batch(client, qty=6.0, use_by=far, batch_date="2025-09-15")
    _batch(client, qty=6.0, use_by=far, batch_date="2026-09-15")

    rows = client.get("/pantry-batches", headers=PARENT).json()["batches"]

    oldest = next(row for row in rows if row["batch_date"] == "2025-09-15")
    assert oldest["oldest_in_label"] is True
    assert oldest["use_first"] is True
    assert next(row for row in rows if row["batch_date"] == "2026-09-15")["use_first"] is False


def test_a_two_word_crop_matches_a_combo_ingredient(session):
    _grow(session, "zucchini")
    _grow(session, "green_bean_bush")

    combos = {c["key"]: c for c in _advice(session)["combos"]}

    # "green bean" is a two-word optional ingredient of the freezer packs combo:
    # the whole phrase must match, not just its first word.
    assert "Bush Green Bean" in combos["squash_freezer_packs"]["optional_matched"]
    assert combos["squash_freezer_packs"]["ready"] is True
