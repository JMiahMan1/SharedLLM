"""Per-user media event hub (P2-T25, MEDIA_OVERHAUL section 7.2).

One hub per user fans normalized player/queue events out to Server-Sent Event
subscribers. HA states arrive over the HA WebSocket using
``subscribe_events`` + ``state_changed`` — every supported HA version ships
that, and unlike ``subscribe_entities`` it carries the complete ``new_state``
instead of partial attribute diffs (an initial REST ``/api/states`` read warms
the snapshot). Music Assistant events arrive over ``MAWebSocketClient``.

A new subscriber's first message is the full player snapshot, then live
events, with a heartbeat every 15 seconds. The hub starts on the first
subscriber and stops 60 seconds after the last one leaves; the clock is
injectable so tests need not wait.

Feeding ``/execute/media/status`` from this cache is the documented remainder
of BUG-31 and is not wired here yet: the execution handler still reads HA
directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from typing import Any, Protocol

import aiohttp
import websockets

from services.gateway.ma_ws_client import MAWebSocketClient

log = logging.getLogger("gateway.media_events")

HA_HEARTBEAT_INTERVAL = 20.0
DEFAULT_HEARTBEAT_INTERVAL = 15.0
DEFAULT_IDLE_TIMEOUT = 60.0
SUBSCRIBER_QUEUE_SIZE = 100

_MA_EVENT_TYPES = (
    "player_updated",
    "queue_updated",
    "queue_items_updated",
    "queue_time_updated",
)
_MA_QUEUE_EVENT_TYPES = frozenset(("queue_updated", "queue_items_updated", "queue_time_updated"))


class MediaSource(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


def sse_event(event_type: str, payload: Mapping[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def normalize_ha_state(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """HA entity state -> normalized player event (None for non media_players)."""
    if not state:
        return None
    entity_id = str(state.get("entity_id") or "")
    if not entity_id.startswith("media_player."):
        return None
    attrs = state.get("attributes") or {}
    st = str(state.get("state") or "")
    title = attrs.get("media_title")
    content_id = attrs.get("media_content_id")
    item = None
    if title or content_id:
        artist = attrs.get("media_artist")
        item = {
            "uri": content_id or "",
            "title": title or "",
            "artists": [artist] if artist else [],
            "album": attrs.get("media_album_name") or attrs.get("media_album") or "",
            "image": attrs.get("entity_picture") or "",
            "duration": attrs.get("media_duration"),
        }
    return {
        "type": "player",
        "output_id": entity_id,
        "state": st,
        "item": item,
        "position": attrs.get("media_position"),
        "position_updated_at": attrs.get("media_position_updated_at"),
        "volume": attrs.get("volume_level"),
        "muted": bool(attrs.get("is_volume_muted", False)),
        "shuffle": attrs.get("shuffle"),
        "repeat": attrs.get("repeat"),
        "available": st not in ("unavailable", "unknown"),
        "group_members": attrs.get("group_members") or [],
    }


def _ma_item(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    artists = raw.get("artists") or []
    if artists and isinstance(artists[0], dict):
        artists = [a.get("name") or "" for a in artists]
    album = raw.get("album")
    if isinstance(album, dict):
        album = album.get("name") or ""
    metadata = raw.get("metadata") or {}
    image = metadata.get("image_url") if isinstance(metadata, dict) else ""
    image = image or raw.get("image_url") or raw.get("image") or ""
    return {
        "uri": raw.get("uri") or "",
        "title": raw.get("name") or raw.get("title") or "",
        "artists": artists,
        "album": album or "",
        "image": image or "",
        "duration": raw.get("duration"),
    }


def normalize_ma_player(player: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """MA player object -> normalized player event (output_id ``ma:<id>``)."""
    if not player:
        return None
    inner = player
    if "player_id" not in inner and isinstance(player.get("data"), Mapping):
        inner = player["data"]
    player_id = str(inner.get("player_id") or inner.get("id") or "")
    if not player_id:
        return None
    return {
        "type": "player",
        "output_id": f"ma:{player_id}",
        "state": str(inner.get("state") or ""),
        "item": _ma_item(inner.get("current_item") or inner.get("current_media")),
        "position": inner.get("elapsed_time"),
        "position_updated_at": inner.get("elapsed_time_last_updated"),
        "volume": inner.get("volume_level"),
        "muted": bool(inner.get("volume_muted", False)),
        "shuffle": inner.get("shuffle"),
        "repeat": inner.get("repeat_mode"),
        "available": bool(inner.get("available", True)),
        "group_members": inner.get("group_childs") or [],
    }


def normalize_ma_event(event_type: str, data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """MA websocket event -> normalized player/queue event."""
    data = data or {}
    if event_type == "player_updated":
        return normalize_ma_player(data)
    if event_type in _MA_QUEUE_EVENT_TYPES:
        queue_id = str(data.get("queue_id") or data.get("player_id") or "")
        if not queue_id:
            return None
        event: dict[str, Any] = {
            "type": "queue",
            "output_id": f"ma:{queue_id}",
            "items_changed": event_type != "queue_time_updated",
        }
        if event_type == "queue_time_updated":
            event["position"] = data.get("elapsed_time")
        return event
    return None


class HAEntitySource:
    """HA ``state_changed`` websocket subscription plus an initial REST snapshot."""

    def __init__(self, ha_url: str, ha_token: str, on_state: Callable[[dict], None]):
        self._ha_url = (ha_url or "").rstrip("/")
        self._ha_token = ha_token
        self._on_state = on_state
        self._ws = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await self._load_snapshot()
        self._task = asyncio.create_task(self._run())

    async def _load_snapshot(self) -> None:
        try:
            timeout = aiohttp.ClientTimeout(total=10.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self._ha_url}/api/states",
                    headers={"Authorization": f"Bearer {self._ha_token}"},
                ) as resp:
                    if resp.status != 200:
                        log.warning("[media_events] HA snapshot HTTP %s", resp.status)
                        return
                    states = await resp.json()
        except Exception as exc:  # noqa: BLE001 - a broken snapshot must not kill the hub
            log.warning("[media_events] HA snapshot failed: %s", exc)
            return
        for state in states or []:
            if isinstance(state, dict):
                self._on_state(state)

    async def _run(self) -> None:
        ws_url = self._ha_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/api/websocket"
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    ws_url, ping_interval=HA_HEARTBEAT_INTERVAL, ping_timeout=10.0,
                ) as ws:
                    self._ws = ws
                    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                    if hello.get("type") != "auth_required":
                        log.warning("[media_events] HA ws unexpected hello: %s", hello.get("type"))
                        return
                    await ws.send(json.dumps({"type": "auth", "access_token": self._ha_token}))
                    auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
                    if auth.get("type") != "auth_ok":
                        log.error("[media_events] HA ws auth failed: %s", auth.get("type"))
                        return
                    await ws.send(json.dumps({
                        "id": 1, "type": "subscribe_events", "event_type": "state_changed",
                    }))
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        self._handle_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any transport failure
                log.warning("[media_events] HA ws error: %s; reconnecting in 5s", exc)
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=5.0)

    def _handle_message(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return
        if message.get("type") != "event":
            return
        event = message.get("event") or {}
        if event.get("event_type") != "state_changed":
            return
        new_state = (event.get("data") or {}).get("new_state")
        if new_state:
            self._on_state(new_state)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None
        ws, self._ws = self._ws, None
        if ws is not None:
            with suppress(Exception):
                await ws.close()


class MASource:
    """Music Assistant websocket event source with a ``players/all`` snapshot."""

    def __init__(
        self,
        mass_url: str,
        mass_token: str,
        on_player: Callable[[dict], None],
        on_event: Callable[[str, dict], None],
    ):
        self._on_player = on_player
        self._on_event = on_event
        self._client = MAWebSocketClient(mass_url, mass_token)

    def _callback(self, event_type: str, data: dict[str, Any]) -> None:
        self._on_event(event_type, data)

    async def start(self) -> None:
        for event_type in _MA_EVENT_TYPES:
            self._client.register_event_callback(event_type, self._callback)
        await self._client.connect()
        try:
            players = await self._client.send_command("players/all")
        except Exception as exc:  # noqa: BLE001 - snapshot is best effort
            log.warning("[media_events] MA players/all failed: %s", exc)
            players = None
        for player in players or []:
            if isinstance(player, dict):
                self._on_player(player)

    async def stop(self) -> None:
        with suppress(Exception):
            await self._client.disconnect()


class MediaEventHub:
    """Fan out normalized media events to one user's SSE subscribers."""

    def __init__(
        self,
        user: str,
        *,
        sources: list[MediaSource] | None = None,
        clock: Callable[[], float] = time.monotonic,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        queue_size: int = SUBSCRIBER_QUEUE_SIZE,
        on_stop: Callable[[], None] | None = None,
    ):
        self.user = user
        self._sources = sources or []
        self._clock = clock
        self._idle_timeout = idle_timeout
        self._heartbeat_interval = heartbeat_interval
        self._queue_size = queue_size
        self._on_stop = on_stop
        self._subscribers: set[asyncio.Queue] = set()
        self._players: dict[str, dict[str, Any]] = {}
        self._started = False
        self._stopped = False
        self._last_subscriber_left: float | None = None
        self._idle_task: asyncio.Task | None = None

    @property
    def started(self) -> bool:
        return self._started

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self._players.values())

    async def start(self) -> None:
        if self._started or self._stopped:
            return
        self._started = True
        for source in self._sources:
            try:
                await source.start()
            except Exception:  # noqa: BLE001 - one dead source must not block the hub
                log.exception("[media_events] source start failed for %s", self.user)
        self._idle_task = asyncio.create_task(self._idle_monitor())

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        task = self._idle_task
        self._idle_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for source in self._sources:
            with suppress(Exception):
                await source.stop()
        self._subscribers.clear()
        if self._on_stop is not None:
            self._on_stop()

    async def check_idle(self) -> bool:
        """Stop the hub once the idle timeout has passed with no subscribers."""
        if self._stopped or self._subscribers or self._last_subscriber_left is None:
            return False
        if self._clock() - self._last_subscriber_left < self._idle_timeout:
            return False
        await self.stop()
        return True

    async def _idle_monitor(self) -> None:
        interval = max(0.25, min(5.0, self._idle_timeout / 4))
        while not self._stopped:
            await asyncio.sleep(interval)
            if await self.check_idle():
                return

    def add_subscriber(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        self._last_subscriber_left = None
        return queue

    def remove_subscriber(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
        if not self._subscribers:
            self._last_subscriber_left = self._clock()

    def publish(self, event: dict[str, Any]) -> None:
        if event.get("type") == "player" and event.get("output_id"):
            self._players[str(event["output_id"])] = event
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    def ingest_ha_state(self, state: Mapping[str, Any]) -> None:
        event = normalize_ha_state(state)
        if event:
            self.publish(event)

    def ingest_ma_player(self, player: Mapping[str, Any]) -> None:
        event = normalize_ma_player(player)
        if event:
            self.publish(event)

    def ingest_ma_event(self, event_type: str, data: Mapping[str, Any]) -> None:
        event = normalize_ma_event(event_type, data)
        if event:
            self.publish(event)

    async def subscribe(self) -> AsyncIterator[str]:
        """SSE body: retry hint, snapshot, live events and 15s heartbeats."""
        if not self._started:
            await self.start()
        queue = self.add_subscriber()
        try:
            yield "retry: 3000\n\n"
            yield sse_event("snapshot", {"type": "snapshot", "players": self.snapshot()})
            while not self._stopped:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=self._heartbeat_interval,
                    )
                except asyncio.TimeoutError:
                    yield sse_event("heartbeat", {"type": "heartbeat"})
                    continue
                yield sse_event(str(event.get("type") or "message"), event)
        finally:
            self.remove_subscriber(queue)


_MEDIA_HUBS: dict[str, MediaEventHub] = {}


def _build_sources(hub: MediaEventHub, creds: Mapping[str, Any]) -> list[MediaSource]:
    sources: list[MediaSource] = []
    ha_url = creds.get("ha_url")
    ha_token = creds.get("ha_token")
    if ha_url and ha_token:
        sources.append(HAEntitySource(str(ha_url), str(ha_token), hub.ingest_ha_state))
    mass_url = creds.get("mass_url")
    mass_token = creds.get("mass_token")
    if mass_url and mass_token:
        sources.append(MASource(
            str(mass_url), str(mass_token), hub.ingest_ma_player, hub.ingest_ma_event,
        ))
    return sources


async def acquire_media_hub(user: str, creds: Mapping[str, Any]) -> MediaEventHub:
    """Return the user's hub, creating (but not starting) it on first use."""
    hub = _MEDIA_HUBS.get(user)
    if hub is not None and not hub.stopped:
        return hub

    def _remove() -> None:
        if _MEDIA_HUBS.get(user) is hub:
            _MEDIA_HUBS.pop(user, None)

    hub = MediaEventHub(user, on_stop=_remove)
    hub._sources = _build_sources(hub, creds)
    _MEDIA_HUBS[user] = hub
    return hub


def get_media_hub(user: str) -> MediaEventHub | None:
    return _MEDIA_HUBS.get(user)


async def stop_all_media_hubs() -> None:
    for hub in list(_MEDIA_HUBS.values()):
        await hub.stop()
    _MEDIA_HUBS.clear()
