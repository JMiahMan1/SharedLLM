"""Tests for echo_ota."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.homehub import echo_ota
from services.homehub.tests.conftest import make_hub


class FakeStore:
    def __init__(self):
        self._d = {}

    def get_kv(self, key, default=None):
        return self._d.get(key, default)

    def set_kv(self, key, value):
        self._d[key] = value


@pytest.mark.asyncio
async def test_channel_set_only_on_off_and_only_once():
    hub, _entry = make_hub(
        "esphome",
        data={},
        entities=[
            {"entity_id": "assist_satellite.echo1", "unique_id": "as1"},
            {"entity_id": "select.echo1_online_updates", "unique_id": "sel1"},
            {"entity_id": "update.update_echo1_firmware", "unique_id": "upd1"},
        ],
        entry_id="e1",
    )
    hub.store = FakeStore()
    hub.states.set("select.echo1_online_updates", "off", {})
    calls = []

    async def fake_call(domain, service, service_data=None, **kw):
        calls.append((domain, service, service_data))
        return None

    hub.call_service = fake_call
    await echo_ota.ensure_channel(hub)
    assert len([c for c in calls if c[0] == "select"]) == 1
    assert calls[0][2]["entity_id"] == "select.echo1_online_updates"
    await echo_ota.ensure_channel(hub)
    assert len([c for c in calls if c[0] == "select"]) == 1


@pytest.mark.asyncio
async def test_household_beta_never_changed():
    hub, _entry = make_hub(
        "esphome",
        data={},
        entities=[
            {"entity_id": "assist_satellite.echo1", "unique_id": "as1"},
            {"entity_id": "select.echo1_online_updates", "unique_id": "sel1"},
        ],
        entry_id="e1",
    )
    hub.store = FakeStore()
    hub.states.set("select.echo1_online_updates", "beta", {})
    calls = []

    async def fake_call(domain, service, service_data=None, **kw):
        calls.append((domain, service, service_data))
        return None

    hub.call_service = fake_call
    await echo_ota.ensure_channel(hub)
    assert len([c for c in calls if c[0] == "select"]) == 0


@pytest.mark.asyncio
async def test_rollout_only_at_update_hour_and_one_at_a_time(monkeypatch):
    hub, _entry = make_hub(
        "esphome",
        data={},
        entities=[
            {"entity_id": "assist_satellite.echo1", "unique_id": "as1"},
            {"entity_id": "select.echo1_online_updates", "unique_id": "sel1"},
            {"entity_id": "update.upd1", "unique_id": "u1"},
            {"entity_id": "assist_satellite.echo2", "unique_id": "as2"},
            {"entity_id": "select.echo2_online_updates", "unique_id": "sel2"},
            {"entity_id": "update.upd2", "unique_id": "u2"},
        ],
        entry_id="e1",
    )
    hub.store = FakeStore()
    hub.states.set("update.upd1", "on", {"installed_version": "1.0", "latest_version": "1.1"})
    hub.states.set("update.upd2", "on", {"installed_version": "2.0", "latest_version": "2.1"})
    calls = []

    async def fake_call(domain, service, service_data=None, **kw):
        calls.append((domain, service, service_data))
        return None

    hub.call_service = fake_call
    monkeypatch.setattr(echo_ota, "HOMEHUB_ECHO_AUTO_UPDATE", True)
    monkeypatch.setattr(echo_ota, "HOMEHUB_ECHO_UPDATE_HOUR", 3)
    now_bad = datetime(2026, 1, 1, 4, 0, 0, tzinfo=UTC)
    await echo_ota.rollout_once(hub, now_bad)
    assert len([c for c in calls if c[0] == "update"]) == 0
    now_ok = datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC)
    await echo_ota.rollout_once(hub, now_ok)
    update_calls = [c for c in calls if c[0] == "update"]
    assert len(update_calls) == 1


@pytest.mark.asyncio
async def test_rollout_marks_done_on_version_change(monkeypatch):
    hub, _entry = make_hub(
        "esphome",
        data={},
        entities=[
            {"entity_id": "assist_satellite.echo1", "unique_id": "as1"},
            {"entity_id": "select.echo1_online_updates", "unique_id": "sel1"},
            {"entity_id": "update.upd1", "unique_id": "u1"},
        ],
        entry_id="e1",
    )
    hub.store = FakeStore()
    hub.states.set("update.upd1", "on", {"installed_version": "1.0", "latest_version": "1.1"})
    calls = []

    async def fake_call(domain, service, service_data=None, **kw):
        calls.append((domain, service, service_data))
        return None

    hub.call_service = fake_call
    monkeypatch.setattr(echo_ota, "HOMEHUB_ECHO_AUTO_UPDATE", True)
    monkeypatch.setattr(echo_ota, "HOMEHUB_ECHO_UPDATE_HOUR", 3)
    now = datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC)
    await echo_ota.rollout_once(hub, now)
    assert len([c for c in calls if c[0] == "update"]) == 1
    hub.states.set("update.upd1", "off", {"installed_version": "1.1", "latest_version": "1.1"})
    now2 = datetime(2026, 1, 1, 3, 5, 0, tzinfo=UTC)
    await echo_ota.rollout_once(hub, now2)
    assert len([c for c in calls if c[0] == "update"]) == 1


@pytest.mark.asyncio
async def test_rollout_timeout_stops_for_night(monkeypatch):
    hub, _entry = make_hub(
        "esphome",
        data={},
        entities=[
            {"entity_id": "assist_satellite.echo1", "unique_id": "as1"},
            {"entity_id": "select.echo1_online_updates", "unique_id": "sel1"},
            {"entity_id": "update.upd1", "unique_id": "u1"},
        ],
        entry_id="e1",
    )
    hub.store = FakeStore()
    hub.states.set("update.upd1", "on", {"installed_version": "1.0", "latest_version": "1.1"})
    calls = []

    async def fake_call(domain, service, service_data=None, **kw):
        calls.append((domain, service, service_data))
        return None

    hub.call_service = fake_call
    monkeypatch.setattr(echo_ota, "HOMEHUB_ECHO_AUTO_UPDATE", True)
    monkeypatch.setattr(echo_ota, "HOMEHUB_ECHO_UPDATE_HOUR", 3)
    now = datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC)
    await echo_ota.rollout_once(hub, now)
    assert len([c for c in calls if c[0] == "update"]) == 1
    now2 = datetime(2026, 1, 1, 3, 21, 0, tzinfo=UTC)
    await echo_ota.rollout_once(hub, now2)
    data = hub.store.get_kv("echo_ota", {})
    assert data.get("rollout_failed_day") == "2026-01-01"


def test_echo_ota_routes_admin_only():
    import pathlib
    import tempfile

    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store
    from services.homehub.tests.conftest import make_hub

    hub, _entry = make_hub("esphome", data={}, entities=[])
    store = Store(str(pathlib.Path(tempfile.mkdtemp()) / "h.db"), Fernet.generate_key().decode())
    rt = Runtime(hub, store)
    client = TestClient(build_app(rt, Auth(store, "secret")))
    r1 = client.get("/hub/echo_ota/status")
    assert r1.status_code in (401, 403)
    r2 = client.post("/hub/echo_ota/update_all")
    assert r2.status_code in (401, 403)
    r3 = client.get("/hub/echo_ota/status", headers={"authorization": "Bearer secret"})
    assert r3.status_code == 200
    r4 = client.post("/hub/echo_ota/update_all", headers={"authorization": "Bearer secret"})
    assert r4.status_code == 200


def _echo_hub():
    hub, _entry = make_hub(
        "esphome",
        data={},
        entities=[
            {"entity_id": "assist_satellite.echo1", "unique_id": "as1"},
            {"entity_id": "select.echo1_online_updates", "unique_id": "sel1"},
            {"entity_id": "update.update_echo1_firmware", "unique_id": "upd1"},
        ],
        entry_id="e1",
    )
    hub.store = FakeStore()
    calls = []

    async def fake_call(domain, service, service_data=None, **kw):
        calls.append((domain, service, service_data))
        return None

    hub.call_service = fake_call
    return hub, calls


@pytest.mark.asyncio
async def test_a_person_turning_updates_off_after_homehub_turned_them_on_is_respected():
    hub, calls = _echo_hub()
    hub.states.set("select.echo1_online_updates", "off", {})
    await echo_ota.ensure_channel(hub)          # HomeHub turns it on once
    hub.states.set("select.echo1_online_updates", "beta", {})
    await echo_ota.ensure_channel(hub)          # someone picks beta
    hub.states.set("select.echo1_online_updates", "off", {})
    await echo_ota.ensure_channel(hub)          # then turns it off: HomeHub must leave it
    assert len([c for c in calls if c[0] == "select"]) == 1


@pytest.mark.asyncio
async def test_the_update_hour_is_the_households_local_hour():
    """3 means three in the morning in the hub's own time zone, not 3:00 UTC."""
    from zoneinfo import ZoneInfo

    hub, calls = _echo_hub()
    hub.time_zone = "America/Phoenix"
    hub.states.set("update.update_echo1_firmware", "on", {"installed_version": "1.0"})
    now = echo_ota._hub_now(hub)
    assert now.tzinfo is not None and now.utcoffset() == datetime.now(ZoneInfo("America/Phoenix")).utcoffset()
    # 3:00 UTC is 20:00 in Phoenix: no install then.
    await echo_ota.rollout_once(hub, now=datetime(2026, 1, 15, 20, 0, tzinfo=ZoneInfo("America/Phoenix")))
    assert not [c for c in calls if c[0] == "update"]
    await echo_ota.rollout_once(hub, now=datetime(2026, 1, 15, 3, 5, tzinfo=ZoneInfo("America/Phoenix")))
    assert [c for c in calls if c[0] == "update"]


@pytest.mark.asyncio
async def test_an_install_started_late_in_the_hour_is_still_followed_after_it():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Phoenix")
    hub, _calls = _echo_hub()
    hub.time_zone = "America/Phoenix"
    hub.states.set("update.update_echo1_firmware", "on", {"installed_version": "1.0"})
    await echo_ota.rollout_once(hub, now=datetime(2026, 1, 15, 3, 55, tzinfo=tz))
    assert hub.store.get_kv(echo_ota.ECHO_OTA_KV_KEY)["rollout"]["active_node"] == "e1"
    # At 4:05 the Echo reports the new version: the rollout is marked done outside the hour.
    hub.states.set("update.update_echo1_firmware", "off", {"installed_version": "1.1"})
    await echo_ota.rollout_once(hub, now=datetime(2026, 1, 15, 4, 5, tzinfo=tz))
    assert not hub.store.get_kv(echo_ota.ECHO_OTA_KV_KEY).get("rollout")
