"""Polling: a hung poll times out, failures in a row turn the entities unavailable, a good poll brings them back."""
from __future__ import annotations

import asyncio
import logging

from services.homehub import runtime as runtime_mod
from services.homehub.core import UNAVAILABLE, ConfigEntry, Driver, EntityEntry, Hub
from services.homehub.runtime import Runtime


class FlakyDriver(Driver):
    platform = "flaky"
    poll_interval = 0.001
    poll_timeout = 0.05

    def __init__(self, hub, entry):
        super().__init__(hub, entry)
        self.script: list[str] = []  # "ok", "fail", "hang", "publish"
        self.polls = 0

    async def async_poll(self):
        self.polls += 1
        step = self.script.pop(0) if self.script else "ok"
        if step == "fail":
            raise ConnectionError("no route to host")
        if step == "hang":
            await asyncio.Event().wait()
        if step == "publish":
            self.publish("sensor.flaky_reading", "42", {"unit_of_measurement": "W"})


def make() -> tuple[Runtime, FlakyDriver, ConfigEntry]:
    hub = Hub()
    entry = ConfigEntry("e1", "flaky", "Flaky box")
    hub.config_entries["e1"] = entry
    for eid in ("sensor.flaky_reading", "switch.flaky_relay"):
        hub.entities[eid] = EntityEntry(entity_id=eid, platform="flaky", unique_id=eid, config_entry_id="e1")
    rt = Runtime(hub, store=None)  # polling never touches the store
    driver = FlakyDriver(hub, entry)
    driver.publish("sensor.flaky_reading", "7", {"unit_of_measurement": "W"})
    driver.publish("switch.flaky_relay", "on")
    return rt, driver, entry


async def run_until(rt: Runtime, driver: FlakyDriver, entry: ConfigEntry, polls: int) -> None:
    task = asyncio.create_task(rt._poll_forever(entry, driver))
    try:
        for _ in range(2000):
            if driver.polls >= polls and not driver.script:
                await asyncio.sleep(0.01)
                break
            await asyncio.sleep(0.002)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def state(rt: Runtime, eid: str) -> str:
    return rt.hub.states.get(eid).state


async def test_a_hung_poll_times_out_and_the_next_one_runs():
    rt, driver, entry = make()
    driver.script = ["hang", "ok"]
    await run_until(rt, driver, entry, 2)
    assert driver.polls >= 2 and state(rt, "switch.flaky_relay") == "on"


async def test_failures_in_a_row_mark_unavailable_and_a_good_poll_restores(caplog):
    rt, driver, entry = make()
    n = runtime_mod.POLL_FAILURES_UNAVAILABLE
    driver.script = ["fail"] * (n - 1)
    await run_until(rt, driver, entry, n - 1)
    assert state(rt, "switch.flaky_relay") == "on"  # not yet: fewer than N failures

    caplog.clear()
    rt, driver, entry = make()
    driver.script = ["fail", "hang"] + ["fail"] * (n + 2)
    seen: list[str] = []
    rt.hub.bus.subscribe(lambda _t, ev: seen.append(ev["new_state"].state), "state_changed")
    with caplog.at_level(logging.DEBUG, logger="homehub.runtime"):
        task = asyncio.create_task(rt._poll_forever(entry, driver))
        for _ in range(2000):
            if not driver.script:
                break
            await asyncio.sleep(0.002)
        assert state(rt, "switch.flaky_relay") == UNAVAILABLE
        assert state(rt, "sensor.flaky_reading") == UNAVAILABLE
        assert rt.hub.states.get("sensor.flaky_reading").attributes["unit_of_measurement"] == "W"
        driver.script = ["publish"]  # the good poll publishes the reading, not the relay
        for _ in range(2000):
            if state(rt, "switch.flaky_relay") != UNAVAILABLE:
                break
            await asyncio.sleep(0.002)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert state(rt, "sensor.flaky_reading") == "42"  # what the poll said
    assert state(rt, "switch.flaky_relay") == "on"  # what it was before it went unavailable
    assert seen.count(UNAVAILABLE) == 2  # once per entity, not once per failed poll
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len([r for r in warnings if "poll failed" in r.getMessage()]) == 1
    assert len([r for r in warnings if "unavailable until" in r.getMessage()]) == 1
    assert any("polls again" in r.getMessage() for r in caplog.records)
