"""Why an ESPHome node went away: restart or dropped connection, and the likely cause.

Every ESPHome node gets one :class:`RestartMonitor` (``drivers/esphome.py``). It works on the hub's own API
connection to the node; it never opens a second one.

**What it keeps while the node is connected**

* The node's log lines, through a log subscription on that connection at ``warning`` by default (entry option
  ``restart_log_level``: ``off``, ``error``, ``warning``, ``info`` or ``debug``; ``debug`` for a node an admin
  is investigating), in a ring buffer of :data:`RING_LINES` lines. Lines are rate-limited
  (:data:`RATE_LINES_PER_S`), cut to :data:`LINE_MAX_CHARS` and have anything that looks like a secret
  redacted before they are kept.
* The node's own diagnostic sensors, found by what they are, not by a list of devices: an uptime sensor of any
  kind (seconds, or a boot timestamp), and the ESPHome ``debug`` component's reset reason and free heap/PSRAM.

**When the connection ends** (``on_stop``), the monitor notes the time, the last :data:`EVENT_LINES_BEFORE` log
lines, the error the client saw, how long the node had been silent and what the hub was doing with it (a voice
run, an announcement, media playing, a firmware install). Once the node is back it waits :data:`SETTLE_S` for
the node's states and decides:

* ``restart``: the uptime went backwards or has no value yet, the boot timestamp moved, boot messages arrived,
  or the node asked to disconnect first (a planned reboot);
* ``drop``: the uptime kept counting, so only the connection was lost;
* ``unknown``: the node has no uptime sensor; the event says how long it was gone and what the error suggests.

The reset reason (and a last-crash text sensor, if the node has one) is attached to a restart. Events (newest
first, :data:`EVENTS_KEEP`) are kept in the hub's store, so a hub restart keeps them. Three restarts, or
unexplained disconnects, within an hour send one admin notice (:data:`REPEAT_COUNT`, :data:`NOTICE_COOLDOWN_S`),
not one per event.

``GET /hub/devices/{id}/restarts`` (``restarts_api.py``) shows it all; ``/health`` carries :func:`health_summary`.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from services.homehub.core import utcnow

log = logging.getLogger("homehub.esphome.restarts")

#: Log lines kept per node while it is connected.
RING_LINES = 200
#: A longer line is cut (a node can log a whole JSON document in one line).
LINE_MAX_CHARS = 240
#: Lines from before the drop and after the reconnect that are kept with an event.
EVENT_LINES_BEFORE = 60
EVENT_LINES_AFTER = 30
#: Characters of log lines one stored event may hold, so the store stays small whatever the level.
EVENT_CHARS_MAX = 12_000
#: Events kept per node.
EVENTS_KEEP = 20
#: How long after the reconnect the node's states are waited for before deciding.
SETTLE_S = 8.0
#: How long after the reconnect a boot message still turns an undecided event into a restart.
BOOT_WINDOW_S = 90.0
#: Log lines a node may send per second (with a burst of :data:`RATE_BURST`); more are counted, not kept.
RATE_LINES_PER_S = 20.0
RATE_BURST = 60
#: Restarts (or unexplained disconnects) within :data:`REPEAT_WINDOW_S` that send one admin notice.
REPEAT_COUNT = 3
REPEAT_WINDOW_S = 3600.0
#: Something the hub started with a node and never saw finish is no longer "running" after this long.
ACTIVITY_STALE_S = 1800.0
#: After a notice, the next one for the same node waits this long.
NOTICE_COOLDOWN_S = 6 * 3600.0

OPTION = "restart_log_level"
DEFAULT_LOG_LEVEL = "warning"
#: Option value -> aioesphomeapi ``LogLevel`` number.
LOG_LEVELS = {"off": 0, "error": 1, "warning": 2, "info": 3, "debug": 5}
_LEVEL_LETTER = {1: "E", 2: "W", 3: "I", 4: "C", 5: "D", 6: "V", 7: "VV"}
KV_KEY = "esphome_restarts:{entry_id}"

#: The YAML that gives a node what this monitor reads. Generic: it names no device.
DEBUG_YAML = """\
debug:
  update_interval: 30s

text_sensor:
  - platform: debug
    reset_reason:
      name: "Reset reason"

sensor:
  - platform: debug
    free:
      name: "Heap free"
    psram:            # only on an ESP32 with PSRAM
      name: "PSRAM free"
  - platform: uptime
    name: "Uptime"
"""

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_SECRET = re.compile(
    r"(?i)\b(password|passwd|psk|api[_ ]?key|encryption[_ ]?key|noise[_ ]?psk|token|secret)"
    r"(\s*[:=]\s*)(['\"]?)[^\s'\",;]+")
_BEARER = re.compile(r"(?i)\b(bearer|basic)(\s+)()[^\s'\",;]+")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}")
#: ``key=...``/``pass=...`` (a query string, a form) that the named patterns above do not cover.
_KEY_EQ = re.compile(r"(?i)\b((?:[a-z]+_)?(?:key|pass|pwd|auth|sig|signature|authsig))(=)()[^\s&'\",;]+")
#: Home Assistant style proxy URLs whose path itself is the credential (an announcement's audio).
_PROXY_PATH = re.compile(r"(?i)(/api/(?:esphome/ffmpeg_proxy|tts_proxy)/)[^\s'\"<>]+")
#: Lines a node logs as it boots (any ESPHome node; not one firmware's wording alone).
_BOOT = re.compile(r"(?i)running through setup|setup\(\) finished|boot seems successful|reset reason|rst:0x"
                   r"|esphome version .* compiled|starting esphome")
#: Lines that point at why a node fell over.
_CRASH = re.compile(r"(?i)guru meditation|panic|abort\(\)|assert(ion)? failed|watchdog|brownout|out of memory"
                    r"|failed to allocate|alloc(ation)? fail|stack overflow|stack canary|loadprohibited"
                    r"|storeprohibited|illegalinstruction|heap|psram|no free memory|ring buffer full")
_UNITS = {"s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0, "d": 86400.0, "ms": 0.001}

Notifier = Callable[[dict[str, Any]], Awaitable[bool]]


async def _send_notice(payload: dict[str, Any]) -> bool:
    from services.common import notify

    return await notify.send(payload)


def redact(text: str) -> str:
    """Anything in a log line that looks like a credential, replaced by ``[redacted]``."""
    text = _PROXY_PATH.sub(lambda m: f"{m.group(1)}[redacted]", text)
    for pattern in (_SECRET, _BEARER, _KEY_EQ):
        text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]", text)
    return _LONG_TOKEN.sub("[redacted]", text)


def clean_line(raw: Any, max_chars: int = LINE_MAX_CHARS) -> str:
    """One log line as the monitor keeps it: no colour codes, nothing secret, not too long."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes | bytearray) else str(raw or "")
    text = redact(_ANSI.sub("", text).replace("\r", "").strip())
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def sensor_role(info: Any) -> str | None:
    """What a node's entity tells the monitor, from what it is: ``uptime``, ``boot_time``, ``reset_reason``,
    ``last_crash``, ``heap_free``, ``heap_min_free``, ``heap_max_block`` or ``psram_free``; None for the rest."""
    from aioesphomeapi import SensorInfo, TextSensorInfo

    text = f"{getattr(info, 'object_id', '') or ''} {getattr(info, 'name', '') or ''}".lower().replace("_", " ")
    if isinstance(info, TextSensorInfo):
        if "reset reason" in text:
            return "reset_reason"
        if "crash" in text:
            return "last_crash"
        return None
    if not isinstance(info, SensorInfo):
        return None
    unit = str(getattr(info, "unit_of_measurement", "") or "").strip()
    if "uptime" in text:
        if str(getattr(info, "device_class", "") or "") == "timestamp":
            return "boot_time"
        return "uptime" if unit.lower() in _UNITS or not unit else None
    if unit in ("B", "kB", "KB", "bytes") or "heap" in text or "psram" in text:
        if "psram" in text:
            return "psram_free"
        if "block" in text:
            return "heap_max_block"
        if "min" in text:
            return "heap_min_free"
        if "free" in text or "heap" in text:
            return "heap_free"
    return None


def _seconds(value: float, info: Any) -> float:
    unit = str(getattr(info, "unit_of_measurement", "") or "s").strip().lower()
    return float(value) * _UNITS.get(unit, 1.0)


def _iso(dt: Any) -> str:
    return dt.isoformat(timespec="seconds")


class RestartMonitor:
    """Restarts and drops of one ESPHome node, with the log lines and context around each."""

    def __init__(self, driver: Any, *, notifier: Notifier | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.driver = driver
        self.hub = driver.hub
        self.notifier = notifier or _send_notice
        self.clock = clock
        self.ring: deque[tuple[str, str, str]] = deque(maxlen=RING_LINES)  # (at, level letter, text)
        self.dropped = 0  # lines over the rate limit since the node connected
        self._tokens = float(RATE_BURST)
        self._tokens_at = clock()
        self.subscribed = False
        self.subscribe_error: str | None = None
        self._unsub_logs: Callable[[], None] | None = None
        self._unsub_closed: Callable[[], None] | None = None
        self._client: Any = None
        self._roles: dict[tuple[int, int], tuple[str, Any]] = {}  # (device_id, key) -> (role, info)
        self.values: dict[str, dict[str, Any]] = {}  # role -> {"value", "missing", "at"}
        self.heap_low: dict[str, float] = {}  # lowest free heap/PSRAM seen since the node connected
        self.last_heard: float | None = None
        self.connected_at: float | None = None
        self._close_error: str | None = None
        self._close_reason: str | None = None
        self._pending: dict[str, Any] | None = None
        self._after: dict[str, dict[str, Any]] = {}
        self._settle_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._recent: dict[str, Any] | None = None  # the last event, while a boot line may still decide it
        self._activity: dict[str, dict[str, float | None]] = {}  # "announcement" -> {"started", "ended"}
        self.events: list[dict[str, Any]] = []
        self.notified_at: str | None = None
        from services.homehub.esphome_logs import DeviceLogs

        #: Every line the node sends, for Devices > a device > Logs (``esphome_logs.py``); its viewers raise the
        #: subscription level while they watch.
        self.logs = DeviceLogs(self._viewers_changed, clock=clock)
        self.subscribed_level = 0  # the aioesphomeapi level the node was last asked for
        self._dump_timer: asyncio.TimerHandle | None = None
        self._restore()

    # -- settings and store ------------------------------------------------------------
    @property
    def name(self) -> str:
        return str(self.driver.device_name or self.driver.host)

    @property
    def title(self) -> str:
        entry = getattr(self.driver, "entry", None)
        return str(getattr(entry, "title", "") or self.name)

    @property
    def log_level(self) -> str:
        entry = getattr(self.driver, "entry", None)
        level = str((entry.options.get(OPTION) if entry is not None else None) or DEFAULT_LOG_LEVEL).lower()
        return level if level in LOG_LEVELS else DEFAULT_LOG_LEVEL

    @property
    def _kv_key(self) -> str | None:
        entry = getattr(self.driver, "entry", None)
        return KV_KEY.format(entry_id=entry.entry_id) if entry is not None else None

    def _restore(self) -> None:
        store, key = getattr(self.hub, "store", None), self._kv_key
        if store is None or key is None:
            return
        try:
            doc = store.get_kv(key) or {}
        except Exception:
            log.exception("ESPHome %s: its restart history could not be read", self.name)
            return
        self.events = [e for e in doc.get("events") or [] if isinstance(e, dict)][:EVENTS_KEEP]
        self.notified_at = doc.get("notified_at")

    def _save(self) -> None:
        store, key = getattr(self.hub, "store", None), self._kv_key
        if store is None or key is None:
            return
        try:
            events = [{k: v for k, v in e.items() if not k.startswith("_")} for e in self.events[:EVENTS_KEEP]]
            store.set_kv(key, {"events": events, "notified_at": self.notified_at})
        except Exception as exc:  # the event is still in memory and in the API
            log.warning("ESPHome %s: its restart history could not be saved: %s", self.name, exc)

    # -- the connection ----------------------------------------------------------------------
    def attach(self, client: Any, infos: list[Any]) -> None:
        """A new connection: find the node's diagnostic sensors, subscribe to its logs, and, when the node
        was away, start deciding what happened."""
        self._client = client
        self._roles = {}
        for info in infos:
            role = sensor_role(info)
            if role is not None:
                self._roles[(getattr(info, "device_id", 0) or 0, info.key)] = (role, info)
        now = self.clock()
        self.connected_at = self.last_heard = now
        self.heap_low = {}
        self.dropped = 0
        self._close_error = self._close_reason = None
        self._watch_close(client)
        self._subscribe_logs()
        self.logs.add_marker("online", "Reconnected" if self._pending is not None else "Connected")
        if self._pending is not None:
            self._pending["reconnected"] = now
            self._pending["reconnected_at"] = _iso(utcnow())
            self._after = {}
            self._settle_task = self._spawn(self._settle(), "settle")

    def _watch_close(self, client: Any) -> None:
        add = getattr(client, "add_connection_closed_callback", None)
        if not callable(add):
            return
        try:
            self._unsub_closed = add(self._on_closed)
        except Exception:
            self._unsub_closed = None

    def _on_closed(self, event: Any) -> None:
        err = getattr(event, "error", None)
        self._close_error = clean_line(f"{type(err).__name__}: {err}") if err is not None else None
        reason = getattr(event, "reason", None)
        self._close_reason = getattr(reason, "name", None) if reason is not None else None

    @property
    def effective_level(self) -> int:
        """The level the node is asked for: the configured one, raised while someone watches its logs."""
        return max(LOG_LEVELS[self.log_level], self.logs.wanted_level())

    def _subscribe_logs(self, *, dump_config: bool = False) -> None:
        self._unsubscribe_logs()
        client, level = self._client, self.effective_level
        self.subscribe_error = None
        if client is None:
            return
        subscribe = getattr(client, "subscribe_logs", None)
        if not callable(subscribe):
            self.subscribe_error = "this connection cannot subscribe to logs"
            return
        try:
            from aioesphomeapi import LogLevel

            kwargs: dict[str, Any] = {"log_level": LogLevel(level)}
            if dump_config:
                kwargs["dump_config"] = True
            self._unsub_logs = subscribe(self._on_log, **kwargs)
        except Exception as exc:  # the node still works; only its log lines are not kept
            self.subscribe_error = f"{type(exc).__name__}: {exc}"
            log.warning("ESPHome %s: subscribing to its logs failed: %s", self.name, exc)
            return
        self.subscribed_level = level
        self.subscribed = level > 0
        if level == 0:  # the node keeps sending until told NONE; the callback is not needed
            self._unsubscribe_logs()

    def _viewers_changed(self) -> None:
        """A viewer came or went (or a config dump's hold ended): ask the node for the level now needed."""
        if self._client is not None and self.effective_level != self.subscribed_level:
            log.info("ESPHome %s: log level %s while %d viewer(s) watch", self.name, self.effective_level,
                     len(self.logs.viewers))
            self._subscribe_logs()

    def request_dump_config(self) -> None:
        """Ask the node to log its configuration again (what it logs at boot). Raises when it is not connected."""
        if self._client is None:
            raise RuntimeError("The device is not connected")
        from services.homehub.esphome_logs import DUMP_HOLD_S

        self.logs.dump_until = self.clock() + DUMP_HOLD_S
        self.logs.add_marker("dump_config", "Config dump requested")
        self._subscribe_logs(dump_config=True)
        if self.subscribe_error:
            raise RuntimeError(self.subscribe_error)
        if self._dump_timer is not None:
            self._dump_timer.cancel()
        with contextlib.suppress(RuntimeError):  # no running loop: the next viewer change drops the level
            self._dump_timer = asyncio.get_running_loop().call_later(DUMP_HOLD_S + 0.1, self._viewers_changed)

    def _unsubscribe_logs(self) -> None:
        unsub, self._unsub_logs = self._unsub_logs, None
        self.subscribed = False
        self.subscribed_level = 0
        if unsub is not None:
            with contextlib.suppress(Exception):
                unsub()

    def set_log_level(self, level: str) -> None:
        """Change the level on the live connection (the caller saves the entry option)."""
        if level not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of {', '.join(LOG_LEVELS)}")
        if self._client is not None:
            self._subscribe_logs()

    def detach(self) -> None:
        """The connection is gone or the driver unloads (no event is recorded for that)."""
        self._unsubscribe_logs()
        unsub, self._unsub_closed = self._unsub_closed, None
        if unsub is not None:
            with contextlib.suppress(Exception):
                unsub()
        self._client = None

    def close(self) -> None:
        self.detach()
        if self._dump_timer is not None:
            self._dump_timer.cancel()
        self.logs.close()
        for t in list(self._tasks):
            t.cancel()

    def _spawn(self, coro: Any, what: str) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro, name=f"esphome-restarts:{self.name}:{what}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- what the node sends ----------------------------------------------------------------
    def _on_log(self, msg: Any) -> None:
        now = self.clock()
        self.last_heard = now
        number = int(getattr(msg, "level", 0) or 0)
        self.logs.add_line(number, getattr(msg, "message", ""))
        if number > LOG_LEVELS[self.log_level] and not _BOOT.search(clean_line(getattr(msg, "message", ""))):
            # Sent because someone is watching the logs: not what the restart history keeps. A boot message
            # still is, since it can decide what the last drop was.
            return
        self._tokens = min(float(RATE_BURST), self._tokens + (now - self._tokens_at) * RATE_LINES_PER_S)
        self._tokens_at = now
        if self._tokens < 1.0:
            self.dropped += 1
            return
        self._tokens -= 1.0
        text = clean_line(getattr(msg, "message", ""))
        if not text:
            return
        level = _LEVEL_LETTER.get(number, "?")
        line = (_iso(utcnow()), level, text)
        self.ring.append(line)
        pending = self._pending
        if pending is not None and pending.get("reconnected") is not None:
            after = pending.setdefault("lines_after", [])
            if len(after) < EVENT_LINES_AFTER:
                after.append(line)
            if _BOOT.search(text):
                pending["boot_line"] = text
        elif self._recent is not None:
            self._late_line(line, now)

    def _late_line(self, line: tuple[str, str, str], now: float) -> None:
        """A line soon after an event was decided: kept with it, and a boot message settles an unknown one."""
        ev = self._recent
        if ev is None or now - float(ev.get("_reconnected", now)) > BOOT_WINDOW_S:
            self._recent = None
            return
        after = ev.setdefault("lines_after", [])
        if len(after) < EVENT_LINES_AFTER:
            after.append(list(line))
        if ev["kind"] == "unknown" and _BOOT.search(line[2]):
            ev["kind"] = "restart"
            ev["evidence"] = f"it logged a boot message after reconnecting: {line[2]}"
            ev["summary"] = self._summary(ev)
            self._save()
            self._maybe_notify()
        else:
            self._save()

    def on_state(self, key: tuple[int, int], state: Any) -> None:
        self.last_heard = self.clock()
        tracked = self._roles.get(key)
        if tracked is None:
            return
        role, info = tracked
        missing = bool(getattr(state, "missing_state", False))
        raw = getattr(state, "state", None)
        value: Any = None
        if not missing and raw is not None:
            if role == "uptime":
                value = _seconds(float(raw), info)
            elif role in ("reset_reason", "last_crash"):
                value = clean_line(raw)
            else:
                with contextlib.suppress(TypeError, ValueError):
                    value = float(raw)
        record = {"value": value, "missing": missing or value is None, "at": self.clock()}
        if role.startswith(("heap", "psram")) and value is not None:
            self.heap_low[role] = min(value, self.heap_low.get(role, value))
        pending = self._pending
        if pending is not None and pending.get("reconnected") is not None:
            self._after[role] = record
            if role in ("uptime", "boot_time") and self._settle_task is not None:
                # The deciding reading is here; the rest of the states arrive with it.
                self._settle_task.cancel()
                self._settle_task = self._spawn(self._settle(min(0.5, SETTLE_S)), "settle")
        self.values[role] = record

    def note(self, activity: str, running: bool) -> None:
        """The hub started or finished something with the node (``announcement``, ``firmware_install``)."""
        now = self.clock()
        cur = self._activity.setdefault(activity, {"started": None, "ended": None})
        if running:
            cur["started"], cur["ended"] = now, None
        else:
            cur["ended"] = now

    # -- the drop ---------------------------------------------------------------------------
    def on_disconnect(self, expected: bool) -> None:
        """The connection ended (not by the hub). Called before the node's entities go unavailable."""
        if self._client is None:
            return  # not a connection the monitor watched (one that failed while connecting)
        now = self.clock()
        if self._pending is not None:  # gone again before the last one was decided
            self._finish()
        self._pending = {
            "at": _iso(utcnow()), "down": now, "expected": bool(expected),
            "error": self._close_error, "reason": self._close_reason,
            "silent_s": round(now - self.last_heard, 1) if self.last_heard is not None else None,
            "connected_s": round(now - self.connected_at, 1) if self.connected_at is not None else None,
            "before": {k: dict(v) for k, v in self.values.items()},
            "heap_low": dict(self.heap_low),
            "lines_before": list(self.ring)[-EVENT_LINES_BEFORE:],
            "dropped_lines": self.dropped, "log_level": self.log_level,
            "context": self._context(now),
        }
        self._recent = None
        self.logs.add_marker("offline", f"Device offline{f' ({self._close_error})' if self._close_error else ''}")
        self.detach()

    def _context(self, now: float) -> dict[str, Any]:
        """What the hub was doing with the node when it went away."""
        ctx: dict[str, Any] = {}
        drv = self.driver
        voice = getattr(drv, "voice", None)
        run = getattr(voice, "_run", None)
        if run is not None:
            marks = dict(getattr(run, "marks", {}) or {})
            stage = max(marks, key=marks.get) if marks else "started"
            ctx["voice_run"] = {"running": True, "stage": stage,
                                "started_s_before": round(now - float(run.started), 1)}
        else:
            last = getattr(voice, "last_run", None)
            ended = (getattr(last, "marks", {}) or {}).get("ended") if last is not None else None
            if ended is not None:
                ctx["voice_run"] = {"running": False, "ended_s_before": round(now - float(ended), 1),
                                    "outcome": getattr(last, "outcome", None)}
        for name, a in self._activity.items():
            if a.get("started") is None or (a.get("ended") is None and now - float(a["started"]) > ACTIVITY_STALE_S):
                continue
            if a.get("ended") is None:
                ctx[name] = {"running": True, "started_s_before": round(now - float(a["started"]), 1)}
            else:
                ctx[name] = {"running": False, "ended_s_before": round(now - float(a["ended"]), 1)}
        media = []
        for entity in list(getattr(drv, "_entity_for", {}).values()):
            if getattr(entity, "domain", "") != "media_player":
                continue
            st = self.hub.states.get(entity.entity_id)
            if st is None:
                continue
            since = None
            changed = getattr(st, "last_changed", None)
            if changed is not None:
                with contextlib.suppress(Exception):
                    since = round((utcnow() - changed).total_seconds(), 1)
            media.append({"entity_id": entity.entity_id, "state": st.state, "since_s": since})
        if media:
            ctx["media"] = media
        with contextlib.suppress(Exception):
            fw = drv._firmware_entity()
            st = self.hub.states.get(fw.entity_id) if fw is not None else None
            if st is not None and st.attributes.get("in_progress"):
                ctx["firmware_update"] = True
        return ctx

    # -- the reconnect -----------------------------------------------------------------------
    async def _settle(self, delay: float | None = None) -> None:
        await asyncio.sleep(SETTLE_S if delay is None else delay)
        self._finish()

    def _finish(self) -> None:
        """Decide what the last drop was and keep it."""
        pending, self._pending = self._pending, None
        task, self._settle_task = self._settle_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        if pending is None:
            return
        after, self._after = self._after, {}
        reconnected = pending.get("reconnected")
        downtime = round(float(reconnected) - float(pending["down"]), 1) if reconnected is not None else None
        kind, evidence = self._decide(pending, after)
        reset = after.get("reset_reason") or pending["before"].get("reset_reason")
        crash = after.get("last_crash") or pending["before"].get("last_crash")
        reset_reason = reset.get("value") if reset and not reset.get("missing") else None
        lines_before = [list(x) for x in pending.get("lines_before") or []]
        lines_after = [list(x) for x in pending.get("lines_after") or []]
        lines_before, lines_after = _fit(lines_before, lines_after)
        ev: dict[str, Any] = {
            "id": f"{int(time.time() * 1000):x}", "at": pending["at"], "reconnected_at": pending.get("reconnected_at"),
            "kind": kind, "evidence": evidence, "downtime_s": downtime, "expected": pending["expected"],
            "error": pending.get("error"), "silent_s": pending.get("silent_s"),
            "connected_s": pending.get("connected_s"),
            "reset_reason": reset_reason if kind == "restart" else None,
            "last_reset_reason": reset_reason if kind != "restart" else None,
            "last_crash": crash.get("value") if crash and kind == "restart" else None,
            "memory": self._memory(pending, after), "context": pending.get("context") or {},
            "log_level": pending.get("log_level"), "dropped_lines": pending.get("dropped_lines", 0),
            "lines_before": lines_before, "lines_after": lines_after,
        }
        if (ev["context"].get("firmware_install") or {}).get("running"):
            self._activity["firmware_install"]["ended"] = self.clock()  # the install's reboot is this event
        ev["hint"] = self._hint(ev)
        ev["cause"] = self._cause(ev)
        ev["summary"] = self._summary(ev)
        ev["context_text"] = context_text(ev["context"])
        stored = {k: v for k, v in ev.items() if not k.startswith("_")}
        self.events.insert(0, stored)
        del self.events[EVENTS_KEEP:]
        self._recent = stored
        stored["_reconnected"] = reconnected if reconnected is not None else self.clock()
        self.logs.add_marker(kind, ev["summary"], restart_id=ev["id"])
        self._save()
        (log.warning if kind != "drop" else log.info)(
            "ESPHome %s: %s (%s)%s", self.name, ev["summary"], evidence,
            f"; {'; '.join(ev['context_text'])}" if ev["context_text"] else "")
        self._maybe_notify()

    def _decide(self, pending: dict[str, Any], after: dict[str, dict[str, Any]]) -> tuple[str, str]:
        before = pending["before"]
        b, a = before.get("uptime"), after.get("uptime")
        if b is not None and not b.get("missing") and b.get("value") is not None:
            v0 = float(b["value"])
            if a is not None:
                if a.get("missing") or a.get("value") is None:
                    return "restart", f"its uptime sensor had {_dur(v0)} and has no reading yet after reconnecting"
                v1 = float(a["value"])
                if v1 + 1.0 < v0:
                    return "restart", f"its uptime went back from {_dur(v0)} to {_dur(v1)}"
                return "drop", f"its uptime kept counting ({_dur(v0)}, then {_dur(v1)})"
        bt, at = before.get("boot_time"), after.get("boot_time")
        if bt is not None and at is not None and bt.get("value") is not None:
            if at.get("value") is None or abs(float(at["value"]) - float(bt["value"])) > 30:
                return "restart", "its boot time changed"
            return "drop", "its boot time did not change"
        if pending.get("boot_line"):
            return "restart", f"it logged a boot message after reconnecting: {pending['boot_line']}"
        if pending.get("expected"):
            return "restart", "it asked to disconnect first, which ESPHome does just before a planned reboot"
        if "uptime" in before or "uptime" in after or "boot_time" in after:
            return "unknown", "its uptime sensor did not report before and after"
        return "unknown", "it has no uptime sensor, so a restart cannot be told from a dropped connection"

    @staticmethod
    def _memory(pending: dict[str, Any], after: dict[str, dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for role in ("heap_free", "heap_min_free", "heap_max_block", "psram_free"):
            b, a = pending["before"].get(role), after.get(role)
            item = {"before": b.get("value") if b else None, "lowest": pending["heap_low"].get(role),
                    "after": a.get("value") if a else None}
            if any(v is not None for v in item.values()):
                out[role] = item
        return out

    @staticmethod
    def _hint(ev: dict[str, Any]) -> str | None:
        if ev["kind"] != "unknown":
            return None
        err = str(ev.get("error") or "").lower()
        silent = ev.get("silent_s")
        if "ping" in err or "timeout" in err or "timed out" in err:
            return (f"The node stopped answering{f' for {_dur(silent)}' if silent else ''}: it hung, lost Wi-Fi, "
                    "or was restarting.")
        if any(w in err for w in ("reset", "closed", "lost", "eof", "broken pipe", "refused")):
            return ("The node closed the connection or no longer knew it, which happens when it restarts "
                    "(and also when its Wi-Fi reconnects).")
        return None

    @staticmethod
    def _cause(ev: dict[str, Any]) -> str | None:
        if ev.get("context", {}).get("firmware_update") or ev.get("context", {}).get("firmware_install", {}).get(
                "running"):
            return "A firmware update was being installed."
        if ev.get("last_crash"):
            return f"Last crash: {ev['last_crash']}"
        if ev.get("reset_reason"):
            return f"Reset reason: {ev['reset_reason']}"
        for _at, _lvl, text in reversed(ev.get("lines_before") or []):
            if _CRASH.search(text):
                return f"Its last log lines before the drop mention: {text}"
        return None

    @staticmethod
    def _summary(ev: dict[str, Any]) -> str:
        down = f", {_dur(ev['downtime_s'])} offline" if ev.get("downtime_s") is not None else ""
        if ev["kind"] == "restart":
            why = f" ({ev['reset_reason']})" if ev.get("reset_reason") else ""
            return f"Restarted{why}{down}"
        if ev["kind"] == "drop":
            return f"Connection dropped, the device kept running{down}"
        return f"Disconnected; restart or dropped connection not known{down}"

    # -- repeated restarts ----------------------------------------------------------------------
    def counts(self, window_s: float, now: Any = None) -> dict[str, int]:
        now = now or utcnow()
        since = _iso(now - timedelta(seconds=window_s))
        out = {"restart": 0, "drop": 0, "unknown": 0}
        for ev in self.events:
            if str(ev.get("at") or "") >= since and ev.get("kind") in out:
                out[ev["kind"]] += 1
        return out

    def _maybe_notify(self) -> None:
        now = utcnow()
        since = _iso(now - timedelta(seconds=REPEAT_WINDOW_S))
        recent = [e for e in self.events if str(e.get("at") or "") >= since and e.get("kind") in ("restart", "unknown")
                  and not (e.get("context") or {}).get("firmware_update")]
        if len(recent) < REPEAT_COUNT:
            return
        if self.notified_at and str(self.notified_at) >= _iso(now - timedelta(seconds=NOTICE_COOLDOWN_S)):
            return
        self.notified_at = _iso(now)
        self._save()
        restarts = sum(1 for e in recent if e["kind"] == "restart")
        what = (f"restarted {len(recent)} times" if restarts == len(recent)
                else f"went offline {len(recent)} times")
        cause = next((e["cause"] for e in recent if e.get("cause")), None)
        if cause:
            body = f"{self.title} {what} in the last hour. {cause} See Devices, {self.title}, Restarts."
        elif not self.has_debug:
            body = (f"{self.title} {what} in the last hour and the cause is not known: it does not report a reset "
                    f"reason. Devices, {self.title}, Restarts shows what to add to its configuration.")
        else:
            body = f"{self.title} {what} in the last hour. Devices, {self.title}, Restarts has the details."
        entry = getattr(self.driver, "entry", None)
        entry_id = getattr(entry, "entry_id", self.name)
        from services.common import notify

        payload = notify.notice(
            "@admins", "admin", f"{self.title} keeps restarting" if restarts == len(recent)
            else f"{self.title} keeps going offline", body,
            # Devices > Setup, at this device's integration (its Restarts).
            link=notify.link("devices", hash="setup", focus=f"entry-{entry_id}"), priority=3,
            tag=f"admin:device-restarts:{entry_id}", once=f"homehub:device-restarts:{entry_id}:{self.notified_at}",
            kind="admin", audience="parents")

        async def send() -> None:
            try:
                await self.notifier(payload)
            except Exception as exc:
                log.warning("ESPHome %s: the repeated-restart notice was not sent: %s", self.name, exc)

        with contextlib.suppress(RuntimeError):  # no running loop (a synchronous caller): best effort
            self._spawn(send(), "notice")

    # -- what the API shows -----------------------------------------------------------------------
    @property
    def roles(self) -> set[str]:
        return {r for r, _info in self._roles.values()}

    @property
    def has_debug(self) -> bool:
        return "reset_reason" in self.roles

    def guidance(self) -> dict[str, Any] | None:
        roles = self.roles
        if self._client is None and not roles:
            return None  # never connected in this run of the hub: nothing is known about its sensors yet
        missing = [label for role, label in (("reset_reason", "reset reason (debug component)"),
                                             ("heap_free", "free heap (debug component)"),
                                             ("uptime", "uptime sensor")) if role not in roles
                   and not (role == "uptime" and "boot_time" in roles)]
        if not missing:
            return None
        return {"missing": missing, "yaml": DEBUG_YAML,
                "text": ("Without a reset reason the hub cannot say why the device restarted, and without an uptime "
                         "sensor it cannot tell a restart from a dropped connection. Add this to the device's "
                         "ESPHome configuration and install it.")}

    def public(self, *, lines: int = 50) -> dict[str, Any]:
        now = self.clock()
        pending = self._pending
        current = {role: v.get("value") for role, v in self.values.items() if not v.get("missing")}
        out: dict[str, Any] = {
            "entry_id": getattr(getattr(self.driver, "entry", None), "entry_id", None),
            "name": self.name, "title": self.title, "connected": self._client is not None,
            "log_level": self.log_level, "log_levels": list(LOG_LEVELS), "subscribed": self.subscribed,
            "subscribe_error": self.subscribe_error,
            "sensors": sorted(self.roles), "has_debug": self.has_debug, "guidance": self.guidance(),
            "current": current, "heap_lowest": dict(self.heap_low),
            "buffer": {"lines": len(self.ring), "max": RING_LINES, "dropped": self.dropped},
            "offline": None, "counts_1h": self.counts(3600), "counts_24h": self.counts(86400),
            "events": [{k: v for k, v in e.items() if not k.startswith("_")} for e in self.events],
            "recent_lines": [list(x) for x in list(self.ring)[-lines:]] if lines else [],
        }
        if pending is not None:
            out["offline"] = {"since": pending["at"], "for_s": round(now - float(pending["down"]), 1),
                              "reconnected": pending.get("reconnected") is not None}
        return out

    def summary(self) -> dict[str, Any]:
        last = self.events[0] if self.events else None
        return {"name": self.name, "connected": self._client is not None, "has_debug": self.has_debug,
                "log_level": self.log_level, "counts_24h": self.counts(86400),
                "last": {k: last.get(k) for k in ("at", "kind", "summary", "cause")} if last else None}


def _fit(before: list[list[str]], after: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    """Keep an event's lines under :data:`EVENT_CHARS_MAX`: the oldest lines before the drop go first."""
    def size(rows: list[list[str]]) -> int:
        return sum(len(r[2]) + 30 for r in rows)
    while before and size(before) + size(after) > EVENT_CHARS_MAX:
        before.pop(0)
    while after and size(after) > EVENT_CHARS_MAX:
        after.pop()
    return before, after


def _dur(seconds: Any) -> str:
    if seconds is None:
        return "?"
    s = float(seconds)
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    if s < 172800:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} days"


def context_text(ctx: dict[str, Any]) -> list[str]:
    """The context in words, for the event list and the hub's log."""
    out = []
    v = ctx.get("voice_run")
    if v:
        if v.get("running"):
            out.append(f"A voice run was in progress (at {v.get('stage')}, started {_dur(v.get('started_s_before'))} "
                       "before)")
        else:
            outcome = f" ({v['outcome']})" if v.get("outcome") else ""
            out.append(f"A voice run ended {_dur(v.get('ended_s_before'))} before{outcome}")
    for key, label in (("announcement", "An announcement"), ("firmware_install", "A firmware install")):
        a = ctx.get(key)
        if a:
            out.append(f"{label} was playing (started {_dur(a.get('started_s_before'))} before)"
                       if a.get("running") and key == "announcement"
                       else f"{label} was running" if a.get("running")
                       else f"{label} ended {_dur(a.get('ended_s_before'))} before")
    for m in ctx.get("media") or []:
        if m.get("state") in ("playing", "buffering", "paused", "announcing"):
            since = f" for {_dur(m['since_s'])}" if m.get("since_s") is not None else ""
            out.append(f"{m['entity_id']} was {m['state']}{since}")
    if ctx.get("firmware_update"):
        out.append("A firmware update was in progress")
    return out


def monitors(hub: Any) -> list[RestartMonitor]:
    return [m for d in list(hub.drivers.values()) if isinstance(m := getattr(d, "restarts", None), RestartMonitor)]


def health_summary(hub: Any) -> dict[str, Any]:
    """For ``/health``: how many ESPHome nodes are watched and what happened to them in the last day."""
    mons = monitors(hub)
    totals = {"restart": 0, "drop": 0, "unknown": 0}
    last: dict[str, Any] | None = None
    for m in mons:
        for k, v in m.counts(86400).items():
            totals[k] += v
        if m.events and (last is None or str(m.events[0].get("at")) > str(last["at"])):
            ev = m.events[0]
            last = {"device": m.title, "at": ev.get("at"), "kind": ev.get("kind"), "summary": ev.get("summary")}
    return {"watched": len(mons), "logs_subscribed": sum(1 for m in mons if m.subscribed),
            "without_debug": sum(1 for m in mons if m.connected_at is not None and not m.has_debug),
            "last_24h": totals, "last": last}


__all__ = ["DEBUG_YAML", "LOG_LEVELS", "OPTION", "RestartMonitor", "clean_line", "context_text", "health_summary",
           "monitors", "sensor_role"]
