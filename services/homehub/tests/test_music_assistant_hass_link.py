"""music_assistant: reloading MA's failed Home Assistant link (its ``hass`` provider) after a HomeHub restart."""
from __future__ import annotations

import logging

from services.homehub.drivers.music_assistant_hass_link import (
    MIN_RELOAD_GAP_S,
    PASS_INTERVAL_S,
    STARTUP_WINDOW_S,
    HassLinkWatch,
)
from services.homehub.tests.test_drivers_media_music_assistant import FakeConn, build

HASS = "hass--abc123"
FAILED = {"error_code": 5, "message": "Setup failed. Please check the configuration and try again."}


def hass_cfg(instance_id=HASS, last_error=None, **kw):
    return {"type": "plugin", "domain": "hass", "instance_id": instance_id, "enabled": True,
            "name": "Home Assistant", "last_error": last_error, **kw}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeMA:
    """The two commands the watch sends, with a provider listing and a reload that may fail."""

    def __init__(self, configs, loaded=None):
        self.configs = configs
        self.loaded = loaded
        self.sent: list[tuple[str, dict]] = []
        self.reload_error: Exception | None = None

    async def __call__(self, command, **args):
        self.sent.append((command, args))
        if command == "config/providers":
            return self.configs
        if command == "providers":
            return self.loaded
        if command == "config/providers/reload":
            if self.reload_error is not None:
                raise self.reload_error
            for c in self.configs:
                if c["instance_id"] == args["instance_id"]:
                    c["last_error"] = None
            return None
        raise AssertionError(command)

    def reloads(self):
        return [a["instance_id"] for c, a in self.sent if c == "config/providers/reload"]


def watch(ma, clock):
    return HassLinkWatch(ma, logging.getLogger("test.hass_link"), clock=clock)


async def test_a_failed_hass_provider_is_reloaded_once(caplog):
    ma, clock = FakeMA([hass_cfg(last_error=FAILED), hass_cfg("hass--other")]), Clock()
    w = watch(ma, clock)
    with caplog.at_level(logging.INFO, logger="test.hass_link"):
        assert await w.check() == [HASS]
    assert ma.reloads() == [HASS]
    assert "Music Assistant's Home Assistant link was down (Setup failed. Please check the configuration and try " \
           "again.); reloaded it" in caplog.text
    clock.t += PASS_INTERVAL_S
    assert await w.check() == []
    assert ma.reloads() == [HASS]


async def test_a_healthy_hass_provider_is_never_reloaded():
    ma = FakeMA([hass_cfg(), {"domain": "spotify", "instance_id": "spotify--1", "last_error": "x"}],
                loaded=[{"instance_id": HASS, "domain": "hass", "available": True}])
    clock = Clock()
    w = watch(ma, clock)
    for _ in range(5):
        await w.check()
        clock.t += MIN_RELOAD_GAP_S
    assert ma.reloads() == []


async def test_a_hass_provider_that_is_not_loaded_or_not_available_is_reloaded():
    ma = FakeMA([hass_cfg(), hass_cfg("hass--two")],
                loaded=[{"instance_id": "hass--two", "domain": "hass", "available": False},
                        {"instance_id": "spotify--1", "domain": "spotify", "available": True}])
    assert await watch(ma, Clock()).check() == [HASS, "hass--two"]


async def test_a_disabled_hass_provider_is_left_alone():
    ma = FakeMA([hass_cfg(last_error=FAILED, enabled=False)])
    assert await watch(ma, Clock()).check() == []


async def test_a_failed_reload_is_retried_on_the_next_pass_but_not_within_a_minute(caplog):
    ma, clock = FakeMA([hass_cfg(last_error=FAILED)]), Clock()
    ma.reload_error = RuntimeError("MA busy")
    w = watch(ma, clock)
    with caplog.at_level(logging.WARNING, logger="test.hass_link"):
        assert await w.check() == []
    assert "reload failed: MA busy" in caplog.text
    clock.t += MIN_RELOAD_GAP_S / 2
    await w.check()
    assert ma.reloads() == [HASS]
    clock.t += MIN_RELOAD_GAP_S / 2
    ma.reload_error = None
    assert await w.check() == [HASS]
    assert ma.reloads() == [HASS, HASS]


async def test_a_failed_providers_read_is_a_warning_not_an_error(caplog):
    async def broken(command, **args):
        raise RuntimeError("not connected")
    with caplog.at_level(logging.WARNING, logger="test.hass_link"):
        assert await watch(broken, Clock()).check() == []
    assert "Could not read Music Assistant's providers" in caplog.text


async def test_passes_run_once_a_minute_inside_the_startup_window_then_only_on_reconnect():
    ma, clock = FakeMA([hass_cfg(last_error=FAILED)]), Clock()
    w = watch(ma, clock)
    w.kick()
    await w.task
    assert ma.reloads() == [HASS]
    ma.configs[0]["last_error"] = FAILED
    clock.t += PASS_INTERVAL_S / 2
    w.kick()
    assert w.task.done() and ma.reloads() == [HASS]
    clock.t += PASS_INTERVAL_S / 2
    w.kick()
    await w.task
    assert ma.reloads() == [HASS, HASS]
    clock.t = w.started_at + STARTUP_WINDOW_S + 1
    ma.configs[0]["last_error"] = FAILED
    count = len(ma.sent)
    w.kick()
    await w.stop()
    assert len(ma.sent) == count
    w.kick(reconnected=True)
    await w.task
    assert ma.reloads() == [HASS, HASS, HASS]


# -- wired into the driver ------------------------------------------------------------------------


def hass_build(configs):
    hub, drv, made = build()

    def factory(*a):
        c = FakeConn(*a)
        c.replies["config/providers"] = configs
        c.replies["providers"] = None
        made["c"] = c
        return c
    drv.connection_factory = factory
    return hub, drv, made


def reloads(conn):
    return [a["instance_id"] for c, a in conn.sent if c == "config/providers/reload"]


async def test_the_driver_checks_on_connect_and_on_reconnect_and_stops_on_unload():
    configs = [hass_cfg(last_error=FAILED)]
    _hub, drv, made = hass_build(configs)
    clock = Clock()
    drv.hass_link.clock = clock
    drv.hass_link.started_at = clock.t
    await drv.async_setup()
    await drv.hass_link.task
    assert reloads(made["c"]) == [HASS]

    # outside the startup window a poll does nothing, but a reconnect checks again
    clock.t += STARTUP_WINDOW_S + MIN_RELOAD_GAP_S
    await drv.async_poll()
    assert drv.hass_link.task.done()
    assert reloads(made["c"]) == [HASS]
    first = made["c"]
    first.connected = False
    await drv.async_poll()
    await drv.hass_link.task
    assert made["c"] is not first
    assert reloads(first) == [HASS]
    assert reloads(made["c"]) == [HASS]

    await drv.async_unload()
    assert drv.hass_link.task is None


async def test_a_healthy_link_is_not_touched_by_the_driver():
    _hub, drv, made = hass_build([hass_cfg()])
    await drv.async_setup()
    await drv.hass_link.task
    assert ("config/providers", {}) in made["c"].sent
    assert reloads(made["c"]) == []
    await drv.async_unload()
