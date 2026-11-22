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


# ------------------------------------------------ readiness: answered is not ready


def _ready(ok: bool, answered: bool = True, reason: str = ""):
    from services.common.backend_health import ProbeResult

    return ProbeResult(ok, answered=answered, reason=reason)


class Probe:
    """A scripted probe: each call pops the next verdict, and counts the calls."""

    def __init__(self, *verdicts):
        self.verdicts = list(verdicts)
        self.calls = 0

    def __call__(self, url, timeout):
        self.calls += 1
        return self.verdicts.pop(0)


def test_a_proxy_that_answers_but_cannot_serve_does_not_close_it():
    # The GPU box rebooted without its GPU: the proxy answers, llama-server is gone.
    probe = Probe(_ready(False, reason="found the proxy up but its model server unreachable"))
    health = _health(probe=probe)
    health.mark_down("refused")
    assert health.probe_once() is False
    assert health.state == DOWN


def test_a_ready_answer_closes_it():
    health = _health(probe=Probe(_ready(True, reason="found the model server ready")))
    health.mark_down("refused")
    assert health.probe_once() is True
    assert health.state == UP


def test_a_slow_answer_while_a_model_loads_never_opens_it():
    # The failed call is suspect; the probe finds the server there (loading): stays up.
    probe = Probe(_ready(True, reason="found the model server answering (HTTP 503)"))
    health = _health(probe=probe)
    assert health.record_failure(aiohttp.SocketTimeoutError("Timeout on reading data from socket")) is False
    assert health.state == UP
    assert health.seconds_to_next_probe() == 0.0  # a confirming probe is due now
    health.probe_once()
    assert health.state == UP and probe.calls == 1
    assert health.seconds_to_next_probe() is None  # nothing more to check


def test_a_failed_call_then_the_proxy_saying_its_server_is_gone_opens_it():
    probe = Probe(_ready(False, reason="found the proxy up but its model server unreachable (HTTP 502)"))
    health = _health(probe=probe)
    health.record_failure(RuntimeError("Ollama HTTP 503: No llama-server slots available within timeout"))
    assert health.state == UP  # the failed call alone proves nothing
    health.probe_once()
    assert health.state == DOWN
    assert "model server unreachable" in health.detail
    with pytest.raises(BackendUnavailable):
        health.check()


def test_silence_after_a_failed_call_needs_a_second_probe_spaced_out():
    clock = Clock()
    probe = Probe(_ready(False, answered=False), _ready(False, answered=False))
    health = _health(probe=probe, clock=clock, verify_interval=10.0)
    health.record_failure(TimeoutError())
    health.probe_once()
    assert health.state == UP  # no answer once can be a busy moment
    assert health.seconds_to_next_probe() == 10.0
    # More failed calls meanwhile do not pull the second probe forward.
    health.record_failure(TimeoutError())
    assert health.seconds_to_next_probe() == 10.0
    clock.now += 10
    health.probe_once()
    assert health.state == DOWN


def test_a_watch_probe_finding_it_not_ready_needs_two_in_a_row():
    # No failed call: the proxy's "not ready" during a quick restart must not open it alone.
    probe = Probe(_ready(False), _ready(True), _ready(False), _ready(False))
    health = _health(probe=probe)
    health.probe_once()
    assert health.state == UP
    health.probe_once()
    health.probe_once()
    assert health.state == UP
    health.probe_once()
    assert health.state == DOWN


def test_confirm_probes_inline_and_says_whether_to_go_ahead():
    health = _health(probe=Probe(_ready(True)))
    assert health.confirm("TimeoutError reading /api/ps") is True
    dead = _health(probe=Probe(_ready(False, reason="found the proxy up but its model server unreachable")))
    assert dead.confirm("TimeoutError reading /api/ps") is False
    assert dead.state == DOWN
    assert dead.confirm("again") is False  # down already: no second probe


@pytest.mark.parametrize("exc", [
    TimeoutError(),
    aiohttp.SocketTimeoutError("Timeout on reading data from socket"),
    aiohttp.ServerDisconnectedError(),
    RuntimeError("Ollama HTTP 503: model is loading"),
    RuntimeError("No slots available within 120.0s"),
])
def test_inconclusive_failures_are_suspect(exc):
    from services.common.backend_health import is_suspect

    assert is_suspect(exc)


@pytest.mark.parametrize("exc", [ValueError("bad json"), RuntimeError("Ollama HTTP 400: bad request")])
def test_a_plain_error_is_not_suspect(exc):
    from services.common.backend_health import is_suspect

    health = _health(probe=Probe())
    assert not is_suspect(exc)
    health.record_failure(exc)
    assert health.seconds_to_next_probe() is None


def test_http_probe_hands_every_answer_to_the_judge_and_silence_is_not_ready():
    import http.server
    import threading

    from services.common.backend_health import ProbeResult, http_probe

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code = 200 if self.path == "/ok" else 502
            body = b'{"detail": "Failed to fetch props: All connection attempts failed"}'
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            seen.append(self.headers.get("X-API-Key"))

        def log_message(self, *a):
            pass

    seen: list[str | None] = []
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        probe = http_probe(lambda status, body: ProbeResult(status == 200, reason=f"{status} {body[:30]!r}"),
                           headers={"X-API-Key": "k"})
        assert probe(f"{base}/ok", 2).ok is True
        bad = probe(f"{base}/admin/props", 2)
        assert bad.ok is False and bad.answered is True and "502" in bad.reason
        assert seen == ["k", "k"]
    finally:
        server.shutdown()
        server.server_close()
    silent = probe(base, 0.5)  # nothing listens there any more
    assert silent.ok is False and silent.answered is False
