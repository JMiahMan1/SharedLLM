"""Whether a backend on another machine can be reached, and failing fast while it cannot.

The household's local AI (the alpaca proxy), its voice server, its image
server and its arcade all run on one GPU box that the main server reaches over
the LAN. When that box is off, every call to it used to wait out its own
connect timeout and retry ladder: a chat question took 37 seconds to report a
socket error. ``BackendHealth`` is the shared answer to "is it up?":

* The first call that cannot *connect* (refused, no route, DNS failure, a
  connect timeout) marks the backend down. That is the only kind of failure
  that does: a read timeout mid-generation or a 503 while a model loads means
  the box is up, and the caller's own retries still apply to those.
* While it is down, ``check()`` raises ``BackendUnavailable`` at once, so a
  request fails in well under a second instead of timing out again.
* A daemon thread probes it in the background, every ``probe_interval``
  seconds at first and backing off to ``probe_max``. A passing probe closes it
  again; the next request goes through as normal. What "passing" means is the
  backend's own business: by default any HTTP answer (the machine is up), but a
  backend that sits in front of another one passes a readiness probe
  (``http_probe``) so that a proxy answering while the server behind it is
  dead still counts as down. The thread is loop-agnostic on purpose: the
  gateway runs two event loops (the API and the Raven worker) in one process,
  and both read the same state.
* A failure that does not prove anything by itself (a timeout, a 502/503/504,
  a dropped connection: a model loading after a swap looks exactly like that)
  never opens it. It asks the probe instead (``verify_soon`` in the
  background, ``confirm`` inline). Two failures in a row open it: that call
  plus a probe the backend *answered* with "I cannot serve" (the proxy's own
  word that the server behind it is gone), or ``WATCH_MISSES`` failed probes
  ``verify_interval`` apart when nothing answers. A probe that passes clears
  it, so a slow first answer while a model loads stays a slow answer, and a
  dead server behind a live proxy is found in seconds instead of one long
  timeout per request.
* The log says it once: one error when it goes down (with an error id that the
  responses carry), one line when it comes back, and a summary every
  ``summary_interval`` seconds while it stays down. Each refused request is
  logged at DEBUG only.

The probe uses the standard library so the thread needs no event loop. Every
interval is a setting (environment variables named in ``Settings``); nothing
here knows a host name.
"""
from __future__ import annotations

import errno
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass

UP = "up"
DOWN = "down"
#: Probes (while up) that must fail in a row before it is marked down.
WATCH_MISSES = 2
#: How much of a probe's answer is read: a status route's JSON, never a page.
PROBE_READ_BYTES = 65536

_log = logging.getLogger("backend_health")

#: Every instance in this process, so tests (and a reconfiguration) can reset them all.
_ALL: list[BackendHealth] = []


def probes_enabled() -> bool:
    """Background probe threads run unless ``BACKEND_HEALTH_PROBES=0`` (the test suite)."""
    return os.getenv("BACKEND_HEALTH_PROBES", "1").strip() != "0"


def reset_all() -> None:
    """Stop every probe thread and mark every backend up again (tests)."""
    for health in list(_ALL):
        health.stop()
        health.reset()


def _env_seconds(name: str, default: float, lo: float, hi: float) -> float:
    """A duration setting read once; a malformed or out-of-range value stops the
    service with the variable named rather than running on a guess."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{name}={raw!r} is not a number of seconds") from None
    if not lo <= value <= hi:
        raise RuntimeError(f"{name}={value} is outside {lo}..{hi} seconds")
    return value


@dataclass(frozen=True)
class Settings:
    """The timing every backend shares, from the environment."""

    #: First probe after the backend goes down, in seconds.
    probe_interval: float = 15.0
    #: The backoff stops growing here.
    probe_max: float = 30.0
    #: How often a backend that is up is probed anyway (0 = only after a failure),
    #: so the offline banner and the admin alert do not wait for someone to ask.
    watch_interval: float = 60.0
    #: A "still down" summary line this often while it stays down.
    summary_interval: float = 600.0
    #: How long one probe may take.
    probe_timeout: float = 4.0
    #: Connect timeout for real calls to the backend: long enough for a busy
    #: LAN, short enough that a powered-off box is noticed on the first call.
    connect_timeout: float = 5.0
    #: After a failed probe while up, the next one comes this soon instead of a
    #: whole ``watch_interval`` later, so a dead backend is confirmed quickly and
    #: a server restart shorter than this never opens the breaker.
    verify_interval: float = 10.0

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            probe_interval=_env_seconds("LOCAL_AI_PROBE_INTERVAL_S", 15.0, 1.0, 3600.0),
            probe_max=_env_seconds("LOCAL_AI_PROBE_MAX_S", 30.0, 1.0, 3600.0),
            watch_interval=_env_seconds("LOCAL_AI_WATCH_INTERVAL_S", 60.0, 0.0, 86400.0),
            summary_interval=_env_seconds("LOCAL_AI_LOG_SUMMARY_S", 600.0, 10.0, 86400.0),
            probe_timeout=_env_seconds("LOCAL_AI_PROBE_TIMEOUT_S", 4.0, 0.5, 60.0),
            connect_timeout=_env_seconds("LOCAL_AI_CONNECT_TIMEOUT_S", 5.0, 0.5, 120.0),
            verify_interval=_env_seconds("LOCAL_AI_VERIFY_INTERVAL_S", 10.0, 1.0, 600.0),
        )


class BackendUnavailable(RuntimeError):
    """The backend is known to be unreachable, so the call was not attempted.

    ``message`` is the plain-language sentence for a person; ``error_id`` ties
    the response to the one server-side log line that has the detail.
    """

    def __init__(self, backend: str, label: str, error_id: str, message: str = ""):
        self.backend = backend
        self.label = label
        self.error_id = error_id
        self.message = message or f"{label} is offline right now."
        super().__init__(f"{backend} is unreachable (error id {error_id})")


_CONNECT_ERRNOS = frozenset(
    e for e in (
        getattr(errno, "ECONNREFUSED", None), getattr(errno, "EHOSTUNREACH", None),
        getattr(errno, "ENETUNREACH", None), getattr(errno, "EHOSTDOWN", None),
        getattr(errno, "ENETDOWN", None), getattr(errno, "ETIMEDOUT", None),
    ) if e is not None
)
#: What the error text says when a wrapper (RuntimeError(str(e))) lost the type.
_CONNECT_TEXT = (
    "cannot connect to host", "connect call failed", "connection refused", "no route to host",
    "network is unreachable", "host is down", "name or service not known",
    "temporary failure in name resolution", "nodename nor servname", "connection timeout to host",
)


def _causes(exc: BaseException | None):
    seen = 0
    while exc is not None and seen < 8:
        yield exc
        exc = exc.__cause__ or exc.__context__
        seen += 1


def is_unreachable(exc: BaseException | None) -> bool:
    """True when ``exc`` (or what caused it) means the machine could not be reached.

    Only connection-level failures count. A read timeout, a reset mid-stream or
    an HTTP error status means the machine answered, so it is a transient error
    for the caller's own retry, not a reason to stop calling.
    """
    for e in _causes(exc):
        if isinstance(e, BackendUnavailable | ConnectionRefusedError):
            return True
        name = type(e).__name__
        # aiohttp's connect failures, matched by name so this module needs no aiohttp:
        # ClientConnectorError (refused, no route, DNS) and ConnectionTimeoutError
        # (sock_connect / connect timeout, aiohttp >= 3.10).
        if name in ("ClientConnectorError", "ClientConnectorDNSError", "ConnectionTimeoutError"):
            return True
        if isinstance(e, OSError) and e.errno in _CONNECT_ERRNOS:
            return True
        if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
            return True
        text = str(e).lower()
        if any(t in text for t in _CONNECT_TEXT):
            return True
    return False


#: Failures that are not proof either way: the machine answered (or may have),
#: but the call still did not get through. Matched by name like ``is_unreachable``.
_SUSPECT_NAMES = frozenset({
    "TimeoutError", "ServerTimeoutError", "SocketTimeoutError", "ServerDisconnectedError",
    "ClientPayloadError", "ClientOSError", "RemoteProtocolError", "ReadTimeout",
})
_SUSPECT_TEXT = (
    "http 502", "http 503", "http 504", "bad gateway", "service unavailable", "gateway timeout",
    "no slots available", "queue_timeout", "timed out",
)


def is_suspect(exc: BaseException | None) -> bool:
    """True when ``exc`` could mean the server behind the machine is gone.

    A timeout, a 502/503/504 or a dropped connection proves nothing alone (a
    model loading after a swap gives the same), so it never opens the breaker;
    it is the reason to ask the backend's readiness probe right away.
    """
    for e in _causes(exc):
        if isinstance(e, TimeoutError) or type(e).__name__ in _SUSPECT_NAMES:
            return True
        text = str(e).lower()
        if any(t in text for t in _SUSPECT_TEXT):
            return True
    return False


@dataclass(frozen=True)
class ProbeResult:
    """One probe's verdict. Truthy when the backend can serve.

    ``answered`` says whether anything answered at all, so the log can tell
    "the machine is off" from "the machine is up but cannot serve"."""

    ok: bool
    answered: bool = True
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def http_probe(
    judge: Callable[[int, bytes], ProbeResult],
    headers: dict[str, str] | None = None,
) -> Callable[[str, float], ProbeResult]:
    """A probe that GETs a status route and lets ``judge(status, body)`` decide.

    No answer (refused, timeout, DNS) is never ready. Anything that answered,
    error statuses included, goes to ``judge``: it knows what the backend's
    own "I am up but cannot serve" looks like.
    """
    extra = dict(headers or {})

    def probe(url: str, timeout: float) -> ProbeResult:
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "sharedllm-health-probe", **extra})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, body = resp.status, resp.read(PROBE_READ_BYTES)
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                body = exc.read(PROBE_READ_BYTES) or b""
            except Exception:
                body = b""
        except Exception as exc:
            return ProbeResult(False, answered=False, reason=f"no answer ({type(exc).__name__})")
        return judge(status, body)

    return probe


def default_probe(url: str, timeout: float) -> bool:
    """True when ``url`` gave any HTTP answer: the question is whether the machine is up."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "sharedllm-health-probe"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status > 0
    except urllib.error.HTTPError:
        return True  # an error status is still an answer
    except Exception:
        return False


class BackendHealth:
    """Up/down state, fail-fast check and background probe for one backend."""

    def __init__(
        self,
        name: str,
        label: str,
        *,
        url: str = "",
        probe_path: str = "/",
        message: str = "",
        settings: Settings | None = None,
        probe: Callable[[str, float], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        logger: logging.Logger | None = None,
        autostart: bool | None = None,
    ):
        self.name = name
        self.label = label
        self.message = message or f"{label} is offline right now."
        self.probe_path = probe_path
        self.settings = settings or Settings()
        self._probe = probe or default_probe
        self._clock = clock
        self._wall = wall
        self._log = logger or _log
        self._autostart = probes_enabled() if autostart is None else autostart
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._url = (url or "").rstrip("/")
        self.reset()
        _ALL.append(self)

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        """Back to up with no history (tests, and a changed backend URL)."""
        with getattr(self, "_lock", threading.RLock()):
            self.state = UP
            self.down_since: float | None = None  # wall clock
            self._down_at: float | None = None  # monotonic
            self.error_id = ""
            self.detail = ""
            self.refused = 0
            self.probes = 0
            self.generation = 0  # bumps on every transition, for pollers
            self._next_probe_at: float | None = None
            self._watch_misses = 0
            self._verify_at: float | None = None  # monotonic: a confirming probe is due
            self._suspect = False  # a real call just failed in a way that proved nothing
            self._last_summary_at: float | None = None
            self.last_change: float | None = None

    @property
    def url(self) -> str:
        return self._url

    def set_url(self, url: str) -> None:
        """The backend's base URL, as the caller resolved it from its settings."""
        url = (url or "").rstrip("/")
        if url != self._url:
            with self._lock:
                self._url = url

    def is_down(self) -> bool:
        return self.state == DOWN

    def unavailable(self) -> BackendUnavailable:
        """The exception a caller raises when it finds this backend down."""
        return BackendUnavailable(self.name, self.label, self.error_id, self.message)

    def check(self) -> None:
        """Raise ``BackendUnavailable`` at once if the backend is known to be down."""
        if self.state != DOWN:
            return
        with self._lock:
            self.refused += 1
        self._log.debug("%s is down (error id %s); refused a call without trying", self.name, self.error_id)
        self._maybe_summarise()
        raise self.unavailable()

    def record_failure(self, exc: BaseException) -> bool:
        """Note a failed call; marks the backend down when it could not connect.

        Returns True when ``exc`` was a connection failure (whether or not this
        call is the one that opened the breaker), so callers know not to retry.
        """
        if not is_unreachable(exc):
            if is_suspect(exc):
                self.verify_soon(f"{type(exc).__name__}: {exc}")
            return False
        if isinstance(exc, BackendUnavailable):
            return True
        self.mark_down(f"{type(exc).__name__}: {exc}")
        return True

    def verify_soon(self, detail: str = "") -> None:
        """A call failed in a way that proves nothing alone: probe now, not at the next watch.

        Never opens the breaker by itself. The probe does, after ``WATCH_MISSES``
        failures in a row ``verify_interval`` apart; one that passes (the server
        is loading a model, or was only busy) clears it. A confirmation already
        on its way is not brought forward, so a burst of failed calls cannot
        turn the spaced probes into back-to-back ones.
        """
        with self._lock:
            if self.state == DOWN or not self._url:
                return
            self._suspect = self._suspect or bool(detail)
            if self._verify_at is not None:
                return
            self._verify_at = self._clock()
        self._log.debug("%s: %s; probing now", self.name, detail or "checking")
        if self._autostart:
            self.start()
        self._wake.set()

    def confirm(self, detail: str) -> bool:
        """``verify_soon``, but probe here and now; returns whether calls may go ahead.

        Blocks for up to ``probe_timeout`` (async callers run it in a thread).
        For a call that is about to wait a long time on a backend that may be
        gone: the proxy timing out on its model list is that call's first
        failure, and a probe answered "cannot serve" is its second.
        """
        with self._lock:
            if self.state == DOWN:
                return False
            if not self._url:
                return True
            self._suspect = True
        self.probe_once()
        return self.state != DOWN

    def record_success(self) -> None:
        """A real call got an answer: the backend is up."""
        if self.state == DOWN:
            self.mark_up("a request got an answer")

    def mark_down(self, detail: str) -> None:
        with self._lock:
            if self.state == DOWN:
                self.refused += 1
                self._log.debug("%s still unreachable (error id %s): %s", self.name, self.error_id, detail)
                return
            self.state = DOWN
            self._watch_misses = 0
            self._verify_at = None
            self.down_since = self._wall()
            self._down_at = self._clock()
            self.error_id = uuid.uuid4().hex[:8]
            self.detail = detail
            self.refused = 0
            self.probes = 0
            self.generation += 1
            self.last_change = self.down_since
            self._last_summary_at = self._down_at
            self._next_probe_at = self._down_at + self.next_probe_delay()
        self._log.error(
            "%s is unreachable (error id %s), failing fast until a probe of %s answers: %s",
            self.name, self.error_id, self._url or "(no url)", detail,
        )
        if self._autostart:
            self.start()
        self._wake.set()

    def mark_up(self, how: str) -> None:
        with self._lock:
            if self.state != DOWN:
                return
            down_for = self._clock() - (self._down_at or self._clock())
            refused, error_id = self.refused, self.error_id
            self.state = UP
            self.down_since = None
            self._down_at = None
            self.generation += 1
            self.last_change = self._wall()
            self.probes = 0
            self._next_probe_at = None
        self._log.warning(
            "%s is reachable again (%s) after %s; %d call(s) were refused meanwhile (error id %s)",
            self.name, how, _duration(down_for), refused, error_id,
        )

    # ------------------------------------------------------------------ probing
    def next_probe_delay(self) -> float:
        """Seconds until the next probe: doubling from ``probe_interval`` to ``probe_max``
        while down, ``watch_interval`` while up (0 = no probe while up)."""
        s = self.settings
        if self.state != DOWN:
            return s.watch_interval
        return min(s.probe_max, s.probe_interval * (2 ** min(self.probes, 16)))

    def probe_once(self) -> bool:
        """Probe now; closes the breaker when the probe passes, backs off when not.

        Returns whether the probe passed. With no URL configured there is
        nothing to probe and the state is left alone.
        """
        url = self._url
        if not url:
            return self.state == UP
        target = f"{url}{self.probe_path}" if self.probe_path else url
        try:
            result = self._probe(target, self.settings.probe_timeout)
        except Exception as exc:  # a broken probe must not kill the thread
            self._log.debug("%s probe raised %r", self.name, exc)
            result = False
        ok = bool(result)
        answered = bool(getattr(result, "answered", False))
        reason = getattr(result, "reason", "") or ("answered" if ok else "got no answer")
        with self._lock:
            suspect, self._suspect = self._suspect, False
        if ok:
            with self._lock:
                self._watch_misses = 0
                self._verify_at = None
            self.mark_up(f"a background probe {reason}")
            return True
        with self._lock:
            if self.state != DOWN and suspect and answered:
                # A real call failed and the backend itself now says it cannot
                # serve: two failures in a row, the second one definite.
                self._watch_misses = WATCH_MISSES
            if self.state != DOWN:
                # While it is up, one failed probe can be a busy moment or a quick
                # restart: it takes WATCH_MISSES in a row, the next one coming
                # ``verify_interval`` later instead of a whole watch interval.
                self._watch_misses += 1
                if self._watch_misses < WATCH_MISSES:
                    self._verify_at = self._clock() + self.settings.verify_interval
                    self._log.debug("%s probe failed (%d in a row): %s; checking again in %.0fs", self.name,
                                    self._watch_misses, reason, self.settings.verify_interval)
                    return False
            if self.state == DOWN:
                self.probes += 1
                self._next_probe_at = self._clock() + self.next_probe_delay()
                self._log.debug("%s probe %d failed: %s; next in %.0fs", self.name, self.probes, reason,
                                self.next_probe_delay())
                self._maybe_summarise()
                return False
        self.mark_down(f"background probe of {target} {reason}")
        return False

    def seconds_to_next_probe(self) -> float | None:
        """How long the probe thread sleeps: None means until something wakes it."""
        now = self._clock()
        if self.state == DOWN:
            if self._next_probe_at is not None:
                return max(0.0, self._next_probe_at - now)
            return self.next_probe_delay()
        waits = []
        if self._verify_at is not None:
            waits.append(max(0.0, self._verify_at - now))
        if self.settings.watch_interval > 0:
            waits.append(self.settings.watch_interval)
        return min(waits) if waits else None

    def _maybe_summarise(self) -> None:
        if self.state != DOWN or self._last_summary_at is None:
            return
        now = self._clock()
        if now - self._last_summary_at < self.settings.summary_interval:
            return
        with self._lock:
            self._last_summary_at = now
            down_for = now - (self._down_at or now)
        self._log.warning(
            "%s still unreachable after %s (error id %s): %d call(s) refused, %d probe(s) unanswered",
            self.name, _duration(down_for), self.error_id, self.refused, self.probes,
        )

    def start(self) -> None:
        """Start the background probe thread (idempotent; off when probes are disabled)."""
        if not self._autostart:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name=f"health-{self.name}", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            delay = self.seconds_to_next_probe()
            woke = self._wake.wait(timeout=delay)  # None: not watching, sleep until woken
            self._wake.clear()
            if self._stop.is_set():
                return
            if woke:
                continue  # state changed: recompute the delay
            self.probe_once()

    def snapshot(self) -> dict[str, object]:
        """The state for a status endpoint: no host names, no exception text."""
        with self._lock:
            down = self.state == DOWN
            now = self._clock()
            return {
                "name": self.name,
                "label": self.label,
                "state": self.state,
                "configured": bool(self._url),
                "since": self.down_since if down else None,
                "down_for_s": round(now - self._down_at, 1) if down and self._down_at is not None else 0.0,
                "error_id": self.error_id if down else None,
                "message": self.message if down else None,
                "next_probe_in_s": round(max(0.0, self._next_probe_at - now), 1)
                if down and self._next_probe_at is not None else None,
                "refused": self.refused if down else 0,
            }


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds / 3600:.1f} h"
