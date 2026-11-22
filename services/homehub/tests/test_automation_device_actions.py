"""A step that calls a device's own action while its device is away fails at that step, visibly.

ESPHome nodes bring their user-defined actions with them (``esphome.<node>_<action>``) and take them away when
they disconnect; a phone brings ``notify.mobile_app_<phone>``. A missing one is checked when a run reaches the
step, like Home Assistant: the steps before it ran, the run stops there with an error in its trace and a WARNING,
a repair notice stays until the device is back, and ``continue_on_error`` lets the rest run. An action named
only in a branch that is not taken costs nothing: the run is never skipped as a whole.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from aioesphomeapi import UserService, UserServiceArg, UserServiceArgType

from services.homehub.automation import DEVICE_REPAIR_PREFIX, AutomationEngine, FakeClock
from services.homehub.automation_editor import Tracer
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_automation import START, FakeStore, advance, auto, record, settle

SET_WEATHER = UserService(name="set_weather", key=7, args=[UserServiceArg(name="summary",
                                                                          type=UserServiceArgType.STRING)])
ACTION = "esphome.wall_screen_set_weather"
OFFLINE = f"{ACTION} is not offered: Wall Screen is offline"
ONLINE_NO_SERVICE = (f"{ACTION} is not offered: Wall Screen is online but its firmware does not "
                     "offer wall_screen_set_weather")
EVERY_MINUTE = [{"trigger": "time_pattern", "minutes": "/1"}]
SYNC = auto(EVERY_MINUTE, [{"action": "test.before"}, {"action": ACTION, "data": {"summary": "sunny"}},
                           {"action": "test.after"}], id="screen_sync", alias="Screen sync")
REPAIR = f"persistent_notification.{DEVICE_REPAIR_PREFIX}automation_screen_sync"


class FakeNode:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    async def execute_service(self, service: Any, data: dict, *, return_response: Any = None) -> None:
        self.executed.append((service.name, data))


@pytest.fixture
async def setup():
    hub, entry = make_hub("esphome", {"host": "192.0.2.10", "port": 6053, "password": "",
                                      "device_name": "wall-screen"})
    entry.title = "Wall Screen"
    drv = ESPHomeDriver(hub, entry)
    hub.drivers[entry.entry_id] = drv
    node = FakeNode()
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    eng.tracer = Tracer(eng)
    yield hub, drv, node, eng
    await eng.stop()


def connect(drv: ESPHomeDriver, node: FakeNode) -> None:
    drv._client = node
    drv._register_services("wall-screen", [SET_WEATHER])


def status(eng: AutomationEngine, item_id: str) -> dict:
    return next(s for s in eng.status() if s["id"] == item_id)


def last_trace(eng: AutomationEngine, kind: str, item_id: str) -> dict:
    return eng.tracer.list(kind, item_id)[0]


async def test_a_run_reaching_a_missing_device_action_fails_there_visibly(setup, caplog) -> None:
    hub, drv, node, eng = setup
    calls = record(hub, "test.before", "test.after")
    caplog.set_level(logging.INFO, logger="homehub.automation")
    await eng.load([SYNC], {}, [])
    await eng.start()
    await settle()
    assert status(eng, "screen_sync")["waiting_reason"].startswith(f"needs {ACTION}, which no connected device")

    await advance(eng, 60)
    # The run started (never skipped), the step before ran, the run stopped at the device's action.
    assert [c[0] for c in calls] == ["test.before"]
    st = status(eng, "screen_sync")
    assert st["last_triggered"] is not None
    assert st["last_error"] == OFFLINE
    trace = last_trace(eng, "automation", "screen_sync")
    assert trace["result"] == "error" and trace["steps"][-1]["error"] == OFFLINE
    assert [r for r in caplog.records if r.levelno == logging.WARNING and ACTION in r.getMessage()]
    repair = hub.states.get(REPAIR)
    assert repair is not None and repair.attributes["action"] == ACTION
    assert repair.attributes["entity_id"] == "automation.screen_sync"

    # The device comes back: the repair notice goes, and the next run goes through.
    connect(drv, node)
    await settle()
    assert hub.states.get(REPAIR) is None
    assert status(eng, "screen_sync")["waiting_reason"] is None
    await advance(eng, 60)
    assert node.executed == [("set_weather", {"summary": "sunny"})]
    assert [c[0] for c in calls] == ["test.before", "test.before", "test.after"]
    assert status(eng, "screen_sync")["last_error"] is None


async def test_an_online_device_whose_firmware_lacks_the_action_says_so(setup, caplog) -> None:
    hub, drv, node, eng = setup
    record(hub, "test.before", "test.after")
    caplog.set_level(logging.INFO, logger="homehub.automation")
    # The node answers, but this firmware has no user-defined actions at all.
    drv._client = node
    drv._register_services("wall-screen", [])
    await eng.load([SYNC], {}, [])
    await eng.start()
    await advance(eng, 60)
    assert status(eng, "screen_sync")["last_error"] == ONLINE_NO_SERVICE
    assert last_trace(eng, "automation", "screen_sync")["steps"][-1]["error"] == ONLINE_NO_SERVICE
    assert [r for r in caplog.records if r.levelno == logging.WARNING and ACTION in r.getMessage()]
    repair = hub.states.get(REPAIR)
    assert repair is not None and "is online but its firmware does not offer wall_screen_set_weather" in repair.attributes["message"]


async def test_an_action_in_a_branch_not_taken_never_skips_the_run(setup, caplog) -> None:
    hub, _drv, _node, eng = setup
    calls = record(hub, "test.note")
    caplog.set_level(logging.INFO, logger="homehub.automation")
    branchy = auto(EVERY_MINUTE, [
        {"action": "test.note"},
        {"if": [{"condition": "template", "value_template": "{{ false }}"}],
         "then": [{"action": ACTION}, {"action": "notify.mobile_app_spare_phone", "data": {"message": "x"}}]},
        {"action": "test.note"},
    ], id="branchy")
    await eng.load([branchy], {}, [])
    await eng.start()
    await advance(eng, 3 * 60)
    assert len(calls) == 6
    assert status(eng, "branchy")["last_error"] is None
    assert hub.states.get(f"persistent_notification.{DEVICE_REPAIR_PREFIX}automation_branchy") is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_a_step_allowed_to_fail_records_the_error_and_the_rest_runs(setup, caplog) -> None:
    hub, _drv, _node, eng = setup
    calls = record(hub, "test.note")
    caplog.set_level(logging.INFO, logger="homehub.automation")
    step = {"action": ACTION, "continue_on_error": True}
    await eng.load([auto(EVERY_MINUTE, [step, {"action": "test.note"}], id="lenient")], {}, [])
    await eng.start()
    await advance(eng, 60)
    assert len(calls) == 1
    assert last_trace(eng, "automation", "lenient")["steps"][0]["error"] == OFFLINE
    assert [r for r in caplog.records if r.levelno == logging.WARNING and ACTION in r.getMessage()]
    assert hub.states.get(f"persistent_notification.{DEVICE_REPAIR_PREFIX}automation_lenient") is not None


async def test_a_script_calling_a_missing_device_action_fails_and_says_why(setup) -> None:
    hub, drv, node, eng = setup
    await eng.load([], {"push_weather": {"sequence": [{"action": ACTION, "data": {"summary": "rain"}}]}}, [])
    await eng.start()
    await hub.call_service("script", "push_weather")
    await settle()
    assert status(eng, "push_weather")["last_error"] == OFFLINE
    connect(drv, node)
    await hub.call_service("script", "push_weather")
    await settle()
    assert node.executed == [("set_weather", {"summary": "rain"})]


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
