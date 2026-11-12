"""Music Assistant: its players as HA media_players, plus HA's ``music_assistant.*`` services.

MA ran as an HA add-on and moves to a standalone container (see
``services/homehub/MUSIC_ASSISTANT.md``). The add-on's URL in the HA entry
(``http://d5369777-music-assistant:8094``) only resolved inside HA's network,
so the URL is taken from, in order: ``entry.options["url"]``, the
``MUSIC_ASSISTANT_URL`` environment variable, ``entry.data["url"]``. The
token (the long-lived token MA issued to HA, kept in MA's ``auth.db``) comes
from ``options["token"]``, ``MUSIC_ASSISTANT_TOKEN`` or ``data["token"]``.

One persistent WebSocket (``/ws``, JSON-RPC: ``{"message_id", "command",
"args"}``) carries commands and MA's pushed events (``player_updated``,
``queue_updated``, ``queue_time_updated``...). A poll every 15 s refetches all
players and queues as well, so a missed event costs at most one poll.

Entities map by unique_id: a media_player's unique_id is the MA player_id
(``media_player.office_jarvis``, ``up309587157251``, a cast UUID...), and the
"favorite current song" button is ``<player_id>_favorite_now_playing``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import urllib.parse
import uuid
from collections.abc import Callable
from typing import Any

import aiohttp

from services.homehub.core import (
    UNAVAILABLE,
    DeviceUnavailable,
    Driver,
    EntityEntry,
    HubError,
    ServiceCall,
    ServiceNotFound,
)
from services.homehub.drivers.media_common import F, as_list, clamp01, clean, utc_iso

COMMAND_TIMEOUT_S = 30.0
FAVORITE_SUFFIX = "_favorite_now_playing"

#: HA's music_assistant base feature set (every MA player), then the optional bits.
BASE_FEATURES = (F.PAUSE | F.SEEK | F.PREVIOUS_TRACK | F.NEXT_TRACK | F.PLAY_MEDIA | F.STOP | F.CLEAR_PLAYLIST
                 | F.PLAY | F.SHUFFLE_SET | F.BROWSE_MEDIA | F.REPEAT_SET | F.MEDIA_ANNOUNCE | F.MEDIA_ENQUEUE
                 | F.SEARCH_MEDIA)

#: HA media_player.play_media ``enqueue`` -> MA QueueOption.
ENQUEUE = {"add": "add", "next": "next", "play": "play", "replace": "replace", "replace_next": "replace_next"}

#: get_library media_type -> MA library command.
LIBRARY = {"artist": "artists", "album": "albums", "track": "tracks", "playlist": "playlists",
           "radio": "radios", "audiobook": "audiobooks", "podcast": "podcasts"}
SEARCH_KEYS = ("artists", "albums", "tracks", "playlists", "radio", "audiobooks", "podcasts")


class MACommandError(HubError):
    """MA answered a command with an error_code."""


class MAConnection:
    """One authenticated WebSocket to MA, with a reader task routing results and events."""

    def __init__(self, url: str, token: str | None, on_event: Callable[[dict[str, Any]], None],
                 on_close: Callable[[], None]) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.on_event = on_event
        self.on_close = on_close
        self.server_info: dict[str, Any] = {}
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self._futures: dict[str, asyncio.Future] = {}
        self._partial: dict[str, list[Any]] = {}

    @property
    def ws_url(self) -> str:
        base = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return base if base.endswith("/ws") else base + "/ws"

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self.ws_url, heartbeat=55, max_msg_size=0,
                                                      timeout=aiohttp.ClientWSTimeout(ws_close=10))
            first = await asyncio.wait_for(self._ws.receive(), 15)
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            await self.close()
            raise DeviceUnavailable(f"Music Assistant at {self.url} did not answer: {exc}") from exc
        if first.type != aiohttp.WSMsgType.TEXT:
            await self.close()
            raise DeviceUnavailable(f"Music Assistant at {self.url} closed the socket")
        self.server_info = json.loads(first.data)
        self._reader = asyncio.create_task(self._read_loop())
        if self.token:
            try:
                ok = await self.command("auth", token=self.token)
            except MACommandError as exc:
                await self.close()
                raise HubError(f"Music Assistant refused the token: {exc}") from exc
            if not ok:
                await self.close()
                raise HubError("Music Assistant refused the token")

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                        break
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                self.dispatch(data)
        finally:
            for fut in self._futures.values():
                if not fut.done():
                    fut.set_exception(DeviceUnavailable("Music Assistant connection closed"))
            self._futures.clear()
            with contextlib.suppress(Exception):
                self.on_close()

    def dispatch(self, data: dict[str, Any]) -> None:
        """Route one decoded message: an event, a (partial) result or an error."""
        if "event" in data:
            self.on_event(data)
            return
        mid = str(data.get("message_id", ""))
        fut = self._futures.get(mid)
        if fut is None or fut.done():
            self._partial.pop(mid, None)
            return
        if "error_code" in data:
            self._partial.pop(mid, None)
            fut.set_exception(MACommandError(f"{data.get('error_code')}: {data.get('details')}"))
            return
        result = data.get("result")
        if data.get("partial"):
            self._partial.setdefault(mid, []).extend(result or [])
            return
        if mid in self._partial:
            result = self._partial.pop(mid) + list(result or [])
        fut.set_result(result)

    async def command(self, command: str, **args: Any) -> Any:
        if not self.connected:
            raise DeviceUnavailable("Music Assistant is not connected")
        mid = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._futures[mid] = fut
        msg: dict[str, Any] = {"message_id": mid, "command": command}
        payload = {k: v for k, v in args.items() if v is not None}
        if payload:
            msg["args"] = payload
        try:
            await self._ws.send_str(json.dumps(msg))  # type: ignore[union-attr]
            return await asyncio.wait_for(fut, COMMAND_TIMEOUT_S)
        except TimeoutError as exc:
            raise DeviceUnavailable(f"Music Assistant did not answer {command}") from exc
        finally:
            self._futures.pop(mid, None)

    async def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
            with contextlib.suppress(BaseException):
                await self._reader
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.close()
        self._ws = self._session = self._reader = None


def _image_url(image: Any, base_url: str) -> str | None:
    """MA's own rule (music_assistant_client.get_image_url, size 0)."""
    if isinstance(image, str):
        return image or None
    if not isinstance(image, dict) or not image.get("path"):
        return None
    if image.get("remotely_accessible"):
        return image["path"]
    if image.get("proxy_id"):
        return f"{base_url}/imageproxy/{image['proxy_id']}?size=0"
    encoded = urllib.parse.quote(urllib.parse.quote(str(image["path"])))
    return f"{base_url}/imageproxy?path={encoded}&provider={image.get('provider', '')}&size=0"


def _item_image(item: dict[str, Any]) -> Any:
    if item.get("image"):
        return item["image"]
    images = ((item.get("metadata") or {}).get("images")) or []
    return next((i for i in images if i.get("type") == "thumb"), images[0] if images else None)


class MusicAssistantDriver(Driver):
    platform = "music_assistant"
    poll_interval = 15.0
    connection_factory = MAConnection

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.url = str(entry.options.get("url") or os.environ.get("MUSIC_ASSISTANT_URL")
                       or entry.data.get("url") or "")
        self.token = (entry.options.get("token") or os.environ.get("MUSIC_ASSISTANT_TOKEN")
                      or entry.data.get("token"))
        self.conn: Any = None
        self.players: dict[str, dict[str, Any]] = {}
        self.queues: dict[str, dict[str, Any]] = {}
        self._pressed: dict[str, str] = {}

    # -- lifecycle ------------------------------------------------------------------
    async def async_setup(self) -> None:
        if not self.url:
            raise HubError("music_assistant has no URL: set options.url or MUSIC_ASSISTANT_URL")
        for service, schema in SERVICE_SCHEMAS.items():
            self.hub.register_service("music_assistant", service, _ma_service, schema)
        await self._connect()

    async def _connect(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None
        conn = self.connection_factory(self.url, self.token, self._on_event, self._on_close)
        await conn.connect()
        self.conn = conn
        await self.refresh()

    def _on_close(self) -> None:
        self.log.warning("Music Assistant connection to %s closed", self.url)
        self.mark_unavailable()

    async def async_poll(self) -> None:
        if self.conn is None or not self.conn.connected:
            try:
                await self._connect()
            except HubError as exc:
                self.mark_unavailable()
                raise DeviceUnavailable(f"Music Assistant reconnect failed: {exc}") from exc
            return
        await self.refresh()

    async def async_unload(self) -> None:
        if self.conn is not None:
            await self.conn.close()

    @property
    def base_url(self) -> str:
        return str((self.conn.server_info if self.conn else {}).get("base_url") or self.url).rstrip("/")

    async def refresh(self) -> None:
        players = await self.cmd("players/all") or []
        queues = await self.cmd("player_queues/all") or []
        self.players = {p["player_id"]: p for p in players if isinstance(p, dict) and p.get("player_id")}
        self.queues = {q["queue_id"]: q for q in queues if isinstance(q, dict) and q.get("queue_id")}
        self.publish_all()

    async def cmd(self, command: str, **args: Any) -> Any:
        if self.conn is None or not self.conn.connected:
            raise DeviceUnavailable("Music Assistant is not connected")
        return await self.conn.command(command, **args)

    # -- events -----------------------------------------------------------------------
    def _on_event(self, msg: dict[str, Any]) -> None:
        event, oid, data = msg.get("event"), msg.get("object_id"), msg.get("data")
        if event in ("player_added", "player_updated") and isinstance(data, dict) and data.get("player_id"):
            self.players[data["player_id"]] = data
            self.publish_player(data["player_id"])
        elif event == "player_removed" and oid:
            self.players.pop(str(oid), None)
            self.publish_player(str(oid))
        elif event in ("queue_added", "queue_updated") and isinstance(data, dict) and data.get("queue_id"):
            self.queues[data["queue_id"]] = data
            self._publish_queue(data["queue_id"])
        elif event == "queue_time_updated" and oid in self.queues:
            # MA sends this every second while playing. Readers extrapolate from
            # media_position_updated_at, as HA's do, so only a jump (a seek) is published:
            # a state write per second per speaker would flood the history store.
            q = self.queues[oid]
            try:
                new = float(data)
            except (TypeError, ValueError):
                return
            now = time.time()
            old, ts = q.get("elapsed_time"), q.get("elapsed_time_last_updated")
            expected = None
            if old is not None and ts:
                expected = float(old) + (now - float(ts) if q.get("state") == "playing" else 0.0)
            q["elapsed_time"], q["elapsed_time_last_updated"] = new, now
            if expected is None or abs(new - expected) > 5:
                self._publish_queue(str(oid))

    def _publish_queue(self, queue_id: str) -> None:
        for pid, p in self.players.items():
            if pid == queue_id or p.get("active_source") == queue_id:
                self.publish_player(pid)

    # -- state ------------------------------------------------------------------------
    def entity_for(self, player_id: str) -> EntityEntry | None:
        return next((e for e in self.entities if e.domain == "media_player" and e.unique_id == player_id), None)

    def player_for(self, entity: EntityEntry) -> dict[str, Any]:
        p = self.players.get(entity.unique_id)
        if p is None:
            raise DeviceUnavailable(f"Music Assistant has no player {entity.unique_id} for {entity.entity_id}")
        return p

    def queue_for(self, player: dict[str, Any]) -> dict[str, Any] | None:
        src = player.get("active_source")
        if src and src in self.queues:
            return self.queues[src]
        return self.queues.get(player["player_id"])

    def publish_all(self) -> None:
        for e in self.entities:
            if e.domain == "media_player":
                self.publish_player(e.unique_id)
            elif e.domain == "button":
                self.publish(e, self._pressed.get(e.unique_id, "unknown"), {})

    @staticmethod
    def _features(p: dict[str, Any]) -> int:
        feats = set(p.get("supported_features") or [])
        sf = BASE_FEATURES
        if "volume_set" in feats or p.get("volume_control", "none") != "none":
            sf |= F.VOLUME_SET | F.VOLUME_STEP
        if "volume_mute" in feats or p.get("mute_control", "none") != "none":
            sf |= F.VOLUME_MUTE
        if "power" in feats or p.get("power_control", "none") != "none":
            sf |= F.TURN_ON | F.TURN_OFF
        if "set_members" in feats:
            sf |= F.GROUPING
        if "select_source" in feats:
            sf |= F.SELECT_SOURCE
        if "select_sound_mode" in feats:
            sf |= F.SELECT_SOUND_MODE
        return sf

    def player_state(self, p: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if not p.get("available", True) or not p.get("enabled", True):
            return UNAVAILABLE, {}
        q = self.queue_for(p)
        queue_active = q is not None and bool(q.get("active"))
        raw = (q.get("state") if queue_active and q else None) or p.get("playback_state") or p.get("state") or "idle"
        state = raw if raw in ("playing", "paused", "idle") else "idle"
        if p.get("powered") is False:
            state = "off"
        media = p.get("current_media") or {}
        item = ((q or {}).get("current_item") or {}) if queue_active else {}
        mitem = item.get("media_item") or {}
        artists = mitem.get("artists") or []
        album = mitem.get("album") or {}
        if queue_active and q:
            position, pos_ts = q.get("elapsed_time"), q.get("elapsed_time_last_updated")
        else:
            position = media.get("elapsed_time", p.get("elapsed_time"))
            pos_ts = media.get("elapsed_time_last_updated", p.get("elapsed_time_last_updated"))
        image = media.get("image_url") or _image_url(item.get("image") or _item_image(mitem), self.base_url)
        members = [m for m in (p.get("group_members") or p.get("group_childs") or []) if m != p["player_id"]]
        member_ids = [e.entity_id for m in [p["player_id"], *members] if (e := self.entity_for(m))] if members else None
        sources = [s.get("name") or s.get("id") for s in (p.get("source_list") or []) if isinstance(s, dict)]
        active_source = p.get("active_source")
        source_name = next((s.get("name") for s in (p.get("source_list") or [])
                            if isinstance(s, dict) and s.get("id") == active_source), None)
        attrs: dict[str, Any] = {
            "volume_level": p["volume_level"] / 100 if isinstance(p.get("volume_level"), (int, float)) else None,
            "is_volume_muted": p.get("volume_muted"),
            "group_members": member_ids,
            "source_list": sources or None,
            "source": source_name,
            "shuffle": q.get("shuffle_enabled") if q else None,
            "repeat": q.get("repeat_mode") if q else None,
            "app_id": "music_assistant" if queue_active else active_source,
            "mass_player_type": p.get("type"),
            "mass_player_id": p["player_id"],
            "active_queue": q.get("queue_id") if queue_active and q else None,
            "device_class": "speaker",
            "supported_features": self._features(p),
        }
        if state in ("playing", "paused", "idle") and (media or mitem):
            attrs.update({
                "media_content_id": media.get("uri") or mitem.get("uri"),
                "media_content_type": "music" if (media.get("media_type") or mitem.get("media_type"))
                in ("track", "radio", "podcast_episode", "audiobook", None) else media.get("media_type"),
                "media_title": media.get("title") or mitem.get("name") or item.get("name"),
                "media_artist": media.get("artist") or ", ".join(a.get("name", "") for a in artists) or None,
                "media_album_name": media.get("album") or album.get("name"),
                "media_album_artist": media.get("album_artist"),
                "media_duration": media.get("duration") or item.get("duration") or mitem.get("duration"),
                "media_position": position,
                "media_position_updated_at": utc_iso(pos_ts) if position is not None else None,
                "entity_picture": image,
            })
        out = clean(attrs)
        out.setdefault("active_queue", None)  # HA always carries the key; Jarvis tests it for None
        return state, out

    def publish_player(self, player_id: str) -> None:
        ent = self.entity_for(player_id)
        if ent is None:
            return
        p = self.players.get(player_id)
        if p is None:
            self.mark_unavailable([ent])
            return
        state, attrs = self.player_state(p)
        if state == UNAVAILABLE:
            self.mark_unavailable([ent])
        else:
            self.publish(ent, state, attrs)

    # -- entity services ------------------------------------------------------------------
    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain == "button" and service == "press":
            pid = entity.unique_id.removesuffix(FAVORITE_SUFFIX)
            await self.cmd("players/add_currently_playing_to_favorites", player_id=pid)
            self._pressed[entity.unique_id] = utc_iso()
            self.publish(entity, self._pressed[entity.unique_id], {})
            return None
        if domain != "media_player":
            raise ServiceNotFound(f"music_assistant has no {domain}.{service}")
        p = self.player_for(entity)
        pid = p["player_id"]
        q = self.queue_for(p)
        qid = q["queue_id"] if q else pid
        simple = {"media_play": "players/cmd/play", "media_pause": "players/cmd/pause",
                  "media_play_pause": "players/cmd/play_pause", "media_stop": "players/cmd/stop",
                  "media_next_track": "players/cmd/next", "media_previous_track": "players/cmd/previous",
                  "volume_up": "players/cmd/volume_up", "volume_down": "players/cmd/volume_down"}
        if service in simple:
            await self.cmd(simple[service], player_id=pid)
        elif service in ("turn_on", "turn_off", "toggle"):
            powered = service == "turn_on" or (service == "toggle" and p.get("powered") is False)
            await self.cmd("players/cmd/power", player_id=pid, powered=powered)
        elif service == "volume_set":
            await self.cmd("players/cmd/volume_set", player_id=pid,
                           volume_level=round(clamp01(data.get("volume_level", 0)) * 100))
        elif service == "volume_mute":
            await self.cmd("players/cmd/volume_mute", player_id=pid, muted=bool(data.get("is_volume_muted", True)))
        elif service == "media_seek":
            await self.cmd("players/cmd/seek", player_id=pid, position=round(float(data.get("seek_position") or 0)))
        elif service == "shuffle_set":
            await self.cmd("player_queues/shuffle", queue_id=qid, shuffle_enabled=bool(data.get("shuffle")))
        elif service == "repeat_set":
            mode = str(data.get("repeat") or "off")
            if mode not in ("off", "one", "all"):
                raise HubError(f"repeat must be off, one or all, not {mode!r}")
            await self.cmd("player_queues/repeat", queue_id=qid, repeat_mode=mode)
        elif service == "clear_playlist":
            await self.cmd("player_queues/clear", queue_id=qid)
        elif service == "select_source":
            source = str(data.get("source") or "")
            sid = next((s.get("id") for s in (p.get("source_list") or [])
                        if isinstance(s, dict) and source in (s.get("id"), s.get("name"))), source)
            await self.cmd("players/cmd/select_source", player_id=pid, source=sid)
        elif service == "play_media":
            extra = data.get("extra") or {}
            if data.get("announce"):
                await self.play_announcement(pid, str(data.get("media_content_id") or ""),
                                             extra.get("use_pre_announce"), extra.get("announce_volume"))
            else:
                await self.play_media(pid, data.get("media_content_id"), media_type=_ma_type(data.get(
                    "media_content_type")), enqueue=data.get("enqueue"), radio_mode=extra.get("radio_mode"))
        else:
            raise ServiceNotFound(f"music_assistant cannot run media_player.{service}")
        return None

    # -- the operations both entity and music_assistant.* services use -------------------
    async def resolve_uris(self, media_ids: Any, media_type: str | None, artist: str | None = None,
                           album: str | None = None) -> list[str]:
        uris: list[str] = []
        for mid in [str(m) for m in as_list(media_ids) if str(m).strip()]:
            if "://" in mid:
                uris.append(mid)
                continue
            if media_type and mid.isnumeric():
                item = await self.cmd("music/item", media_type=media_type, item_id=mid,
                                      provider_instance_id_or_domain="library")
            else:
                item = await self.cmd("music/item_by_name", name=mid, artist=artist, album=album,
                                      media_type=media_type)
            if not isinstance(item, dict) or not item.get("uri"):
                raise HubError(f"Music Assistant found nothing for {mid!r}")
            uris.append(item["uri"])
        if not uris:
            raise HubError("play_media needs a media_id")
        return uris

    async def play_media(self, player_id: str, media_ids: Any, *, media_type: str | None = None,
                         artist: str | None = None, album: str | None = None, enqueue: str | None = None,
                         radio_mode: bool | None = None) -> None:
        uris = await self.resolve_uris(media_ids, media_type, artist, album)
        option = ENQUEUE.get(str(enqueue)) if enqueue else None
        await self.cmd("player_queues/play_media", queue_id=player_id, media=uris, option=option,
                       radio_mode=True if radio_mode else None)

    async def play_announcement(self, player_id: str, url: str, use_pre_announce: Any = None,
                                announce_volume: Any = None, pre_announce_url: str | None = None) -> None:
        if not url:
            raise HubError("play_announcement needs a url")
        await self.cmd("players/cmd/play_announcement", player_id=player_id, url=url,
                       pre_announce=None if use_pre_announce is None else bool(use_pre_announce),
                       volume_level=None if announce_volume is None else int(announce_volume),
                       pre_announce_url=pre_announce_url)

    def media_item_dict(self, item: Any) -> dict[str, Any]:
        """HA's ``media_item_dict_from_mass_item`` shape."""
        if not isinstance(item, dict):
            return {}
        out: dict[str, Any] = {
            "media_type": item.get("media_type"), "uri": item.get("uri"), "name": item.get("name"),
            "version": item.get("version", ""), "image": _image_url(_item_image(item), self.base_url),
        }
        if item.get("artists"):
            out["artists"] = [self.media_item_dict(a) for a in item["artists"]]
        if isinstance(item.get("album"), dict):
            out["album"] = self.media_item_dict(item["album"])
        return out

    def queue_item_dict(self, qi: Any) -> dict[str, Any] | None:
        if not isinstance(qi, dict):
            return None
        out = {"queue_item_id": qi.get("queue_item_id"), "name": qi.get("name"), "duration": qi.get("duration"),
               "media_item": self.media_item_dict(qi.get("media_item"))}
        title = ((qi.get("streamdetails") or {}).get("stream_title"))
        if title:
            out["stream_title"] = title
        return out

    async def queue_response(self, player_id: str) -> dict[str, Any]:
        p = self.players.get(player_id) or {"player_id": player_id}
        q = None
        with contextlib.suppress(HubError):
            q = await self.cmd("player_queues/get_active_queue", player_id=player_id)
        q = q if isinstance(q, dict) else self.queue_for(p)
        if not q:
            raise HubError(f"Music Assistant has no queue for {player_id}")
        return {"queue_id": q.get("queue_id"), "active": q.get("active"), "name": q.get("display_name"),
                "items": q.get("items"), "shuffle_enabled": q.get("shuffle_enabled"),
                "repeat_mode": q.get("repeat_mode"), "current_index": q.get("current_index"),
                "elapsed_time": q.get("elapsed_time"), "current_item": self.queue_item_dict(q.get("current_item")),
                "next_item": self.queue_item_dict(q.get("next_item"))}

    async def search(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "")
        artist, album = data.get("artist"), data.get("album")
        if album and artist:
            name = f"{artist} - {album} - {name}"
        elif album:
            name = f"{album} - {name}"
        elif artist:
            name = f"{artist} - {name}"
        types = [str(t) for t in as_list(data.get("media_type"))] or None
        result = await self.cmd("music/search", search_query=name, media_types=types,
                                limit=int(data.get("limit") or 5), library_only=bool(data.get("library_only", False)))
        result = result if isinstance(result, dict) else {}
        return {k: [self.media_item_dict(i) for i in (result.get(k) or [])] for k in SEARCH_KEYS}

    async def get_library(self, data: dict[str, Any]) -> dict[str, Any]:
        media_type = str(data.get("media_type") or "")
        if media_type not in LIBRARY:
            raise HubError(f"get_library media_type must be one of {sorted(LIBRARY)}")
        limit, offset = int(data.get("limit") or 25), int(data.get("offset") or 0)
        order_by = data.get("order_by")
        args: dict[str, Any] = {"favorite": data.get("favorite"), "search": data.get("search"), "limit": limit,
                                "offset": offset, "order_by": order_by}
        if media_type == "album" and data.get("album_type"):
            args["album_types"] = as_list(data.get("album_type"))
        if media_type == "artist" and data.get("album_artists_only") is not None:
            args["album_artists_only"] = bool(data.get("album_artists_only"))
        items = await self.cmd(f"music/{LIBRARY[media_type]}/library_items", **args) or []
        return {"items": [self.media_item_dict(i) for i in items], "limit": limit, "offset": offset,
                "order_by": order_by, "media_type": media_type}


def _ma_type(content_type: Any) -> str | None:
    ct = str(content_type or "").lower()
    return {"music": None, "url": None, "": None, "playlist": "playlist", "album": "album", "artist": "artist",
            "track": "track", "radio": "radio", "podcast": "podcast", "audiobook": "audiobook",
            "genre": "genre", "folder": "folder"}.get(ct)


SERVICE_SCHEMAS: dict[str, dict[str, Any]] = {
    "play_media": {"media_id": {"required": True}, "media_type": {}, "artist": {}, "album": {}, "enqueue": {},
                   "radio_mode": {}},
    "play_announcement": {"url": {"required": True}, "use_pre_announce": {}, "announce_volume": {}},
    "transfer_queue": {"source_player": {}, "auto_play": {}},
    "get_queue": {},
    "search": {"config_entry_id": {}, "name": {"required": True}, "media_type": {}, "artist": {}, "album": {},
               "limit": {}, "library_only": {}},
    "get_library": {"config_entry_id": {}, "media_type": {"required": True}, "favorite": {}, "search": {},
                    "limit": {}, "offset": {}, "order_by": {}, "album_type": {}, "album_artists_only": {}},
}


def _targets(hub, call: ServiceCall) -> list[tuple[MusicAssistantDriver, EntityEntry]]:
    out = []
    for eid in call.entity_ids:
        ent = hub.entities.get(eid)
        drv = hub.drivers.get(ent.config_entry_id or "") if ent else None
        if isinstance(drv, MusicAssistantDriver) and ent is not None and ent.domain == "media_player":
            out.append((drv, ent))
    if not out:
        raise HubError(f"music_assistant.{call.service} needs a Music Assistant media_player target")
    return out


def _driver_for_entry(hub, call: ServiceCall) -> MusicAssistantDriver:
    wanted = call.data.get("config_entry_id")
    drivers = [d for d in hub.drivers.values() if isinstance(d, MusicAssistantDriver)]
    for d in drivers:
        if not wanted or d.entry.entry_id == wanted:
            return d
    if drivers:  # an entry id from the HA era that the hub renamed still means "the" MA server
        return drivers[0]
    raise HubError("Music Assistant is not running")


async def _ma_service(hub, call: ServiceCall) -> Any:
    """HA's ``music_assistant.*`` services, with the data keys HA's integration takes."""
    d = call.data
    s = call.service
    if s == "search":
        return await _driver_for_entry(hub, call).search(d)
    if s == "get_library":
        return await _driver_for_entry(hub, call).get_library(d)
    targets = _targets(hub, call)
    if s == "get_queue":
        return {ent.entity_id: await drv.queue_response(ent.unique_id) for drv, ent in targets}
    for drv, ent in targets:
        pid = ent.unique_id
        if s == "play_media":
            await drv.play_media(pid, d.get("media_id"), media_type=d.get("media_type"), artist=d.get("artist"),
                                 album=d.get("album"), enqueue=d.get("enqueue"), radio_mode=d.get("radio_mode"))
        elif s == "play_announcement":
            await drv.play_announcement(pid, str(d.get("url") or ""), d.get("use_pre_announce"),
                                        d.get("announce_volume"), d.get("pre_announce_url"))
        elif s == "transfer_queue":
            source_qid = None
            if d.get("source_player"):
                src = hub.entities.get(str(d["source_player"]))
                if src is None:
                    raise HubError(f"unknown source_player {d['source_player']}")
                sp = drv.players.get(src.unique_id) or {"player_id": src.unique_id}
                sq = drv.queue_for(sp)
                source_qid = sq["queue_id"] if sq else src.unique_id
            else:
                source_qid = next((qid for qid, q in drv.queues.items() if q.get("state") == "playing"), None)
                if source_qid is None:
                    raise HubError("Source queue not specified and no active queue found.")
            await drv.cmd("player_queues/transfer", source_queue_id=source_qid, target_queue_id=pid,
                          auto_play=d.get("auto_play"))
        else:
            raise ServiceNotFound(f"music_assistant.{s} is not supported")
    return None
