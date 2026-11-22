"""Registered devices that stop checking in are archived, restored on check-in, and deleted a year later.

Every name, key and model here is made up.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

from services.identity import device_archive
from services.identity.main import app
from services.identity.models import AuditEvent, CapabilityInventory, Device, DeviceEvent
from services.identity.tests.test_user_panel_devices import REGISTER, _client, _phone_payload, login
from services.identity.tests.test_user_panel_devices import session_fixture as session_fixture

NOW = datetime(2026, 6, 1, 9, 0)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


@pytest.fixture
def admin(session: Session):
    yield _client(session, "alex")
    app.dependency_overrides = {}


def add(session: Session, key: str, *, seen: float | None, kind: str = "phone", archived: float | None = None,
        revoked: bool = False) -> Device:
    d = Device(device_key=key, kind=kind, label=f"Test {key}", owner_username="sam", revoked=revoked,
               last_seen_at=ago(seen) if seen is not None else None, first_seen_at=ago(400),
               archived_at=ago(archived) if archived is not None else None)
    session.add(d)
    session.commit()
    return d


def test_archives_after_ninety_days_without_a_check_in(session: Session, admin):
    add(session, "fresh", seen=10)
    add(session, "quiet", seen=91)
    add(session, "edge", seen=89)
    add(session, "revoked", seen=200, revoked=True)
    result = device_archive.run_once(session, NOW)
    assert [i["id"] for i in result["archived"]] == ["quiet"]
    rows = {d.device_key: d for d in session.exec(select(Device)).all()}
    assert rows["quiet"].archived_at == NOW.isoformat()
    assert rows["fresh"].archived_at is None and rows["edge"].archived_at is None and rows["revoked"].archived_at is None
    # Out of the lists; still there with include_archived.
    keys = {d["device_key"] for d in admin.get("/api/user-panel/devices").json()}
    assert "quiet" not in keys and "fresh" in keys
    keys = {d["device_key"] for d in admin.get("/api/user-panel/devices?include_archived=true").json()}
    assert "quiet" in keys
    assert session.exec(select(AuditEvent).where(AuditEvent.action == "device.archive")).first() is not None


def test_a_check_in_restores_an_archived_phone(session: Session, admin):
    login(session, "sam")
    from fastapi.testclient import TestClient

    sam = TestClient(app)
    sam.post(REGISTER, json=_phone_payload())
    phone = session.exec(select(Device)).one()
    phone.archived_at = ago(5)
    session.add(phone)
    session.commit()
    sam.post(REGISTER, json=_phone_payload(app_build="25"))
    session.refresh(phone)
    assert phone.archived_at is None


def test_deleted_only_a_year_after_archiving_and_only_if_never_back(session: Session):
    add(session, "gone", seen=500, archived=366)
    add(session, "young", seen=200, archived=100)
    back = add(session, "back", seen=500, archived=366)
    back.last_seen_at = ago(10)  # checked in after it was archived (before the restore ran)
    session.add(back)
    session.add(DeviceEvent(device_key="gone", username="sam", event="app_open"))
    session.add(CapabilityInventory(device_key="gone", username="sam"))
    session.add(DeviceEvent(device_key="young", username="sam", event="app_open"))
    session.commit()
    preview = device_archive.preview(session)
    assert "gone" in [i["id"] for i in preview["delete"]] and "back" not in [i["id"] for i in preview["delete"]]
    assert len(session.exec(select(Device)).all()) == 3  # the dry run deleted nothing
    assert [d.device_key for d in device_archive.due(session, NOW)] == ["gone"]
    result = device_archive.run_once(session, NOW)
    assert [i["id"] for i in result["deleted"]] == ["gone"]
    assert result["deleted"][0]["events"] == 1 and result["deleted"][0]["capability_reports"] == 1
    keys = {d.device_key for d in session.exec(select(Device)).all()}
    assert keys == {"young", "back"}
    assert session.exec(select(DeviceEvent).where(DeviceEvent.device_key == "young")).first() is not None
    assert session.exec(select(AuditEvent).where(AuditEvent.action == "device.delete_archived")).first() is not None
    with pytest.raises(device_archive.ArchiveError, match="checked in since"):
        device_archive.delete_device(session, "back", actor="alex", why="admin")


def test_turned_off_does_nothing(session: Session):
    add(session, "quiet", seen=200)
    device_archive.update_settings(session, {"enabled": False})
    assert device_archive.run_once(session, NOW) == {"archived": [], "deleted": []}


def test_admin_routes(session: Session, admin):
    add(session, "old", seen=200, archived=1)
    add(session, "live", seen=1)
    body = admin.get("/api/user-panel/devices/archive").json()
    assert [i["id"] for i in body["items"]] == ["old"] and body["settings"]["archive_after_days"] == 90
    assert admin.put("/api/user-panel/devices/archive/settings", json={"delete_after_days": 5}).status_code == 400
    assert admin.put("/api/user-panel/devices/archive/settings",
                     json={"archive_after_days": 120}).json()["settings"]["archive_after_days"] == 120
    assert admin.delete("/api/user-panel/devices/archive/live").status_code == 409
    assert admin.post("/api/user-panel/devices/archive/old/restore").json()["status"] == "restored"
    assert admin.post("/api/user-panel/devices/archive/old/restore").status_code == 409
    add(session, "older", seen=300, archived=2)
    assert admin.delete("/api/user-panel/devices/archive/older").json()["status"] == "deleted"
    sam = login(session, "sam")
    assert sam.get("/api/user-panel/devices/archive").status_code == 403


def test_one_notice_per_pass(session: Session):
    sent = []

    async def notifier(payload):
        sent.append(payload)
        return True

    asyncio.run(device_archive.notify_admins({"archived": [{"id": "a", "name": "Old tablet"}], "deleted": []},
                                             notifier))
    assert len(sent) == 1 and sent[0]["user"] == "@admins" and "Old tablet" in sent[0]["body"]
