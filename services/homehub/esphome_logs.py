"""Live logs of an ESPHome node: Devices > a device > Logs (what Home Assistant's ESPHome dashboard "Logs" shows).

Every node's :class:`~services.homehub.esphome_restarts.RestartMonitor` owns one :class:`DeviceLogs`. It is fed by
the monitor's log subscription on the hub's own API connection to the node; no viewer ever opens a connection of
its own. Many viewers fan out from that one subscription.

**The buffer** keeps the node's last :data:`BUFFER_LINES` lines, each with a sequence number, the time the hub
received it, its level, the ESPHome tag (``[D][sensor:093]`` gives ``sensor``) and the message. Colour codes are
stripped and anything that looks like a credential is redacted (``esphome_restarts.redact``) before a line is kept,
so nothing secret reaches the buffer, the API or a download. Lines are rate-limited (:data:`RATE_LINES_PER_S`) so a
node logging at ``verbose`` cannot flood the hub; the ones over the limit are counted.

**Markers** go into the same stream: the node going offline, coming back, and the restart monitor's verdict on the
drop ("Restarted (Power on), 2 s offline"), so a reader sees why the log has a gap.

**The subscription level** is the highest of the node's configured restart-monitor level (entry option
``restart_log_level``) and the level of every viewer watching now; while nobody watches it drops back to the
configured level. A config dump request (:meth:`RestartMonitor.request_dump_config`) resubscribes with
``dump_config=True`` and holds at least the ``config`` level for :data:`DUMP_HOLD_S` so the dump is sent.

Routes are in ``esphome_logs_api.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

from services.homehub.core import utcnow

#: Lines kept per node.
BUFFER_LINES = 2000
#: A longer line is cut.
LINE_MAX_CHARS = 2000
#: Lines a node may send per second into the buffer (with a burst of :data:`RATE_BURST`).
RATE_LINES_PER_S = 200.0
RATE_BURST = 400
#: Items one viewer may have waiting; more are counted and reported as skipped.
QUEUE_MAX = 1000
#: A stream with nothing to send says it is alive this often.
HEARTBEAT_S = 15.0
#: An HTTP stream ends after this long; the client reopens it from the last sequence number.
STREAM_MAX_S = 1800.0
#: After a config dump request the subscription stays at least at ``config`` level this long.
DUMP_HOLD_S = 15.0

#: The levels a viewer can choose -> the aioesphomeapi ``LogLevel`` number. ``info`` includes ESPHome's ``config``
#: level, so the config dump shows with it.
VIEW_LEVELS = {"error": 1, "warning": 2, "info": 4, "debug": 5, "verbose": 6}
DEFAULT_VIEW_LEVEL = "debug"
CONFIG_LEVEL = 4
LEVEL_NAMES = {1: "error", 2: "warning", 3: "info", 4: "config", 5: "debug", 6: "verbose", 7: "very_verbose"}
_ALIASES = {"warn": "warning", "err": "error", "very_verbose": "verbose"}

#: ``[12:34:56][D][sensor:093]: text`` or ``[D][sensor:093]: text`` (the time is added by some tools, not the node).
_PREFIX = re.compile(r"^(?:\[\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\])?\[(?:VV|[EWICDV])\]\[([^\]\s:]+)(?::\d+)?\]"
                     r"(?:\[[^\]]*\])?:?\s?(.*)$", re.DOTALL)


def view_level(raw: Any, default: str = DEFAULT_VIEW_LEVEL) -> str | None:
    """A viewer's level name, or None when it is not one (``warn`` is ``warning``)."""
    if raw is None or raw == "":
        return default
    name = str(raw).strip().lower()
    name = _ALIASES.get(name, name)
    return name if name in VIEW_LEVELS else None


def parse(text: str) -> tuple[str, str]:
    """``(tag, message)`` of a cleaned ESPHome line; no tag when it does not have ESPHome's prefix."""
    m = _PREFIX.match(text)
    if m is None:
        return "", text
    return m.group(1), m.group(2)


class LogViewer:
    """One reader of a node's logs (an HTTP stream or a WebSocket subscription)."""

    def __init__(self, level: str) -> None:
        self.level = level
        self.max = VIEW_LEVELS[level]
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=QUEUE_MAX)
        self.skipped = 0

    def offer(self, item: dict[str, Any] | None) -> None:
        if item is not None and item["type"] == "line" and item["_lvl"] > self.max:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            if item is None:  # the end must arrive: make room for it
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()
                self.queue.put_nowait(None)
            else:
                self.skipped += 1


def public(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if not k.startswith("_")}


class DeviceLogs:
    """The last lines of one node and the people watching them."""

    def __init__(self, on_level_change: Callable[[], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.on_level_change = on_level_change
        self.clock = clock
        self.lines: deque[dict[str, Any]] = deque(maxlen=BUFFER_LINES)
        self.seq = 0
        self.dropped = 0
        self.viewers: set[LogViewer] = set()
        self.dump_until = 0.0
        self._tokens = float(RATE_BURST)
        self._tokens_at = clock()

    # -- what comes in ---------------------------------------------------------------------
    def add_line(self, level: int, raw: Any) -> dict[str, Any] | None:
        from services.homehub.esphome_restarts import clean_line

        now = self.clock()
        self._tokens = min(float(RATE_BURST), self._tokens + (now - self._tokens_at) * RATE_LINES_PER_S)
        self._tokens_at = now
        if self._tokens < 1.0:
            self.dropped += 1
            return None
        self._tokens -= 1.0
        text = clean_line(raw, max_chars=LINE_MAX_CHARS)
        if not text:
            return None
        tag, message = parse(text)
        lvl = int(level or 0)
        return self._add({"type": "line", "level": LEVEL_NAMES.get(lvl, "info"), "tag": tag, "message": message,
                          "_lvl": lvl})

    def add_marker(self, event: str, message: str, **extra: Any) -> dict[str, Any]:
        return self._add({"type": "marker", "event": event, "message": message, **extra})

    def _add(self, item: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        item = {"seq": self.seq, "at": utcnow().isoformat(timespec="milliseconds"), **item}
        self.lines.append(item)
        for v in list(self.viewers):
            v.offer(item)
        return item

    # -- what goes out ---------------------------------------------------------------------
    def history(self, since: int | None = None, level: str = DEFAULT_VIEW_LEVEL,
                limit: int = BUFFER_LINES) -> list[dict[str, Any]]:
        """Lines and markers after ``since`` at or above ``level`` (the newest ``limit``), oldest first."""
        cap = VIEW_LEVELS[level]
        after = int(since) if since is not None else 0
        rows = [i for i in self.lines if i["seq"] > after and (i["type"] != "line" or i["_lvl"] <= cap)]
        return [public(i) for i in rows[-max(0, limit):]] if limit else []

    def wanted_level(self) -> int:
        """The level the viewers need from the node now (0: nobody is watching)."""
        want = max((v.max for v in self.viewers), default=0)
        if self.clock() < self.dump_until:
            want = max(want, CONFIG_LEVEL)
        return want

    def open_viewer(self, level: str) -> LogViewer:
        viewer = LogViewer(level)
        before = self.wanted_level()
        self.viewers.add(viewer)
        if self.wanted_level() != before:
            self._changed()
        return viewer

    def close_viewer(self, viewer: LogViewer) -> None:
        if viewer not in self.viewers:
            return
        before = self.wanted_level()
        self.viewers.discard(viewer)
        if self.wanted_level() != before:
            self._changed()

    def _changed(self) -> None:
        if self.on_level_change is not None:
            self.on_level_change()

    def close(self) -> None:
        """The node is gone from the hub: every stream ends."""
        for v in list(self.viewers):
            v.offer(None)

    async def stream(self, level: str, *, since: int | None = None, max_s: float | None = STREAM_MAX_S,
                     heartbeat_s: float | None = HEARTBEAT_S,
                     hello: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        """The lines after ``since`` (all kept ones when None), then each new one as it arrives.

        While this runs the viewer counts towards the node's subscription level; when it ends (the reader went
        away, ``max_s`` passed, or the node left the hub) the level drops back.
        """
        viewer = self.open_viewer(level)
        try:
            if hello is not None:
                yield {"type": "hello", "level": level, "seq": self.seq, **hello}
            for item in self.history(since, level):
                yield item
            deadline = None if max_s is None else self.clock() + max_s
            while True:
                wait = heartbeat_s
                if deadline is not None:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        yield {"type": "end", "reason": "time", "seq": self.seq}
                        return
                    wait = remaining if wait is None else min(wait, remaining)
                try:
                    item = await asyncio.wait_for(viewer.queue.get(), wait)
                except TimeoutError:
                    if heartbeat_s is not None and (deadline is None or self.clock() < deadline):
                        yield {"type": "ping", "seq": self.seq}
                    continue
                if viewer.skipped:
                    n, viewer.skipped = viewer.skipped, 0
                    yield {"type": "marker", "event": "skipped", "seq": None, "at": utcnow().isoformat(),
                           "message": f"{n} lines came faster than this view could show and were skipped"}
                if item is None:
                    yield {"type": "end", "reason": "removed", "seq": self.seq}
                    return
                yield public(item)
        finally:
            self.close_viewer(viewer)

    def status(self) -> dict[str, Any]:
        return {"buffered": len(self.lines), "max": BUFFER_LINES, "dropped": self.dropped, "viewers": len(self.viewers),
                "seq": self.seq}


def text_line(item: dict[str, Any]) -> str:
    """One item as a line of a downloaded log."""
    if item.get("type") == "marker":
        return f"{item.get('at', '')} ---- {item.get('message', '')} ----"
    tag = f"[{item['tag']}] " if item.get("tag") else ""
    return f"{item.get('at', '')} [{str(item.get('level', '')).upper()}] {tag}{item.get('message', '')}"


__all__ = ["BUFFER_LINES", "DEFAULT_VIEW_LEVEL", "VIEW_LEVELS", "DeviceLogs", "LogViewer", "parse", "text_line",
           "view_level"]
