"""Is Home Assistant up? One answer per HA host, shared by every caller.

Before this, an HA outage cost every call its full timeout (45 s, several in a
row in the announce and media flows), most reads quietly returned "nothing",
and the hardware router's direct ESPHome fallback only fired when the error
text happened to contain "unreachable" - which a hung HA (a timeout) never
produced. So a down HA looked like a slow, empty house.

The rules:

* A connection error or a timeout is HA *unreachable*. An HTTP answer of any
  status, even 401 or 500, is HA *up*: it answered, so the problem is the
  request, and falling back to direct control would be wrong.
* ``DOWN_AFTER_FAILURES`` unreachable results in a row mark the host down.
  While it is down, callers get an immediate "unreachable" error instead of
  waiting out a timeout, and ``hardware_router`` falls back on that text.
* Every ``PROBE_INTERVAL_S`` one real call is let through as a probe; the first
  answer marks the host up again. No background task is needed: background
  pollers (home alerts, state sync) keep probing on their own schedule.
* Admins get one notice once HA has been down for ``NOTIFY_AFTER_S`` (an HA
  restart takes about a minute and should not ring phones), and the recovery
  notice replaces it under the same tag.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

import aiohttp

from services.execution.http_client import host_of

log = logging.getLogger("execution.ha_health")

DOWN_AFTER_FAILURES = 2
PROBE_INTERVAL_S = 15.0
NOTIFY_AFTER_S = 60.0

#: The error text callers see while HA is down. ``hardware_router`` keys its
#: direct fallback on the word "unreachable"; keep it in every message.
UNREACHABLE = "Home Assistant is unreachable"


@dataclass
class _Host:
    failures: int = 0
    down_since: float | None = None
    last_error: str = ""
    last_probe: float = 0.0
    notified: bool = False


_hosts: dict[str, _Host] = {}
_tasks: set[asyncio.Task] = set()


def _key(ha_url: str) -> str:
    return host_of(ha_url) or ha_url


def is_unreachable_error(exc: BaseException) -> bool:
    """A failure that means HA did not answer at all (vs answered with an error)."""
    if isinstance(exc, aiohttp.ClientResponseError):
        return False
    return isinstance(exc, (aiohttp.ClientConnectionError, asyncio.TimeoutError, TimeoutError, OSError))


def describe(exc: BaseException) -> str:
    """A readable reason. aiohttp's timeouts stringify to "", which hid the cause."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timed out"
    return str(exc) or type(exc).__name__


def is_unreachable_result(result: dict | None) -> bool:
    """An ha_client result that failed because HA did not answer."""
    return bool(result) and not result.get("ok") and UNREACHABLE.lower() in str(result.get("error", "")).lower()


def is_down(ha_url: str) -> bool:
    host = _hosts.get(_key(ha_url))
    return bool(host and host.down_since is not None)


def skip_reason(ha_url: str) -> str | None:
    """None to make the call; otherwise the error to return without calling HA.

    While the host is down, one call per ``PROBE_INTERVAL_S`` goes through as the
    probe. A probe that never reports back (a cancelled request) is simply
    replaced by the next one after another interval.
    """
    host = _hosts.get(_key(ha_url))
    if host is None or host.down_since is None:
        return None
    now = time.monotonic()
    if now - host.last_probe >= PROBE_INTERVAL_S:
        host.last_probe = now
        return None
    _maybe_notify(ha_url, host, now)
    return f"{UNREACHABLE} (down for {int(now - host.down_since)}s: {host.last_error})"


def record_answer(ha_url: str) -> None:
    """HA answered (any HTTP status). If it was down, it is back."""
    if not ha_url:
        return
    host = _hosts.get(_key(ha_url))
    if host is None:
        return
    was_down = host.down_since is not None
    if was_down:
        log.warning(
            f"[ha_health] Home Assistant at {_key(ha_url)} is back after "
            f"{int(time.monotonic() - host.down_since)}s"
        )
        if host.notified:
            _spawn(_send_notice(ha_url, recovered=True))
    _hosts.pop(_key(ha_url), None)


def record_unreachable(ha_url: str, exc: BaseException | str) -> None:
    """HA did not answer (connection refused/reset, DNS, timeout)."""
    if not ha_url:
        return
    host = _hosts.setdefault(_key(ha_url), _Host())
    host.failures += 1
    host.last_error = exc if isinstance(exc, str) else describe(exc)
    now = time.monotonic()
    if host.down_since is None and host.failures >= DOWN_AFTER_FAILURES:
        host.down_since = now
        host.last_probe = now
        log.warning(
            f"[ha_health] Home Assistant at {_key(ha_url)} is DOWN ({host.last_error}); "
            f"failing fast and probing every {int(PROBE_INTERVAL_S)}s"
        )
    _maybe_notify(ha_url, host, now)


def status() -> dict:
    """Per-host state for /health. Hosts never seen failing are not listed (up)."""
    now = time.monotonic()
    out = {}
    for key, host in _hosts.items():
        if host.down_since is None:
            out[key] = {"status": "failing", "failures": host.failures, "last_error": host.last_error}
        else:
            out[key] = {
                "status": "down",
                "down_for_s": int(now - host.down_since),
                "last_error": host.last_error,
            }
    return out


def reset() -> None:
    """Forget everything (tests)."""
    _hosts.clear()


def _maybe_notify(ha_url: str, host: _Host, now: float) -> None:
    if host.down_since is None or host.notified or now - host.down_since < NOTIFY_AFTER_S:
        return
    host.notified = True
    _spawn(_send_notice(ha_url, recovered=False, reason=host.last_error))


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _send_notice(ha_url: str, *, recovered: bool, reason: str = "") -> None:
    with contextlib.suppress(Exception):
        from services.common import notify

        host = _key(ha_url)
        if recovered:
            title, body, priority = (
                "Home Assistant is back",
                f"Home Assistant at {host} is answering again; Jarvis is using it normally.",
                2,
            )
        else:
            title, body, priority = (
                "Home Assistant is down",
                f"Home Assistant at {host} is not answering ({reason}). Jarvis is controlling "
                "devices it can reach directly; the rest will fail until it is back.",
                3,
            )
        await notify.send(
            notify.notice("@admins", "admin", title, body, url="/", priority=priority,
                          tag=f"admin:ha-down:{host}", kind="admin")
        )
