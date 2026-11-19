"""An entry that cannot load by retrying is parked: bad credentials and permanent setup errors.

Every name and address here is made up.
"""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet

from services.homehub import entries
from services.homehub import runtime as rt
from services.homehub.core import AuthRequired, ConfigEntry, Driver, EntityEntry, Hub, SetupError
from services.homehub.runtime import Runtime
from services.homehub.store import Store


def _runtime(tmp_path, monkeypatch, cls, domain="tasmota"):
    monkeypatch.setattr(rt, "driver_class", lambda _domain: cls)
    monkeypatch.setattr(rt, "RETRY_DELAYS", (0.001,))
    hub = Hub()
    hub.config_entries["x"] = ConfigEntry("x", domain, title="Shelf plug")
    hub.entities["switch.shelf"] = EntityEntry(entity_id="switch.shelf", platform=domain, unique_id="u1",
                                               config_entry_id="x")
    hub.states.set("switch.shelf", "on")
    return hub, Runtime(hub, Store(str(tmp_path / "h.db"), Fernet.generate_key().decode()))


async def test_a_refused_password_parks_the_entry_and_is_never_retried(tmp_path, monkeypatch):
    tries = []

    class Refuses(Driver):
        async def async_setup(self):
            tries.append(1)
            raise AuthRequired("Device 192.0.2.5 did not accept its web password")

    hub, runtime = _runtime(tmp_path, monkeypatch, Refuses)
    await runtime.start_all()
    assert await runtime.wait_first_setups(2) == []
    await asyncio.sleep(0.05)  # many retry delays: none is taken
    entry = hub.config_entries["x"]
    assert tries == [1]
    assert entry.state == "setup_error" and entry.reason.startswith("AuthRequired: ")
    assert entry.needs == "password" and entries.needs_of(entry) == "password"
    assert hub.states.get("switch.shelf").state == "unavailable"
    assert runtime.failing_since["x"] is not None
    await runtime.shutdown()


async def test_a_login_refusal_says_it_needs_the_login_and_reload_tries_again(tmp_path, monkeypatch):
    answers = [AuthRequired("Account refused the email and password", needs="login"), None]

    class Account(Driver):
        poll_interval = None

        async def async_setup(self):
            exc = answers.pop(0)
            if exc is not None:
                raise exc

    hub, runtime = _runtime(tmp_path, monkeypatch, Account, domain="emporia_vue")
    await runtime.start_all()
    await runtime.wait_first_setups(2)
    entry = hub.config_entries["x"]
    assert entry.state == "setup_error" and entries.needs_of(entry) == "login"
    # Saving a new login restarts the entry (relogin / reconfigure): it loads and the need is gone.
    await runtime.stop_entry("x")
    runtime.start_entry(entry)
    for _ in range(100):
        if entry.state == "loaded":
            break
        await asyncio.sleep(0.01)
    assert entry.state == "loaded" and entry.needs is None and entries.needs_of(entry) is None
    await runtime.shutdown()


async def test_a_password_changed_while_running_parks_the_entry_from_its_poll(tmp_path, monkeypatch):
    polls = []

    class Changed(Driver):
        poll_interval = 0.001

        async def async_setup(self):
            return None

        async def async_poll(self):
            polls.append(1)
            raise AuthRequired("Device 192.0.2.5 did not accept its web password")

    hub, runtime = _runtime(tmp_path, monkeypatch, Changed)
    await runtime.start_all()
    for _ in range(100):
        if hub.config_entries["x"].state == "setup_error":
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert polls == [1] and hub.config_entries["x"].state == "setup_error"
    assert "x" not in hub.drivers
    assert hub.states.get("switch.shelf").state == "unavailable"
    await runtime.shutdown()


def test_the_need_is_never_stored(tmp_path):
    store = Store(str(tmp_path / "h.db"), Fernet.generate_key().decode())
    entry = ConfigEntry("x", "reolink", state="setup_error", reason="AuthRequired: refused", needs="login")
    store.save_config_entry(entry)
    loaded = store.load_config_entries()[0]
    assert loaded.state == "not_loaded" and loaded.needs is None


def test_needs_of_still_reads_an_older_auth_reason():
    entry = ConfigEntry("x", "tasmota", state="setup_retry", reason="AuthRequired: needs its web password")
    assert entries.needs_of(entry) == "password"
    entry.state = "loaded"
    assert entries.needs_of(entry) is None


@pytest.mark.parametrize("needs", ["pairing", "bindkey"])
def test_auth_required_carries_what_it_needs(needs):
    exc = AuthRequired("refused", needs=needs)
    assert exc.needs == needs and str(exc) == "refused" and exc.status == 503


def test_setup_error_is_a_hub_error():
    assert issubclass(SetupError, Exception) and SetupError("x").status == 400


# -- permanent errors and failing polls ------------------------------------------------------

async def test_a_setup_error_parks_the_entry(tmp_path, monkeypatch):
    tries = []

    class NoAddress(Driver):
        async def async_setup(self):
            tries.append(1)
            raise SetupError("Tasmota: no address; set entry data or options 'host'")

    hub, runtime = _runtime(tmp_path, monkeypatch, NoAddress)
    await runtime.start_all()
    await runtime.wait_first_setups(2)
    await asyncio.sleep(0.05)
    entry = hub.config_entries["x"]
    assert tries == [1] and entry.state == "setup_error" and entry.reason.startswith("SetupError: ")
    assert entries.needs_of(entry) is None
    await runtime.shutdown()


async def test_a_driver_whose_settings_are_unusable_is_parked_not_left_silent(tmp_path, monkeypatch):
    class BadZone(Driver):
        def __init__(self, hub, entry):
            raise SetupError("needs a time zone")

    hub, runtime = _runtime(tmp_path, monkeypatch, BadZone)
    await runtime.start_all()
    assert await runtime.wait_first_setups(2) == []
    entry = hub.config_entries["x"]
    assert entry.state == "setup_error" and entry.reason == "SetupError: needs a time zone"
    await runtime.shutdown()


async def test_failing_polls_mark_the_entities_unavailable_and_say_why_logging_on_change(
        tmp_path, monkeypatch, caplog):
    import logging

    outcome: list = []
    waiting = asyncio.Event()

    class Flaky(Driver):
        poll_interval = 0.001

        async def async_setup(self):
            return None

        async def async_poll(self):
            while not outcome:
                waiting.set()
                await asyncio.sleep(0.001)
            result = outcome.pop(0)
            if isinstance(result, Exception):
                raise result
            self.publish(self.entities[0], "on")

    hub, runtime = _runtime(tmp_path, monkeypatch, Flaky)
    monkeypatch.setattr(rt, "POLL_FAILURES_UNAVAILABLE", 3)
    entry = hub.config_entries["x"]

    async def run(*results):
        outcome.extend(results)
        for _ in range(400):
            await asyncio.sleep(0.005)
            if not outcome and waiting.is_set():
                break
        waiting.clear()
        for _ in range(400):  # the last result has been handled once the poll waits again
            if waiting.is_set():
                break
            await asyncio.sleep(0.005)

    with caplog.at_level(logging.DEBUG, logger="homehub.runtime"):
        await runtime.start_all()
        await runtime.wait_first_setups(2)
        await run(OSError("timed out"), OSError("timed out"))
        assert hub.states.get("switch.shelf").state == "on" and entry.state == "loaded"  # two blips: kept
        await run(OSError("timed out"), OSError("timed out"))
        assert hub.states.get("switch.shelf").state == "unavailable"
        assert entry.state == "setup_retry" and entry.reason == "OSError: timed out"
        assert runtime.failing_since["x"] is not None
        await run(None)
        assert entry.state == "loaded" and entry.reason is None and "x" not in runtime.failing_since
        assert hub.states.get("switch.shelf").state == "on"
        await runtime.shutdown()
    failed = [r for r in caplog.records if r.levelno == logging.WARNING and "poll failed" in r.getMessage()]
    assert len(failed) == 1  # the same reason four times: logged once
    assert sum("unavailable until" in r.getMessage() for r in caplog.records) == 1
    assert any("polls again" in r.getMessage() for r in caplog.records)


async def test_a_driver_that_keeps_its_own_state_is_not_overruled_by_a_working_poll(tmp_path, monkeypatch):
    polled = []

    class OwnState(Driver):
        poll_interval = 0.001

        async def async_setup(self):
            return None

        async def async_poll(self):
            polled.append(1)
            self.entry.state, self.entry.reason = "setup_retry", "the builder is not answering"

    hub, runtime = _runtime(tmp_path, monkeypatch, OwnState, domain="esphome_builder")
    await runtime.start_all()
    for _ in range(100):
        if len(polled) >= 3:
            break
        await asyncio.sleep(0.005)
    assert hub.config_entries["x"].state == "setup_retry"
    await runtime.shutdown()
