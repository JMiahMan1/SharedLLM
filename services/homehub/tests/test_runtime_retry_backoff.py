"""A setup that cannot reach its device backs off instead of retrying every minute.

Every name and address here is made up.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from cryptography.fernet import Fernet

from services.homehub import entries
from services.homehub import runtime as rt
from services.homehub.core import ConfigEntry, Driver, Hub
from services.homehub.runtime import Runtime
from services.homehub.store import Store


class Clock:
    """A wall clock a test can move, standing in for ``utcnow``."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return datetime.fromtimestamp(self.t, UTC)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _runtime(tmp_path, monkeypatch, cls, domain: str = "esphome"):
    monkeypatch.setattr(rt, "driver_class", lambda _domain: cls)
    hub = Hub()
    hub.config_entries["x"] = ConfigEntry("x", domain, title="Shed sensor")
    return hub, Runtime(hub, Store(str(tmp_path / "h.db"), Fernet.generate_key().decode()))


def _spy_retries(monkeypatch) -> list[tuple[int, float]]:
    """Record each computed retry and make it fast, so the loop runs in the test."""
    attempts: list[tuple[int, float]] = []
    real = rt.retry_delay

    def spy(attempt: int) -> float:
        delay = real(attempt)
        attempts.append((attempt, delay))
        return 0.001

    monkeypatch.setattr(rt, "retry_delay", spy)
    return attempts


async def _wait_for(predicate, tries: int = 300) -> None:
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.005)


def test_the_retry_delay_starts_fast_then_doubles_to_the_cap():
    assert [rt.retry_delay(n) for n in range(9)] == [5.0, 10.0, 30.0, 60.0, 120.0, 240.0, 480.0, 900.0, 900.0]


async def test_a_failing_entry_backs_off_quietly_then_says_it_is_back(tmp_path, monkeypatch, caplog):
    clock = Clock()
    monkeypatch.setattr(rt, "utcnow", clock)
    monkeypatch.setattr(rt, "RETRY_REPORT_EVERY_S", 0.0)
    attempts = _spy_retries(monkeypatch)

    class Flaky(Driver):
        poll_interval = None
        calls = 0
        hook = None

        async def async_setup(self):
            cls = type(self)
            cls.calls += 1
            if cls.hook is not None:
                cls.hook(cls.calls)
            if cls.calls <= 5:
                raise OSError("Connect call failed: not found by mDNS")

    def tick(call: int) -> None:
        clock.advance(65)

    Flaky.hook = staticmethod(tick)
    hub, runtime = _runtime(tmp_path, monkeypatch, Flaky)
    entry = hub.config_entries["x"]

    with caplog.at_level(logging.DEBUG, logger="homehub.runtime"):
        await runtime.start_all()
        await runtime.wait_first_setups(2)
        await _wait_for(lambda: entry.state == "loaded")
        assert entries.summary(runtime, entry)["retry_at"] is None
        await runtime.shutdown()

    assert [attempt for attempt, _ in attempts] == [0, 1, 2, 3, 4], "it waited longer each time"
    assert entry.state == "loaded" and entry.reason is None
    assert runtime.next_retry == {} and "x" not in runtime.failing_since

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "not found by mDNS" in warnings[0]
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert sum("still unreachable for" in m and "next try in" in m for m in infos) == 4
    assert any("still unreachable for 4 minutes" in m for m in infos), infos
    assert any("is back after 5 minutes" in m for m in infos), infos


async def test_a_reload_starts_the_backoff_over(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "utcnow", Clock())
    attempts = _spy_retries(monkeypatch)

    class AlwaysDown(Driver):
        poll_interval = None

        async def async_setup(self):
            raise OSError("Connect call failed: not found by mDNS")

    hub, runtime = _runtime(tmp_path, monkeypatch, AlwaysDown)
    entry = hub.config_entries["x"]
    await runtime.start_all()
    await runtime.wait_first_setups(2)
    await _wait_for(lambda: len(attempts) >= 3)
    assert [attempt for attempt, _ in attempts[:3]] == [0, 1, 2]
    before = len(attempts)

    await runtime.stop_entry("x")
    runtime.start_entry(entry)
    assert "x" not in runtime.next_retry, "a reload forgets the backoff"
    await _wait_for(lambda: len(attempts) > before)

    assert attempts[before][0] == 0, "the first retry after a reload is the fast one again"
    await runtime.shutdown()


async def test_a_new_failure_reason_warns_again(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(rt, "utcnow", Clock())
    _spy_retries(monkeypatch)

    class TwoReasons(Driver):
        poll_interval = None
        calls = 0
        errors = (OSError("Connect call failed: not found by mDNS"), TimeoutError("timed out"))

        async def async_setup(self):
            cls = type(self)
            cls.calls += 1
            raise cls.errors[min(cls.calls - 1, len(cls.errors) - 1)]

    _hub, runtime = _runtime(tmp_path, monkeypatch, TwoReasons)
    with caplog.at_level(logging.DEBUG, logger="homehub.runtime"):
        await runtime.start_all()
        await runtime.wait_first_setups(2)
        await asyncio.sleep(0.05)
        await runtime.shutdown()

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, warnings
    assert "not found by mDNS" in warnings[0] and "timed out" in warnings[1]


def test_the_entry_view_reports_the_next_retry(tmp_path, monkeypatch):
    hub, runtime = _runtime(tmp_path, monkeypatch, Driver)
    entry = hub.config_entries["x"]
    when = datetime.now(UTC)
    runtime.next_retry["x"] = when

    view = entries.summary(runtime, entry)

    assert view["retry_at"] == when.isoformat()
    assert view["state"] == "not_loaded", "the state the UI shows is unchanged"
    runtime.next_retry.clear()
    assert entries.summary(runtime, entry)["retry_at"] is None
