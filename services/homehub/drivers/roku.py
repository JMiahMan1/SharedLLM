"""Roku over ECP (HTTP on port 8060): no pairing, no library, no push.

One entry per Roku; ``data["host"]`` is all HA stored and the unique_id is the
serial number. Entities: the media_player and remote (unique_id = serial),
binary sensors and sensors (``<serial>_<key>``) and the disabled application
select. State follows HA's roku integration: standby when the power mode is
not ``PowerOn``, idle on the home screen or the screensaver, playing/paused
while the media player reports a stream, on for any other app.
"""
from __future__ import annotations

import asyncio
from typing import Any
from xml.etree import ElementTree as ET

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, HubError, ServiceCall, ServiceNotFound
from services.homehub.drivers.media_common import F, Http, clean, send_command_list, utc_iso

ECP_PORT = 8060
PLAY_ON_ROKU = "15985"

#: rokuecp's VALID_REMOTE_KEYS (the names HA's ``remote.send_command`` takes for a Roku).
ROKU_KEYS = {
    "home": "Home", "reverse": "Rev", "forward": "Fwd", "play": "Play", "select": "Select", "left": "Left",
    "right": "Right", "down": "Down", "up": "Up", "back": "Back", "replay": "InstantReplay", "info": "Info",
    "backspace": "Backspace", "search": "Search", "enter": "Enter", "find_remote": "FindRemote",
    "volume_down": "VolumeDown", "volume_up": "VolumeUp", "volume_mute": "VolumeMute", "sleep": "Sleep",
    "channel_up": "ChannelUp", "channel_down": "ChannelDown", "input_tuner": "InputTuner",
    "input_hdmi1": "InputHDMI1", "input_hdmi2": "InputHDMI2", "input_hdmi3": "InputHDMI3",
    "input_hdmi4": "InputHDMI4", "input_av1": "InputAV1", "power": "Power", "poweroff": "PowerOff",
    "poweron": "PowerOn",
    # Friendlier spellings Jarvis's own tables use.
    "power_off": "PowerOff", "power_on": "PowerOn", "ok": "Select", "pause": "Play", "fast_forward": "Fwd",
    "rewind": "Rev", "mute": "VolumeMute", "menu": "Info", "next": "Fwd", "previous": "Rev",
}
_ECP_NAMES = {v.lower(): v for v in ROKU_KEYS.values()}

BASE_FEATURES = (F.PAUSE | F.VOLUME_MUTE | F.PREVIOUS_TRACK | F.NEXT_TRACK | F.TURN_ON | F.TURN_OFF
                 | F.PLAY_MEDIA | F.VOLUME_STEP | F.SELECT_SOURCE | F.PLAY | F.BROWSE_MEDIA)

BINARY_SENSORS = {
    "headphones_connected": "headphones-connected", "supports_airplay": "supports-airplay",
    "supports_ethernet": "supports-ethernet", "supports_find_remote": "supports-find-remote",
}


def ecp_key(command: str) -> str:
    """A command as HA or Jarvis spells it -> the ECP key name. ``Lit_x`` passes through."""
    c = command.strip()
    if c.lower().startswith("lit_"):
        return "Lit_" + c[4:]
    return ROKU_KEYS.get(c.lower()) or _ECP_NAMES.get(c.lower()) or c


def _xml(text: str) -> ET.Element:
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise DeviceUnavailable("the Roku sent unreadable XML") from exc


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() == "true"


def _ms(v: str | None) -> float | None:
    try:
        return int((v or "").split()[0]) / 1000
    except (ValueError, IndexError):
        return None


class RokuDriver(Driver):
    platform = "roku"
    poll_interval = 10.0
    #: ECP answers before the screen has changed; wait this long before reading state back.
    settle_delay = 0.5

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.http = Http(timeout=6.0)
        self.host = str(entry.options.get("host") or entry.data.get("host") or "")
        self.info: dict[str, str] = {}
        self.apps: list[dict[str, str]] = []
        self.active: dict[str, Any] = {}
        self.media: dict[str, Any] = {}
        self.state = "standby"
        self._apps_at = 0.0

    @property
    def base(self) -> str:
        return f"http://{self.host}:{ECP_PORT}"

    async def async_setup(self) -> None:
        if not self.host:
            raise HubError("roku entry has no host")
        self.hub.register_service("roku", "search", _roku_search, {"keyword": {"required": True}})
        await self.refresh()

    async def async_poll(self) -> None:
        await self.refresh()

    async def async_unload(self) -> None:
        await self.http.close()

    # -- ECP --------------------------------------------------------------------
    async def _get(self, path: str) -> ET.Element:
        status, text = await self.http.request("GET", f"{self.base}/{path}")
        if status >= 400:
            raise DeviceUnavailable(f"Roku {path} answered HTTP {status}")
        return _xml(text)

    async def _post(self, path: str, params: dict[str, str] | None = None) -> None:
        status, _ = await self.http.request("POST", f"{self.base}/{path}", params=params, data=b"")
        if status >= 400:
            raise HubError(f"Roku refused {path} (HTTP {status})")

    async def keypress(self, key: str) -> None:
        await self._post(f"keypress/{ecp_key(key)}")

    async def refresh(self) -> None:
        try:
            info = await self._get("query/device-info")
            self.info = {child.tag: (child.text or "") for child in info}
            loop_time = asyncio.get_running_loop().time()
            if not self.apps or loop_time - self._apps_at > 300:
                apps = await self._get("query/apps")
                self.apps = [{"id": a.get("id", ""), "type": a.get("type", ""), "name": (a.text or "").strip()}
                             for a in apps.iter("app")]
                self._apps_at = loop_time
            active = await self._get("query/active-app")
            app = active.find("app")
            saver = active.find("screensaver")
            self.active = {
                "id": app.get("id") if app is not None else None,
                "name": (app.text or "").strip() if app is not None else None,
                "screensaver": saver is not None,
            }
            self.media = {}
            if self.active.get("id"):
                try:
                    player = await self._get("query/media-player")
                    self.media = {
                        "state": player.get("state", ""),
                        "position": _ms(player.findtext("position")),
                        "duration": _ms(player.findtext("duration")),
                        "is_live": _truthy(player.findtext("is_live")),
                    }
                except DeviceUnavailable:
                    self.media = {}
        except DeviceUnavailable:
            self.mark_unavailable()
            return
        self._publish_all()

    def _compute_state(self) -> str:
        if self.info.get("power-mode", "PowerOn") != "PowerOn":
            return "standby"
        name = self.active.get("name")
        if name in (None, "", "Roku", "Power Saver") or self.active.get("screensaver"):
            return "idle"
        mstate = self.media.get("state")
        if mstate == "pause":
            return "paused"
        if mstate in ("play", "buffer", "startup"):
            return "playing"
        return "on"

    def _publish_all(self) -> None:
        self.state = self._compute_state()
        serial = self.info.get("serial-number") or self.entry.unique_id or ""
        app_name = self.active.get("name")
        on_home = app_name in (None, "", "Roku")
        source_list = ["Home", *sorted(a["name"] for a in self.apps if a["name"])]
        app_id = self.active.get("id")
        for e in self.entities:
            uid = e.unique_id
            if e.domain == "media_player":
                attrs: dict[str, Any] = {"source_list": source_list, "supported_features": BASE_FEATURES}
                if self.state not in ("standby",):
                    attrs.update(clean({
                        "source": "Home" if on_home else app_name,
                        "app_id": app_id,
                        "app_name": "Home" if on_home else app_name,
                        "media_content_type": ("channel" if app_id == "tvinput.dtv" else "app") if app_id else None,
                    }))
                    if self.state in ("playing", "paused"):
                        attrs.update(clean({
                            "media_position": self.media.get("position"),
                            "media_duration": None if self.media.get("is_live") else self.media.get("duration"),
                            "media_position_updated_at": utc_iso() if self.media.get("position") is not None else None,
                        }))
                self.publish(e, self.state, attrs)
            elif e.domain == "remote":
                self.publish(e, "off" if self.state == "standby" else "on", {"supported_features": 0})
            elif e.domain == "binary_sensor":
                key = uid[len(serial) + 1:] if serial and uid.startswith(serial + "_") else uid.split("_", 1)[-1]
                field = BINARY_SENSORS.get(key)
                if field is not None:
                    self.publish(e, "on" if _truthy(self.info.get(field)) else "off", {})
            elif e.domain == "sensor":
                if uid.endswith("_active_app_id"):
                    self.publish(e, app_id or "unknown", {})
                elif uid.endswith("_active_app"):
                    self.publish(e, "Home" if on_home else app_name, {})
            elif e.domain == "select" and uid.endswith("_application"):
                self.publish(e, "Home" if on_home else (app_name or "Home"), {"options": source_list})

    # -- services ----------------------------------------------------------------
    def _app_id(self, source: str) -> str | None:
        for a in self.apps:
            if source in (a["id"], a["name"]):
                return a["id"]
        low = source.lower()
        return next((a["id"] for a in self.apps if a["name"].lower() == low), None)

    async def launch(self, source: str, params: dict[str, str] | None = None) -> None:
        if source.lower() == "home":
            await self.keypress("home")
            return
        app_id = self._app_id(source) or (source if source.replace(".", "").isalnum() else None)
        if app_id is None:
            raise HubError(f"the Roku has no app called {source!r}")
        await self._post(f"launch/{app_id}", params)

    async def search(self, keyword: str) -> None:
        await self._post("search/browse", {"keyword": keyword})

    async def _play_media(self, data: dict[str, Any]) -> None:
        media_id = str(data.get("media_content_id") or "")
        mtype = str(data.get("media_content_type") or "").lower()
        extra = dict(data.get("extra") or {})
        if not media_id:
            raise HubError("play_media needs media_content_id")
        if mtype == "app":
            params = {k: str(v) for k, v in extra.items() if k in ("contentID", "MediaType", "mediaType")}
            await self.launch(media_id, params or None)
        elif mtype == "channel":
            await self._post("launch/tvinput.dtv", {"ch": media_id})
        elif mtype in ("music", "audio") or mtype.startswith("audio/"):
            params = clean({"t": "a", "u": media_id, "songName": extra.get("name"),
                            "songFormat": extra.get("format") or "mp3", "artistName": extra.get("artist_name"),
                            "albumArt": extra.get("thumbnail")})
            await self._post(f"input/{PLAY_ON_ROKU}", params)
        else:  # url, video or a video MIME type
            params = clean({"t": "v", "u": media_id, "videoName": extra.get("name"),
                            "videoFormat": extra.get("format")})
            await self._post(f"input/{PLAY_ON_ROKU}", params)

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain == "remote":
            if service == "send_command":
                await send_command_list(data.get("command"), data, self.keypress)
            elif service == "turn_on":
                if data.get("activity"):
                    await self.launch(str(data["activity"]))
                else:
                    await self.keypress("poweron")
            elif service == "turn_off":
                await self.keypress("poweroff")
            else:
                raise ServiceNotFound(f"roku has no remote.{service}")
        elif domain == "select" and service == "select_option":
            await self.launch(str(data.get("option") or ""))
        elif domain == "media_player":
            await self._media_call(service, data)
        else:
            raise ServiceNotFound(f"roku has no {domain}.{service}")
        await asyncio.sleep(self.settle_delay)
        await self.refresh()

    async def _media_call(self, service: str, data: dict[str, Any]) -> None:
        state = self.state
        if service == "turn_on":
            await self.keypress("poweron")
        elif service == "turn_off":
            await self.keypress("poweroff")
        elif service == "toggle":
            await self.keypress("poweron" if state == "standby" else "poweroff")
        elif service == "media_play":
            if state not in ("standby", "playing"):
                await self.keypress("play")
        elif service == "media_pause":
            if state not in ("standby", "paused"):
                await self.keypress("play")
        elif service in ("media_play_pause", "media_stop"):
            if state != "standby":
                await self.keypress("play")
        elif service == "media_next_track":
            await self.keypress("forward")
        elif service == "media_previous_track":
            await self.keypress("reverse")
        elif service == "volume_up":
            await self.keypress("volume_up")
        elif service == "volume_down":
            await self.keypress("volume_down")
        elif service == "volume_mute":
            await self.keypress("volume_mute")  # ECP can only toggle, and cannot read mute back
        elif service == "select_source":
            await self.launch(str(data.get("source") or ""))
        elif service == "play_media":
            await self._play_media(data)
        else:
            raise ServiceNotFound(f"roku cannot run media_player.{service}")


async def _roku_search(hub, call: ServiceCall) -> None:
    """HA's ``roku.search``: open the Roku search screen for ``keyword`` on each target."""
    keyword = str(call.data.get("keyword") or "")
    if not keyword:
        raise HubError("roku.search needs a keyword")
    done = 0
    for eid in call.entity_ids:
        ent = hub.entities.get(eid)
        drv = hub.drivers.get(ent.config_entry_id or "") if ent else None
        if isinstance(drv, RokuDriver):
            await drv.search(keyword)
            done += 1
    if not done:
        raise HubError("roku.search needs a Roku media_player target")
