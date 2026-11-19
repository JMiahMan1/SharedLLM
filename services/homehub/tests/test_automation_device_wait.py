"""An automation that calls a device's own action waits for that device instead of failing every run.

ESPHome nodes bring their user-defined actions with them (``esphome.<node>_<action>``) and take them away when
they disconnect. An automation calling one while the node is away is waiting, not broken: its runs are skipped
quietly, that is said once, and it resumes by itself when the node connects. A missing service that is not a
device's (a typo, a core service that does not exist) still fails where everyone can see it.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from aioesphomeapi import UserService, UserServiceArg, UserServiceArgType

from services.homehub.automation import AutomationEngine, FakeClock
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_automation import START, FakeStore, advance, auto, record, settle

SET_WEATHER = UserService(name="set_weather", key=7, args=[UserServiceArg(name="summary",
                                                                          type=UserServiceArgType.STRING)])
ACTION = "esphome.wall_screen_set_weather"
EVERY_MINUTE = [{"trigger": "time_pattern", "minutes": "/1"}]
SYNC = auto(EVERY_MINUTE, [{"action": ACTION, "data": {"summary": "sunny"}}, {"action": "test.note"}],
            id="screen_sync", alias="Screen sync")


class FakeNode:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def execute_service(self, service: Any, data: dict, *, return_response: Any = None) -> None:
        self.executed.append((service.name, data))


@pytest.fixture
async def setup():
    hub, entry = make_hub("esphome", {"host": "192.0.2.10", "port": 6053, "password": ""})
    drv = ESPHomeDriver(hub, entry)
    node = FakeNode()
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    yield hub, drv, node, eng
    await eng.stop()


def connect(drv: ESPHomeDriver, node: FakeNode) -> None:
    drv._client = node
    drv._register_services("wall-screen", [SET_WEATHER])


def disconnect(drv: ESPHomeDriver) -> None:
    drv._client = None
    drv._unregister_services()


def status(eng: AutomationEngine, item_id: str) -> dict:
    return next(s for s in eng.status() if s["id"] == item_id)


async def test_waits_silently_resumes_when_the_node_connects_and_waits_again(setup, caplog) -> None:
    hub, drv, node, eng = setup
    notes = record(hub, "test.note")
    caplog.set_level(logging.INFO, logger="homehub.automation")
    await eng.load([SYNC], {}, [])
    await eng.start()
    await settle()

    st = status(eng, "screen_sync")
    assert st["waiting_reason"] == f"needs {ACTION}, which no connected device offers"
    assert st["enabled"] is True and st["last_error"] is None and st["unsupported_reason"] is None
    assert hub.states.get("automation.screen_sync").attributes["waiting_reason"] == st["waiting_reason"]

    await advance(eng, 5 * 60)
    assert notes == []  # skipped, not half-run
    assert status(eng, "screen_sync")["last_error"] is None
    assert status(eng, "screen_sync")["last_triggered"] is None
    waits = [r for r in caplog.records if "waits for its device" in r.getMessage()]
    assert len(waits) == 1 and waits[0].levelno == logging.INFO  # said once, not every run
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    connect(drv, node)
    await settle()
    assert status(eng, "screen_sync")["waiting_reason"] is None
    assert "waiting_reason" not in hub.states.get("automation.screen_sync").attributes
    assert len([r for r in caplog.records if "runs resume" in r.getMessage()]) == 1
    await advance(eng, 60)
    assert node.executed == [("set_weather", {"summary": "sunny"})]
    assert len(notes) == 1

    disconnect(drv)
    await settle()
    assert status(eng, "screen_sync")["waiting_reason"] == f"needs {ACTION}, which no connected device offers"
    await advance(eng, 3 * 60)
    assert len(node.executed) == 1 and len(notes) == 1
    assert len([r for r in caplog.records if "waits for its device" in r.getMessage()]) == 2
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_a_reload_does_not_announce_the_wait_again(setup, caplog) -> None:
    _hub, _drv, _node, eng = setup
    caplog.set_level(logging.INFO, logger="homehub.automation")
    await eng.load([SYNC], {}, [])
    await eng.load([SYNC], {}, [])
    assert status(eng, "screen_sync")["waiting_reason"]
    assert len([r for r in caplog.records if "waits for its device" in r.getMessage()]) == 1


async def test_a_script_calling_a_missing_device_action_waits_too(setup) -> None:
    hub, drv, node, eng = setup
    await eng.load([], {"push_weather": {"sequence": [{"action": ACTION, "data": {"summary": "rain"}}]}}, [])
    await eng.start()
    assert status(eng, "push_weather")["waiting_reason"]
    assert await hub.call_service("script", "push_weather") == {"changed_states": [], "response": None}
    connect(drv, node)
    await hub.call_service("script", "push_weather")
    await settle()
    assert node.executed == [("set_weather", {"summary": "rain"})]


async def test_a_step_allowed_to_fail_does_not_hold_the_item_back(setup) -> None:
    hub, _drv, _node, eng = setup
    notes = record(hub, "test.note")
    step = {"action": ACTION, "continue_on_error": True}
    await eng.load([auto(EVERY_MINUTE, [step, {"action": "test.note"}], id="lenient")], {}, [])
    await eng.start()
    assert status(eng, "lenient")["waiting_reason"] is None
    await advance(eng, 60)
    assert len(notes) == 1


async def test_an_unknown_core_service_still_fails_visibly(setup, caplog) -> None:
    _hub, _drv, _node, eng = setup
    await eng.load([auto(EVERY_MINUTE, [{"action": "light.turn_onn"}], id="typo"),
                    auto(EVERY_MINUTE, [{"action": "nosuchdomain.do_it"}], id="unknown")], {}, [])
    await eng.start()
    for item_id in ("typo", "unknown"):
        assert status(eng, item_id)["waiting_reason"] is None
    await advance(eng, 60)
    assert "needs a target entity" in status(eng, "typo")["last_error"]
    assert "nosuchdomain.do_it not found" in status(eng, "unknown")["last_error"]
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "failed" in r.getMessage()]


def test_device_actions_are_declared_by_the_drivers_that_register_them() -> None:
    hub, _entry = make_hub()
    assert hub.is_device_action("esphome", "wall_screen_set_weather")
    assert hub.is_device_action("notify", "mobile_app_some_phone")
    assert not hub.is_device_action("notify", "notify")
    assert not hub.is_device_action("light", "turn_on")
    assert not hub.is_device_action("esphome", "")


def test_the_hub_announces_services_coming_and_going() -> None:
    hub, _entry = make_hub()
    seen: list[tuple[str, dict]] = []
    hub.bus.subscribe(lambda t, d: seen.append((t, d)), "service_registered")
    hub.bus.subscribe(lambda t, d: seen.append((t, d)), "service_removed")

    async def handler(_hub: Any, _call: Any) -> None:
        return None

    hub.register_service("esphome", "x_y", handler)
    hub.register_service("esphome", "x_y", handler)  # re-registering is not news
    hub.unregister_service("esphome", "x_y")
    hub.unregister_service("esphome", "x_y")
    assert seen == [("service_registered", {"domain": "esphome", "service": "x_y"}),
                    ("service_removed", {"domain": "esphome", "service": "x_y"})]
    assert ("esphome", "x_y") not in hub.service_schemas
