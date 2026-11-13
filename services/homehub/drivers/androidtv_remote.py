"""Android TV / Google TV over the Android TV Remote protocol v2 (androidtvremote2).

The TV paired with HA's client certificate, not with a name, so the hub must
present the same certificate. HA kept it in ``.storage/androidtv_remote_cert.pem``
and ``androidtv_remote_key.pem`` (one pair for every Android TV); the importer
hands them over in ``entry.data["cert_pem"]`` and ``entry.data["key_pem"]``.
They are written to a private 0600 directory because the library opens files.

Entities: media_player and remote, both with unique_id = the TV's MAC. State is
on/off as in HA (the protocol reports power, the foreground app and volume, not
playback). A TV that cannot be reached is ``unavailable`` and the poll retries.

Once connected, the TV's own ``device_info`` (maker, model, software version)
is confirmed onto its device; the MAC is the entities' unique_id, which pairing
read from the TV.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, HubError, ServiceNotFound
from services.homehub.drivers.media_common import REMOTE_ACTIVITY, F, as_list, clamp01, clean, known_macs, wake_on_lan

#: The client name HA used; the TV shows it in its list of paired remotes.
CLIENT_NAME = "Home Assistant"
CONNECT_TIMEOUT_S = 10.0

BASE_FEATURES = (F.PAUSE | F.VOLUME_STEP | F.VOLUME_MUTE | F.PREVIOUS_TRACK | F.NEXT_TRACK | F.TURN_ON
                 | F.TURN_OFF | F.PLAY_MEDIA | F.STOP | F.PLAY | F.BROWSE_MEDIA)

#: Friendly command names -> Android key codes (androidtvremote2 accepts names with or without KEYCODE_).
KEYS = {
    "home": "HOME", "back": "BACK", "menu": "MENU", "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT", "enter": "DPAD_CENTER", "select": "DPAD_CENTER", "ok": "DPAD_CENTER",
    "play": "MEDIA_PLAY", "resume": "MEDIA_PLAY", "pause": "MEDIA_PAUSE", "play_pause": "MEDIA_PLAY_PAUSE",
    "stop": "MEDIA_STOP", "next": "MEDIA_NEXT", "previous": "MEDIA_PREVIOUS", "fast_forward": "MEDIA_FAST_FORWARD",
    "rewind": "MEDIA_REWIND", "volume_up": "VOLUME_UP", "volume_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE",
    "volume_mute": "VOLUME_MUTE", "power": "POWER", "power_on": "POWER", "power_off": "POWER",
    "channel_up": "CHANNEL_UP", "channel_down": "CHANNEL_DOWN", "info": "INFO", "search": "SEARCH",
    "settings": "SETTINGS", "guide": "GUIDE",
}

#: A few common apps so ``app_name``/``source`` read like HA's (HA used the user's app list option).
APP_NAMES = {
    "com.google.android.youtube.tv": "YouTube", "com.netflix.ninja": "Netflix",
    "com.amazon.amazonvideo.livingroom": "Prime Video", "com.disney.disneyplus": "Disney+",
    "com.google.android.apps.tv.launcherx": "Google TV", "com.google.android.tvlauncher": "Home",
    "com.hulu.livingroomplus": "Hulu", "com.plexapp.android": "Plex", "com.spotify.tv.android": "Spotify",
    "com.google.android.backdrop": "Ambient mode", "tv.pluto.android": "Pluto TV",
}


def key_code(command: str) -> str:
    c = command.strip()
    up = c.upper()
    if up.startswith("KEYCODE_"):
        return up[8:]
    return KEYS.get(c.lower()) or up


class AndroidTVRemoteDriver(Driver):
    platform = "androidtv_remote"
    poll_interval = 15.0
    app_names = APP_NAMES  # read by the cast driver to check which app is on screen
    remote_factory = AndroidTVRemote

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.host = str(entry.options.get("host") or entry.data.get("host") or "")
        self.api: Any = None
        self.available = False
        self._cert_dir: Path | None = None

    # -- credentials ----------------------------------------------------------------
    def _cert_files(self) -> tuple[str, str]:
        d = self.entry.data
        opts = self.entry.options
        if opts.get("cert_file") and opts.get("key_file"):
            return str(opts["cert_file"]), str(opts["key_file"])
        cert, key = d.get("cert_pem"), d.get("key_pem")
        if not cert or not key:
            raise HubError("androidtv_remote needs HA's client certificate: entry.data cert_pem and key_pem "
                           "(from .storage/androidtv_remote_cert.pem and androidtv_remote_key.pem)")
        tag = hashlib.sha256(f"{self.entry.entry_id}".encode()).hexdigest()[:12]
        self._cert_dir = Path(tempfile.gettempdir()) / f"homehub-atv-{tag}"
        self._cert_dir.mkdir(mode=0o700, exist_ok=True)
        paths = []
        for name, pem in (("cert.pem", cert), ("key.pem", key)):
            p = self._cert_dir / name
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(pem)
            paths.append(str(p))
        return paths[0], paths[1]

    # -- lifecycle ----------------------------------------------------------------
    async def async_setup(self) -> None:
        if not self.host:
            raise HubError("androidtv_remote entry has no host")
        certfile, keyfile = await asyncio.to_thread(self._cert_files)
        self.api = self.remote_factory(CLIENT_NAME, certfile, keyfile, self.host)
        self.api.add_is_on_updated_callback(lambda _v: self._publish())
        self.api.add_current_app_updated_callback(lambda _v: self._publish())
        self.api.add_volume_info_updated_callback(lambda _v: self._publish())
        self.api.add_is_available_updated_callback(self._on_available)
        await self._connect()

    async def _connect(self) -> None:
        try:
            await asyncio.wait_for(self.api.async_connect(), CONNECT_TIMEOUT_S)
        except InvalidAuth as exc:
            # Retrying cannot help: the TV no longer trusts this certificate.
            raise HubError(f"Android TV {self.host} needs pairing again (the certificate was refused)") from exc
        except (CannotConnect, ConnectionClosed, TimeoutError, OSError) as exc:
            self.log.debug("Android TV %s not reachable: %r", self.host, exc)
            self.available = False
            self.mark_unavailable()
            return
        self.available = True
        self.api.keep_reconnecting()
        self._confirm_info()
        self._publish()

    def _confirm_info(self) -> None:
        info = getattr(self.api, "device_info", None) or {}
        ent = next((e for e in self.entities if e.device_id), None)
        if ent is None or not isinstance(info, dict) or not info:
            return
        self.confirm_device(ent, manufacturer=info.get("manufacturer") or None, model=info.get("model") or None,
                            sw_version=info.get("sw_version") or None)

    def _on_available(self, available: bool) -> None:
        self.available = bool(available)
        if available:
            self._publish()
        else:
            self.mark_unavailable()

    async def async_poll(self) -> None:
        if not self.available:
            await self._connect()
        else:
            self._publish()

    async def async_unload(self) -> None:
        if self.api is not None:
            with contextlib.suppress(Exception):
                self.api.disconnect()
        if self._cert_dir is not None:
            for name in ("cert.pem", "key.pem"):
                with contextlib.suppress(OSError):
                    (self._cert_dir / name).unlink()

    # -- state --------------------------------------------------------------------
    def _publish(self) -> None:
        if not self.available or self.api is None:
            return
        is_on = bool(self.api.is_on)
        app = self.api.current_app
        app_name = APP_NAMES.get(app or "", app)
        vol = self.api.volume_info or {}
        sf = BASE_FEATURES | (F.VOLUME_SET if vol.get("max") else 0)
        for e in self.entities:
            if e.domain == "media_player":
                attrs: dict[str, Any] = {"supported_features": sf, "device_class": "tv"}
                if is_on:
                    attrs.update(clean({
                        "app_id": app, "app_name": app_name,
                        "volume_level": (vol["level"] / vol["max"]) if vol.get("max") else None,
                        "is_volume_muted": vol.get("muted"),
                    }))
                self.publish(e, "on" if is_on else "off", attrs)
            elif e.domain == "remote":
                self.publish(e, "on" if is_on else "off", clean({
                    "supported_features": REMOTE_ACTIVITY,
                    "current_activity": app_name if is_on else None,
                    "activity_list": sorted(set(APP_NAMES.values())),
                }))

    # -- services -----------------------------------------------------------------
    def _send(self, key: str, direction: str = "SHORT") -> None:
        if not self.available or self.api is None:
            raise DeviceUnavailable(f"Android TV {self.host} is not connected")
        try:
            self.api.send_key_command(key_code(key), direction)
        except ConnectionClosed as exc:
            raise DeviceUnavailable(f"Android TV {self.host} dropped the connection") from exc

    def _launch(self, app_link_or_id: str) -> None:
        if not self.available or self.api is None:
            raise DeviceUnavailable(f"Android TV {self.host} is not connected")
        target = next((pkg for pkg, name in APP_NAMES.items() if name.lower() == app_link_or_id.lower()),
                      app_link_or_id)
        self.api.send_launch_app_command(target)

    async def _send_many(self, keys: list[str], delay: float = 0.0) -> None:
        for i, k in enumerate(keys):
            if i and delay:
                await asyncio.sleep(delay)
            self._send(k)

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain == "remote":
            await self._remote_call(entity, service, data)
        elif domain == "media_player":
            await self._media_call(entity, service, data)
        else:
            raise ServiceNotFound(f"androidtv_remote has no {domain}.{service}")
        self._publish()

    async def _power(self, entity: EntityEntry, on: bool) -> None:
        if not self.available:
            if on and await wake_on_lan(known_macs(self, entity, self.host)):
                return
            raise DeviceUnavailable(f"Android TV {self.host} is not connected")
        if bool(self.api.is_on) != on:
            self._send("POWER")

    async def _remote_call(self, entity: EntityEntry, service: str, data: dict[str, Any]) -> None:
        if service == "turn_on":
            await self._power(entity, True)
            if data.get("activity"):
                self._launch(str(data["activity"]))
        elif service == "turn_off":
            await self._power(entity, False)
        elif service == "send_command":
            cmds = [str(c) for c in as_list(data.get("command")) if str(c).strip()]
            if not cmds:
                raise HubError("remote.send_command needs a command")
            repeats = max(1, int(data.get("num_repeats") or 1))
            delay = float(data.get("delay_secs") if data.get("delay_secs") is not None else 0.4)
            hold = float(data.get("hold_secs") or 0)
            for _ in range(repeats):
                for cmd in cmds:
                    if hold > 0:
                        self._send(cmd, "START_LONG")
                        await asyncio.sleep(hold)
                        self._send(cmd, "END_LONG")
                    else:
                        self._send(cmd)
                    if delay:
                        await asyncio.sleep(delay)
        else:
            raise ServiceNotFound(f"androidtv_remote has no remote.{service}")

    async def _media_call(self, entity: EntityEntry, service: str, data: dict[str, Any]) -> None:
        simple = {"media_play": "MEDIA_PLAY", "media_pause": "MEDIA_PAUSE", "media_play_pause": "MEDIA_PLAY_PAUSE",
                  "media_stop": "MEDIA_STOP", "media_next_track": "MEDIA_NEXT",
                  "media_previous_track": "MEDIA_PREVIOUS", "volume_up": "VOLUME_UP", "volume_down": "VOLUME_DOWN"}
        if service in simple:
            self._send(simple[service])
        elif service == "turn_on":
            await self._power(entity, True)
        elif service == "turn_off":
            await self._power(entity, False)
        elif service == "toggle":
            await self._power(entity, not (self.available and self.api.is_on))
        elif service == "volume_mute":
            want = bool(data.get("is_volume_muted", True))
            if bool((self.api.volume_info or {}).get("muted")) != want:
                self._send("VOLUME_MUTE")
        elif service == "volume_set":
            vol = self.api.volume_info or {}
            if not vol.get("max"):
                raise ServiceNotFound("this Android TV does not report its volume")
            diff = round(clamp01(data.get("volume_level", 0)) * vol["max"]) - int(vol.get("level") or 0)
            # The protocol has no absolute volume: step there, as HA does.
            await self._send_many(["VOLUME_UP" if diff > 0 else "VOLUME_DOWN"] * abs(diff))
        elif service == "play_media":
            media_id = str(data.get("media_content_id") or "")
            mtype = str(data.get("media_content_type") or "").lower()
            if not media_id:
                raise HubError("play_media needs media_content_id")
            if mtype == "channel":
                if not media_id.isdigit():
                    raise HubError(f"channel must be digits, not {media_id!r}")
                await self._send_many([f"KEYCODE_{d}" for d in media_id] + ["DPAD_CENTER"], 0.1)
            elif mtype in ("app", "url"):
                self._launch(media_id)
            else:
                raise ServiceNotFound(f"androidtv_remote plays only app/url/channel, not {mtype!r}; "
                                      "use the Cast entity for media URLs")
        elif service == "select_source":
            self._launch(str(data.get("source") or ""))
        else:
            raise ServiceNotFound(f"androidtv_remote cannot run media_player.{service}")

