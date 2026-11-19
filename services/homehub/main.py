"""homehub: the household's device hub, replacing Home Assistant.

Serves HA's REST/WebSocket API on ``HOMEHUB_PORT`` (8123, HA's own port) so
every Jarvis caller that speaks HA keeps working, and drives the devices
directly. See services/homehub/README.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

# uvloop (uvicorn's loop) runs every DNS lookup, and the address of every UDP socket a driver opens with
# ``remote_addr``, on libuv's thread pool: four threads by default, read once when the pool first starts.
# A few slow lookups filled it on 2026-11-18 and WiZ bulbs were called unreachable. More threads are
# cheap (they sleep in the kernel); set before anything can start the pool.
os.environ.setdefault("UV_THREADPOOL_SIZE", "16")

import uvicorn

from services.homehub import (
    abuse_ch_move,
    app_checks_migration,
    automation_api,
    builtin,
    camera_recording,
    conversation,
    light_cluster_move,
    recorder_statistics,
    registries,
    time_date,
    tts,
)
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Hub
from services.homehub.loop_monitor import LoopMonitor
from services.homehub.runtime import Runtime
from services.homehub.store import Store

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("homehub")
# aioesphomeapi warns on every reconnect attempt; with devices switched off at
# the wall that is one warning per device per minute. The ESPHome driver logs
# a device going unreachable (and coming back) once, which is what matters.
logging.getLogger("aioesphomeapi.connection").setLevel(logging.ERROR)
logging.getLogger("aioesphomeapi.reconnect_logic").setLevel(logging.ERROR)

DB_PATH = os.getenv("HOMEHUB_DB", "/data/homehub.db")
IMPORT_DIR = os.getenv("HOMEHUB_IMPORT_DIR", "/import")
PORT = int(os.getenv("HOMEHUB_PORT", "8123"))
# How long automations wait for the integrations to load before starting anyway.
STARTUP_WAIT_S = float(os.getenv("HOMEHUB_STARTUP_WAIT_S", "60"))


def _household_time_zone(home: dict | None = None) -> str:
    """The household zone, or SystemExit naming every way to set it."""
    from services.common import household_tz

    identity = (os.getenv("IDENTITY_SVC_URL") or "").strip()
    secret = os.getenv("INTERNAL_SECRET") or ""
    fetch = (lambda: household_tz.fetch_identity_settings(identity, secret)) if identity else None
    try:
        return household_tz.refresh_sync("homehub", fetch, home=home)[0]
    except household_tz.HouseholdTimeZoneError as exc:
        raise SystemExit(f"homehub: {exc}") from None


class Service:
    def __init__(self) -> None:
        self.store = Store(DB_PATH, os.getenv("FERNET_KEY"))
        self.auth = Auth(self.store, os.getenv("INTERNAL_SECRET"))
        # One setting governs Jarvis and the hub (services/common/household_tz.py:
        # the household setting, then TZ, then the home location). No guessed
        # zone: schedules, sun times and energy periods would all be wrong
        # without a word. A change applies when the hub restarts.
        self.hub = Hub(time_zone=_household_time_zone(self.store.get_kv("core_config", {}) or None))
        self.hub.store = self.store  # drivers that keep their own data (to-do lists) use it
        # Where drivers keep files (camera clips waiting for upload), beside the database.
        self.hub.data_dir = os.path.dirname(os.path.abspath(DB_PATH))
        # Jarvis's own Nextcloud account, read once here: the motion-recording settings can use it.
        self.hub.household_nextcloud = camera_recording.household_account_from_env()
        self.runtime = Runtime(self.hub, self.store)
        # A stalled loop or a saturated thread pool makes devices look unreachable; this says which.
        self.runtime.loop_monitor = LoopMonitor()
        self._lock = asyncio.Lock()
        self._bg: list[asyncio.Task] = []

    async def start(self) -> None:
        self._bg.append(asyncio.create_task(self.runtime.loop_monitor.run(), name="loop-monitor"))
        self.runtime.load_registry()
        self.hub.entity_registry_ids = self.store.get_kv("entity_registry_ids", {}) or {}
        # HA user id -> name: mobile_app maps a phone to its Jarvis user through it.
        self.hub.ha_users = {u["id"]: u.get("name") or "" for u in self.store.get_kv("users", []) or []}
        self.runtime.start_recording()
        builtin.register(self.hub)
        tts.register(self.hub)
        await self._start_helpers()  # before the automations: they name the helpers
        conversation.register(self.hub)  # conversation.process and ai_task.generate_data, answered by Jarvis
        # HA's time_date sensors on every hub, imported or not (services/homehub/time_date.py).
        self._bg.append(asyncio.create_task(time_date.run(self.hub)))
        await self._start_people()
        await self._start_bluetooth()  # before the drivers: ESPHome proxies and BLE sensors attach to it
        await self.runtime.start_all()
        # Once: the Common Sense switch moves from identity's settings to an integration.
        self._bg.append(asyncio.create_task(app_checks_migration.run(self.runtime)))
        # Once: the abuse.ch integration's key moves to identity's household default and the entry goes.
        self._bg.append(asyncio.create_task(abuse_ch_move.run(self.runtime)))
        # Once: the admin page's light clusters (identity) become light groups here.
        self._bg.append(asyncio.create_task(light_cluster_move.run(self.runtime)))
        # Automations wait (in the background, so the API is up at once) for
        # every integration's first setup attempt, as HA starts them only once
        # it has started.
        self._bg.append(asyncio.create_task(self._start_automations_when_ready()))
        self._bg.append(asyncio.create_task(self.runtime.housekeeping()))
        self._bg.append(asyncio.create_task(recorder_statistics.start(self.runtime)))  # recorder statistics
        await self._start_discovery()

    async def _start_helpers(self) -> None:
        """HA's helpers (input_*, counter, timer) with their restored values (services/homehub/helpers.py)."""
        from services.homehub.helpers import Helpers

        manager = Helpers(self.hub, self.store)
        await manager.start()
        self.runtime.helpers = manager  # the /hub/helpers API reads it from here
        if manager.problems:
            log.error("helpers left out: %s", "; ".join(manager.problems))

    async def _start_discovery(self) -> None:
        """New devices on the LAN (services/homehub/discovery.py); ``HOMEHUB_DISCOVERY=0`` turns it off."""
        if os.getenv("HOMEHUB_DISCOVERY", "1").lower() in ("0", "false", "no", "off"):
            return
        try:
            from services.homehub.discovery import DiscoveryManager

            manager = DiscoveryManager(self.hub, self.store)
            manager.runtime = self.runtime  # following a device to a new address reloads its entry
            await manager.start()
            self.runtime.discovery = manager  # the /hub/discovered API reads it from here
        except Exception:
            log.exception("device discovery could not start")

    async def _start_bluetooth(self) -> None:
        """The Bluetooth bus (services/homehub/bluetooth.py); the host adapter only when configured."""
        try:
            from services.homehub.bluetooth import BluetoothManager

            manager = BluetoothManager(self.hub, self.store, discovery=lambda: getattr(self.runtime, "discovery", None))
            await manager.start()
            self.runtime.bluetooth = manager
        except Exception:
            log.exception("Bluetooth could not start")

    async def _start_people(self) -> None:
        try:
            from services.homehub.drivers import mobile_app

            # Once: the old read-only mirror of the family's saved places becomes ordinary zones (the hub
            # is the only store of places now; geo moves the rest of its places in at its own start).
            registries.adopt_mirrored_places(self.runtime)
            registries.apply_zones(self.runtime)  # zone.home and every family zone (never a private place)
            await mobile_app.setup_persons(self.hub, self.store.get_kv("persons", []) or [])
        except Exception:
            log.exception("people and zones could not start")

    async def _start_automations_when_ready(self) -> None:
        pending = await self.runtime.wait_first_setups(STARTUP_WAIT_S)
        if pending:
            log.warning("starting automations with %d integrations still loading", len(pending))
        await self._start_automations()

    async def _start_automations(self) -> None:
        try:
            from services.homehub.automation import AutomationEngine

            engine = AutomationEngine(self.hub, self.store)
            automation_api.prepare(engine, self.store)  # editor, traces, remap, thermostat holds
            scripts = {s["id"]: {k: v for k, v in s.items() if k != "id"} for s in self.store.load_automations("script")}
            await engine.load(self.store.load_automations("automation"), scripts,
                              self.store.load_automations("scene"), self.store.get_kv("scheduler"))
            await engine.start()
            self.runtime.automations = engine
            await automation_api.start(self.runtime)
        except Exception:
            log.exception("automation engine could not start")

    async def stop(self) -> None:
        helpers = getattr(self.runtime, "helpers", None)
        if helpers is not None:
            self.runtime.helpers = None
            await helpers.stop()
        bluetooth = getattr(self.runtime, "bluetooth", None)
        if bluetooth is not None:
            self.runtime.bluetooth = None
            await bluetooth.stop()
        for t in self._bg:
            t.cancel()
        self._bg.clear()
        discovery = getattr(self.runtime, "discovery", None)
        if discovery is not None:
            self.runtime.discovery = None
            await discovery.stop()
        if self.runtime.automations is not None:
            await automation_api.stop(self.runtime.automations)
            await self.runtime.automations.stop()
            self.runtime.automations = None
        await self.runtime.shutdown()

    async def do_import(self, path: str | None, history_days: int = 0) -> dict[str, Any]:
        """Import an HA config dir, then restart every driver on the new registry."""
        from services.homehub.importer import import_config_dir

        src = path or os.path.join(IMPORT_DIR, "homeassistant")
        if not os.path.realpath(src).startswith(os.path.realpath(IMPORT_DIR)):
            raise ValueError(f"imports are read from {IMPORT_DIR} only")
        async with self._lock:
            await self.stop()
            report = await asyncio.to_thread(import_config_dir, self.store, src, history_days=history_days)
            for eid in self.hub.states.entity_ids():
                self.hub.states.remove(eid)
            self.hub.drivers.clear()
            self.hub.services.clear()
            self.hub.service_schemas.clear()
            await self.start()
        return report


service = Service()


@asynccontextmanager
async def lifespan(_app):
    await service.start()
    try:
        yield
    finally:
        await service.stop()


app = build_app(service.runtime, service.auth, admin_hooks={"import": service.do_import})
app.router.lifespan_context = lifespan


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOMEHUB_HOST", "0.0.0.0"), port=PORT,
                log_level=os.getenv("LOG_LEVEL", "info").lower(), ws_ping_interval=30)
