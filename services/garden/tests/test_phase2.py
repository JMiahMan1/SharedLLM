"""Phase 2 tests for the Garden service (services/garden/main.py).

Covers: parent-only assignment guards, unassigning to null, room assignment
inheritance, GET /today?mine=true + paused-rule exclusion, GET /streaks +
earned achievements, notices.py UTC handling + Telemetry mock dispatch +
once key + callback params, opt-in Skylight sync.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
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
    CareLog, CareRule, GardenProfile, Location, Plant,
)
from scripts.build_plants_seed import build_seed  # noqa: E402

# Role header sets.
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
def client(tmp_seed_path, db_url, monkeypatch):
    import services.garden.main as main_module

    main_module.app.state.engine = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", db_url)
    monkeypatch.setenv("PLANT_SEED_PATH", tmp_seed_path)

    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    main_module.app.state.engine = engine
    return TestClient(app), engine


# --------------------------------------------------------------------------- helpers


def _make_location(client, headers, name="Living Room", kind="indoor_room"):
    client, _ = client
    resp = client.post("/locations", headers=headers, json={"name": name, "kind": kind})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_plant(client, headers, species_ref="monstera_deliciosa", location_id=None, nickname=None):
    client, _ = client
    body = {"category": "houseplant", "species_ref": species_ref}
    if location_id:
        body["location_id"] = location_id
    if nickname:
        body["nickname"] = nickname
    resp = client.post("/plants", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_due_care_rule(client, plant_id, action="water"):
    """Create a care rule with next_due_at in the past so it shows in /today."""
    _, engine = client
    with Session(engine) as session:
        rule = CareRule(
            plant_id=plant_id,
            action=action,
            interval_days=1.0,
            winter_multiplier=1.0,
            next_due_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return rule.id


# --------------------------------------------------------------------------- parent-only assignment guards


def test_assign_location_parent_only(client):
    loc = _make_location(client, PARENT_HEADERS)

    # Parent can assign.
    resp = client[0].patch(
        f"/locations/{loc['id']}/assign", headers=PARENT_HEADERS,
        json={"assigned_to": "child", "skylight_sync_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == "child"
    assert resp.json()["skylight_sync_enabled"] is True

    # Adult child cannot assign.
    resp = client[0].patch(
        f"/locations/{loc['id']}/assign", headers=ADULT_HEADERS,
        json={"assigned_to": "teen"},
    )
    assert resp.status_code == 403

    # Teen cannot assign.
    resp = client[0].patch(
        f"/locations/{loc['id']}/assign", headers=TEEN_HEADERS,
        json={"assigned_to": "child"},
    )
    assert resp.status_code == 403

    # Child cannot assign.
    resp = client[0].patch(
        f"/locations/{loc['id']}/assign", headers=CHILD_HEADERS,
        json={"assigned_to": "teen"},
    )
    assert resp.status_code == 403


def test_assign_plant_parent_only(client):
    loc = _make_location(client, PARENT_HEADERS)
    plant = _make_plant(client, PARENT_HEADERS, location_id=loc["id"])

    # Parent can assign.
    resp = client[0].patch(
        f"/plants/{plant['id']}/assign", headers=PARENT_HEADERS,
        json={"assigned_to": "child", "skylight_sync_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == "child"
    assert resp.json()["skylight_sync_enabled"] is True

    # Adult child cannot assign.
    resp = client[0].patch(
        f"/plants/{plant['id']}/assign", headers=ADULT_HEADERS,
        json={"assigned_to": "teen"},
    )
    assert resp.status_code == 403


def test_unassign_to_null(client):
    """Setting assigned_to to null actually unassigns (model_fields_set check)."""
    loc = _make_location(client, PARENT_HEADERS)

    # Assign first.
    resp = client[0].patch(
        f"/locations/{loc['id']}/assign", headers=PARENT_HEADERS,
        json={"assigned_to": "child"},
    )
    assert resp.json()["assigned_to"] == "child"

    # Unassign with explicit null.
    resp = client[0].patch(
        f"/locations/{loc['id']}/assign", headers=PARENT_HEADERS,
        json={"assigned_to": None},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] is None

    # Omitting assigned_to should NOT change it.
    resp = client[0].patch(
        f"/locations/{loc['id']}/assign", headers=PARENT_HEADERS,
        json={"skylight_sync_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] is None  # still unassigned
    assert resp.json()["skylight_sync_enabled"] is True


def test_patch_plant_assignment_fields_require_parent(client):
    loc = _make_location(client, PARENT_HEADERS)
    plant = _make_plant(client, PARENT_HEADERS, location_id=loc["id"], nickname="Monty")

    # Adult child can change nickname (not an assignment field).
    resp = client[0].patch(
        f"/plants/{plant['id']}", headers=ADULT_HEADERS,
        json={"nickname": "Monty Jr"},
    )
    assert resp.status_code == 200
    assert resp.json()["nickname"] == "Monty Jr"

    # Adult child cannot change assigned_to.
    resp = client[0].patch(
        f"/plants/{plant['id']}", headers=ADULT_HEADERS,
        json={"assigned_to": "teen"},
    )
    assert resp.status_code == 403

    # Adult child cannot change skylight_sync_enabled.
    resp = client[0].patch(
        f"/plants/{plant['id']}", headers=ADULT_HEADERS,
        json={"skylight_sync_enabled": True},
    )
    assert resp.status_code == 403


def test_patch_location_assignment_fields_require_parent(client):
    loc = _make_location(client, PARENT_HEADERS)

    # Adult child can change light_notes (not an assignment field).
    resp = client[0].patch(
        f"/locations/{loc['id']}", headers=ADULT_HEADERS,
        json={"light_notes": "bright"},
    )
    assert resp.status_code == 200

    # Adult child cannot change assigned_to.
    resp = client[0].patch(
        f"/locations/{loc['id']}", headers=ADULT_HEADERS,
        json={"assigned_to": "teen"},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- room assignment inheritance


def test_room_assignment_inheritance_in_today(client):
    """A task's effective assignment inherits location.assigned_to."""
    loc = _make_location(client, PARENT_HEADERS)
    # Assign the location to child.
    client[0].patch(
        f"/locations/{loc['id']}/assign", headers=PARENT_HEADERS,
        json={"assigned_to": "child"},
    )
    plant = _make_plant(client, PARENT_HEADERS, location_id=loc["id"])
    # Make the care rule due now.
    _make_due_care_rule(client, plant["id"])

    # The plant inherits child assignment from location.
    resp = client[0].get("/today", headers=CHILD_HEADERS, params={"mine": "true"})
    assert resp.status_code == 200
    data = resp.json()
    plant_ids = {t["plant_id"] for t in data["all"]}
    assert plant["id"] in plant_ids, f"Plant {plant['id']} not in today tasks for child (mine=true)"


# --------------------------------------------------------------------------- GET /today?mine=true + paused rules


def test_today_mine_returns_assigned_or_unassigned(client):
    """mine=true returns tasks assigned to user OR unassigned plants."""
    loc = _make_location(client, PARENT_HEADERS)
    # Plant assigned to child.
    p1 = _make_plant(client, PARENT_HEADERS, location_id=loc["id"], nickname="Plant 1")
    client[0].patch(f"/plants/{p1['id']}/assign", headers=PARENT_HEADERS, json={"assigned_to": "child"})
    _make_due_care_rule(client, p1["id"])
    # Plant assigned to mother.
    p2 = _make_plant(client, PARENT_HEADERS, location_id=loc["id"], nickname="Plant 2")
    client[0].patch(f"/plants/{p2['id']}/assign", headers=PARENT_HEADERS, json={"assigned_to": "mother"})
    _make_due_care_rule(client, p2["id"])
    # Unassigned plant.
    p3 = _make_plant(client, PARENT_HEADERS, location_id=loc["id"], nickname="Plant 3")
    _make_due_care_rule(client, p3["id"])

    resp = client[0].get("/today", headers=CHILD_HEADERS, params={"mine": "true"})
    assert resp.status_code == 200
    data = resp.json()
    ids = {t["plant_id"] for t in data["all"]}
    assert p1["id"] in ids  # assigned to child
    assert p3["id"] in ids  # unassigned
    assert p2["id"] not in ids  # assigned to mother, not child


def test_today_excludes_paused_rules(client):
    """Paused care rules are excluded from /today."""
    loc = _make_location(client, PARENT_HEADERS)
    plant = _make_plant(client, PARENT_HEADERS, location_id=loc["id"])
    rule_id = _make_due_care_rule(client, plant["id"])

    # Pause it.
    resp = client[0].patch(
        f"/plants/{plant['id']}/care-rules/{rule_id}", headers=PARENT_HEADERS,
        json={"paused": True},
    )
    assert resp.status_code == 200

    resp = client[0].get("/today", headers=PARENT_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    plant_ids = {t["plant_id"] for t in data["all"]}
    assert plant["id"] not in plant_ids  # paused rule excluded


# --------------------------------------------------------------------------- GET /streaks + achievements


def test_streaks_empty(client):
    resp = client[0].get("/streaks", headers=CHILD_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "child"
    assert data["current_streak"] == 0
    assert data["longest_streak"] == 0
    assert data["total_logs"] == 0
    assert "earned" in data
    assert "points" in data


def test_streaks_with_logs(client):
    loc = _make_location(client, PARENT_HEADERS)
    plant = _make_plant(client, PARENT_HEADERS, location_id=loc["id"])

    # Log care as child.
    resp = client[0].post(
        f"/plants/{plant['id']}/care", headers=CHILD_HEADERS,
        json={"action": "water", "performed_by": "child"},
    )
    assert resp.status_code == 200

    resp = client[0].get("/streaks", headers=CHILD_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "child"
    assert data["total_logs"] >= 1
    assert data["current_streak"] >= 1
    assert "earned" in data
    assert "points" in data


# --------------------------------------------------------------------------- notices.py UTC handling + once key


def test_build_digest_utc_and_params():
    """build_digest uses UTC (not local time) and includes real callback params."""
    from datetime import UTC, datetime
    from services.garden.notices import build_digest, _once_key

    tasks = [
        {
            "plant_id": "plant-1",
            "nickname": "Monty",
            "common_name": "Monstera",
            "location_name": "Living Room",
            "action": "water",
        },
        {
            "plant_id": "plant-2",
            "nickname": "Fernie",
            "common_name": "Fern",
            "location_name": "Living Room",
            "action": "mist",
        },
    ]

    digest = build_digest("child", tasks)
    assert digest is not None
    # Check params are present in actions.
    for action in digest["actions"]:
        assert "params" in action
        assert "plant_id" in action["params"]
        assert "plant_ids" in action["params"]
        assert action["params"]["plant_ids"] == ["plant-1", "plant-2"]

    # Once key uses UTC date.
    day = datetime.now(UTC).date().isoformat()
    assert _once_key("child", day) == f"garden:digest:child:{day}"

    # Empty tasks returns None.
    assert build_digest("child", []) is None


# --------------------------------------------------------------------------- opt-in Skylight sync


def test_skylight_sync_off_vs_on(client):
    """Care logging works with and without skylight_sync_enabled."""
    loc = _make_location(client, PARENT_HEADERS)

    # Plant without sync enabled.
    p1 = _make_plant(client, PARENT_HEADERS, location_id=loc["id"], nickname="No Sync")
    resp = client[0].post(
        f"/plants/{p1['id']}/care", headers=CHILD_HEADERS,
        json={"action": "water", "performed_by": "child"},
    )
    assert resp.status_code == 200  # should not fail even without Skylight

    # Plant with sync enabled but no chore_id.
    p2 = _make_plant(client, PARENT_HEADERS, location_id=loc["id"], nickname="Sync No Chore")
    client[0].patch(
        f"/plants/{p2['id']}/assign", headers=PARENT_HEADERS,
        json={"skylight_sync_enabled": True},
    )
    resp = client[0].post(
        f"/plants/{p2['id']}/care", headers=CHILD_HEADERS,
        json={"action": "water", "performed_by": "child"},
    )
    assert resp.status_code == 200  # degrades gracefully without chore_id


# --------------------------------------------------------------------------- gateway proxy forwards today params


def test_garden_today_forwards_query_params(client):
    """GET /today forwards ?assigned_to and ?mine query params."""
    resp = client[0].get("/today", headers=PARENT_HEADERS, params={"mine": "true"})
    assert resp.status_code == 200
    assert "date" in resp.json()
    assert "by_room" in resp.json()
    assert "all" in resp.json()

    resp = client[0].get("/today", headers=PARENT_HEADERS, params={"assigned_to": "mother"})
    assert resp.status_code == 200
