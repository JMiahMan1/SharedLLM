"""Says so when the hub's event loop, or a thread pool it leans on, stops keeping up.

Everything the hub does runs on one asyncio loop, and two thread pools sit behind it:

- the **default executor** (``asyncio.to_thread``: SQLite history queries, blocking libraries);
- under uvloop, which uvicorn runs the hub on, **libuv's pool** (four threads unless
  ``UV_THREADPOOL_SIZE`` says otherwise). uvloop sends every ``getaddrinfo`` there, and also the
  address of every ``create_datagram_endpoint(remote_addr=...)``, numeric or not.

A stall in any of them looks the same from a driver: a request that should take milliseconds times out,
and the device is called unreachable. On 2026-11-18 WiZ bulbs flicked to unavailable every minute; the
loop itself was fine (``/health`` never took 0.4 s in 7 minutes), but libuv's pool was busy for 4-8 s at
a time with DNS lookups of offline ESPHome nodes' bare names, and opening a socket to a bulb queued behind
them. Nothing in the log said so. This monitor would have.

Three checks, all cheap:

- a **heartbeat** task sleeps ``beat_s`` and measures how late it wakes; a watchdog thread notices while
  the loop is still stuck and captures the loop thread's stack, so the warning names what was running;
- every ``probe_every_s`` it times one no-op job on **each pool** and warns when the job waited.

Warnings are rate-limited (one per kind per ``report_every_s``, with a count of the ones held back).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

log = logging.getLogger("homehub.loop")

#: A loop that wakes this late (seconds) has stalled.
STALL_S = 0.5
#: A pool job that waits this long before running means the pool is saturated.
POOL_WARN_S = 1.0
#: How often the heartbeat runs, and how often the pools are probed.
BEAT_S = 0.25
PROBE_EVERY_S = 30.0
#: A probe gives up (and reports) after this long.
PROBE_TIMEOUT_S = 15.0
#: At most one warning per kind in this many seconds.
REPORT_EVERY_S = 60.0
#: Frames of the loop thread's stack kept in a stall warning.
STACK_FRAMES = 14


def _noop() -> None:
    return None


def loop_kind(loop: asyncio.AbstractEventLoop) -> str:
    mod = type(loop).__module__
    return "uvloop" if mod.startswith("uvloop") else "asyncio"


class LoopMonitor:
    def __init__(self, *, stall_s: float = STALL_S, pool_warn_s: float = POOL_WARN_S, beat_s: float = BEAT_S,
                 probe_every_s: float = PROBE_EVERY_S, report_every_s: float = REPORT_EVERY_S,
                 probe_timeout_s: float = PROBE_TIMEOUT_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.stall_s = stall_s
        self.pool_warn_s = pool_warn_s
        self.beat_s = beat_s
        self.probe_every_s = probe_every_s
        self.report_every_s = report_every_s
        self.probe_timeout_s = probe_timeout_s
        self.clock = clock
        self._beat = clock()
        self._loop_thread: int | None = None
        self._stall_stack: str | None = None
        self._stop = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._last_report: dict[str, float] = {}
        self._held_back: dict[str, int] = {}
        self.stats: dict[str, Any] = {"stalls": 0, "worst_stall_s": 0.0, "last_stall": None,
                                      "pool_waits": 0, "pools": {}}

    # -- reporting ---------------------------------------------------------------------------------
    def _report(self, kind: str, msg: str, *args: Any) -> bool:
        now = self.clock()
        last = self._last_report.get(kind)
        if last is not None and now - last < self.report_every_s:
            self._held_back[kind] = self._held_back.get(kind, 0) + 1
            return False
        held = self._held_back.pop(kind, 0)
        self._last_report[kind] = now
        if held:
            msg += " (%d more like it in the last %.0fs not logged)"
            args = (*args, held, self.report_every_s)
        log.warning(msg, *args)
        return True

    # -- the loop ----------------------------------------------------------------------------------
    def _capture_stack(self) -> str | None:
        if self._loop_thread is None:
            return None
        frame = sys._current_frames().get(self._loop_thread)
        if frame is None:
            return None
        return "".join(traceback.format_stack(frame)[-STACK_FRAMES:]).rstrip()

    def _watch(self) -> None:
        """Thread: while the loop is stuck, grab what it is running (once per stall)."""
        while not self._stop.wait(self.beat_s / 2):
            if self.clock() - self._beat > self.stall_s + self.beat_s and self._stall_stack is None:
                self._stall_stack = self._capture_stack() or "(stack not available)"

    def stalled(self, lag: float) -> None:
        """The heartbeat woke ``lag`` seconds late."""
        stack = self._stall_stack
        self._stall_stack = None
        self.stats["stalls"] += 1
        self.stats["worst_stall_s"] = max(self.stats["worst_stall_s"], round(lag, 3))
        self.stats["last_stall"] = {"at": time.time(), "seconds": round(lag, 3)}
        self._report("stall", "event loop stalled %.2fs (nothing else ran meanwhile: device requests time out); "
                     "it was running:\n%s", lag, stack or "(it ended before the watchdog looked)")

    async def _heartbeat(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            self._beat = self.clock()
            expected = loop.time() + self.beat_s
            await asyncio.sleep(self.beat_s)
            lag = loop.time() - expected
            self._beat = self.clock()
            if lag > self.stall_s:
                self.stalled(lag)
            else:
                self._stall_stack = None

    # -- the pools ---------------------------------------------------------------------------------
    async def _time(self, job: Callable[[], Any]) -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            await asyncio.wait_for(job(), self.probe_timeout_s)
        except TimeoutError:
            return float("inf")
        return loop.time() - started

    async def _executor_job(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, _noop)

    async def _resolver_job(self) -> None:
        """One libuv pool job under uvloop (it resolves ``remote_addr`` there); a few microseconds otherwise."""
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=("127.0.0.1", 9))
        transport.close()

    async def probe_pools(self) -> dict[str, float]:
        kind = loop_kind(asyncio.get_running_loop())
        results = {"executor": await self._time(self._executor_job),
                   "resolver": await self._time(self._resolver_job)}
        self.stats["pools"] = {k: (None if v == float("inf") else round(v, 3)) for k, v in results.items()}
        for pool, waited in results.items():
            if waited <= self.pool_warn_s:
                continue
            self.stats["pool_waits"] += 1
            said = f"more than {self.probe_timeout_s:.0f}" if waited == float("inf") else f"{waited:.2f}"
            if pool == "resolver":
                self._report("resolver", "the %s resolver pool took %ss to run a no-op job: DNS lookups are queued "
                             "(slow names?), and so is opening every UDP socket that names its address", kind, said)
            else:
                self._report("executor", "the default thread pool took %ss to start a no-op job: to_thread work "
                             "(database queries, blocking libraries) is queued", said)
        return results

    async def _probe_loop(self) -> None:
        while True:
            await asyncio.sleep(self.probe_every_s)
            try:
                await self.probe_pools()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a probe that fails is itself worth one line
                self._report("probe", "loop monitor probe failed: %s: %s", type(exc).__name__, exc)

    # -- lifecycle ---------------------------------------------------------------------------------
    async def run(self) -> None:
        """Run until cancelled (main.py starts it as a background task)."""
        self._loop_thread = threading.get_ident()
        self._beat = self.clock()
        self._stop.clear()
        self._watchdog = threading.Thread(target=self._watch, name="homehub-loop-watchdog", daemon=True)
        self._watchdog.start()
        log.info("loop monitor: %s loop, warns at a %.1fs stall or a %.1fs pool wait",
                 loop_kind(asyncio.get_running_loop()), self.stall_s, self.pool_warn_s)
        try:
            await asyncio.gather(self._heartbeat(), self._probe_loop())
        finally:
            self._stop.set()

    def snapshot(self) -> dict[str, Any]:
        return {"stalls": self.stats["stalls"], "worst_stall_s": self.stats["worst_stall_s"],
                "last_stall": self.stats["last_stall"], "pool_waits": self.stats["pool_waits"],
                "pools_s": dict(self.stats["pools"])}

