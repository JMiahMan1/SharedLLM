"""Samsung TVs: websocket (2016+, samsungtvws), encrypted (2014-2015 H/J, port 8000) and legacy (pre-2014, port 55000).

The HA entry's ``method`` picks the protocol. Both identify themselves exactly
as HA's samsungtv integration did (name ``HomeAssistant``, legacy id
``ha.component.samsung``), so the TVs that already allowed Home Assistant do
not ask again; the websocket TV also needs the ``token`` from the entry.

Volume and mute go through UPnP RenderingControl at the entry's
``ssdp_rendering_control_location``, as HA does, because neither remote
protocol can read the volume. Power on is Wake-on-LAN to every MAC the
registry knows (the Q900 wakes on LAN; the 2012 EH5300 generally does not).
URLs play through the same UPnP device's AVTransport when it has one.

What the TV is comes from the TV: the websocket models' REST ``/api/v2/``
(model, MAC, firmware) and the UPnP description (maker, model, serial) are
confirmed onto its device while it is on. The legacy and encrypted protocols
report nothing about the set, so for those only the UPnP description counts.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from samsungtvws.async_remote import SamsungTVWSAsyncRemote
from samsungtvws.exceptions import ConnectionFailure, UnauthorizedError
from samsungtvws.legacy.remote import SamsungTVLegacyAsyncRemote
from samsungtvws.remote import ChannelEmitCommand, SendRemoteKey

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, HubError, ServiceNotFound
from services.homehub.drivers.media_common import (
    F,
    Http,
    UpnpDevice,
    clamp01,
    clean,
    known_macs,
    mime_for,
    send_command_list,
    wake_on_lan,
)

HA_NAME = "HomeAssistant"
HA_LEGACY_ID = "ha.component.samsung"
#: After power off the TV keeps answering for a while; HA holds it ``off`` this long.
POWER_OFF_GRACE_S = 15.0
VOLUME_STEP = 0.1

#: Friendly names (as Jarvis and HA automations spell them) -> Samsung key codes.
SAMSUNG_KEYS = {
    "home": "KEY_HOME", "back": "KEY_RETURN", "return": "KEY_RETURN", "enter": "KEY_ENTER", "select": "KEY_ENTER",
    "ok": "KEY_ENTER", "play": "KEY_PLAY", "resume": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP",
    "fastforward": "KEY_FF", "ff": "KEY_FF", "next": "KEY_FF", "rewind": "KEY_REWIND", "previous": "KEY_REWIND",
    "channelup": "KEY_CHUP", "channeldown": "KEY_CHDOWN", "volumeup": "KEY_VOLUP", "volumedown": "KEY_VOLDOWN",
    "mute": "KEY_MUTE", "volumemute": "KEY_MUTE", "power": "KEY_POWER", "poweroff": "KEY_POWER",
    "poweron": "KEY_POWERON", "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
    "info": "KEY_INFO", "menu": "KEY_MENU", "tools": "KEY_TOOLS", "exit": "KEY_EXIT", "source": "KEY_SOURCE",
    "guide": "KEY_GUIDE", "red": "KEY_RED", "green": "KEY_GREEN", "yellow": "KEY_YELLOW", "blue": "KEY_BLUE",
    "tv": "KEY_TV", "hdmi": "KEY_HDMI",
}
SOURCES = {"TV": "KEY_TV", "HDMI": "KEY_HDMI"}

BASE_FEATURES = (F.PAUSE | F.VOLUME_STEP | F.VOLUME_MUTE | F.PREVIOUS_TRACK | F.SELECT_SOURCE | F.NEXT_TRACK
                 | F.TURN_OFF | F.PLAY | F.PLAY_MEDIA | F.STOP)


def samsung_key(command: str) -> str:
    c = command.strip()
    if c.upper().startswith("KEY_"):
        return c.upper()
    return SAMSUNG_KEYS.get(c.lower().replace("_", "").replace(" ", "")) or "KEY_" + c.upper()


class LegacyBridge:
    """The binary protocol on port 55000: one connection per batch of keys, as HA did."""

    power_key = "KEY_POWEROFF"
    remote_cls = SamsungTVLegacyAsyncRemote

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port or 55000

    async def is_on(self) -> bool:
        try:
            _reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 2.0)
        except (OSError, TimeoutError):
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True

    async def send_keys(self, keys: list[str], delay: float) -> None:
        remote = self.remote_cls(self.host, port=self.port, name=HA_NAME, description=HA_NAME,
                                 remote_id=HA_LEGACY_ID, timeout=8)
        try:
            await remote.open()
            for i, key in enumerate(keys):
                if i and delay > 0:
                    await asyncio.sleep(delay)
                await remote.control(key)
        except UnauthorizedError as exc:
            raise HubError(f"the TV refused the remote: allow '{HA_NAME}' on its screen ({exc})") from exc
        except ConnectionFailure as exc:
            raise DeviceUnavailable(f"Samsung {self.host} did not answer: {exc}") from exc
        finally:
            await remote.close()

    async def launch_app(self, app_id: str, app_type: str = "DEEP_LINK", meta: str = "") -> None:
        raise ServiceNotFound("a legacy Samsung TV cannot launch apps")

    async def close(self) -> None:
        pass


class EncryptedBridge:
    """2014-2015 (H/J series) TVs: an encrypted websocket on port 8000 with the token and session id from pairing."""

    power_key = "KEY_POWEROFF"

    def __init__(self, host: str, port: int, token: str | None, session_id: str | None) -> None:
        self.host, self.port = host, port or 8000
        self.token, self.session_id = token or "", session_id or ""
        self.remote: Any = None
        self._session: Any = None

    async def is_on(self) -> bool:
        return await LegacyBridge(self.host, self.port).is_on()

    async def send_keys(self, keys: list[str], delay: float) -> None:
        import aiohttp
        from samsungtvws.encrypted.remote import SamsungTVEncryptedWSAsyncRemote
        from samsungtvws.encrypted.remote import SendRemoteKey as EncryptedKey

        if not self.token or not self.session_id:
            raise HubError(f"Samsung {self.host} has no pairing token; add it again to pair")
        if self._session is None:
            self._session = aiohttp.ClientSession()
        if self.remote is None:
            self.remote = SamsungTVEncryptedWSAsyncRemote(self.host, web_session=self._session, token=self.token,
                                                          session_id=self.session_id, port=self.port, timeout=8)
        try:
            await self.remote.send_commands([EncryptedKey.click(k) for k in keys], key_press_delay=delay)
        except (OSError, TimeoutError, ConnectionFailure) as exc:
            await self.close()
            raise DeviceUnavailable(f"Samsung {self.host} did not answer: {exc}") from exc

    async def launch_app(self, app_id: str, app_type: str = "DEEP_LINK", meta: str = "") -> None:
        raise ServiceNotFound("an encrypted (2014-2015) Samsung TV cannot launch apps")

    async def close(self) -> None:
        remote, self.remote = self.remote, None
        if remote is not None:
            with contextlib.suppress(Exception):
                await remote.close()
        session, self._session = self._session, None
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()


class WebsocketBridge:
    """samsungtvws over wss:8002 with the stored token; REST :8001 for power state."""

    power_key = "KEY_POWER"
    remote_cls = SamsungTVWSAsyncRemote

    def __init__(self, host: str, port: int, token: str | None, http: Http) -> None:
        self.host, self.port, self.token, self.http = host, port or 8002, token, http
        self.remote: Any = None
        #: The TV's own ``/api/v2/`` "device" record (model, MAC, firmware), once it has answered.
        self.info: dict[str, Any] = {}

    async def is_on(self) -> bool:
        try:
            status, text = await self.http.request("GET", f"http://{self.host}:8001/api/v2/", timeout=3)
        except DeviceUnavailable:
            return False
        if status >= 400:
            return False
        try:
            device = json.loads(text).get("device") or {}
            self.info = device if isinstance(device, dict) else {}
            power = self.info.get("PowerState")
        except (ValueError, AttributeError):
            power = None
        # Older Tizen firmware has no PowerState; answering REST at all means on.
        return power in (None, "on")

    async def _remote(self) -> Any:
        if self.remote is None or not self.remote.is_alive():
            await self.close()
            self.remote = self.remote_cls(host=self.host, port=self.port, token=self.token, timeout=10,
                                          key_press_delay=0, name=HA_NAME)
            try:
                await self.remote.open()
            except UnauthorizedError as exc:
                self.remote = None
                raise HubError(f"Samsung {self.host} refused the token; allow '{HA_NAME}' on the TV") from exc
            except (ConnectionFailure, OSError, TimeoutError) as exc:
                self.remote = None
                raise DeviceUnavailable(f"Samsung {self.host} did not answer: {exc}") from exc
            new_token = getattr(self.remote, "token", None)
            if new_token and new_token != self.token:
                # The TV issued a fresh token; keep it for this run (the store keeps the imported one).
                self.token = new_token
        return self.remote

    async def _send(self, commands: list[Any], delay: float) -> None:
        remote = await self._remote()
        try:
            await remote.send_commands(commands, key_press_delay=delay)
        except (ConnectionFailure, OSError, TimeoutError) as exc:
            await self.close()
            raise DeviceUnavailable(f"Samsung {self.host} dropped the connection: {exc}") from exc

    async def send_keys(self, keys: list[str], delay: float) -> None:
        await self._send([SendRemoteKey.click(k) for k in keys], delay)

    async def launch_app(self, app_id: str, app_type: str = "DEEP_LINK", meta: str = "") -> None:
        await self._send([ChannelEmitCommand.launch_app(app_id, app_type, meta)], 0)

    async def close(self) -> None:
        if self.remote is not None:
            with contextlib.suppress(Exception):
                await self.remote.close()
        self.remote = None


class SamsungTVDriver(Driver):
    platform = "samsungtv"
    poll_interval = 10.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        d = entry.data
        self.http = Http(timeout=5.0)
        self.host = str(entry.options.get("host") or d.get("host") or "")
        self.method = str(d.get("method") or "websocket")
        port = int(d.get("port") or 0)
        if self.method == "legacy":
            self.bridge: LegacyBridge | WebsocketBridge | EncryptedBridge = LegacyBridge(self.host, port)
        elif self.method == "encrypted":
            self.bridge = EncryptedBridge(self.host, port, d.get("token"), d.get("session_id"))
        else:
            self.bridge = WebsocketBridge(self.host, port, d.get("token"), self.http)
        loc = d.get("ssdp_rendering_control_location")
        self.upnp = UpnpDevice(self.http, loc) if loc else None
        self.is_on = False
        self.volume: float | None = None
        self.muted: bool | None = None
        self._playing = False
        self._off_until = 0.0

    async def async_setup(self) -> None:
        if not self.host:
            raise HubError("samsungtv entry has no host")
        await self.refresh()

    async def async_poll(self) -> None:
        await self.refresh()

    async def async_unload(self) -> None:
        await self.bridge.close()
        await self.http.close()

    def _media(self) -> EntityEntry | None:
        return next((e for e in self.entities if e.domain == "media_player"), None)

    def _features(self) -> int:
        sf = BASE_FEATURES
        if self.upnp is not None:
            sf |= F.VOLUME_SET
        if known_macs(self, self._media()):
            sf |= F.TURN_ON
        return sf

    async def refresh(self) -> None:
        on = await self.bridge.is_on()
        if time.monotonic() < self._off_until:
            on = False
        self.is_on = on
        if on and self.upnp is not None:
            try:
                self.volume = await self.upnp.get_volume()
                self.muted = await self.upnp.get_mute()
            except HubError:
                self.volume = self.muted = None
        if on:
            self._confirm_info()
        self._publish()

    def _confirm_info(self) -> None:
        from services.homehub.drivers.media_common import normalize_mac

        ent = self._media()
        if ent is None:
            return
        fields: dict[str, Any] = self.upnp.device_fields() if self.upnp is not None else {}
        info = getattr(self.bridge, "info", None) or {}
        mac = normalize_mac(info.get("wifiMac"))
        fields = {**{k: v for k, v in fields.items() if v}, "manufacturer": fields.get("manufacturer") or "Samsung",
                  "model": info.get("modelName") or fields.get("model"),
                  "sw_version": info.get("firmwareVersion") or None}
        if mac:
            fields["connections"] = [*fields.get("connections", []), ["mac", mac]]
        self.confirm_device(ent, **fields)

    def _publish(self) -> None:
        sf = self._features()
        for e in self.entities:
            if e.domain == "media_player":
                attrs: dict[str, Any] = {"source_list": list(SOURCES), "supported_features": sf,
                                         "device_class": "tv"}
                if self.is_on:
                    attrs.update(clean({"volume_level": self.volume, "is_volume_muted": self.muted}))
                self.publish(e, "on" if self.is_on else "off", attrs)
            elif e.domain == "remote":
                self.publish(e, "on" if self.is_on else "off", {"supported_features": 0})

    async def _keys(self, *keys: str, delay: float = 0.4) -> None:
        await self.bridge.send_keys(list(keys), delay)

    async def _turn_on(self) -> None:
        macs = known_macs(self, self._media(), self.host)
        if not await wake_on_lan(macs):
            raise HubError(f"no MAC address is known for {self.host}, so it cannot be woken")
        self._off_until = 0.0

    async def _turn_off(self) -> None:
        await self._keys(self.bridge.power_key)
        self._off_until = time.monotonic() + POWER_OFF_GRACE_S
        self.is_on = False

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain == "remote":
            if service == "send_command":
                delay = float(data.get("delay_secs") if data.get("delay_secs") is not None else 0.4)
                await send_command_list(data.get("command"), {**data, "delay_secs": delay},
                                        lambda c: self._keys(samsung_key(c)))
            elif service == "turn_on":
                await self._turn_on()
            elif service == "turn_off":
                await self._turn_off()
            else:
                raise ServiceNotFound(f"samsungtv has no remote.{service}")
        elif domain == "media_player":
            await self._media_call(service, data)
        else:
            raise ServiceNotFound(f"samsungtv has no {domain}.{service}")
        if service not in ("turn_on",):
            with contextlib.suppress(HubError):
                await self.refresh()

    async def _media_call(self, service: str, data: dict[str, Any]) -> None:
        if service == "turn_on":
            await self._turn_on()
        elif service == "turn_off":
            await self._turn_off()
        elif service == "toggle":
            await (self._turn_off() if self.is_on else self._turn_on())
        elif service == "volume_up":
            await self._keys("KEY_VOLUP")
        elif service == "volume_down":
            await self._keys("KEY_VOLDOWN")
        elif service == "volume_set":
            if self.upnp is None:
                raise ServiceNotFound("this Samsung TV has no UPnP volume control")
            await self.upnp.set_volume(clamp01(data.get("volume_level", 0)))
        elif service == "volume_mute":
            if self.upnp is not None:
                await self.upnp.set_mute(bool(data.get("is_volume_muted", True)))
            else:
                await self._keys("KEY_MUTE")
        elif service == "media_play":
            await self._keys("KEY_PLAY")
            self._playing = True
        elif service == "media_pause":
            await self._keys("KEY_PAUSE")
            self._playing = False
        elif service == "media_play_pause":
            await self._keys("KEY_PAUSE" if self._playing else "KEY_PLAY")
            self._playing = not self._playing
        elif service == "media_stop":
            await self._keys("KEY_STOP")
            self._playing = False
        elif service == "media_next_track":
            await self._keys("KEY_CHUP")  # HA's samsungtv maps next/previous to channel up/down
        elif service == "media_previous_track":
            await self._keys("KEY_CHDOWN")
        elif service == "select_source":
            source = str(data.get("source") or "")
            if source not in SOURCES:
                raise HubError(f"unknown source {source!r}; choose one of {list(SOURCES)}")
            await self._keys(SOURCES[source])
        elif service == "play_media":
            await self._play_media(data)
        else:
            raise ServiceNotFound(f"samsungtv cannot run media_player.{service}")

    async def _play_media(self, data: dict[str, Any]) -> None:
        media_id = str(data.get("media_content_id") or "")
        mtype = str(data.get("media_content_type") or "").lower()
        if not media_id:
            raise HubError("play_media needs media_content_id")
        if mtype == "channel":
            if not media_id.isdigit():
                raise HubError(f"channel must be digits, not {media_id!r}")
            await self._keys(*[f"KEY_{d}" for d in media_id], "KEY_ENTER", delay=0.3)
        elif mtype == "app":
            await self.bridge.launch_app(media_id)
        else:
            if self.upnp is None:
                raise ServiceNotFound("this Samsung TV has no UPnP renderer to play a URL")
            await self.upnp.load()
            if not self.upnp.has("AVTransport"):
                raise ServiceNotFound("this Samsung TV's UPnP device cannot play a URL")
            extra = data.get("extra") or {}
            await self.upnp.set_uri_and_play(media_id, mime_for(mtype, media_id), str(extra.get("title") or ""))
            self._playing = True
