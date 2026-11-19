"""LG webOS TVs over aiowebostv, plus HA's ``webostv.*`` services.

The entry holds ``host`` and ``client_secret`` (the pairing key HA got when
the TV showed its "Allow" prompt; aiowebostv calls it ``client_key``). With
that key the TV accepts the connection silently.

A TV in standby closes its websocket, so "cannot connect" means ``off``, as in
HA, and the poll keeps trying. Power on is Wake-on-LAN: the export has no MAC
for this TV, so :func:`known_macs` falls back to ``options["mac"]`` and the
host's ARP cache (learned while the TV is on). Jarvis calls
``webostv.command`` (``system.launcher/launch`` and friends) and
``webostv.button``; both are registered here with HA's data keys.

While connected, the TV's ``system_info`` / ``software_info`` (model, serial,
firmware) are confirmed onto its device (``confirm_device``); the feature flags,
sources and device class are published from the TV's live state. The MAC for
Wake-on-LAN is an address to reach it by, learned from the ARP cache.

Remote: HA's webostv has no remote entity, only ``webostv.button``. So every LG
TV has a remote too, registered at setup beside its media player (same
unique_id, same device), taking the standard key names
(``media_common.STANDARD_REMOTE_KEYS``, listed in ``command_list``) and any LG
button name (``UP``, ``ENTER``, ``NETFLIX``...). ``power`` toggles,
``power_off`` turns the TV off over the API, ``power_on`` is Wake-on-LAN, ``play_pause`` toggles,
``select`` is ENTER. webOS has no input button: inputs are sources
(``select_source``), so ``input`` is not in the list.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from aiowebostv import WebOsClient
from aiowebostv.exceptions import WebOsTvCommandError, WebOsTvPairError

from services.homehub.core import (
    AuthRequired,
    DeviceUnavailable,
    Driver,
    EntityEntry,
    HubError,
    ServiceCall,
    ServiceNotFound,
)
from services.homehub.drivers.media_common import (
    F,
    as_list,
    clamp01,
    clean,
    known_macs,
    remember_mac,
    remote_command_list,
    send_command_list,
    wake_on_lan,
)

LIVE_TV_APP_ID = "com.webos.app.livetv"
BROWSER_APP_ID = "com.webos.app.browser"
CONNECT_TIMEOUT_S = 10.0

#: Standard key names -> LG pointer-socket button names. ``power*`` and ``play_pause`` are answered
#: by the driver (``REMOTE_ACTIONS``), not by a button.
LG_BUTTONS = {
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "select": "ENTER", "ok": "ENTER",
    "enter": "ENTER", "back": "BACK", "home": "HOME", "menu": "MENU", "info": "INFO", "guide": "GUIDE",
    "exit": "EXIT", "volume_up": "VOLUMEUP", "volume_down": "VOLUMEDOWN", "mute": "MUTE",
    "channel_up": "CHANNELUP", "channel_down": "CHANNELDOWN", "play": "PLAY", "pause": "PAUSE", "stop": "STOP",
    "fast_forward": "FASTFORWARD", "rewind": "REWIND", "next": "FASTFORWARD", "previous": "REWIND",
    **{str(d): str(d) for d in range(10)},
}
#: Native LG buttons offered beside the standard names.
LG_EXTRAS = ("RED", "GREEN", "YELLOW", "BLUE", "NETFLIX", "AMAZON", "ASTERISK", "DASH", "CC", "3D_MODE")
REMOTE_ACTIONS = ("power", "power_on", "power_off", "play_pause")
COMMAND_LIST = remote_command_list(lambda k: k in LG_BUTTONS or k in REMOTE_ACTIONS, LG_EXTRAS)

BASE_FEATURES = (F.NEXT_TRACK | F.PAUSE | F.PREVIOUS_TRACK | F.SELECT_SOURCE | F.PLAY_MEDIA | F.PLAY | F.STOP
                 | F.VOLUME_MUTE | F.VOLUME_STEP | F.VOLUME_SET | F.TURN_OFF)


class WebOSDriver(Driver):
    platform = "webostv"
    poll_interval = 15.0
    client_factory = WebOsClient

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.host = str(entry.options.get("host") or entry.data.get("host") or "")
        self.key = entry.data.get("client_secret")
        self.client: Any = None
        self.sources: dict[str, dict[str, Any]] = {}
        self.current_source: str | None = None
        self._paused = False

    # -- connection ---------------------------------------------------------------
    async def async_setup(self) -> None:
        if not self.host:
            raise HubError("webostv entry has no host")
        if not self.key:
            raise HubError("webostv entry has no client_secret; the TV would need to be paired again")
        for service, fields in (("command", {"command": {"required": True}, "payload": {}}),
                                ("button", {"button": {"required": True}}),
                                ("select_sound_output", {"sound_output": {"required": True}})):
            self.hub.register_service("webostv", service, _webostv_service, fields)
        self._ensure_remote()
        await self.connect()
        self._publish()

    async def async_poll(self) -> None:
        await self.connect()
        self._publish()

    async def async_unload(self) -> None:
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.disconnect()

    @property
    def connected(self) -> bool:
        if self.client is None:
            return False
        try:
            return bool(self.client.is_connected())
        except Exception:
            # aiowebostv re-raises the failed connection attempt here when the
            # TV is off or asleep; that is "not connected", not a crash.
            return False

    async def connect(self) -> bool:
        if self.client is None:
            self.client = self.client_factory(self.host, client_key=self.key, connect_timeout=CONNECT_TIMEOUT_S / 2)
            await self.client.register_state_update_callback(self._on_state)
        if self.connected:
            return True
        try:
            await asyncio.wait_for(self.client.connect(), CONNECT_TIMEOUT_S)
        except WebOsTvPairError as exc:
            # The key was refused: the TV would show "Allow" again, which needs a person. The runtime
            # parks the entry instead of asking again every poll.
            raise AuthRequired(f"LG TV {self.host} refused the stored pairing key; pair it again",
                               needs="pairing") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # standby, unreachable, or a dropped socket: all mean "off"
            self.log.debug("LG TV %s not reachable: %r", self.host, exc)
            return False
        if self.connected:
            remember_mac(self, self.host)  # so "turn on" can wake it once it is off again
        return self.connected

    async def _on_state(self, _tv_state: Any) -> None:
        self._publish()

    def _ensure_remote(self) -> None:
        """A remote beside the media player (same unique_id and device), registered when there is none."""
        media = self._media()
        if media is None:
            return
        self.ensure_entity("remote", media.unique_id, None, device=self.hub.devices.get(media.device_id or ""))

    # -- state --------------------------------------------------------------------
    def _media(self) -> EntityEntry | None:
        return next((e for e in self.entities if e.domain == "media_player"), None)

    def _update_sources(self, ts: Any) -> None:
        sources: dict[str, dict[str, Any]] = {}
        current = None
        for app in (ts.apps or {}).values():
            title = app.get("title") or app.get("id")
            sources[title] = app
            if app.get("id") == ts.current_app_id:
                current = title
        for inp in (ts.inputs or {}).values():
            label = inp.get("label") or inp.get("id")
            sources[label] = inp
            if inp.get("appId") == ts.current_app_id:
                current = label
        self.sources, self.current_source = sources, current

    def _publish(self) -> None:
        ent = self._media()
        sf = BASE_FEATURES | (F.TURN_ON if known_macs(self, ent) else 0)
        ts = self.client.tv_state if self.connected else None
        if ts is None or not ts.is_on:
            for e in self.entities:
                if e.domain == "media_player":
                    self.publish(e, "off", {"supported_features": sf, "device_class": "tv",
                                            **({"source_list": sorted(self.sources)} if self.sources else {})})
                elif e.domain == "remote":
                    self.publish(e, "off", self._remote_attrs())
            return
        self._update_sources(ts)
        self._confirm_info(ts, ent)
        state = "on"
        play_state = next((m.get("playState") for m in (ts.media_state or []) if m.get("playState")), None)
        if play_state == "playing":
            state, self._paused = "playing", False
        elif play_state == "paused":
            state, self._paused = "paused", True
        channel = ts.current_channel or {}
        attrs = clean({
            "volume_level": ts.volume / 100 if isinstance(ts.volume, (int, float)) else None,
            "is_volume_muted": ts.muted,
            "source": self.current_source,
            "source_list": sorted(self.sources),
            "app_id": ts.current_app_id,
            "media_content_type": "channel" if ts.current_app_id == LIVE_TV_APP_ID else None,
            "media_title": channel.get("channelName") if ts.current_app_id == LIVE_TV_APP_ID else None,
            "sound_output": ts.sound_output,
            "device_class": "tv",
            "supported_features": sf,
        })
        for e in self.entities:
            if e.domain == "media_player":
                self.publish(e, state, attrs)
            elif e.domain == "remote":
                self.publish(e, "on", self._remote_attrs())
            elif e.domain == "switch" and e.unique_id.endswith("_screen"):
                self.publish(e, "on" if ts.is_screen_on else "off", {})

    def _remote_attrs(self) -> dict[str, Any]:
        return {"supported_features": 0, "command_list": COMMAND_LIST}

    def _confirm_info(self, ts: Any, ent: EntityEntry | None) -> None:
        system = getattr(ts, "system_info", None) or {}
        software = getattr(ts, "software_info", None) or {}
        if ent is None or not (system or software):
            return
        major, minor = software.get("major_ver"), software.get("minor_ver")
        self.confirm_device(ent, manufacturer="LG", model=system.get("modelName") or None,
                            serial_number=system.get("serialNumber") or None,
                            sw_version=f"{major}.{minor}" if major is not None and minor is not None else None)

    # -- services -----------------------------------------------------------------
    async def require_client(self) -> Any:
        if not await self.connect():
            raise DeviceUnavailable(f"LG TV {self.host} is off or unreachable")
        return self.client

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain == "switch" and entity.unique_id.endswith("_screen"):
            client = await self.require_client()
            await client.set_screen_state(service == "turn_on" or (service == "toggle"
                                                                   and not client.tv_state.is_screen_on))
            self._publish()
            return None
        if domain == "remote":
            return await self._remote_call(entity, service, data)
        if domain != "media_player":
            raise ServiceNotFound(f"webostv has no {domain}.{service}")
        if service == "turn_on":
            macs = known_macs(self, entity, self.host)
            if not await wake_on_lan(macs):
                raise HubError(f"no MAC address is known for the LG TV at {self.host}; "
                               "set options.mac on its entry so Wake-on-LAN can reach it")
            return None
        if service == "toggle":
            if self.connected and self.client.tv_state.is_on:
                await self.client.power_off()
                self._publish()
                return None
            return await self.async_call(entity, domain, "turn_on", data)
        client = await self.require_client()
        live_tv = client.tv_state.current_app_id == LIVE_TV_APP_ID
        try:
            if service == "turn_off":
                await client.power_off()
            elif service == "volume_up":
                await client.volume_up()
            elif service == "volume_down":
                await client.volume_down()
            elif service == "volume_set":
                await client.set_volume(round(clamp01(data.get("volume_level", 0)) * 100))
            elif service == "volume_mute":
                await client.set_mute(bool(data.get("is_volume_muted", True)))
            elif service == "media_play":
                await client.play()
                self._paused = False
            elif service == "media_pause":
                await client.pause()
                self._paused = True
            elif service == "media_play_pause":
                await (client.play() if self._paused else client.pause())
                self._paused = not self._paused
            elif service == "media_stop":
                await client.stop()
            elif service == "media_next_track":
                await (client.channel_up() if live_tv else client.fast_forward())
            elif service == "media_previous_track":
                await (client.channel_down() if live_tv else client.rewind())
            elif service == "select_source":
                await self._select_source(client, str(data.get("source") or ""))
            elif service == "play_media":
                await self._play_media(client, data)
            else:
                raise ServiceNotFound(f"webostv cannot run media_player.{service}")
        except WebOsTvCommandError as exc:
            raise HubError(f"LG TV refused {service}: {exc}") from exc
        self._publish()
        return None

    async def _remote_call(self, entity: EntityEntry, service: str, data: dict[str, Any]) -> Any:
        media = self._media() or entity
        if service == "turn_on":
            await self.async_call(media, "media_player", "turn_on", {})
            if data.get("activity"):
                await self.async_call(media, "media_player", "select_source", {"source": data["activity"]})
            return None
        if service == "turn_off":
            return await self.async_call(media, "media_player", "turn_off", {})
        if service != "send_command":
            raise ServiceNotFound(f"webostv has no remote.{service}")

        async def press(command: str) -> None:
            name = command.strip().lower()
            if name == "power":
                await self.async_call(media, "media_player", "toggle", {})
            elif name == "power_off":
                await self.async_call(media, "media_player", "turn_off", {})
            elif name == "power_on":
                await self.async_call(media, "media_player", "turn_on", {})
            elif name == "play_pause":
                await self.async_call(media, "media_player", "media_play_pause", {})
            else:
                client = await self.require_client()
                try:
                    await client.button(LG_BUTTONS.get(name) or command.strip().upper())
                except WebOsTvCommandError as exc:
                    raise HubError(f"LG TV refused the {command} button: {exc}") from exc

        if not as_list(data.get("command")):
            raise HubError("remote.send_command needs a command")
        await send_command_list(data.get("command"), data, press)
        self._publish()
        return None

    async def _select_source(self, client: Any, source: str) -> None:
        if not self.sources:
            self._update_sources(client.tv_state)
        src = self.sources.get(source) or next(
            (v for k, v in self.sources.items() if k.lower() == source.lower()), None)
        if src is None:
            raise HubError(f"the LG TV has no source called {source!r}")
        if src.get("title"):
            await client.launch_app(src["id"])
        else:
            await client.set_input(src["id"])

    async def _play_media(self, client: Any, data: dict[str, Any]) -> None:
        media_id = str(data.get("media_content_id") or "")
        mtype = str(data.get("media_content_type") or "").lower()
        if not media_id:
            raise HubError("play_media needs media_content_id")
        if mtype == "channel":
            channels = client.tv_state.channels or []
            match = next((c for c in channels if media_id in (c.get("channelNumber"), c.get("channelId"))), None)
            if match is None:
                match = next((c for c in channels if (c.get("channelName") or "").lower() == media_id.lower()), None)
            await client.set_channel(match["channelId"] if match else media_id)
        elif mtype == "app":
            await client.launch_app(media_id)
        else:
            # URLs open in the TV's browser, the same call Jarvis's TV stage makes.
            await client.request("system.launcher/open", {"target": media_id})


async def _webostv_service(hub, call: ServiceCall) -> Any:
    """HA's ``webostv.command`` / ``webostv.button`` / ``webostv.select_sound_output``."""
    drivers = []
    for eid in call.entity_ids:
        ent = hub.entities.get(eid)
        drv = hub.drivers.get(ent.config_entry_id or "") if ent else None
        if isinstance(drv, WebOSDriver):
            drivers.append(drv)
    if not drivers:
        raise HubError(f"webostv.{call.service} needs an LG TV media_player target")
    responses = []
    for drv in drivers:
        client = await drv.require_client()
        try:
            if call.service == "command":
                command = str(call.data.get("command") or "")
                if not command:
                    raise HubError("webostv.command needs a command")
                responses.append(await client.request(command, call.data.get("payload")))
            elif call.service == "button":
                button = str(call.data.get("button") or "")
                if not button:
                    raise HubError("webostv.button needs a button")
                await client.button(button.upper())
            elif call.service == "select_sound_output":
                await client.change_sound_output(str(call.data.get("sound_output") or ""))
        except WebOsTvCommandError as exc:
            raise HubError(f"LG TV refused webostv.{call.service}: {exc}") from exc
    if call.return_response and call.service == "command":
        return responses[0] if len(responses) == 1 else responses
    return None
