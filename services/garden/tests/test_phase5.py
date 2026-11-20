"""Phase 5 Garden Planner tests: zone maths, sizing, checks, auto-arrange,
timeline, plan cascade, and role guards.

Uses the same temporary garden database + real plant seed as ``test_api.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

ROOT = Path(__file__).resolve().parents[3]

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden import planner  # noqa: E402
from services.garden.main import app  # noqa: E402
from services.garden.models import GardenPlan, Location, PlanCell  # noqa: E402
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


def _species(key: str) -> dict:
    for name in ("vegetable", "herb", "fruit"):
        records = json.loads((ROOT / "data" / "plants_seed" / f"{name}.json").read_text())
        for record in records:
            if record["key"] == key:
                return record
    raise KeyError(key)


def _make_bed(session: Session, name: str = "Bed A", width: float = 2, length: float = 2) -> Location:
    bed = Location(name=name, kind="raised_bed", width_ft=width, length_ft=length)
    session.add(bed)
    session.commit()
    session.refresh(bed)
    return bed


def _make_plan(session: Session, year: int = 2027, name: str = "Plan") -> GardenPlan:
    plan = GardenPlan(name=name, season="spring_summer", year=year, family_size=4, mode="fresh_only")
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def _cell(plan: GardenPlan, bed: Location, col: int, row: int, species_ref: str, family: str | None = None) -> PlanCell:
    cell = PlanCell(
        plan_id=plan.id,
        location_id=bed.id,
        col=col,
        row=row,
        species_ref=species_ref,
        crop_family=family,
    )
    return cell


# --------------------------------------------------------------------------- date / zone maths


def test_add_weeks_parses_mmdd_and_subtracts():
    assert planner.add_weeks("05-15", -6) == "04-03"
    assert planner.add_weeks("05-15", 2) == "05-29"
    assert planner.add_weeks("01-01", -1) == "12-25"
    assert planner.add_weeks("not-a-date", 1) is None


def test_mmdd_sort_key_is_chronological():
    values = ["10-15", "04-01", "05-15", "01-05"]
    assert sorted(values, key=planner.mmdd_sort_key) == ["01-05", "04-01", "05-15", "10-15"]


def test_zone_from_min_temp_halves():
    assert planner.zone_from_min_temp(0) == "7a"
    assert planner.zone_from_min_temp(5) == "7b"
    assert planner.zone_from_min_temp(-20) == "5a"
    assert planner.zone_from_min_temp(-15) == "5b"


def test_zone_in_range_accepts_ints_and_strings():
    assert planner.zone_in_range("7b", 3, 11) is True
    assert planner.zone_in_range("7b", "8a", "11b") is False
    assert planner.zone_in_range(7, "3a", "11b") is True
    assert planner.zone_in_range("3a", 3, 11) is True


# --------------------------------------------------------------------------- milestones


def test_planting_milestones_use_real_seed_columns():
    tomato = _species("tomato_roma")
    milestones = planner.planting_milestones(tomato, "05-15", "10-15")
    by_stage = {m["stage"]: m["mmdd"] for m in milestones}

    assert by_stage["Start seeds indoors"] == "04-03"   # 6 weeks before 05-15
    assert by_stage["Transplant"] == "05-15"            # 0 weeks before
    assert by_stage["Harden off"] == "05-08"            # one week before transplant
    assert by_stage["Expected harvest"] == "07-29"      # 05-15 + 75 days
    assert by_stage["Harvest window ends"] == "08-28"   # + 30 days
    assert all(m["mmdd"] for m in milestones)


def test_planting_milestones_negative_offset_means_after_frost():
    cucumber = _species("cucumber_slicing")
    milestones = planner.planting_milestones(cucumber, "05-15", "10-15")
    direct = next(m for m in milestones if m["stage"] == "Direct sow")
    assert direct["mmdd"] == "05-29"  # -2 weeks = 2 weeks *after* the last frost


def test_planting_milestones_degrades_without_frost_dates():
    assert planner.planting_milestones(_species("tomato_roma"), None, None) == []


# --------------------------------------------------------------------------- sizing


def test_plant_count_uses_yield_per_plant():
    tomato = _species("tomato_roma")
    # 20 lbs / 20 lbs-per-plant = 1 plant (the old 1.5x inflation made it 1 too,
    # so also assert a case the old code got wrong: 20 lbs of basil).
    assert planner.plant_count_for_raw_lbs(20, tomato) == 1
    basil = _species("basil_sweet")
    assert planner.plant_count_for_raw_lbs(20, basil) == 25  # 20 / 0.8


def test_plant_count_is_monotonic():
    tomato = _species("tomato_roma")
    counts = [planner.plant_count_for_raw_lbs(lbs, tomato) for lbs in (1, 10, 20, 40, 80)]
    assert counts == sorted(counts)
    assert counts[0] >= 1


def test_preservation_conversion_parsed_from_seed_prose():
    tomato = _species("tomato_roma")
    ratio, source = planner.preservation_conversion_for("water_bath_canning", "quarts", tomato)
    assert ratio == 2.5
    assert source == "seed"


def test_preservation_conversion_falls_back_with_warning():
    basil = _species("basil_sweet")
    ratio, source = planner.preservation_conversion_for("water_bath_canning", "quarts", basil)
    assert ratio == 3.0  # PRESERVATION_RATIOS fallback
    assert source == "fallback"


def test_pantry_goal_backcalc_is_monotonic(session):
    from services.garden.models import PantryGoalIn

    small = planner.pantry_goal_backcalc(
        session,
        PantryGoalIn(item="Marinara", crop_refs_json='["tomato_roma"]', target_qty=10, unit="quarts", method="water_bath_canning"),
    )
    large = planner.pantry_goal_backcalc(
        session,
        PantryGoalIn(item="Marinara", crop_refs_json='["tomato_roma"]', target_qty=40, unit="quarts", method="water_bath_canning"),
    )
    assert large["total_raw_lbs_needed"] == 100.0  # 40 quarts x 2.5 lbs/quart
    assert large["total_plant_count"] >= small["total_plant_count"]
    assert large["total_sqft"] >= small["total_sqft"]
    assert large["crops"][0]["conversion_source"] == "seed"


def test_simple_sizing_scales_with_family_and_preserving(session):
    fresh = planner.simple_sizing(session, 4, "fresh_only", ["tomato_roma"])
    preserving = planner.simple_sizing(session, 4, "fresh_plus_preserving", ["tomato_roma"])
    bigger = planner.simple_sizing(session, 8, "fresh_only", ["tomato_roma"])
    assert fresh[0]["plant_count"] == 4
    assert preserving[0]["plant_count"] == 8
    assert bigger[0]["plant_count"] == 8


# --------------------------------------------------------------------------- checks


def test_companion_adjacency_is_informational(session):
    bed = _make_bed(session)
    plan = _make_plan(session)
    cells = [
        _cell(plan, bed, 0, 0, "tomato_roma", "Solanaceae"),
        _cell(plan, bed, 1, 0, "basil_sweet", "Lamiaceae"),
    ]
    session.add_all(cells)
    session.commit()
    checks = planner.check_plan(session, plan, cells)
    assert any(c["kind"] == "companion" for c in checks)
    assert not any(c["kind"] == "rotation" for c in checks)


def test_antagonist_adjacency_warns(session):
    bed = _make_bed(session)
    plan = _make_plan(session)
    cells = [
        _cell(plan, bed, 0, 0, "tomato_roma", "Solanaceae"),
        _cell(plan, bed, 1, 0, "potato", "Solanaceae"),
    ]
    session.add_all(cells)
    session.commit()
    checks = planner.check_plan(session, plan, cells)
    assert any(c["kind"] == "antagonist" for c in checks)


def test_same_year_same_family_is_not_a_rotation_warning(session):
    bed = _make_bed(session)
    plan = _make_plan(session, year=2027)
    cells = [
        _cell(plan, bed, 0, 0, "tomato_roma", "Solanaceae"),
        _cell(plan, bed, 0, 1, "potato", "Solanaceae"),
    ]
    session.add_all(cells)
    session.commit()
    checks = planner.check_plan(session, plan, cells)
    assert not any(c["kind"] == "rotation" for c in checks)


def test_prior_year_same_family_is_a_rotation_warning(session):
    bed = _make_bed(session)
    prior = _make_plan(session, year=2025, name="Prior")
    session.add(_cell(prior, bed, 0, 0, "potato", "Solanaceae"))
    current = _make_plan(session, year=2027)
    cells = [_cell(current, bed, 0, 0, "tomato_roma", "Solanaceae")]
    session.add_all(cells)
    session.commit()

    checks = planner.check_plan(session, current, cells)
    assert any(c["kind"] == "rotation" for c in checks)


def test_rotation_ignores_plans_older_than_three_years(session):
    bed = _make_bed(session)
    old = _make_plan(session, year=2020, name="Old")
    session.add(_cell(old, bed, 0, 0, "potato", "Solanaceae"))
    current = _make_plan(session, year=2027)
    cells = [_cell(current, bed, 0, 0, "tomato_roma", "Solanaceae")]
    session.add_all(cells)
    session.commit()
    assert not any(c["kind"] == "rotation" for c in planner.check_plan(session, current, cells))


def test_tall_crop_south_of_short_crop_warns_shading(session):
    bed = _make_bed(session)
    plan = _make_plan(session)
    cells = [
        _cell(plan, bed, 0, 0, "tomato_roma", "Solanaceae"),  # south (row 0)
        _cell(plan, bed, 0, 1, "basil_sweet", "Lamiaceae"),  # north (row 1)
    ]
    session.add_all(cells)
    session.commit()
    checks = planner.check_plan(session, plan, cells)
    assert any(c["kind"] == "shading" for c in checks)


# --------------------------------------------------------------------------- auto-arrange


def test_auto_arrange_places_tall_crops_north(session):
    bed = _make_bed(session, width=2, length=4)
    plan = _make_plan(session)
    placements = planner.auto_arrange_bed(
        session, [], bed.id, ["tomato_roma", "basil_sweet"], plan=plan
    )
    tomato = next(p for p in placements if p["species_ref"] == "tomato_roma")
    assert tomato["row"] == 3  # high row index = north edge
    assert not tomato.get("overflow")


def test_auto_arrange_respects_previous_year_families(session):
    bed = _make_bed(session, width=2, length=2)
    prior = _make_plan(session, year=2025, name="Prior")
    session.add(_cell(prior, bed, 0, 0, "basil_sweet", "Lamiaceae"))
    current = _make_plan(session, year=2027)
    session.commit()

    placements = planner.auto_arrange_bed(
        session, [], bed.id, ["basil_sweet"], plan=current
    )
    assert (placements[0]["col"], placements[0]["row"]) != (0, 0)


def test_auto_arrange_reports_overflow_when_bed_is_full(session):
    bed = _make_bed(session, width=1, length=1)
    plan = _make_plan(session)
    placements = planner.auto_arrange_bed(
        session, [], bed.id, ["tomato_roma", "basil_sweet"], plan=plan
    )
    assert sum(1 for p in placements if p.get("overflow")) == 1


# --------------------------------------------------------------------------- API: plans, cells, cascade


def _create_bed(client, width=2, length=2):
    resp = client.post(
        "/locations",
        headers=ADMIN_HEADERS,
        json={"name": "Raised Bed", "kind": "raised_bed", "width_ft": width, "length_ft": length},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_plan(client, year=2027):
    resp = client.post(
        "/plans",
        headers=ADMIN_HEADERS,
        json={"name": "Spring 2027", "season": "spring_summer", "year": year, "family_size": 4},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_plan_cells_upsert_and_auto_arrange_persist(client):
    bed_id = _create_bed(client)
    plan_id = _create_plan(client)

    resp = client.put(
        f"/plans/{plan_id}/beds/{bed_id}/cells",
        headers=ADMIN_HEADERS,
        json=[
            {"col": 0, "row": 0, "species_ref": "tomato_roma"},
            {"col": 1, "row": 0, "species_ref": "basil_sweet"},
        ],
    )
    assert resp.status_code == 200
    assert resp.json()["cells_upserted"] == 2

    resp = client.post(
        f"/plans/{plan_id}/beds/{bed_id}/auto-arrange",
        headers=ADMIN_HEADERS,
        json={"apply": True},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["applied"] is True
    assert len(data["placements"]) == 2
    assert "checks" in data

    cells = client.get(
        f"/plans/{plan_id}/beds/{bed_id}/cells", headers=ADMIN_HEADERS
    ).json()["cells"]
    assert len(cells) == 2
    tomato = next(c for c in cells if c["species_ref"] == "tomato_roma")
    assert tomato["row"] == 1  # north edge of a 2x2 bed


def test_delete_plan_cascades_cells(client, session):
    bed_id = _create_bed(client)
    plan_id = _create_plan(client)
    client.put(
        f"/plans/{plan_id}/beds/{bed_id}/cells",
        headers=ADMIN_HEADERS,
        json=[{"col": 0, "row": 0, "species_ref": "basil_sweet"}],
    )

    resp = client.delete(f"/plans/{plan_id}", headers=ADMIN_HEADERS)
    assert resp.status_code == 204
    assert client.get(f"/plans/{plan_id}", headers=ADMIN_HEADERS).status_code == 404
    remaining = session.exec(select(PlanCell).where(PlanCell.plan_id == plan_id)).all()
    assert remaining == []


def test_plan_checks_exclude_companions_from_warning_count(client):
    bed_id = _create_bed(client)
    plan_id = _create_plan(client)
    client.put(
        f"/plans/{plan_id}/beds/{bed_id}/cells",
        headers=ADMIN_HEADERS,
        json=[
            {"col": 0, "row": 0, "species_ref": "tomato_roma"},
            {"col": 1, "row": 0, "species_ref": "basil_sweet"},
        ],
    )
    data = client.get(f"/plans/{plan_id}/checks", headers=ADMIN_HEADERS).json()
    assert data["warning_count"] == 0
    assert any(c["kind"] == "companion" for c in data["checks"])


def test_plan_timeline_returns_dated_milestones(client):
    client.put(
        "/profile",
        headers=ADMIN_HEADERS,
        json={"last_spring_frost_mmdd": "05-15", "first_fall_frost_mmdd": "10-15"},
    )
    bed_id = _create_bed(client)
    plan_id = _create_plan(client)
    client.put(
        f"/plans/{plan_id}/beds/{bed_id}/cells",
        headers=ADMIN_HEADERS,
        json=[{"col": 0, "row": 0, "species_ref": "tomato_roma"}],
    )
    data = client.get(f"/plans/{plan_id}/timeline", headers=ADMIN_HEADERS).json()
    assert data["timeline"][0]["milestones"]
    stages = {m["stage"] for m in data["timeline"][0]["milestones"]}
    assert "Expected harvest" in stages


# --------------------------------------------------------------------------- API: pantry goals


def test_pantry_goal_crud_and_calculate(client):
    resp = client.post(
        "/pantry-goals",
        headers=ADMIN_HEADERS,
        json={
            "item": "Marinara sauce",
            "crop_refs_json": json.dumps(["tomato_roma"]),
            "target_qty": 40,
            "unit": "quarts",
            "method": "water_bath_canning",
            "season_year": 2027,
        },
    )
    assert resp.status_code == 201, resp.text
    goal = resp.json()

    listed = client.get("/pantry-goals", headers=ADMIN_HEADERS).json()
    assert listed["count"] == 1

    calc = client.get(
        "/pantry-goals/calculate",
        headers=ADMIN_HEADERS,
        params={
            "item": "Marinara sauce",
            "crop_refs": "tomato_roma",
            "target_qty": 40,
            "unit": "quarts",
            "method": "water_bath_canning",
        },
    ).json()
    assert calc["total_raw_lbs_needed"] == 100.0
    assert calc["total_plant_count"] >= 1

    assert client.delete(f"/pantry-goals/{goal['id']}", headers=ADMIN_HEADERS).status_code == 204


def test_profile_detect_zone_creates_profile_and_uses_real_location(client, monkeypatch):
    import services.garden.main as main_module

    calls: list[tuple[float, float]] = []

    async def fake_forecast():
        return {"location": {"latitude": 35.5, "longitude": -86.5}}

    async def fake_detect(lat, lon):
        calls.append((lat, lon))
        return "7a", 2.0

    monkeypatch.setattr(main_module.weather, "forecast", fake_forecast)
    monkeypatch.setattr(main_module, "detect_zone_for_location", fake_detect)

    resp = client.post("/profile/detect-zone", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["zone"] == "7a"
    assert data["zone_source"] == "auto"
    assert data["latitude"] == 35.5
    assert calls == [(35.5, -86.5)]

    profile = client.get("/profile", headers=ADMIN_HEADERS).json()
    assert profile["hardiness_zone"] == "7a"
    assert profile["zone_source"] == "auto"


def test_profile_detect_zone_degrades_without_weather(client, monkeypatch):
    import services.garden.main as main_module

    async def no_forecast():
        return None

    monkeypatch.setattr(main_module.weather, "forecast", no_forecast)
    resp = client.post("/profile/detect-zone", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert "not configured" in resp.json()["message"].lower()


# --------------------------------------------------------------------------- role guards


def test_child_cannot_create_pantry_goal(client):
    resp = client.post(
        "/pantry-goals",
        headers=KID_HEADERS,
        json={"item": "Salsa", "target_qty": 2, "unit": "quarts", "method": "water_bath_canning"},
    )
    assert resp.status_code == 403


def test_child_cannot_create_plan_or_write_cells(client):
    assert client.post(
        "/plans",
        headers=KID_HEADERS,
        json={"name": "Kid plan", "season": "spring_summer", "year": 2027},
    ).status_code == 403

    bed_id = _create_bed(client)
    plan_id = _create_plan(client)
    assert client.put(
        f"/plans/{plan_id}/beds/{bed_id}/cells",
        headers=KID_HEADERS,
        json=[{"col": 0, "row": 0, "species_ref": "basil_sweet"}],
    ).status_code == 403
    assert client.post(
        f"/plans/{plan_id}/beds/{bed_id}/auto-arrange",
        headers=KID_HEADERS,
        json={"apply": True},
    ).status_code == 403
