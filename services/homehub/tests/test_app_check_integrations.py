"""The app vetting accounts as integrations: abuse.ch MalwareBazaar and Common Sense Media.

Adding them by hand (the key checked with one lookup), changing the key, the
internal-secret-only read other services use, their driver, and the one-time
move from identity's old global settings. Every key and secret here is made up.
"""
from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.common import malwarebazaar
from services.homehub import app_checks_migration as move
from services.homehub import entries, flows, manual_flows
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, Hub
from services.homehub.drivers import driver_class
from services.homehub.flows import FlowError, NeedsInput
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "made-up-internal-secret"
GOOD = "made-up-good-abuse-ch-key"
BAD = "made-up-revoked-abuse-ch-key"
INTERNAL = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def rt(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    return FakeRuntime(hub, store)


@pytest.fixture
def abuse_ch(monkeypatch):
    """MalwareBazaar as the flow sees it: GOOD (and GOOD-something) is accepted, anything else refused."""
    calls: list[tuple[str, str]] = []

    async def query(auth_key, sha256):
        calls.append((auth_key, sha256))
        if auth_key.startswith(GOOD):
            return 200, {"query_status": "hash_not_found"}
        return 200, {"query_status": "unknown_auth_key"}

    monkeypatch.setattr(manual_flows, "malwarebazaar_query", query)
    return calls


async def add(rt, domain, input_=None):
    finding = manual_flows.manual_finding(domain, {"input": input_ or {}})
    return await flows.adopt(rt, finding, {"input": input_ or {}})


def test_both_are_cloud_accounts_one_per_hub(rt):
    listed = {f["domain"]: f for f in manual_flows.manual_list(rt.hub)}
    mb, csm = listed["malwarebazaar"], listed["common_sense_media"]
    assert mb["label"] == "abuse.ch MalwareBazaar (app malware check)"
    assert csm["label"] == "Common Sense Media (app age ratings)"
    assert mb["kind"] == csm["kind"] == "cloud" and mb["single"] and csm["single"]
    assert csm["fields"] == []
    [key] = mb["fields"]
    assert key["key"] == "auth_key" and key["type"] == "password"
    assert "auth.abuse.ch" in key["help"] and "SHA-256" in key["help"]
    assert manual_flows.manual_finding("malwarebazaar", {})["integration"] == "malwarebazaar"  # no address asked


def test_their_driver_loads_with_nothing_to_drive(rt):
    for domain in ("malwarebazaar", "common_sense_media"):
        cls = driver_class(domain)
        assert cls is not None and cls.poll_interval is None


async def test_a_good_key_is_checked_once_and_kept(rt, abuse_ch):
    out = await add(rt, "malwarebazaar", {"auth_key": f"  {GOOD}  "})
    entry = rt.hub.config_entries[out["entry_id"]]
    assert entry.data == {"auth_key": GOOD} and entry.unique_id == "malwarebazaar"
    assert abuse_ch == [(GOOD, malwarebazaar.KEY_CHECK_SHA256)]  # the empty file's hash: nothing about the family
    assert out["entities"] == [] and rt.hub.devices[out["device_id"]].entry_type == "service"
    assert rt.started == [out["entry_id"]]


async def test_a_refused_key_is_not_saved(rt, abuse_ch):
    with pytest.raises(NeedsInput) as err:
        await add(rt, "malwarebazaar", {"auth_key": BAD})
    assert "abuse.ch refused the Auth-Key" in (err.value.error or "")
    assert rt.hub.config_entries == {}
    with pytest.raises(NeedsInput):
        await add(rt, "malwarebazaar", {})  # no key at all: asked for, never looked up
    assert len(abuse_ch) == 1


async def test_abuse_ch_not_answering_is_an_error_not_a_pass(rt, monkeypatch):
    async def down(auth_key, sha256):
        raise TimeoutError

    monkeypatch.setattr(manual_flows, "malwarebazaar_query", down)
    with pytest.raises(FlowError) as err:
        await add(rt, "malwarebazaar", {"auth_key": GOOD})
    assert err.value.status == 502 and GOOD not in str(err.value)
    assert rt.hub.config_entries == {}


async def test_each_is_one_per_hub(rt, abuse_ch):
    await add(rt, "malwarebazaar", {"auth_key": GOOD})
    with pytest.raises(FlowError) as err:
        await add(rt, "malwarebazaar", {"auth_key": GOOD})
    assert err.value.status == 409
    out = await add(rt, "common_sense_media")
    assert rt.hub.config_entries[out["entry_id"]].data == {}
    with pytest.raises(FlowError) as err:
        await add(rt, "common_sense_media")
    assert err.value.status == 409
    listed = {f["domain"]: f for f in manual_flows.manual_list(rt.hub)}
    assert listed["malwarebazaar"]["configured"] and listed["common_sense_media"]["configured"]


async def test_the_key_can_be_replaced_never_shown_and_is_checked(rt, abuse_ch):
    out = await add(rt, "malwarebazaar", {"auth_key": GOOD})
    entry = rt.hub.config_entries[out["entry_id"]]
    fields = entries.summary(rt, entry)["reconfigure"]
    assert fields == [{"key": "auth_key", "label": "Auth-Key", "type": "password", "set": True,
                       "help": manual_flows.MB_AUTH_KEY["help"]}]
    assert GOOD not in str(entries.list_entries(rt))
    with pytest.raises(entries.EntryError) as err:
        await entries.reconfigure_entry(rt, entry.entry_id, {"data": {"auth_key": BAD}})
    assert "refused" in str(err.value)
    assert entry.data == {"auth_key": GOOD}  # a refused key is not saved
    await entries.reconfigure_entry(rt, entry.entry_id, {"data": {"auth_key": ""}, "title": "Malware check"})
    assert entry.data == {"auth_key": GOOD} and entry.title == "Malware check"  # empty keeps it, unchecked
    checks = len(abuse_ch)
    await entries.reconfigure_entry(rt, entry.entry_id, {"data": {"auth_key": GOOD + "-2"}})
    assert len(abuse_ch) == checks + 1 and entry.data == {"auth_key": GOOD + "-2"}


async def test_internal_read_answers_only_the_stacks_services(rt, abuse_ch):
    client = TestClient(build_app(rt, Auth(rt.store, SECRET)))
    assert client.get("/hub/integrations/malwarebazaar", headers=INTERNAL).json() == {
        "domain": "malwarebazaar", "configured": False, "data": {}}
    out = await add(rt, "malwarebazaar", {"auth_key": GOOD})
    await add(rt, "common_sense_media")
    r = client.get("/hub/integrations/malwarebazaar", headers=INTERNAL)
    assert r.status_code == 200 and r.json()["configured"] is True and r.json()["data"] == {"auth_key": GOOD}
    assert client.get("/hub/integrations/malwarebazaar", headers={"X-Internal-Secret": SECRET}).status_code == 200
    assert client.get("/hub/integrations/common_sense_media", headers=INTERNAL).json()["configured"] is True
    for headers in ({}, {"Authorization": "Bearer not-the-secret"}, {"X-Internal-Secret": "nope"}):
        r = client.get("/hub/integrations/malwarebazaar", headers=headers)
        assert r.status_code == 401 and GOOD not in r.text
    rt.hub.config_entries[out["entry_id"]].disabled_by = "user"
    assert client.get("/hub/integrations/malwarebazaar", headers=INTERNAL).json()["configured"] is False


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


async def test_a_household_with_both_gets_both_entries_once(rt, identity, caplog):
    caplog.set_level(logging.DEBUG)
    identity["settings"] = {move.OLD_KEY: GOOD, move.OLD_SWITCH: "true", "other": "x"}
    out = await move.migrate(rt)
    assert out["status"] == "moved" and _domains(rt) == ["common_sense_media", "malwarebazaar"]
    mb = next(e for e in rt.hub.config_entries.values() if e.domain == "malwarebazaar")
    assert mb.data == {"auth_key": GOOD}
    assert identity["cleared"] == [[move.OLD_KEY, move.OLD_SWITCH]]
    assert GOOD not in caplog.text and GOOD not in str(rt.store.get_kv(move.MARKER))
    # Removed by an admin later: never brought back.
    rt.hub.config_entries.clear()
    assert (await move.migrate(rt))["status"] == "done before"
    assert rt.hub.config_entries == {} and identity["reads"] == 1


async def test_common_sense_switched_off_stays_off(rt, identity):
    identity["settings"] = {move.OLD_KEY: "", move.OLD_SWITCH: "False"}
    await move.migrate(rt)
    assert _domains(rt) == []
    assert identity["cleared"] == [[move.OLD_SWITCH]]


async def test_a_fresh_hub_keeps_common_sense_on_by_default(rt, identity):
    await move.migrate(rt)  # no rows at all (identity no longer seeds them)
    assert _domains(rt) == ["common_sense_media"] and identity["cleared"] == []


async def test_an_entry_already_there_is_not_made_twice(rt, identity, abuse_ch):
    await add(rt, "malwarebazaar", {"auth_key": GOOD})
    identity["settings"] = {move.OLD_KEY: "made-up-older-key"}
    await move.migrate(rt)
    mbs = [e for e in rt.hub.config_entries.values() if e.domain == "malwarebazaar"]
    assert len(mbs) == 1 and mbs[0].data == {"auth_key": GOOD}


async def test_identity_down_means_try_again_later(rt, identity, monkeypatch, caplog):
    identity["settings"] = ConnectionError("refused")
    with pytest.raises(ConnectionError):
        await move.migrate(rt)
    assert rt.store.get_kv(move.MARKER) is None and _domains(rt) == []

    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            identity["settings"] = {move.OLD_KEY: GOOD}

    monkeypatch.setattr(move.asyncio, "sleep", sleep)
    caplog.set_level(logging.INFO, logger="homehub.app_checks_migration")
    await move.run(rt)
    assert sleeps == [30, 60, 120]
    assert _domains(rt) == ["common_sense_media", "malwarebazaar"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert GOOD not in caplog.text
