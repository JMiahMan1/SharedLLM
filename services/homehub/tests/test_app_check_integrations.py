"""The app vetting account as an integration: Common Sense Media.

Adding it by hand, the internal-secret-only read telemetry uses, its driver,
the one-time move of the Common Sense switch from identity's old global
settings, and the one-time move of the former abuse.ch MalwareBazaar
integration's key to identity's household default (``abuse_ch_move``). Every
key and secret here is made up.
"""
from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import abuse_ch_move, flows, manual_flows
from services.homehub import app_checks_migration as move
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, Hub
from services.homehub.drivers import driver_class
from services.homehub.flows import FlowError
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "made-up-internal-secret"
GOOD = "made-up-good-abuse-ch-key"
INTERNAL = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def rt(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    return FakeRuntime(hub, store)


async def add(rt, domain, input_=None):
    finding = manual_flows.manual_finding(domain, {"input": input_ or {}})
    return await flows.adopt(rt, finding, {"input": input_ or {}})


def _old_abuse_ch_entry(rt, key=GOOD, entry_id="mb"):
    """The hub's former abuse.ch integration, as an install that had it still holds it."""
    entry = ConfigEntry(entry_id, "malwarebazaar", "MalwareBazaar", {"auth_key": key}, source="user",
                        unique_id="malwarebazaar")
    rt.hub.config_entries[entry_id] = entry
    rt.store.save_config_entry(entry)
    return entry


def test_common_sense_is_a_cloud_account_one_per_hub(rt):
    listed = {f["domain"]: f for f in manual_flows.manual_list(rt.hub)}
    csm = listed["common_sense_media"]
    assert csm["label"] == "Common Sense Media (app age ratings)"
    assert csm["kind"] == "cloud" and csm["single"] and csm["fields"] == []


def test_abuse_ch_is_no_hub_integration_any_more(rt):
    listed = {f["domain"] for f in manual_flows.manual_list(rt.hub)}
    assert "malwarebazaar" not in listed and "malwarebazaar" not in manual_flows.FLOWS
    assert "malwarebazaar" not in manual_flows.RECONFIGURE_CHECKS
    assert driver_class("malwarebazaar") is None


def test_common_senses_driver_loads_with_nothing_to_drive(rt):
    cls = driver_class("common_sense_media")
    assert cls is not None and cls.poll_interval is None


async def test_common_sense_is_one_per_hub(rt):
    out = await add(rt, "common_sense_media")
    assert rt.hub.config_entries[out["entry_id"]].data == {}
    with pytest.raises(FlowError) as err:
        await add(rt, "common_sense_media")
    assert err.value.status == 409
    listed = {f["domain"]: f for f in manual_flows.manual_list(rt.hub)}
    assert listed["common_sense_media"]["configured"]


async def test_internal_read_answers_only_the_stacks_services(rt):
    client = TestClient(build_app(rt, Auth(rt.store, SECRET)))
    assert client.get("/hub/integrations/common_sense_media", headers=INTERNAL).json() == {
        "domain": "common_sense_media", "configured": False, "data": {}}
    out = await add(rt, "common_sense_media")
    r = client.get("/hub/integrations/common_sense_media", headers=INTERNAL)
    assert r.status_code == 200 and r.json()["configured"] is True
    assert client.get("/hub/integrations/common_sense_media", headers={"X-Internal-Secret": SECRET}).status_code == 200
    for headers in ({}, {"Authorization": "Bearer not-the-secret"}, {"X-Internal-Secret": "nope"}):
        assert client.get("/hub/integrations/common_sense_media", headers=headers).status_code == 401
    rt.hub.config_entries[out["entry_id"]].disabled_by = "user"
    assert client.get("/hub/integrations/common_sense_media", headers=INTERNAL).json()["configured"] is False


def test_an_admins_own_token_is_refused(rt, monkeypatch):
    from services.homehub.auth import Principal

    auth = Auth(rt.store, SECRET)
    monkeypatch.setattr(auth, "authenticate", lambda tok: Principal("u1", "Robin", True) if tok else None)
    client = TestClient(build_app(rt, auth))
    rt.hub.config_entries["mb"] = ConfigEntry("mb", "malwarebazaar", "MalwareBazaar", {"auth_key": GOOD})
    assert client.get("/hub/entries", headers={"Authorization": "Bearer admin-token"}).status_code == 200
    r = client.get("/hub/integrations/malwarebazaar", headers={"Authorization": "Bearer admin-token"})
    assert r.status_code == 401 and GOOD not in r.text
    assert GOOD not in client.get("/hub/entries", headers={"Authorization": "Bearer admin-token"}).text


# -- the one-time move from identity ------------------------------------------------------------

@pytest.fixture
def identity(monkeypatch):
    state: dict = {"settings": {}, "cleared": [], "reads": 0}

    def read():
        state["reads"] += 1
        if isinstance(state["settings"], Exception):
            raise state["settings"]
        return dict(state["settings"])

    def clear(keys):
        state["cleared"].append(list(keys))

    monkeypatch.setattr(move, "read_identity_settings", read)
    monkeypatch.setattr(move, "clear_identity_settings", clear)
    return state


def _domains(rt):
    return sorted(e.domain for e in rt.hub.config_entries.values())


async def test_the_common_sense_switch_moves_once(rt, identity, caplog):
    caplog.set_level(logging.DEBUG)
    identity["settings"] = {"malwarebazaar_auth_key": GOOD, move.OLD_SWITCH: "true", "other": "x"}
    out = await move.migrate(rt)
    assert out["status"] == "moved" and _domains(rt) == ["common_sense_media"]
    # The old abuse.ch key setting is identity's to move now: the hub neither copies nor empties it.
    assert identity["cleared"] == [[move.OLD_SWITCH]]
    assert GOOD not in caplog.text and GOOD not in str(rt.store.get_kv(move.MARKER))
    # Removed by an admin later: never brought back.
    rt.hub.config_entries.clear()
    assert (await move.migrate(rt))["status"] == "done before"
    assert rt.hub.config_entries == {} and identity["reads"] == 1


async def test_common_sense_switched_off_stays_off(rt, identity):
    identity["settings"] = {move.OLD_SWITCH: "False"}
    await move.migrate(rt)
    assert _domains(rt) == []
    assert identity["cleared"] == [[move.OLD_SWITCH]]


async def test_a_fresh_hub_keeps_common_sense_on_by_default(rt, identity):
    await move.migrate(rt)  # no rows at all (identity no longer seeds them)
    assert _domains(rt) == ["common_sense_media"] and identity["cleared"] == []


async def test_an_entry_already_there_is_not_made_twice(rt, identity):
    await add(rt, "common_sense_media")
    await move.migrate(rt)
    assert _domains(rt) == ["common_sense_media"]


async def test_identity_down_means_try_again_later(rt, identity, monkeypatch, caplog):
    identity["settings"] = ConnectionError("refused")
    with pytest.raises(ConnectionError):
        await move.migrate(rt)
    assert rt.store.get_kv(move.MARKER) is None and _domains(rt) == []

    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            identity["settings"] = {move.OLD_SWITCH: "true"}

    monkeypatch.setattr(move.asyncio, "sleep", sleep)
    caplog.set_level(logging.INFO, logger="homehub.app_checks_migration")
    await move.run(rt)
    assert sleeps == [30, 60, 120]
    assert _domains(rt) == ["common_sense_media"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


# -- the one-time move of the abuse.ch integration to identity ---------------------------------

@pytest.fixture
def household(monkeypatch):
    """Identity's household default abuse.ch key: ``key`` is what it holds, ``down`` makes it unreachable."""
    state: dict = {"key": None, "down": None, "calls": 0}

    def give(key):
        state["calls"] += 1
        if state["down"] is not None:
            raise state["down"]
        if state["key"]:
            return "kept"
        state["key"] = key
        return "set"

    monkeypatch.setattr(abuse_ch_move, "give_identity_the_key", give)
    return state


async def test_the_key_goes_to_the_household_default_and_the_entry_goes(rt, household, caplog):
    caplog.set_level(logging.DEBUG)
    _old_abuse_ch_entry(rt)
    await add(rt, "common_sense_media")
    out = await abuse_ch_move.migrate(rt)
    assert out["status"] == "moved" and out["household_key"] == "set" and out["removed"] == 1
    assert household["key"] == GOOD
    assert _domains(rt) == ["common_sense_media"]  # Common Sense is left alone
    assert all(e.domain != "malwarebazaar" for e in rt.store.load_config_entries())
    assert GOOD not in caplog.text and GOOD not in str(rt.store.get_kv(abuse_ch_move.MARKER))
    # Once: a second start asks identity nothing.
    assert (await abuse_ch_move.migrate(rt))["status"] == "done before" and household["calls"] == 1


async def test_a_household_key_already_set_is_kept_and_the_entry_still_goes(rt, household):
    household["key"] = "made-up-key-set-in-identity"
    _old_abuse_ch_entry(rt)
    out = await abuse_ch_move.migrate(rt)
    assert out["household_key"] == "kept" and household["key"] == "made-up-key-set-in-identity"
    assert _domains(rt) == []


async def test_without_the_old_integration_nothing_is_sent(rt, household):
    out = await abuse_ch_move.migrate(rt)
    assert out["removed"] == 0 and household["calls"] == 0
    assert rt.store.get_kv(abuse_ch_move.MARKER)


async def test_identity_down_keeps_the_entry_and_tries_again(rt, household, monkeypatch, caplog):
    _old_abuse_ch_entry(rt)
    household["down"] = ConnectionError(f"could not post {GOOD}")
    with pytest.raises(ConnectionError):
        await abuse_ch_move.migrate(rt)
    assert _domains(rt) == ["malwarebazaar"] and rt.store.get_kv(abuse_ch_move.MARKER) is None

    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            household["down"] = None

    monkeypatch.setattr(abuse_ch_move.asyncio, "sleep", sleep)
    caplog.set_level(logging.DEBUG, logger="homehub.abuse_ch_move")
    await abuse_ch_move.run(rt)
    assert sleeps == [30, 60] and household["key"] == GOOD and _domains(rt) == []
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert GOOD not in caplog.text
