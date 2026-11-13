"""Loading the store into a hub and keeping drivers running."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from services.homehub.core import UNAVAILABLE, ConfigEntry, Driver, Hub
from services.homehub.drivers import driver_class, replaced_reason
from services.homehub.store import Store

log = logging.getLogger("homehub.runtime")

# Capped at a minute: devices on a switched outlet or wall switch come back
# often, and waiting five minutes for one to show again felt broken.
RETRY_DELAYS = (5, 10, 30, 60)
HISTORY_KEEP_DAYS = 30


class Runtime:
    """Owns the hub, the store and one supervising task per config entry."""

    def __init__(self, hub: Hub, store: Store) -> None:
        self.hub = hub
        self.store = store
        self._tasks: dict[str, asyncio.Task] = {}
        #: Set once an entry's first setup attempt has finished, loaded or not.
        self._first_attempt: dict[str, asyncio.Event] = {}
        self._unsub_record = None
        self.automations: Any = None  # set by main once the engine is loaded

    def load_registry(self) -> None:
        hub = self.hub
        hub.config_entries = {e.entry_id: e for e in self.store.load_config_entries()}
        hub.devices = {d.id: d for d in self.store.load_devices()}
        hub.entities = {e.entity_id: e for e in self.store.load_entities()}
        hub.areas = {a.area_id: a for a in self.store.load_areas()}
        hub.floors = self.store.get_kv("floors", {}) or {}
        cfg = self.store.get_kv("core_config", {}) or {}
        if cfg:
            hub.location = cfg
            # The zone is the household's (household_tz), not the imported
            # Home Assistant config's: one setting governs Jarvis and the hub.
            imported = cfg.get("time_zone")
            if imported and imported != hub.time_zone:
                log.info("homehub: imported config says time zone %s; using the household zone %s", imported, hub.time_zone)
        # Last known states come back first so readers see something while
        # drivers connect; anything a driver cannot reach turns unavailable.
        for st in self.store.load_last_states():
            if st.entity_id in hub.entities:
                hub.states.set(st.entity_id, st.state, st.attributes, restore=st)

    def start_recording(self) -> None:
        def on_change(_t: str, ev: dict[str, Any]) -> None:
            new, old = ev.get("new_state"), ev.get("old_state")
            if new is None:
                return
            try:
                self.store.save_last_state(new)
                self.store.record(new, state_changed=old is None or old.state != new.state)
            except Exception:
                log.exception("recording %s failed", new.entity_id)

        self._unsub_record = self.hub.bus.subscribe(on_change, "state_changed")

    async def start_all(self) -> None:
        for entry in list(self.hub.config_entries.values()):
            if entry.disabled_by or entry.source == "ignore":
                continue
            self.start_entry(entry)

    def start_entry(self, entry: ConfigEntry) -> None:
        old = self._tasks.pop(entry.entry_id, None)
        if old is not None:
            old.cancel()
        self._first_attempt.setdefault(entry.entry_id, asyncio.Event())
        self._tasks[entry.entry_id] = asyncio.create_task(self._supervise(entry), name=f"entry:{entry.domain}")

    async def stop_entry(self, entry_id: str) -> None:
        task = self._tasks.pop(entry_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        driver = self.hub.drivers.pop(entry_id, None)
        if driver is not None:
            with contextlib.suppress(Exception):
                await driver.async_unload()

    async def _supervise(self, entry: ConfigEntry) -> None:
        first = self._first_attempt.setdefault(entry.entry_id, asyncio.Event())
        replaced = replaced_reason(entry.domain, entry.data)
        if replaced:
            entry.state, entry.reason = "not_loaded", replaced
            first.set()
            return
        cls = driver_class(entry.domain)
        if cls is None:
            entry.state, entry.reason = "not_loaded", "no driver for this integration yet"
            first.set()
            return
        attempt = 0
        while True:
            driver: Driver = cls(self.hub, entry)
            try:
                await driver.async_setup()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                attempt += 1
                entry.state, entry.reason = "setup_retry", f"{type(exc).__name__}: {exc}"
                log.warning("%s (%s) setup failed, retry in %ss: %s", entry.domain, entry.title, delay, exc)
                driver.mark_unavailable()
                with contextlib.suppress(Exception):
                    await driver.async_unload()
                first.set()
                await asyncio.sleep(delay)
                continue
            self.hub.drivers[entry.entry_id] = driver
            entry.state, entry.reason = "loaded", None
            first.set()
            attempt = 0
            log.info("%s (%s) loaded with %d entities", entry.domain, entry.title, len(driver.entities))
            if driver.poll_interval is None:
                await asyncio.Event().wait()  # push-only: live until cancelled
            while True:
                await asyncio.sleep(driver.poll_interval or 30.0)
                try:
                    await driver.async_poll()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("%s (%s) poll failed: %s", entry.domain, entry.title, exc)

    async def wait_first_setups(self, timeout: float) -> list[str]:
        """Wait until every started entry has tried to load once; returns the ones still pending.

        Automations and the scheduler start after this: HA starts them once
        its integrations are up, and a schedule replayed at startup must reach
        a loaded thermostat driver, not "no driver is running".
        """
        events = {eid: ev for eid, ev in self._first_attempt.items() if eid in self._tasks}
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(*(ev.wait() for ev in events.values())), timeout)
        return [eid for eid, ev in events.items() if not ev.is_set()]

    async def housekeeping(self) -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                n = await asyncio.to_thread(self.store.prune_history, HISTORY_KEEP_DAYS)
                if n:
                    log.info("pruned %d history rows", n)
            except Exception:
                log.exception("history prune failed")

    async def shutdown(self) -> None:
        for entry_id in list(self._tasks):
            await self.stop_entry(entry_id)
        if self._unsub_record:
            self._unsub_record()

    def entry_status(self) -> list[dict[str, Any]]:
        out = []
        for e in self.hub.config_entries.values():
            ents = [x for x in self.hub.entities.values() if x.config_entry_id == e.entry_id and not x.disabled_by]
            unavailable = sum(1 for x in ents if (s := self.hub.states.get(x.entity_id)) is None or s.state == UNAVAILABLE)
            out.append({"entry_id": e.entry_id, "domain": e.domain, "title": e.title, "state": e.state,
                        "reason": e.reason, "source": e.source, "entities": len(ents), "unavailable": unavailable})
        return sorted(out, key=lambda r: (r["domain"], r["title"]))
