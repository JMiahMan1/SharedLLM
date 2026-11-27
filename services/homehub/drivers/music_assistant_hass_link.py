"""Bring Music Assistant's Home Assistant link back after HomeHub restarts.

HomeHub plays Home Assistant's part for Music Assistant: MA's ``hass`` plugin
provider (instance ``hass--XXXX``) connects to HomeHub's HA-compatible
WebSocket. When HomeHub restarts, MA logs "Connection to HA lost", tries once
while HomeHub is still starting, fails with "Cannot connect to host", and never
tries again: ``config/providers`` shows the provider with a ``last_error``
("Setup failed...") until someone sends ``config/providers/reload`` for it.
While it is down MA cannot pair Sendspin satellites through HomeHub and
HomeHub's media players vanish from MA.

:class:`HassLinkWatch` checks for that and reloads only a failed ``hass``
provider: on every (re)connect of the Music Assistant driver, and once a
minute for the first :data:`STARTUP_WINDOW_S` seconds after the driver starts.
A healthy provider is never reloaded (that would drop MA's live HA connection
for nothing), and one instance is reloaded at most once per
:data:`MIN_RELOAD_GAP_S`, so a reload that fails is retried on a later pass.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

#: How long after start the watch keeps checking on its own.
STARTUP_WINDOW_S = 600.0
#: Seconds between the watch's own passes inside the startup window.
PASS_INTERVAL_S = 60.0
#: Least seconds between two reloads of one provider instance.
MIN_RELOAD_GAP_S = 60.0
HASS_DOMAIN = "hass"

Command = Callable[..., Awaitable[Any]]


def _error_text(err: Any) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("error") or err)
    return str(err)


def _down_reason(cfg: dict[str, Any], loaded: dict[str, dict[str, Any]] | None) -> str | None:
    """Why this ``hass`` provider config is down, or None when it is healthy (or disabled)."""
    if cfg.get("enabled") is False:
        return None
    if cfg.get("last_error"):
        return _error_text(cfg["last_error"])
    for key in ("loaded", "available"):
        if cfg.get(key) is False:
            return f"not {key}"
    if loaded is not None:
        inst = loaded.get(str(cfg.get("instance_id")))
        if inst is None:
            return "not loaded"
        if inst.get("available") is False:
            return "not available"
    return None


class HassLinkWatch:
    """Reload a failed ``hass`` provider in Music Assistant, never a healthy one."""

    def __init__(self, command: Command, log: logging.Logger, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.command = command
        self.log = log
        self.clock = clock
        self.started_at = clock()
        self.last_pass: float | None = None
        #: instance_id -> clock() of the last reload attempt.
        self.last_reload: dict[str, float] = {}
        self.task: asyncio.Task | None = None

    def due(self) -> bool:
        """True while inside the startup window and a pass is owed."""
        now = self.clock()
        if now - self.started_at > STARTUP_WINDOW_S:
            return False
        return self.last_pass is None or now - self.last_pass >= PASS_INTERVAL_S

    def kick(self, *, reconnected: bool = False) -> None:
        """Start a pass in the background if one is owed (always on a reconnect) and none is running."""
        if self.task is not None and not self.task.done():
            if not reconnected:
                return
            self.task.cancel()  # it was talking to the connection that just closed
        if not reconnected and not self.due():
            return
        self.last_pass = self.clock()
        self.task = asyncio.create_task(self.check())

    async def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task
        self.task = None

    async def _loaded(self) -> dict[str, dict[str, Any]] | None:
        """MA's loaded provider instances by instance_id, or None when that listing is not usable."""
        try:
            rows = await self.command("providers")
        except Exception:  # optional extra signal only
            return None
        if not isinstance(rows, list) or not rows:
            return None
        return {str(r.get("instance_id")): r for r in rows if isinstance(r, dict) and r.get("instance_id")}

    async def check(self) -> list[str]:
        """One pass: reload every failed ``hass`` provider whose last reload is old enough. Returns those reloaded."""
        self.last_pass = self.clock()
        try:
            configs = await self.command("config/providers")
        except Exception as exc:  # a failed look is retried on the next pass
            self.log.warning("Could not read Music Assistant's providers to check its Home Assistant link: %s", exc)
            return []
        hass = [c for c in (configs or []) if isinstance(c, dict) and c.get("domain") == HASS_DOMAIN
                and c.get("instance_id")]
        if not hass:
            return []
        loaded = await self._loaded()
        reloaded: list[str] = []
        for cfg in hass:
            reason = _down_reason(cfg, loaded)
            if reason is None:
                continue
            instance_id = str(cfg["instance_id"])
            now = self.clock()
            last = self.last_reload.get(instance_id)
            if last is not None and now - last < MIN_RELOAD_GAP_S:
                continue
            self.last_reload[instance_id] = now
            try:
                await self.command("config/providers/reload", instance_id=instance_id)
            except Exception as exc:  # retried on the next pass
                self.log.warning("Music Assistant's Home Assistant link %s is down (%s) and its reload failed: %s",
                                 instance_id, reason, exc)
                continue
            self.log.info("Music Assistant's Home Assistant link was down (%s); reloaded it", reason)
            reloaded.append(instance_id)
        return reloaded
