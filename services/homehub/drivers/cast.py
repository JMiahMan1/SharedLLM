"""Google Cast (Chromecast, Google TV, Cast-built-in TVs) over pychromecast.

HA keeps one ``cast`` entry for every Cast device; each media_player's
unique_id is the device's cast UUID. Devices are found by zeroconf (the hub
runs on the host network) plus the entry's ``known_hosts``, and only UUIDs
with an entity here are connected. pychromecast keeps a socket per device and
pushes status from its own threads, which are handed to the event loop with
``call_soon_threadsafe``; every command runs in a worker thread.

States follow HA's cast integration: playing/paused/idle from the media
session, ``off`` when no app (or only the backdrop) runs, ``on`` for an app
without a media session, ``unavailable`` while the socket is down.

The device describes itself: once connected, its Cast info (maker, model, cast
UUID) is confirmed onto its device, and the feature flags are computed from its
live status (volume control type, what the media session supports). Cast does
not report a MAC or firmware version, so those stay unset (discovery's
neighbour table supplies the MAC for matching).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from typing import Any
from uuid import UUID

import pychromecast
import zeroconf
from pychromecast.discovery import CastBrowser, SimpleCastListener
from pychromecast.response_handler import WaitResponse

from services.homehub.core import UNAVAILABLE, DeviceUnavailable, Driver, EntityEntry, HubError, ServiceNotFound
from services.homehub.drivers.media_common import F, clamp01, clean, device_twins, mime_for

MEDIA_RECEIVER_APP_ID = "CC1AD845"
# An Android/Google TV reports its foreground Android app (Spotify, YouTube) under this id.
ANDROID_NATIVE_APP_ID = "AndroidNativeApp"
_NO_REMOTE = object()
COMMAND_TIMEOUT_S = 10.0
# Loading media may first switch the TV from another app (Spotify, YouTube) to the media receiver.
LOAD_TIMEOUT_S = 30.0
# An announcement waits for its clip to end before putting back what was playing: the clip's own
# length when it is known (a hub clip's WAV header, the ``duration`` Cast reports), this when not.
ANNOUNCE_UNKNOWN_S = 30.0
ANNOUNCE_MAX_S = 600.0
ANNOUNCE_MARGIN_S = 3.0
ANNOUNCE_POLL_S = 0.25


def _squash(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _same_app(cast_name: Any, on_screen: str) -> bool:
    """Whether the app Cast names ("Spotify", "Prime Video") is the one on screen, given the
    remote's app name and package ("Spotify com.spotify.tv.android"). Any app, known or not:
    the name appears in the package or the known name."""
    name = _squash(cast_name)
    return bool(name) and name in _squash(on_screen)


class _Listener:
    """pychromecast status/connection listener for one device; runs on pychromecast threads."""

    def __init__(self, driver: CastDriver, key: str) -> None:
        self.driver, self.key = driver, key

    def new_cast_status(self, _status: Any) -> None:
        self.driver._threadsafe_publish(self.key)

    def new_media_status(self, _status: Any) -> None:
        self.driver._threadsafe_publish(self.key)

    def load_media_failed(self, queue_item_id: int, error_code: int) -> None:
        self.driver.log.warning("cast %s could not load media (item %s, error %s)", self.key, queue_item_id, error_code)

    def new_connection_status(self, status: Any) -> None:
        self.driver.connection[self.key] = getattr(status, "status", "")
        self.driver._threadsafe_publish(self.key)


class CastDriver(Driver):
    platform = "cast"
    poll_interval = 15.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.casts: dict[str, Any] = {}
        self.hosts: dict[str, str] = {}
        self.connection: dict[str, str] = {}
        self.browser: Any = None
        self.zconf: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connecting: set[str] = set()
        #: cast key -> {"snap": what to put back, "task": the task that will} while an announcement plays.
        self.announcing: dict[str, dict[str, Any]] = {}

    # Seams the tests replace.
    @staticmethod
    def zeroconf_factory() -> Any:
        return zeroconf.Zeroconf()

    @staticmethod
    def cast_factory(cast_info: Any, zconf: Any) -> Any:
        return pychromecast.get_chromecast_from_cast_info(cast_info, zconf, tries=None, timeout=COMMAND_TIMEOUT_S)

    # -- discovery ------------------------------------------------------------------
    def wanted(self) -> dict[str, EntityEntry]:
        """cast UUID (lower-case, dashed) -> media_player entity."""
        out = {}
        for e in self.entities:
            if e.domain != "media_player":
                continue
            try:
                out[str(UUID(e.unique_id))] = e
            except ValueError:
                self.log.warning("cast entity %s has a non-UUID unique_id %r", e.entity_id, e.unique_id)
        return out

    async def async_setup(self) -> None:
        self._loop = asyncio.get_running_loop()
        hosts = [str(h) for h in [*(self.entry.options.get("known_hosts") or []),
                                  *(self.entry.data.get("known_hosts") or [])] if h]
        await asyncio.to_thread(self._start_discovery, hosts)
        for key, ent in self.wanted().items():
            if key not in self.casts:
                self.mark_unavailable([ent])

    def _start_discovery(self, hosts: list[str]) -> None:
        self.zconf = self.zeroconf_factory()
        self.browser = CastBrowser(SimpleCastListener(self._on_found, None, self._on_found), self.zconf, hosts)
        self.browser.start_discovery()

    def _on_found(self, uuid: UUID, _service: str) -> None:
        """Discovery thread: a device appeared or changed. Connect it if an entity wants it."""
        key = str(uuid)
        if key in self.wanted() and key not in self.casts and self._loop is not None:
            self._loop.call_soon_threadsafe(self._spawn_connect, key)

    def _spawn_connect(self, key: str) -> None:
        if key in self._connecting or key in self.casts:
            return
        self._connecting.add(key)
        task = asyncio.ensure_future(self._connect(key))
        task.add_done_callback(lambda t: self._connecting.discard(key))

    async def _connect(self, key: str) -> None:
        info = (self.browser.devices if self.browser is not None else {}).get(UUID(key))
        if info is None:
            return
        try:
            cc = await asyncio.to_thread(self.cast_factory, info, self.zconf)
            listener = _Listener(self, key)
            cc.register_status_listener(listener)
            cc.media_controller.register_status_listener(listener)
            cc.register_connection_listener(listener)
            await asyncio.to_thread(cc.start)
        except Exception as exc:  # one bad device must not stop the others
            self.log.warning("cast %s (%s) could not connect: %s", key, getattr(info, "friendly_name", ""), exc)
            return
        self.casts[key] = cc
        self.hosts[key] = str(getattr(info, "host", "") or "")
        ent = self.wanted().get(key)
        if ent is not None:
            self.confirm_device(ent, manufacturer=getattr(info, "manufacturer", None) or None,
                                model=getattr(info, "model_name", None) or None, identifiers=[["cast", key]])
        self.log.info("cast %s connected (%s at %s)", key, getattr(info, "friendly_name", ""), getattr(info, "host", ""))
        self.publish_key(key)

    async def async_poll(self) -> None:
        devices = self.browser.devices if self.browser is not None else {}
        for key in self.wanted():
            if key not in self.casts and UUID(key) in devices:
                self._spawn_connect(key)
            self.publish_key(key)

    async def async_unload(self) -> None:
        for pending in list(self.announcing.values()):
            pending["task"].cancel()
        self.announcing.clear()
        for cc in list(self.casts.values()):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.to_thread(cc.disconnect, 2), 5)
        self.casts.clear()
        if self.browser is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.to_thread(self.browser.stop_discovery), 5)

    # -- state ----------------------------------------------------------------------
    def _threadsafe_publish(self, key: str) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self.publish_key, key)

    @staticmethod
    def _media_status(cc: Any) -> Any:
        ms = getattr(getattr(cc, "media_controller", None), "status", None)
        if ms is None or getattr(ms, "player_state", "UNKNOWN") in (None, "UNKNOWN"):
            return None
        return ms

    def state_of(self, key: str) -> tuple[str, dict[str, Any]]:
        cc = self.casts.get(key)
        if cc is None or self.connection.get(key, "CONNECTED") != "CONNECTED" or cc.status is None:
            return UNAVAILABLE, {}
        cs = cc.status
        ms = self._media_status(cc)
        app_id = getattr(cc, "app_id", None) or getattr(cs, "app_id", None)
        # An Android/Google TV's own apps report through Cast as "AndroidNativeApp", and that
        # report can be false: one Google TV's built-in Spotify app said Spotify was paused or
        # "playing" though Spotify was never played on it. So a native app's session counts
        # only while the TV is awake and, when Jarvis also has the TV's remote connection,
        # that app is the one on screen. Real playback in the app still shows.
        native = app_id == ANDROID_NATIVE_APP_ID
        on_screen = self._screen_app(key) if native else _NO_REMOTE
        asleep = native and getattr(cs, "is_stand_by", None) is True
        hidden = asleep or (native and on_screen is not _NO_REMOTE
                            and not _same_app(getattr(cs, "display_name", None), on_screen))
        if hidden:
            ms = None
            state = "off" if asleep or on_screen == "" else "on"
        elif ms is not None and ms.player_state in ("PLAYING", "BUFFERING"):
            state = "playing"
        elif ms is not None and ms.player_is_paused:
            state = "paused"
        elif ms is not None and ms.player_is_idle and not cc.is_idle:
            state = "idle"
        elif cc.is_idle:
            state = "off"
        else:
            state = "on"
        sf = F.TURN_ON | F.TURN_OFF | F.PLAY_MEDIA | F.BROWSE_MEDIA | F.MEDIA_ANNOUNCE
        if getattr(cs, "volume_control_type", "attenuation") != "fixed":
            sf |= F.VOLUME_MUTE | F.VOLUME_SET | F.VOLUME_STEP
        attrs: dict[str, Any] = {
            "volume_level": cs.volume_level, "is_volume_muted": cs.volume_muted,
        }
        if state != "off" and not hidden:
            attrs.update(app_id=app_id, app_name=getattr(cs, "display_name", None))
        if ms is not None and state in ("playing", "paused", "idle"):
            sf |= F.STOP
            if ms.supports_pause:
                sf |= F.PAUSE | F.PLAY
            if ms.supports_seek:
                sf |= F.SEEK
            if ms.supports_queue_next:
                sf |= F.NEXT_TRACK
            if ms.supports_queue_prev:
                sf |= F.PREVIOUS_TRACK
            images = getattr(ms, "images", None) or []
            updated = getattr(ms, "last_updated", None)
            attrs.update({
                "media_content_id": ms.content_id,
                "media_content_type": _ha_content_type(ms),
                "media_duration": ms.duration,
                "media_position": ms.current_time,
                "media_position_updated_at": updated.isoformat() if updated else None,
                "media_title": ms.title,
                "media_artist": ms.artist,
                "media_album_name": ms.album_name,
                "media_album_artist": ms.album_artist,
                "media_track": ms.track,
                "media_series_title": ms.series_title,
                "media_season": ms.season,
                "media_episode": ms.episode,
                "entity_picture": images[0].url if images else None,
            })
        attrs["supported_features"] = sf
        return state, clean(attrs)

    def _screen_app(self, key: str) -> Any:
        """What the TV's own remote connection says is on screen: the app's name, "" when the
        TV is off or the remote is not connected, or _NO_REMOTE when Jarvis has no remote for
        this TV (then only the standby flag can be checked)."""
        host = self.hosts.get(key)
        remote = next((d for d in self.hub.drivers.values()
                       if getattr(d, "platform", None) == "androidtv_remote" and host and getattr(d, "host", None) == host),
                      None)
        if remote is None:
            return _NO_REMOTE
        api = getattr(remote, "api", None)
        if not getattr(remote, "available", False) or api is None or not api.is_on:
            return ""
        package = str(api.current_app or "")
        return " ".join(filter(None, (getattr(remote, "app_names", {}).get(package), package)))

    def publish_key(self, key: str) -> None:
        ent = self.wanted().get(key)
        if ent is None:
            return
        state, attrs = self.state_of(key)
        if state == UNAVAILABLE:
            self.mark_unavailable([ent])
        else:
            self.publish(ent, state, attrs)

    # -- services -------------------------------------------------------------------
    def _cast_for(self, entity: EntityEntry) -> Any:
        try:
            key = str(UUID(entity.unique_id))
        except ValueError as exc:
            raise HubError(f"{entity.entity_id} has no cast UUID") from exc
        cc = self.casts.get(key)
        if cc is None or self.connection.get(key, "CONNECTED") != "CONNECTED":
            raise DeviceUnavailable(f"{entity.entity_id} is not connected over Cast")
        return cc

    async def _run(self, fn: Any, *args: Any, _timeout: float = COMMAND_TIMEOUT_S, **kwargs: Any) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), _timeout + 5)
        except (TimeoutError, pychromecast.error.PyChromecastError) as exc:
            raise DeviceUnavailable(f"Cast command failed: {type(exc).__name__}: {exc}") from exc

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain != "media_player":
            raise ServiceNotFound(f"cast has no {domain}.{service}")
        cc = self._cast_for(entity)
        mc = cc.media_controller
        if service == "turn_on":
            await self._run(cc.start_app, MEDIA_RECEIVER_APP_ID)
        elif service == "turn_off":
            await self._run(cc.quit_app)
        elif service == "toggle":
            if cc.is_idle:
                await self._run(cc.start_app, MEDIA_RECEIVER_APP_ID)
            else:
                await self._run(cc.quit_app)
        elif service == "volume_set":
            await self._run(cc.set_volume, clamp01(data.get("volume_level", 0)))
        elif service == "volume_up":
            await self._run(cc.volume_up)
        elif service == "volume_down":
            await self._run(cc.volume_down)
        elif service == "volume_mute":
            await self._run(cc.set_volume_muted, bool(data.get("is_volume_muted", True)))
        elif service == "media_play":
            await self._run(mc.play)
        elif service == "media_pause":
            await self._run(mc.pause)
        elif service == "media_play_pause":
            ms = self._media_status(cc)
            await self._run(mc.pause if ms is not None and ms.player_state == "PLAYING" else mc.play)
        elif service == "media_stop":
            await self._run(mc.stop)
        elif service == "media_next_track":
            await self._run(mc.queue_next)
        elif service == "media_previous_track":
            await self._run(mc.queue_prev)
        elif service == "media_seek":
            await self._run(mc.seek, float(data.get("seek_position") or 0))
        elif service == "play_media":
            if data.get("announce") and str(data.get("media_content_type") or "") != "cast":
                await self._announce(entity, cc, data)
            else:
                await self._play_media(cc, data)
        else:
            raise ServiceNotFound(f"cast cannot run media_player.{service}")
        self.publish_key(str(UUID(entity.unique_id)))
        return None

    async def _play_media(self, cc: Any, data: dict[str, Any]) -> None:
        media_id = str(data.get("media_content_id") or "")
        mtype = str(data.get("media_content_type") or "")
        if not media_id:
            raise HubError("play_media needs media_content_id")
        extra = dict(data.get("extra") or {})
        if mtype == "cast":
            # HA's app form: {"app_name": "youtube", "media_id": "..."} handed to pychromecast's quick_play.
            try:
                app_data = json.loads(media_id)
            except ValueError as exc:
                raise HubError("media_content_type 'cast' needs a JSON media_content_id") from exc
            from pychromecast.quick_play import quick_play

            app_name = app_data.pop("app_name", None)
            if not app_name:
                raise HubError("cast app data needs app_name")
            await self._run(quick_play, cc, app_name, app_data)
            return
        kwargs = {k: extra[k] for k in ("title", "thumb", "metadata", "current_time", "subtitles") if k in extra}
        kwargs["stream_type"] = extra.get("stream_type", "BUFFERED")
        if data.get("enqueue") in ("add", "next"):
            kwargs["enqueue"] = True
        await self._run(_load_and_wait, cc, media_id, mime_for(mtype, media_id), kwargs, _timeout=LOAD_TIMEOUT_S)


    # -- announcements ------------------------------------------------------------------
    async def _announce(self, entity: EntityEntry, cc: Any, data: dict[str, Any]) -> None:
        """Speak over what the device plays, then put it back (HA's ``announce: true``).

        A Cast device has no announcement channel of its own: loading a clip replaces the running app.
        When the device's Music Assistant player has a queue, Music Assistant does the announcement
        (it ducks and resumes its own queue). Otherwise what plays is noted, the clip is played, and
        once it has ended (IDLE, bounded by the clip's length) the Default Media Receiver's item is
        loaded again at its position, or the app that was running is started again, and the volume
        is put back. tts.speak, Jarvis and automations all come through here.
        """
        media_id = str(data.get("media_content_id") or "")
        mtype = str(data.get("media_content_type") or "")
        if not media_id:
            raise HubError("play_media needs media_content_id")
        extra = dict(data.get("extra") or {})
        twin = self._ma_twin(entity)
        twin_driver = self.hub._driver_for(twin) if twin is not None else None
        if twin is not None and twin_driver is not None:
            self.log.info("cast %s: announcing through Music Assistant player %s", entity.entity_id, twin.entity_id)
            # Straight to its driver: through the hub's dispatch, a twin combined with this entity would come
            # straight back here.
            await twin_driver.async_call(twin, "media_player", "play_media", {
                "media_content_id": media_id, "media_content_type": mtype or "music", "announce": True,
                "extra": {k: extra[k] for k in ("announce_volume", "use_pre_announce") if k in extra}})
            return
        key = str(UUID(entity.unique_id))
        pending = self.announcing.pop(key, None)
        if pending is not None:  # one announcement after another: put back what was there before the first
            pending["task"].cancel()
            snap = pending["snap"]
        else:
            snap = _snapshot(cc)
        level = _announce_level(extra.get("announce_volume"))
        try:
            if level is not None:
                snap["raised"] = True
                await self._run(cc.set_volume, level)
            await self._run(_load_and_wait, cc, media_id, mime_for(mtype, media_id), {"stream_type": "BUFFERED"},
                            _timeout=LOAD_TIMEOUT_S)
        except Exception:
            with contextlib.suppress(Exception):
                await self._run(_restore, cc, snap, media_id, _timeout=LOAD_TIMEOUT_S)
            raise
        limit = _clip_limit(media_id, extra)
        task = asyncio.ensure_future(self._finish_announcement(key, cc, media_id, snap, limit))
        self.announcing[key] = {"snap": snap, "task": task}

    def _ma_twin(self, entity: EntityEntry) -> EntityEntry | None:
        """The device's Music Assistant player, when it has a queue to come back to."""
        for twin in device_twins(self.hub, entity, "music_assistant"):
            # Its own state: a twin combined with this entity shows the combined (Cast) state under its id.
            st = self.hub.copy_states.get(twin.entity_id) or self.hub.states.get(twin.entity_id)
            if st is not None and st.state != UNAVAILABLE and st.attributes.get("active_queue"):
                return twin
        return None

    async def _finish_announcement(self, key: str, cc: Any, media_id: str, snap: dict[str, Any], limit: float) -> None:
        try:
            await self._run(_wait_for_clip_end, cc, media_id, limit, _timeout=limit)
            await self._run(_restore, cc, snap, media_id, _timeout=LOAD_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the device went away mid-clip; nothing more can be put back
            self.log.warning("cast %s: could not put back what was playing after an announcement: %s", key, exc)
        finally:
            current = self.announcing.get(key)
            if current is not None and current["task"] is asyncio.current_task():
                self.announcing.pop(key, None)
            self.publish_key(key)


def _announce_level(value: Any) -> float | None:
    """``extra.announce_volume`` as Music Assistant takes it (0-100), or a 0-1 level."""
    if value is None or isinstance(value, bool):
        return None
    try:
        level = float(value)
    except (TypeError, ValueError):
        return None
    if level <= 0:
        return None
    return clamp01(level / 100 if level > 1 else level)


def _clip_limit(media_id: str, extra: dict[str, Any]) -> float:
    """Longest wait for the clip to end: its length (when known) plus a margin."""
    seconds: float | None = None
    with contextlib.suppress(TypeError, ValueError):
        if extra.get("duration") is not None:
            seconds = float(extra["duration"])
    if seconds is None and "/api/tts_proxy/" in media_id:
        from services.common.audio_clip import wav_duration
        from services.homehub import tts

        token = media_id.split("?", 1)[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        seconds = wav_duration(tts.clip(token))
    if seconds is None:
        return ANNOUNCE_UNKNOWN_S
    return min(ANNOUNCE_MAX_S, max(0.0, seconds)) + ANNOUNCE_MARGIN_S


def _snapshot(cc: Any) -> dict[str, Any]:
    """What to put back after an announcement: the app, the media receiver's item and position, the volume."""
    cs = cc.status
    snap: dict[str, Any] = {"app_id": cc.app_id, "idle": bool(cc.is_idle),
                            "volume": getattr(cs, "volume_level", None), "muted": getattr(cs, "volume_muted", None)}
    ms = CastDriver._media_status(cc)
    if cc.app_id == MEDIA_RECEIVER_APP_ID and ms is not None and ms.content_id \
            and ms.player_state in ("PLAYING", "BUFFERING", "PAUSED"):
        live = str(getattr(ms, "stream_type", "") or "").upper() == "LIVE"
        position = getattr(ms, "adjusted_current_time", None)
        if position is None:
            position = ms.current_time
        snap["media"] = {"content_id": ms.content_id, "content_type": ms.content_type or "video/mp4",
                         "position": None if live else position, "paused": ms.player_state == "PAUSED",
                         "stream_type": "LIVE" if live else "BUFFERED", "title": ms.title}
    return snap


def _wait_for_clip_end(cc: Any, media_id: str, limit: float, sleep: Any = time.sleep) -> None:
    """Until the clip has played (IDLE after it started, or another item loaded), at most ``limit`` seconds.

    Cast's own ``duration`` for the clip shortens the wait when it is known.
    """
    begun = time.monotonic()
    deadline = begun + limit
    seen = started = False
    while time.monotonic() < deadline:
        ms = cc.media_controller.status
        state = getattr(ms, "player_state", None)
        cid = getattr(ms, "content_id", None)
        if cid == media_id:
            seen = True
        elif cid and seen:  # something else was loaded over the clip
            return
        if seen and state in ("PLAYING", "BUFFERING"):
            started = True
        elif seen and state == "IDLE" and (started or getattr(ms, "idle_reason", None)):
            return
        duration = getattr(ms, "duration", None) if seen else None
        if isinstance(duration, (int, float)) and duration > 0:
            deadline = min(deadline, begun + duration + ANNOUNCE_MARGIN_S)
        sleep(ANNOUNCE_POLL_S)


def _restore(cc: Any, snap: dict[str, Any], announced: str) -> None:
    """Put back what an announcement replaced (runs in a worker thread)."""
    media = snap.get("media")
    app_id = snap.get("app_id")
    if media and media["content_id"] != announced:
        if cc.app_id != MEDIA_RECEIVER_APP_ID:
            cc.start_app(MEDIA_RECEIVER_APP_ID, timeout=LOAD_TIMEOUT_S)
        kwargs: dict[str, Any] = {"autoplay": not media["paused"], "stream_type": media["stream_type"]}
        if media.get("position"):
            kwargs["current_time"] = media["position"]
        if media.get("title"):
            kwargs["title"] = media["title"]
        cc.media_controller.play_media(media["content_id"], media["content_type"], **kwargs)
    elif app_id and app_id not in (MEDIA_RECEIVER_APP_ID, ANDROID_NATIVE_APP_ID) and not snap.get("idle"):
        cc.start_app(app_id, timeout=LOAD_TIMEOUT_S)  # an app's own session cannot be resumed; its app can
    else:
        cc.quit_app()  # nothing was playing (or an Android app Cast cannot start): leave the receiver
    volume = snap.get("volume")
    if isinstance(volume, (int, float)) and (
            snap.get("raised") or abs(float(getattr(cc.status, "volume_level", volume) or 0) - volume) > 0.005):
        cc.set_volume(volume)
    muted = snap.get("muted")
    if isinstance(muted, bool) and getattr(cc.status, "volume_muted", muted) != muted:
        cc.set_volume_muted(muted)


def _load_and_wait(cc: Any, media_id: str, mime: str, kwargs: dict[str, Any]) -> None:
    """Load media and wait for the TV to accept it.

    pychromecast's play_media only sends the request (after launching the media receiver when another
    app holds the screen) and returns, so a TV that refused or never answered looked like success.
    """
    if cc.app_id != MEDIA_RECEIVER_APP_ID:
        # Another app's receiver (Spotify, YouTube) takes a LOAD as its own and refuses a plain URL
        # with INVALID_REQUEST, so switch to the media receiver first, as HA's cast integration does.
        cc.start_app(MEDIA_RECEIVER_APP_ID, timeout=LOAD_TIMEOUT_S)
    waiter = WaitResponse(LOAD_TIMEOUT_S, f"load {media_id}")
    cc.media_controller.play_media(media_id, mime, **kwargs, callback_function=waiter.callback)
    waiter.wait_response()
    response = getattr(waiter, "response", None) or {}
    if isinstance(response, dict) and str(response.get("type") or "").upper() in (
            "LOAD_FAILED", "LOAD_CANCELLED", "INVALID_REQUEST", "ERROR"):
        reason = response.get("reason") or response.get("detailedErrorCode") or response["type"]
        raise pychromecast.error.RequestFailed(f"load {media_id} ({reason})")


def _ha_content_type(ms: Any) -> str | None:
    if getattr(ms, "media_is_tvshow", False):
        return "tvshow"
    if getattr(ms, "media_is_movie", False):
        return "movie"
    if getattr(ms, "media_is_musictrack", False):
        return "music"
    ct = (getattr(ms, "content_type", "") or "").lower()
    if ct.startswith("audio/"):
        return "music"
    if ct.startswith("video/"):
        return "video"
    return ct or None
