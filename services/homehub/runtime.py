"""Loading the store into a hub and keeping drivers running."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any

from services.homehub.core import UNAVAILABLE, AuthRequired, ConfigEntry, Driver, Hub, SetupError, utcnow
from services.homehub.drivers import driver_class, replaced_reason
from services.homehub.store import Store

log = logging.getLogger("homehub.runtime")

# Capped at a minute to start with: devices on a switched outlet or wall switch come back
# often, and waiting five minutes for one to show again felt broken. An entry that keeps
# failing then doubles its wait, up to RETRY_MAX_S, so six unplugged gadgets do not write
# thousands of warnings a day.
RETRY_DELAYS = (5, 10, 30, 60)
#: The longest wait between setup attempts for one entry.
RETRY_MAX_S = 900.0
# A device that stays off is retried quietly after its first warning, with one INFO
# summary an hour: seven unplugged gadgets were writing 2,500 warnings in 12 hours.
RETRY_REPORT_EVERY_S = 3600.0
HISTORY_KEEP_DAYS = 30
#: A poll that has not answered in this long has hung (a driver may set its own ``poll_timeout``).
POLL_TIMEOUT_S = 60.0
#: After this many failed polls in a row the entry's entities turn unavailable, as in HA, and the entry
#: carries the failure (``setup_retry`` with the reason) until a poll works again. One failed poll is often
#: a dropped packet; the entities keep their last state through it.
POLL_FAILURES_UNAVAILABLE = 3


def retry_delay(attempt: int) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based).

    The fast sequence first (a device switched back on should return in seconds), then
    doubling from its last step up to :data:`RETRY_MAX_S`.
    """
    if attempt < len(RETRY_DELAYS):
        return float(RETRY_DELAYS[attempt])
    return min(float(RETRY_DELAYS[-1]) * 2 ** (attempt - len(RETRY_DELAYS) + 1), RETRY_MAX_S)


def _minutes_text(minutes: int) -> str:
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _outage_text(since: datetime | None, now: datetime) -> str:
    """``"5 minutes"`` for a recovery line; a short blip still reads as a minute."""
    seconds = max(0.0, (now - since).total_seconds()) if since is not None else 0.0
    return _minutes_text(max(1, round(seconds / 60)))


class Runtime:
    """Owns the hub, the store and one supervising task per config entry."""

    def __init__(self, hub: Hub, store: Store) -> None:
        self.hub = hub
        self.store = store
        self._tasks: dict[str, asyncio.Task] = {}
        #: Set once an entry's first setup attempt has finished, loaded or not.
        self._first_attempt: dict[str, asyncio.Event] = {}
        #: When each entry that is failing now first failed (this run of the hub).
        self.failing_since: dict[str, datetime] = {}
        #: When the next setup attempt for each failing entry is due (this run of the hub).
        self.next_retry: dict[str, datetime] = {}
        self._unsub_record = None
        self.recorder: Any = None  # recorder.Recorder, from start_recording
        self.automations: Any = None  # set by main once the engine is loaded
        self.helpers: Any = None  # HA's helpers (helpers.py), set by main
        self.loop_monitor: Any = None  # loop_monitor.LoopMonitor, set by main
        self.provider_configs: dict[str, Any] = {}  # login providers the stack configures (main)
        self.provider_sync_every_s: float = 60.0
        self.auth_providers: Any = None  # auth_providers.manager.ProviderManager (ha_auth_api)
        self.archive: Any = None  # device_archive.DeviceArchive, set by main

    def load_registry(self) -> None:
        hub = self.hub
        hub.config_entries = {e.entry_id: e for e in self.store.load_config_entries()}
        # Music Assistant's address and login live in its entry's data only (music_assistant_server.py).
        from services.homehub import music_assistant_server

        music_assistant_server.fold_all(hub, self.store)
        hub.devices = {d.id: d for d in self.store.load_devices()}
        hub.entities = {e.entity_id: e for e in self.store.load_entities()}
        hub.areas = {a.area_id: a for a in self.store.load_areas()}
        # A hidden device's entities follow it, whatever registered them since (a resync, an import).
        hub.reconcile_hidden()
        # Combined duplicates (one device, a main and a fallback) are in place before any driver publishes.
        from services.homehub import duplicates

        duplicates.load_combinations(self)
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

    def start_recording(self, *, threaded: bool | None = None) -> None:
        """History and restore states (recorder.py): a writer thread commits them in batches off the loop."""
        from services.homehub import recorder

        if self.recorder is not None:
            self.recorder.stop()
        self.recorder = recorder.Recorder(self.hub, self.store, threaded=threaded)
        self.recorder.start()
        recorder.register_services(self.hub, self.recorder)
        self._unsub_record = self.recorder.detach

    async def start_all(self) -> None:
        for entry in list(self.hub.config_entries.values()):
            if entry.disabled_by or entry.source == "ignore":
                continue
            self.start_entry(entry)

    def start_entry(self, entry: ConfigEntry) -> None:
        old = self._tasks.pop(entry.entry_id, None)
        if old is not None:
            old.cancel()
        # A reload or a change to the entry is a fresh start: the next failure waits
        # the first, short interval again.
        self.next_retry.pop(entry.entry_id, None)
        if entry.archived_at:
            # Archived for going without contact (device_archive.py): never set up or retried until it is
            # restored (rediscovered, or an admin's Restore), which clears the mark first.
            entry.state, entry.reason, entry.needs = "not_loaded", f"Archived on {entry.archived_at[:10]}", None
            return
        self._first_attempt.setdefault(entry.entry_id, asyncio.Event())
        self._tasks[entry.entry_id] = asyncio.create_task(self._supervise(entry), name=f"entry:{entry.domain}")

    async def stop_entry(self, entry_id: str) -> None:
        task = self._tasks.pop(entry_id, None)
        self.failing_since.pop(entry_id, None)
        self.next_retry.pop(entry_id, None)
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
        reported: tuple[str, float] | None = None  # (reason, when) last logged as a warning
        loop = asyncio.get_running_loop()
        while True:
            try:
                driver: Driver = cls(self.hub, entry)
            except Exception as exc:
                # The entry's own settings are unusable (a constructor checks them): retrying cannot help.
                entry.state, entry.reason, entry.needs = "setup_error", f"{type(exc).__name__}: {exc}", None
                self.failing_since.setdefault(entry.entry_id, utcnow())
                log.warning("%s (%s) cannot be set up and will not be retried until it is changed: %s",
                            entry.domain, entry.title, entry.reason)
                first.set()
                return
            try:
                await driver.async_setup()
            except asyncio.CancelledError:
                raise
            except (AuthRequired, SetupError) as exc:
                await self._park(entry, driver, exc)
                first.set()
                return
            except Exception as exc:
                delay = retry_delay(attempt)
                attempt += 1
                entry.state, entry.reason = "setup_retry", f"{type(exc).__name__}: {exc}"
                now = loop.time()
                # The same outage reads differently from try to try (a timeout has
                # no text, a refusal names the socket): compare its headline only.
                headline = f"{type(exc).__name__}: {str(exc).split(':', 1)[0]}"
                since = self.failing_since.setdefault(entry.entry_id, utcnow())
                self.next_retry[entry.entry_id] = utcnow() + timedelta(seconds=delay)
                if reported is None or reported[0] != headline:
                    reported = (headline, now)
                    log.warning("%s (%s) setup failed (attempt %d), retry in %ss: %s",
                                entry.domain, entry.title, attempt, delay, exc)
                elif now - reported[1] >= RETRY_REPORT_EVERY_S:
                    reported = (headline, now)
                    minutes = max(0, int((utcnow() - since).total_seconds() // 60))
                    log.info("%s (%s) still unreachable for %s, next try in %ds",
                             entry.domain, entry.title, _minutes_text(minutes), int(delay))
                else:
                    log.debug("%s (%s) setup failed (attempt %d), retry in %ss: %s",
                              entry.domain, entry.title, attempt, delay, exc)
                driver.mark_unavailable()
                with contextlib.suppress(Exception):
                    await driver.async_unload()
                first.set()
                await asyncio.sleep(delay)
                continue
            self.hub.drivers[entry.entry_id] = driver
            entry.state, entry.reason, entry.needs = "loaded", None, None
            since = self.failing_since.pop(entry.entry_id, None)
            self.next_retry.pop(entry.entry_id, None)
            first.set()
            if reported is not None:
                log.info("%s (%s) is back after %s", entry.domain, entry.title, _outage_text(since, utcnow()))
            attempt, reported = 0, None
            log.info("%s (%s) loaded with %d entities", entry.domain, entry.title, len(driver.entities))
            if driver.poll_interval is None:
                await asyncio.Event().wait()  # push-only: live until cancelled
            if not await self._poll_forever(entry, driver):
                return

    async def _poll_forever(self, entry: ConfigEntry, driver: Driver) -> bool:
        """Poll on the driver's interval. A poll that hangs times out; several failures in a row mark the
        entry's entities unavailable (and say so once), and the next good poll brings them back. Returns
        False once the entry is parked (credentials refused, a setting wrong): those are never retried."""
        failures = 0
        held: dict[str, tuple[str, dict[str, Any]]] = {}  # states before they were marked unavailable
        while True:
            await asyncio.sleep(driver.poll_interval or 30.0)
            timeout = float(getattr(driver, "poll_timeout", None) or POLL_TIMEOUT_S)
            try:
                # asyncio.timeout, not wait_for: on Python 3.11 wait_for returns the poll's result when a cancel
                # lands just as the poll finishes, swallowing the cancel, so this loop never stopped (the
                # SOA CI shard holding test_runtime_poll hung until its 30 minute timeout).
                async with asyncio.timeout(timeout):
                    await driver.async_poll()
            except asyncio.CancelledError:
                raise
            except (AuthRequired, SetupError) as exc:
                # The password was changed or revoked while running (or a setting is wrong): stop asking.
                self.hub.drivers.pop(entry.entry_id, None)
                await self._park(entry, driver, exc)
                return False
            except Exception as exc:
                failures += 1
                why = f"no answer in {timeout:g} s" if isinstance(exc, TimeoutError) else str(exc) or type(exc).__name__
                if failures == 1:
                    log.warning("%s (%s) poll failed: %s", entry.domain, entry.title, why)
                else:
                    log.debug("%s (%s) poll failed (%d in a row): %s", entry.domain, entry.title, failures, why)
                if failures == POLL_FAILURES_UNAVAILABLE:
                    # Entities a live push still keeps current are left as they are: a poll that fails says
                    # nothing about them (a camera's motion sensor while its event push still arrives).
                    spared = driver.push_fed_entities() if hasattr(driver, "push_fed_entities") else set()
                    ents = [e for e in driver.entities if e.entity_id not in spared]
                    for e in ents:
                        st = self.hub.states.get(e.entity_id)
                        if st is not None and st.state != UNAVAILABLE:
                            held[e.entity_id] = (st.state, dict(st.attributes))
                    log.warning("%s (%s): %d polls failed in a row; its entities are unavailable until one "
                                "succeeds%s", entry.domain, entry.title, failures,
                                f" (except {len(spared)} its push keeps current)" if spared else "")
                    driver.mark_unavailable(ents)
                if failures >= POLL_FAILURES_UNAVAILABLE:
                    # The entry says why (the Devices page lists it as needing attention) until a poll works.
                    entry.state, entry.reason = "setup_retry", f"{type(exc).__name__}: {why}"
                    self.failing_since.setdefault(entry.entry_id, utcnow())
                continue
            if failures:
                if failures >= POLL_FAILURES_UNAVAILABLE:
                    self._restore_after_poll(driver, held)
                    # Only what this loop recorded is cleared: a driver may keep its own state on the entry.
                    self.failing_since.pop(entry.entry_id, None)
                    entry.state, entry.reason, entry.needs = "loaded", None, None
                log.info("%s (%s) polls again after %d failures", entry.domain, entry.title, failures)
            failures = 0
            held.clear()

    def _restore_after_poll(self, driver: Driver, held: dict[str, tuple[str, dict[str, Any]]]) -> None:
        """Entities the good poll did not publish get back the state they had before they were marked unavailable."""
        for e in driver.entities:
            st = self.hub.states.get(e.entity_id)
            if e.entity_id in held and st is not None and st.state == UNAVAILABLE:
                state, attrs = held[e.entity_id]
                driver.publish(e, state, attrs)

    async def _park(self, entry: ConfigEntry, driver: Driver, exc: Exception) -> None:
        """Stop retrying an entry that cannot load until an admin acts (bad credentials, a missing setting).

        Reloading or reconfiguring the entry (``start_entry``) tries again.
        """
        if isinstance(exc, AuthRequired):
            entry.state, entry.reason, entry.needs = "setup_error", f"AuthRequired: {exc}", exc.needs
        else:
            entry.state, entry.reason, entry.needs = "setup_error", f"{type(exc).__name__}: {exc}", None
        self.failing_since.setdefault(entry.entry_id, utcnow())
        log.warning("%s (%s) needs attention and will not be retried until it is changed or reloaded: %s",
                    entry.domain, entry.title, entry.reason)
        driver.mark_unavailable()
        with contextlib.suppress(Exception):
            await driver.async_unload()

    def next_retry_at(self, entry_id: str) -> datetime | None:
        """When the next setup attempt for an entry is due; None while it works or is parked."""
        return self.next_retry.get(entry_id)

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
                rec = self.recorder
                if rec is not None:  # the retention setting, on the writer's own connection
                    n = await rec.purge(rec.keep_days)
                else:
                    n = await asyncio.to_thread(self.store.prune_history, HISTORY_KEEP_DAYS)
                if n:
                    log.info("pruned %d history rows", n)
            except Exception:
                log.exception("history prune failed")

    async def shutdown(self) -> None:
        for entry_id in list(self._tasks):
            await self.stop_entry(entry_id)
        rec, self.recorder, self._unsub_record = self.recorder, None, None
        if rec is not None:
            rec.detach()  # on the loop, where the bus is used
            await asyncio.to_thread(rec.stop)  # commits what the writer still holds

    def entry_status(self) -> list[dict[str, Any]]:
        out = []
        for e in self.hub.config_entries.values():
            ents = [x for x in self.hub.entities.values() if x.config_entry_id == e.entry_id and not x.disabled_by]
            unavailable = sum(1 for x in ents if (s := self.hub.states.get(x.entity_id)) is None or s.state == UNAVAILABLE)
            out.append({"entry_id": e.entry_id, "domain": e.domain, "title": e.title, "state": e.state,
                        "reason": e.reason, "source": e.source, "entities": len(ents), "unavailable": unavailable})
        return sorted(out, key=lambda r: (r["domain"], r["title"]))
