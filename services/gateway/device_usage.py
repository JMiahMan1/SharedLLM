"""Device commands count as use of the device in the RAG device index.

A device row in ``home_devices`` ages out when its hub has not synced it and
nobody has used it for ``HOME_DEVICE_STALE_HOURS``. A search hit is one use; a
command is the other. Every command a home hub runs, whoever sent it (Jarvis by
voice, the app, a schedule, an automation), is announced on the hub's event
bus as ``call_service``. This listener subscribes to that event on each home
hub that is switched on, collects the entity ids, and reports them to RAG
(``POST /rag/devices/used``) once a minute. Nothing about the command beyond
its entity ids leaves the gateway.

HomeHub's event carries the entity ids the call resolved to (areas and
devices included); Home Assistant's carries only what the caller named, so
from HA a call aimed at an area counts nothing. Best effort throughout: a
lost report only shortens a row's life, never breaks a command.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

import aiohttp
import websockets

from services.common.entity_ids import usable_entity_id
from services.common.rag_collections import SOURCE_HOME_ASSISTANT, SOURCE_HOMEHUB

log = logging.getLogger("gateway.device_usage")

#: How often the collected entity ids are sent to RAG.
FLUSH_SECONDS = 60.0
#: Wait before reconnecting to a hub whose event stream dropped.
RECONNECT_SECONDS = 15.0


def entity_ids_of(event_data: dict[str, Any]) -> list[str]:
    """The entity ids a ``call_service`` event names: the resolved list, else the caller's ``entity_id``."""
    resolved = event_data.get("entity_ids")
    if isinstance(resolved, list):
        ids: Iterable[Any] = resolved
    else:
        sd = event_data.get("service_data") or {}
        named: Any = sd.get("entity_id")
        if named is None:
            named = (sd.get("target") or {}).get("entity_id")
        ids = [named] if isinstance(named, str) else (named if isinstance(named, list) else [])
    return [i for i in ids if isinstance(i, str) and i != "all" and usable_entity_id(i)]


class DeviceUsage:
    """Collects the entity ids of commands per hub and reports them to RAG."""

    def __init__(self, rag_url: str, internal_secret: str) -> None:
        self.rag_url = rag_url.rstrip("/")
        self.internal_secret = internal_secret
        self.pending: dict[str, set[str]] = {}

    def note(self, source: str, event_data: dict[str, Any]) -> None:
        ids = entity_ids_of(event_data)
        if ids:
            self.pending.setdefault(source, set()).update(ids)

    async def flush(self, session: aiohttp.ClientSession) -> int:
        """Send what was collected; returns how many entity ids were reported."""
        batch, self.pending = self.pending, {}
        sent = 0
        for source, ids in batch.items():
            try:
                async with session.post(
                    f"{self.rag_url}/rag/devices/used",
                    json={"source": source, "entity_ids": sorted(ids)},
                    headers={"X-Internal-Secret": self.internal_secret},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        sent += len(ids)
                    else:
                        log.warning("[device_usage] RAG answered %s for %d %s devices", resp.status, len(ids), source)
            except Exception as exc:
                log.warning("[device_usage] could not report %d %s devices: %s", len(ids), source, type(exc).__name__)
        return sent

    async def listen(self, source: str, hub_url: str, token: str) -> None:
        """Follow one hub's ``call_service`` events until cancelled, reconnecting on errors."""
        ws_url = hub_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/") + "/api/websocket"
        while True:
            try:
                async with websockets.connect(ws_url, ping_interval=20.0, ping_timeout=10.0) as ws:
                    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                    if hello.get("type") != "auth_required":
                        raise RuntimeError(f"unexpected hello {hello.get('type')!r}")
                    await ws.send(json.dumps({"type": "auth", "access_token": token}))
                    auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                    if auth.get("type") != "auth_ok":
                        log.error("[device_usage] %s refused the login (%s); commands are not counted", source,
                                  auth.get("type"))
                        return
                    await ws.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "call_service"}))
                    log.info("[device_usage] counting device commands from %s", source)
                    async for raw in ws:
                        with suppress(TypeError, ValueError):
                            message = json.loads(raw)
                            event = message.get("event") or {}
                            if message.get("type") == "event" and event.get("event_type") == "call_service":
                                self.note(source, event.get("data") or {})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("[device_usage] %s event stream ended (%s); reconnecting in %ds", source,
                            type(exc).__name__, int(RECONNECT_SECONDS))
            await asyncio.sleep(RECONNECT_SECONDS)

    async def flush_forever(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(FLUSH_SECONDS)
                await self.flush(session)


def hubs_to_follow() -> list[tuple[str, str, str]]:
    """(source, url, token) for each home hub that is switched on and can be logged in to."""
    from services import config

    hubs = []
    if config.homehub_enabled():
        hubs.append((SOURCE_HOMEHUB, config.HOMEHUB_URL, config.INTERNAL_SECRET))
    if config.home_assistant_enabled() and config.HA_TOKEN:
        hubs.append((SOURCE_HOME_ASSISTANT, config.HA_URL, config.HA_TOKEN))
    return hubs


async def run() -> None:
    """Count commands from every hub that is on; returns at once when none is."""
    from services import config

    hubs = hubs_to_follow()
    if not hubs:
        log.info("[device_usage] no home hub is on; device commands are not counted")
        return
    usage = DeviceUsage(config.RAG_SVC_URL, config.INTERNAL_SECRET)
    await asyncio.gather(usage.flush_forever(), *(usage.listen(*hub) for hub in hubs))
