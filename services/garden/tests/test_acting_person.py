"""Who did it, and where it came from, are not the caller's to invent.

``performed_by`` (care) and ``harvested_by`` (harvests) used to be taken from
the body, so anyone could record care or a harvest as anyone. Now the signed-in
person is recorded, and only a parent or an admin may name someone else, who
must be in Identity's household roster. ``source`` is checked against the fixed
set callers may use; the service's own sources cannot be claimed.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from scripts.build_plants_seed import build_seed
from services.config import INTERNAL_SECRET
from services.garden import household
from services.garden.main import app
from services.garden.models import CareLog, HarvestLog


def _headers(user: str, role: str, *, admin: bool = False) -> dict[str, str]:
    return {
        "X-Internal-Secret": INTERNAL_SECRET,
        "X-User-Id": user,
        "X-User-Role": role,
        "X-Is-Admin": "true" if admin else "false",
    }


PARENT = _headers("parent_one", "mother")
ADULT = _headers("adult_one", "adult_child")
CHILD = _headers("child_one", "child")
ROSTER = ("parent_one", "adult_one", "child_one")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import services.garden.main as main_module

    seed = tmp_path / "plants-seed.db"
    build_seed(seed)
    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path / 'garden.db'}")
    monkeypatch.setenv("PLANT_SEED_PATH", str(seed))
    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    main_module.app.state.engine = engine
    monkeypatch.setattr(household, "_cache", (1e18, len(ROSTER), ROSTER))
    return TestClient(app)


@pytest.fixture()
def plant(client) -> str:
    resp = client.post(
        "/plants", headers=PARENT, json={"species_ref": "tomato_roma", "category": "vegetable", "nickname": "Row"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _session():
    import services.garden.main as main_module

    return Session(main_module.app.state.engine)


def _care_by() -> list[str]:
    with _session() as db:
        return [row.performed_by for row in db.exec(select(CareLog).where(CareLog.action == "water")).all()]


def _harvest_by() -> list[str]:
    with _session() as db:
        return [row.harvested_by for row in db.exec(select(HarvestLog)).all()]


def test_a_child_cannot_log_care_as_someone_else(client, plant):
    resp = client.post(f"/plants/{plant}/care", headers=CHILD, json={"action": "water", "performed_by": "parent_one"})

    assert resp.status_code == 403
    assert _care_by() == []


def test_an_adult_child_cannot_either(client, plant):
    resp = client.post(f"/plants/{plant}/care", headers=ADULT, json={"action": "water", "performed_by": "child_one"})

    assert resp.status_code == 403


def test_naming_yourself_is_fine(client, plant):
    resp = client.post(f"/plants/{plant}/care", headers=CHILD, json={"action": "water", "performed_by": "Child_One"})

    assert resp.status_code == 200, resp.text
    assert _care_by() == ["child_one"]


def test_a_parent_may_log_for_someone_in_the_household(client, plant):
    resp = client.post(f"/plants/{plant}/care", headers=PARENT, json={"action": "water", "performed_by": "child_one"})

    assert resp.status_code == 200, resp.text
    assert _care_by() == ["child_one"]


def test_but_not_for_someone_outside_it(client, plant):
    resp = client.post(f"/plants/{plant}/care", headers=PARENT, json={"action": "water", "performed_by": "stranger"})

    assert resp.status_code == 422
    assert "not in the household" in resp.json()["detail"]
    assert _care_by() == []


def test_identity_away_is_a_503_not_a_pass(client, plant, monkeypatch):
    async def away():
        return None

    monkeypatch.setattr(household, "_cache", None)
    monkeypatch.setattr(household, "_roster", away)

    resp = client.post(f"/plants/{plant}/care", headers=PARENT, json={"action": "water", "performed_by": "child_one"})

    assert resp.status_code == 503
    assert _care_by() == []


def test_harvests_follow_the_same_rule(client, plant):
    spoofed = client.post(f"/plants/{plant}/harvest", headers=CHILD, json={"quantity": 2, "harvested_by": "parent_one"})
    own = client.post(f"/plants/{plant}/harvest", headers=CHILD, json={"quantity": 2})
    for_child = client.post(f"/plants/{plant}/harvest", headers=PARENT, json={"quantity": 1, "harvested_by": "child_one"})

    assert spoofed.status_code == 403
    assert own.status_code == 201 and for_child.status_code == 201
    assert sorted(_harvest_by()) == ["child_one", "child_one"]


@pytest.mark.parametrize("source", ("homehub_valve", "skylight_chore", "anything"))
def test_a_caller_cannot_claim_another_source(client, plant, source):
    resp = client.post(f"/plants/{plant}/care", headers=PARENT, json={"action": "water", "source": source})

    assert resp.status_code == 422
    assert "source must be one of" in resp.json()["detail"]


def test_water_all_checks_its_source_too(client):
    room = client.post("/locations", headers=PARENT, json={"name": "Bed A", "kind": "raised_bed"}).json()

    bad = client.post(f"/locations/{room['id']}/water-all", headers=PARENT, params={"source": "skylight_chore"})
    good = client.post(f"/locations/{room['id']}/water-all", headers=PARENT, params={"source": "nfc_tag"})

    assert bad.status_code == 422
    assert good.status_code == 200
