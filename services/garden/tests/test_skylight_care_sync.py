"""Two-way Skylight care sync and who may link a plant's chore.

A plant's watering can be a Skylight chore. Ticking it off there logs the
watering here (POST /hooks/skylight-chore-completed) and advances the schedule;
logging care here still completes the chore. A non-parent ``adult_child`` may
link the chore only for a plant they look after.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

ROOT = Path(__file__).resolve().parents[3]

from services.config import INTERNAL_SECRET  # noqa: E402
from services.garden.main import app  # noqa: E402
from services.garden.models import CareLog, CareRule, Plant  # noqa: E402
from scripts.build_plants_seed import build_seed  # noqa: E402


def _headers(user: str, role: str) -> dict[str, str]:
    return {
        "X-Internal-Secret": INTERNAL_SECRET,
        "X-User-Id": user,
        "X-User-Role": role,
        "X-Is-Admin": "true" if role == "admin" else "false",
        "X-Family-Manage": "true" if role in ("mother", "father") else "false",
    }


HOOK = {"X-Internal-Secret": INTERNAL_SECRET}
PARENT = _headers("mother", "mother")
ADULT = _headers("alex", "adult_child")
CHILD = _headers("jamie", "child")


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


def _plant(client, assigned_to: str | None = "alex", nickname: str = "Monty") -> dict:
    resp = client.post("/plants", headers=PARENT, json={
        "category": "houseplant",
        "species_ref": "monstera_deliciosa",
        "nickname": nickname,
        "assigned_to": assigned_to,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


#: What the gateway's Skylight chore routes send: only they may set these fields.
LINK = {"X-Garden-Skylight-Link": "chore-link"}


# --------------------------------------------------------------------------- who may link

@pytest.mark.parametrize("who", ("PARENT", "ADULT"))
def test_skylight_fields_need_the_chore_route(client, who):
    """Set directly, they skip the chart's rules (who may be given a chore, that it exists)."""
    plant = _plant(client, assigned_to="alex")

    resp = client.patch(f"/plants/{plant['id']}/assign", headers=globals()[who], json={
        "skylight_sync_enabled": True, "skylight_chore_id": "999", "skylight_assignee": "pat",
    })

    assert resp.status_code == 403
    assert "Skylight chore" in resp.json()["detail"]


def test_the_plants_adult_can_link_its_skylight_chore(client):
    plant = _plant(client, assigned_to="alex")

    resp = client.patch(f"/plants/{plant['id']}/assign", headers={**ADULT, **LINK}, json={
        "skylight_sync_enabled": True,
        "skylight_chore_id": "52409380",
        "skylight_assignee": "alex",
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skylight_sync_enabled"] is True
    assert body["skylight_chore_id"] == "52409380"
    assert body["skylight_assignee"] == "alex"


def test_the_adult_cannot_hand_the_plant_to_someone_else(client):
    plant = _plant(client, assigned_to="alex")

    resp = client.patch(
        f"/plants/{plant['id']}/assign", headers=ADULT, json={"assigned_to": "jamie"}
    )

    assert resp.status_code == 403


def test_an_adult_not_assigned_this_plant_cannot_link_it(client):
    plant = _plant(client, assigned_to="riley")

    resp = client.patch(
        f"/plants/{plant['id']}/assign", headers={**ADULT, **LINK}, json={"skylight_sync_enabled": True}
    )

    assert resp.status_code == 403


def test_a_child_cannot_link_a_chore(client):
    plant = _plant(client, assigned_to="jamie")

    resp = client.patch(
        f"/plants/{plant['id']}/assign", headers={**CHILD, **LINK}, json={"skylight_sync_enabled": True}
    )

    assert resp.status_code == 403


def test_a_parent_may_link_any_plant(client):
    plant = _plant(client, assigned_to=None)

    resp = client.patch(
        f"/plants/{plant['id']}/assign", headers={**PARENT, **LINK}, json={"skylight_sync_enabled": True}
    )

    assert resp.status_code == 200


def test_plant_out_reports_the_real_next_care_date_and_the_chore(client):
    plant = _plant(client)
    client.patch(f"/plants/{plant['id']}/assign", headers={**PARENT, **LINK}, json={
        "skylight_sync_enabled": True, "skylight_chore_id": "52409380", "skylight_assignee": "alex",
    })

    body = client.get(f"/plants/{plant['id']}", headers=PARENT).json()

    assert body["next_due_at"], "a plant with a water rule has a next care date"
    assert body["skylight_chore_id"] == "52409380"
    assert body["skylight_assignee"] == "alex"


# --------------------------------------------------------------------------- the completion hook

def _linked_plant(client, chore_id: str = "52409380") -> dict:
    plant = _plant(client, assigned_to="alex")
    resp = client.patch(f"/plants/{plant['id']}/assign", headers={**PARENT, **LINK}, json={
        "skylight_sync_enabled": True, "skylight_chore_id": chore_id, "skylight_assignee": "alex",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_ticking_the_chore_off_logs_the_watering_and_moves_the_schedule(
    client, session, monkeypatch
):
    import services.garden.main as main_module

    mirrored: list[tuple] = []
    monkeypatch.setattr(
        main_module, "mirror_care_log",
        lambda *a, **k: mirrored.append((a, k)),
    )
    plant = _linked_plant(client)
    rule = session.exec(
        select(CareRule).where(CareRule.plant_id == plant["id"], CareRule.action == "water")
    ).first()
    rule.next_due_at = "2020-01-01T00:00:00+00:00"
    session.add(rule)
    session.commit()

    resp = client.post("/hooks/skylight-chore-completed", headers=HOOK, json={
        "chore_id": "52409380-2026-11-20", "performed_by": "alex",
    })

    assert resp.status_code == 200, resp.text
    assert resp.json()["logged"] == 1
    logs = session.exec(select(CareLog).where(CareLog.plant_id == plant["id"])).all()
    assert [(row.action, row.source, row.performed_by) for row in logs] == [
        ("water", "skylight_chore", "alex")
    ]
    session.refresh(rule)
    assert rule.next_due_at and rule.next_due_at > datetime.now(UTC).isoformat()[:10]
    # Never mirrored back: the chore is already complete, and a loop would re-complete it.
    assert mirrored == []


def test_the_same_completion_twice_logs_once(client, session):
    plant = _linked_plant(client)
    payload = {"chore_id": "52409380-2026-11-20", "performed_by": "alex"}

    first = client.post("/hooks/skylight-chore-completed", headers=HOOK, json=payload).json()
    second = client.post("/hooks/skylight-chore-completed", headers=HOOK, json=payload).json()

    assert (first["logged"], first["skipped_duplicate"]) == (1, 0)
    assert (second["logged"], second["skipped_duplicate"]) == (0, 1)
    logs = session.exec(select(CareLog).where(CareLog.plant_id == plant["id"])).all()
    assert len(logs) == 1


def test_an_unlinked_chore_changes_nothing(client, session):
    _linked_plant(client)

    body = client.post("/hooks/skylight-chore-completed", headers=HOOK, json={"chore_id": "99999999-2026-11-20"}).json()

    assert body == {"ok": True, "matched": 0, "logged": 0, "skipped_duplicate": 0}
    assert session.exec(select(CareLog)).all() == []


def test_the_hook_needs_the_internal_secret(client):
    resp = client.post(
        "/hooks/skylight-chore-completed",
        json={"chore_id": "52409380"},
        headers={"X-Internal-Secret": "wrong"},
    )
    assert resp.status_code == 403


def test_logging_care_here_still_mirrors_to_skylight(client, monkeypatch):
    import services.garden.main as main_module

    mirrored: list[tuple] = []
    monkeypatch.setattr(main_module, "mirror_care_log", lambda *a, **k: mirrored.append((a, k)))
    plant = _linked_plant(client)

    resp = client.post(f"/plants/{plant['id']}/care", headers=PARENT, json={"action": "water"})

    assert resp.status_code == 200, resp.text
    assert len(mirrored) == 1


# --------------------------------------------------------------------------- creating a plant for someone

def test_a_parent_may_add_a_plant_for_another_adult(client):
    resp = client.post("/plants", headers=PARENT, json={
        "category": "houseplant", "species_ref": "monstera_deliciosa", "assigned_to": "riley",
    })

    assert resp.status_code == 201, resp.text
    assert resp.json()["assigned_to"] == "riley"


def test_an_adult_may_add_a_plant_only_for_themselves(client):
    mine = client.post("/plants", headers=ADULT, json={
        "category": "houseplant", "species_ref": "monstera_deliciosa", "assigned_to": "alex",
    })
    assert mine.status_code == 201, mine.text

    theirs = client.post("/plants", headers=ADULT, json={
        "category": "houseplant", "species_ref": "monstera_deliciosa", "assigned_to": "jamie",
    })
    assert theirs.status_code == 403
    assert "parents and admins" in theirs.json()["detail"]


def test_a_child_cannot_add_a_plant(client):
    resp = client.post("/plants", headers=CHILD, json={
        "category": "houseplant", "species_ref": "monstera_deliciosa",
    })
    assert resp.status_code == 403


# --------------------------------------------------------------------------- what gets mirrored, and how

def test_only_a_watering_completes_the_chore(client, session, monkeypatch):
    """A mist or a rotation is not the Skylight chore and must not tick it off."""
    from services.garden.skylight_sync import mirror_care_log
    from services.garden.models import Plant

    plant = _linked_plant(client)
    row = session.get(Plant, plant["id"])
    scheduled: list[str] = []
    monkeypatch.setattr(
        "services.garden.skylight_sync.threading.Thread",
        lambda *a, **k: type("T", (), {"start": lambda self: scheduled.append("started")})(),
        raising=False,
    )

    for action in ("mist", "rotate", "fertilize", "check_pests"):
        mirror_care_log(session, plant_id=row.id, action=action, performed_by="alex")

    assert scheduled == []


def test_a_watering_from_a_sync_handler_actually_posts(client, session, monkeypatch):
    """The old code made a loop that never ran, so this never happened at all."""
    import services.garden.skylight_sync as sync
    from services.garden.models import Plant

    plant = _linked_plant(client)
    row = session.get(Plant, plant["id"])
    started: list[dict] = []

    class _Thread:
        def __init__(self, target=None, name=None, daemon=None):
            started.append({"name": name, "daemon": daemon, "target": target})

        def start(self):
            pass

    monkeypatch.setattr(sync.threading, "Thread", _Thread)

    sync.mirror_care_log(session, plant_id=row.id, action="water", performed_by="alex")

    assert len(started) == 1
    assert started[0]["daemon"] is True
    assert started[0]["name"].startswith("skylight-mirror-")


def test_a_watering_inside_a_running_loop_is_scheduled_not_blocked(client, session, monkeypatch):
    import asyncio

    import services.garden.skylight_sync as sync
    from services.garden.models import Plant

    plant = _linked_plant(client)
    row = session.get(Plant, plant["id"])
    created: list[object] = []

    class _Loop:
        def create_task(self, coro):
            created.append(coro)
            coro.close()
            return None

    monkeypatch.setattr(sync.asyncio, "get_running_loop", lambda: _Loop())

    sync.mirror_care_log(session, plant_id=row.id, action="water", performed_by="alex")

    assert len(created) == 1
    assert asyncio.iscoroutine(created[0])
