"""Which Sendspin satellites HomeHub can hand Music Assistant a pairing token for.

Music Assistant pairs an encrypted Sendspin client through Home Assistant: it follows
``sendspin/subscribe_clients`` (the client ids HA can fetch a token for) and asks
``sendspin/get_pairing_token`` for one of them. HomeHub plays HA's part, so it needs to
know each Echo's Sendspin client id and where its token is.

- The client id: an Echo (hassmic) advertises its Sendspin endpoint on mDNS as
  ``_sendspin._tcp`` on port 28928, path ``/sendspin``, at its ESPHome entry's host. A
  WebSocket connection there is answered at once with ``{"type": "client/init",
  "payload": {"client_id": "...", ...}}``; the id is read and the connection closed.
- The token: hassmic's ``Sendspin pairing token`` text sensor, disabled by default on the
  node and published only on an encrypted connection (``music_assistant_server``). A
  disabled entity is enabled once and its entry reloaded, so the node sends it.

That port is the one Music Assistant streams audio through, on a slow single-core device,
so a satellite is probed once and its id kept. It is probed again only when it has no id,
after a failed probe (backing off), or when the node reconnects (its token entity comes
back from unavailable). A failed probe never drops an id already known.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from services.homehub import music_assistant_server as mas

log = logging.getLogger("homehub.sendspin_clients")

SENDSPIN_PORT = 28928
SENDSPIN_PATH = "/sendspin"
PROBE_TIMEOUT_S = 5.0
#: How often the loop looks for satellites that need a probe (it probes none it knows).
CHECK_INTERVAL_S = 60.0
#: Seconds to wait after the 1st, 2nd and later failed probes of a satellite.
BACKOFF_S = (60.0, 300.0, 900.0)
_GONE = ("unavailable", "unknown", "")


def parse_client_init(raw: str) -> str | None:
    """The ``client_id`` of a Sendspin ``client/init`` frame, else None."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "client/init":
        return None
    payload = data.get("payload")
    client_id = payload.get("client_id") if isinstance(payload, dict) else None
    return client_id if isinstance(client_id, str) and client_id else None


async def read_client_id(host: str, *, port: int = SENDSPIN_PORT, timeout: float = PROBE_TIMEOUT_S) -> str | None:
    """Open the satellite's Sendspin WebSocket, read its first frame, close it."""
    url = f"ws://{host}:{port}{SENDSPIN_PATH}"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session, \
            session.ws_connect(url, heartbeat=None, autoping=False) as ws:
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if msg.type != aiohttp.WSMsgType.TEXT:
            return None
        return parse_client_init(msg.data)


class SendspinClients:
    """A ``client_id -> device_id`` map for the hub's Sendspin satellites, kept current."""

    def __init__(self, hub: Any, runtime: Any, *,
                 probe: Callable[[str], Any] = read_client_id,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.hub = hub
        self.runtime = runtime
        self._probe = probe
        self._clock = clock
        self._ids: dict[str, str] = {}  # device_id -> client_id
        self._failures: dict[str, tuple[int, float]] = {}  # device_id -> (count, next try)
        self._enabled: set[str] = set()  # devices whose token entity we turned on
        self._reread: set[str] = set()  # known devices to read again (the node reconnected)
        self._callbacks: list[Callable[[list[str]], None]] = []
        self._task: asyncio.Task[None] | None = None
        self._unsub_bus: Callable[[], None] | None = None
        self._lock = asyncio.Lock()

    # -- what Music Assistant asks ----------------------------------------------------------------------------------
    def client_ids(self) -> list[str]:
        return sorted(set(self._ids.values()))

    def device_for(self, client_id: str) -> str | None:
        return next((d for d, c in self._ids.items() if c == client_id), None)

    def token_for(self, client_id: str) -> str | None:
        device_id = self.device_for(client_id)
        if device_id is None:
            return None
        token, _why = mas.find_sendspin_token(self.hub, device_id)
        return token

    def subscribe(self, callback: Callable[[list[str]], None]) -> Callable[[], None]:
        """Call ``callback`` with the full id list now and whenever it changes."""
        self._callbacks.append(callback)
        callback(self.client_ids())

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._callbacks.remove(callback)

        return unsubscribe

    # -- lifecycle --------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        self._unsub_bus = self.hub.bus.subscribe(self._on_state_changed, "state_changed")
        self._task = asyncio.get_running_loop().create_task(self._run(), name="sendspin-clients")

    async def stop(self) -> None:
        if self._unsub_bus is not None:
            self._unsub_bus()
            self._unsub_bus = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                log.exception("Sendspin satellites could not be refreshed")
            await asyncio.sleep(CHECK_INTERVAL_S)

    # -- the work ---------------------------------------------------------------------------------------------------
    def _satellites(self) -> dict[str, Any]:
        """device_id -> its Sendspin token entity, for every device that has one."""
        out = {}
        for device_id in list(self.hub.devices):
            entity = mas._sendspin_entity(self.hub, device_id)
            if entity is not None:
                out[device_id] = entity
        return out

    async def refresh(self) -> None:
        async with self._lock:
            before = self.client_ids()
            satellites = self._satellites()
            for device_id in list(self._ids):
                if device_id not in satellites:  # the device or its Sendspin entity is gone
                    self._ids.pop(device_id, None)
                    self._failures.pop(device_id, None)
                    self._reread.discard(device_id)
            now = self._clock()
            for device_id, entity in satellites.items():
                if entity.disabled_by:
                    await self._enable(device_id, entity)
                if device_id in self._ids and device_id not in self._reread:
                    continue
                _count, due = self._failures.get(device_id, (0, 0.0))
                if now >= due:
                    await self._probe_device(device_id, now)
            if self.client_ids() != before:
                self._notify()

    async def _probe_device(self, device_id: str, now: float) -> None:
        entry, error = mas._esphome_entry_for_device(self.hub, device_id)
        host = str(((entry.data or {}) if entry is not None else {}).get("host") or "").strip()
        client_id = None
        if error is None and host:
            try:
                client_id = await self._probe(host)
            except Exception as exc:
                log.debug("Sendspin client id of %s not read: %s", device_id, type(exc).__name__)
        if client_id:
            self._ids[device_id] = client_id
            self._failures.pop(device_id, None)
            self._reread.discard(device_id)
            log.info("Sendspin satellite %s has client id %s", device_id, client_id)
            return
        count = self._failures.get(device_id, (0, 0.0))[0] + 1
        wait = BACKOFF_S[min(count, len(BACKOFF_S)) - 1]
        self._failures[device_id] = (count, now + wait)

    async def _enable(self, device_id: str, entity: Any) -> None:
        if device_id in self._enabled:
            return
        self._enabled.add(device_id)
        entry, error = mas._esphome_entry_for_device(self.hub, device_id)
        if error is not None or entry is None:
            return
        log.info("Turning on %s so the node sends its Sendspin pairing token", entity.entity_id)
        entity.disabled_by = None
        self.runtime.store.save_entity(entity)
        self.hub.bus.fire("entity_registry_updated", {"action": "update", "entity_id": entity.entity_id})
        from services.homehub.hardware import _reload

        await _reload(self.runtime, entry)

    async def _on_state_changed(self, _etype: str, data: dict[str, Any]) -> None:
        """A node that reconnects republishes its token entity: probe it again then."""
        entity_id = str(data.get("entity_id") or "")
        if not entity_id.endswith(mas.SENDSPIN_OBJECT_ID):
            return
        old, new = data.get("old_state"), data.get("new_state")
        was = str(getattr(old, "state", "") or "")
        now_state = str(getattr(new, "state", "") or "")
        if was not in _GONE or now_state in _GONE:
            return
        entity = self.hub.entities.get(entity_id) if isinstance(self.hub.entities, dict) else None
        device_id = getattr(entity, "device_id", None)
        if device_id:
            # Its id may have changed (a new key): read it again, keeping the old one until then.
            self._failures.pop(device_id, None)
            self._reread.add(device_id)
            asyncio.get_running_loop().create_task(self.refresh())

    def _notify(self) -> None:
        ids = self.client_ids()
        for callback in list(self._callbacks):
            try:
                callback(ids)
            except Exception:
                log.exception("a Sendspin client subscriber failed")
