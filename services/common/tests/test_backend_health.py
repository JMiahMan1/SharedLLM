"""The GPU box breaker: opens on a connect failure, fails fast, probes with backoff,
closes on the first answer, and says each of those once in the log."""
from __future__ import annotations

import errno
import logging

import aiohttp
import pytest

from services.common.backend_health import (
    DOWN,
    UP,
    BackendHealth,
    BackendUnavailable,
    Settings,
    is_unreachable,
)

FAKE_URL = "http://gpu-box.test:11434"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _connector_error() -> aiohttp.ClientConnectorError:
    key = aiohttp.client_reqrep.ConnectionKey("gpu-box.test", 11434, False, True, None, None, None)
    return aiohttp.ClientConnectorError(key, OSError(errno.EHOSTUNREACH, "No route to host"))


def _health(probe=None, clock=None, **settings) -> BackendHealth:
    base = {"probe_interval": 15.0, "probe_max": 30.0, "watch_interval": 0.0, "summary_interval": 600.0}
    return BackendHealth(
        "local_ai", "The thinking brain", url=FAKE_URL, message="It is offline.",
        settings=Settings(**{**base, **settings}), probe=probe or (lambda url, t: False),
        clock=clock or Clock(), autostart=False,
    )


@pytest.mark.parametrize("exc", [
    _connector_error(),
    ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
    OSError(errno.EHOSTUNREACH, "No route to host"),
    aiohttp.ConnectionTimeoutError("Connection timeout to host http://gpu-box.test:11434"),
    RuntimeError("Cannot connect to host gpu-box.test:11434 ssl:default [Connect call failed]"),
])
def test_connect_failures_count_as_unreachable(exc):
    assert is_unreachable(exc)


@pytest.mark.parametrize("exc", [
    aiohttp.SocketTimeoutError("Timeout on reading data from socket"),  # mid-generation
    TimeoutError(),
    RuntimeError("Ollama HTTP 503: model is loading"),
    aiohttp.ServerDisconnectedError(),
    ValueError("bad json"),
])
def test_transient_errors_do_not_open_the_breaker(exc):
    health = _health()
    assert not is_unreachable(exc)
    assert health.record_failure(exc) is False
    assert health.state == UP
    health.check()  # still lets calls through


def test_a_wrapped_connect_error_is_found_through_the_cause_chain():
    try:
        try:
            raise _connector_error()
        except aiohttp.ClientError as inner:
            raise RuntimeError("inference failed") from inner
    except RuntimeError as outer:
        assert is_unreachable(outer)


def test_a_connect_failure_opens_it_and_later_calls_fail_fast():
    health = _health()
    assert health.record_failure(_connector_error()) is True
    assert health.state == DOWN and health.error_id
    for _ in range(3):
        with pytest.raises(BackendUnavailable) as raised:
            health.check()
        assert raised.value.error_id == health.error_id
        assert raised.value.message == "It is offline."
        assert "gpu-box" not in str(raised.value)
    assert health.refused == 3


def test_probes_back_off_to_the_cap_and_a_success_closes_it():
    clock = Clock()
    answers: list[bool] = [False, False, False, True]
    calls: list[str] = []

    def probe(url, timeout):
        calls.append(url)
        return answers.pop(0)

    health = _health(probe=probe, clock=clock)
    health.mark_down("connect refused")
    delays = [health.next_probe_delay()]
    for _ in range(3):
        assert health.probe_once() is False
        delays.append(health.next_probe_delay())
    assert delays[:3] == [15.0, 30.0, 30.0]  # 15, then doubled, capped at 30
    assert health.probe_once() is True
    assert health.state == UP
    health.check()
    assert calls[0] == f"{FAKE_URL}/"


def test_a_successful_real_call_closes_it_too():
    health = _health()
    health.mark_down("refused")
    health.record_success()
    assert health.state == UP


def test_no_url_means_nothing_to_probe():
    health = BackendHealth("x", "X", settings=Settings(), probe=lambda u, t: pytest.fail("probed"), autostart=False)
    assert health.probe_once() is True


def test_the_log_says_it_once_down_once_up_and_a_summary_in_between(caplog):
    clock = Clock()
    health = _health(clock=clock, summary_interval=60.0)
    caplog.set_level(logging.DEBUG, logger="backend_health")
    health.record_failure(_connector_error())
    for _ in range(50):  # a busy household polling while it is down
        with pytest.raises(BackendUnavailable):
            health.check()
        health.record_failure(_connector_error())
    clock.now += 61
    with pytest.raises(BackendUnavailable):
        health.check()
    health.mark_up("probe answered")

    loud = [r for r in caplog.records if r.levelno >= logging.WARNING]
    errors = [r for r in loud if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "unreachable" in errors[0].getMessage()
    summaries = [r for r in loud if "still unreachable" in r.getMessage()]
    assert len(summaries) == 1
    backs = [r for r in loud if "reachable again" in r.getMessage()]
    assert len(backs) == 1
    assert len(loud) == 3


def test_the_snapshot_names_no_host_or_error_text():
    health = _health()
    health.mark_down("ClientConnectorError: Cannot connect to host gpu-box.test:11434 [10.9.9.9]")
    snap = health.snapshot()
    assert snap["state"] == DOWN and snap["message"] == "It is offline."
    flat = repr(snap)
    assert "gpu-box" not in flat and "10.9.9.9" not in flat and "Cannot connect" not in flat


def test_settings_refuse_a_bad_value(monkeypatch):
    monkeypatch.setenv("LOCAL_AI_PROBE_INTERVAL_S", "soon")
    with pytest.raises(RuntimeError, match="LOCAL_AI_PROBE_INTERVAL_S"):
        Settings.from_env()
    monkeypatch.setenv("LOCAL_AI_PROBE_INTERVAL_S", "20")
    monkeypatch.setenv("LOCAL_AI_PROBE_MAX_S", "90")
    s = Settings.from_env()
    assert (s.probe_interval, s.probe_max) == (20.0, 90.0)


def test_the_background_thread_probes_and_closes_it():
    import threading

    answered = threading.Event()

    def probe(url, timeout):
        answered.set()
        return True

    health = _health(probe=probe, probe_interval=0.05, probe_max=0.05)
    health._autostart = True
    try:
        health.mark_down("refused")
        assert answered.wait(5)
        for _ in range(100):
            if health.state == UP:
                break
            answered.wait(0.02)
        assert health.state == UP
    finally:
        health.stop()


def test_a_watch_probe_needs_two_misses_in_a_row_to_mark_it_down():
    answers = [False, True, False, False]
    health = _health(probe=lambda url, t: answers.pop(0))
    health.probe_once()
    assert health.state == UP  # one miss is a busy moment
    health.probe_once()
    health.probe_once()
    assert health.state == UP  # the answer in between reset the count
    health.probe_once()
    assert health.state == DOWN


def test_probe_max_is_at_least_probe_interval(monkeypatch):
    monkeypatch.setenv("LOCAL_AI_PROBE_INTERVAL_S", "45")
    monkeypatch.delenv("LOCAL_AI_PROBE_MAX_S", raising=False)
    s = Settings.from_env()
    assert s.probe_interval == 45.0
    assert s.probe_max == 45.0
