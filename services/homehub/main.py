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

import uvicorn

from services.homehub import automation_api, builtin, statistics, tts
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Hub
from services.homehub.runtime import Runtime
from services.homehub.store import Store

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("homehub")

DB_PATH = os.getenv("HOMEHUB_DB", "/data/homehub.db")
IMPORT_DIR = os.getenv("HOMEHUB_IMPORT_DIR", "/import")
PORT = int(os.getenv("HOMEHUB_PORT", "8123"))
# How long automations wait for the integrations to load before starting anyway.
STARTUP_WAIT_S = float(os.getenv("HOMEHUB_STARTUP_WAIT_S", "60"))


class Service:
    def __init__(self) -> None:
        self.store = Store(DB_PATH, os.getenv("FERNET_KEY"))
        self.auth = Auth(self.store, os.getenv("INTERNAL_SECRET"))
        self.hub = Hub(time_zone=os.getenv("TZ", "UTC"))
        self.hub.store = self.store  # drivers that keep their own data (to-do lists) use it
        self.runtime = Runtime(self.hub, self.store)
        self._lock = asyncio.Lock()
        self._bg: list[asyncio.Task] = []

    async def start(self) -> None:
        self.runtime.load_registry()
        self.hub.entity_registry_ids = self.store.get_kv("entity_registry_ids", {}) or {}
        # HA user id -> name: mobile_app maps a phone to its Jarvis user through it.
        self.hub.ha_users = {u["id"]: u.get("name") or "" for u in self.store.get_kv("users", []) or []}
        self.runtime.start_recording()
        builtin.register(self.hub)
        tts.register(self.hub)
        await self._start_people()
        await self.runtime.start_all()
        # Automations wait (in the background, so the API is up at once) for
        # every integration's first setup attempt, as HA starts them only once
        # it has started.
        self._bg.append(asyncio.create_task(self._start_automations_when_ready()))
        self._bg.append(asyncio.create_task(self.runtime.housekeeping()))
        self._bg.append(asyncio.create_task(statistics.start(self.runtime)))  # recorder statistics
        await self._start_discovery()

    async def _start_discovery(self) -> None:
        """New devices on the LAN (services/homehub/discovery.py); ``HOMEHUB_DISCOVERY=0`` turns it off."""
        if os.getenv("HOMEHUB_DISCOVERY", "1").lower() in ("0", "false", "no", "off"):
            return
        try:
            from services.homehub.discovery import DiscoveryManager

            manager = DiscoveryManager(self.hub, self.store)
            await manager.start()
            self.runtime.discovery = manager  # the /hub/discovered API reads it from here
        except Exception:
            log.exception("device discovery could not start")

    async def _start_people(self) -> None:
        try:
            from services.homehub.drivers import mobile_app

            home = {"name": (self.hub.location or {}).get("location_name", "Home"), **(self.hub.location or {})}
            mobile_app.setup_zones(self.hub, self.store.get_kv("zones", []) or [], home)
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
