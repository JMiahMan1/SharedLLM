"""WiZ bulbs over their local UDP JSON protocol (port 38899).

One HA config entry is one bulb (``data = {"host": ...}``), so one driver
instance talks to one address. The protocol is two methods that matter,
``getPilot`` (read) and ``setPilot`` (write), plus ``getSystemConfig`` /
``getModelConfig`` for the bulb's type and colour-temperature range and
``getPower`` for the metered models. Raw UDP rather than pywizlight because it
is a dozen lines, needs no dependency and lets every call carry its own
timeout; pywizlight's own retry and push machinery added nothing here.

Entities, by unique_id (HA's wiz integration):
- ``{mac}``              the light
- ``{mac}_effect_speed`` number, the speed of the running scene (10-200)
- ``{mac}_power``        sensor, watts (only bulbs that meter report it)

A bulb switched off at the wall simply stops answering. Setup raises in that
case so the runtime marks the entry ``setup_retry`` and tries again with
backoff, which is what HA does; once loaded, a bulb that goes quiet is marked
unavailable by the poll and comes back on the next poll that it answers.
"""
from __future__ import annotations

import asyncio
import colorsys
import contextlib
import json
from typing import Any

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, ServiceNotFound

PORT = 38899
REQUEST_TIMEOUT = 3.0
# UDP to a bulb on Wi-Fi drops a packet now and then; resend inside the timeout
# rather than failing the whole request on one lost datagram.
RESEND_EVERY = 0.6

# pywizlight's scene table (scenes.py), copied so the driver needs no library.
SCENES: dict[int, str] = {
    1: "Ocean", 2: "Romance", 3: "Sunset", 4: "Party", 5: "Fireplace", 6: "Cozy", 7: "Forest",
    8: "Pastel colors", 9: "Wake-up", 10: "Bedtime", 11: "Warm white", 12: "Daylight", 13: "Cool white",
    14: "Night light", 15: "Focus", 16: "Relax", 17: "True colors", 18: "TV time", 19: "Plantgrowth",
    20: "Spring", 21: "Summer", 22: "Fall", 23: "Deep dive", 24: "Jungle", 25: "Mojito", 26: "Club",
    27: "Christmas", 28: "Halloween", 29: "Candlelight", 30: "Golden white", 31: "Pulse", 32: "Steampunk",
    33: "Diwali", 34: "White", 35: "Alarm", 36: "Snowy sky", 40: "Dim-to-warm", 1000: "Rhythm",
}
TW_SCENES = [6, 9, 10, 11, 12, 13, 14, 15, 16, 18, 29, 30, 31, 32, 33, 35, 40]
DW_SCENES = [9, 10, 14, 29, 31, 32, 34, 35]
SCENE_IDS = {name.lower(): sid for sid, name in SCENES.items()}

# HA light feature bit
FEATURE_EFFECT = 4


class _UDPReply(asyncio.DatagramProtocol):
    def __init__(self, method: str) -> None:
        self.method = method
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        try:
            msg = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return
        if isinstance(msg, dict) and msg.get("method", self.method) == self.method and not self.future.done():
            self.future.set_result(msg)

    def error_received(self, exc: Exception) -> None:
        # ICMP port unreachable and friends: keep resending until the timeout.
        pass


async def udp_request(host: str, method: str, params: dict[str, Any] | None = None, *,
                      port: int = PORT, timeout: float = REQUEST_TIMEOUT) -> dict[str, Any]:
    """Send one WiZ request and return the reply's ``result``. Raises DeviceUnavailable."""
    loop = asyncio.get_running_loop()
    payload = json.dumps({"method": method, "params": params or {}}).encode()
    proto = _UDPReply(method)
    try:
        transport, _ = await asyncio.wait_for(
            loop.create_datagram_endpoint(lambda: proto, remote_addr=(host, port)), timeout)
    except (OSError, TimeoutError) as exc:
        raise DeviceUnavailable(f"WiZ bulb {host} unreachable: {exc}") from exc
    try:
        deadline = loop.time() + timeout
        while True:
            with contextlib.suppress(OSError):
                transport.sendto(payload)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise DeviceUnavailable(f"WiZ bulb {host} did not answer {method}")
            try:
                msg = await asyncio.wait_for(asyncio.shield(proto.future), min(RESEND_EVERY, remaining))
                break
            except TimeoutError:
                continue
    finally:
        transport.close()
    if "error" in msg:
        raise DeviceUnavailable(f"WiZ bulb {host} refused {method}: {msg['error']}")
    result = msg.get("result")
    return result if isinstance(result, dict) else {}


#: Poll period while a bulb answers, and while it does not (off at the switch).
ONLINE_POLL_S = 30.0
OFFLINE_POLL_S = 5.0


class WizDriver(Driver):
    platform = "wiz"
    poll_interval = ONLINE_POLL_S

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.host: str = str(entry.options.get("host") or entry.data.get("host") or "")
        self.mac = ""
        self.module = ""
        self.kelvin_range = (2700, 6500)
        self.light: EntityEntry | None = None
        self.speed: EntityEntry | None = None
        self.power: EntityEntry | None = None
        self.pilot: dict[str, Any] = {}
        self._online = False
        self._identified = False
        self.poll_interval = ONLINE_POLL_S
        for e in self.entities:
            uid = e.unique_id or ""
            if uid.endswith("_effect_speed"):
                self.speed = e
            elif uid.endswith("_power"):
                self.power = e
            elif e.domain == "light":
                self.light = e

    # -- transport (tests replace this) ---------------------------------------
    async def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await udp_request(self.host, method, params)

    # -- bulb type ------------------------------------------------------------
    @property
    def is_rgb(self) -> bool:
        return "RGB" in self.module.upper()

    @property
    def is_dimmable_only(self) -> bool:
        m = self.module.upper()
        return "SHDW" in m and "RGB" not in m

    def _scene_ids(self) -> list[int]:
        if self.is_rgb:
            return [sid for sid in SCENES if sid != 1000] + [1000]
        if self.is_dimmable_only:
            return DW_SCENES
        return TW_SCENES

    def _color_modes(self) -> list[str]:
        if self.is_dimmable_only:
            return ["brightness"]
        return ["color_temp", "rgb"] if self.is_rgb else ["color_temp"]

    # -- lifecycle ------------------------------------------------------------
    async def async_setup(self) -> None:
        if not self.entities:
            return  # an entry whose entities were all removed has nothing to drive
        if not self.host:
            raise DeviceUnavailable("WiZ entry has no host")
        # Bathroom bulbs sit behind a wall switch and lose power many times a
        # day (HA logged 125-160 drops each in ten days). A bulb that is off
        # now is not a broken entry: load it unavailable and keep checking.
        try:
            self.pilot = await self._request("getPilot")
        except DeviceUnavailable as exc:
            self.log.info("WiZ %s (%s) is off or unreachable; waiting for it: %s", self.entry.title, self.host, exc)
            self._went_offline()
            return
        await self._identify()
        self._came_online()
        await self._publish_all()

    async def _identify(self) -> None:
        """Model details, read once per bulb; all best effort."""
        if self._identified:
            return
        try:
            cfg = await self._request("getSystemConfig")
            self.module = str(cfg.get("moduleName") or "")
            self.mac = str(cfg.get("mac") or "")
        except DeviceUnavailable:
            self.log.info("WiZ %s: no system config, assuming tunable white", self.host)
        if not self.module and self.light is not None and "rgb" in (self.light.entity_id + self.entry.title).lower():
            self.module = "RGB"
        with contextlib.suppress(DeviceUnavailable):
            model = await self._request("getModelConfig")
            cct = [int(x) for x in model.get("cctRange") or [] if isinstance(x, int | float) and x > 0]
            if cct:
                self.kelvin_range = (min(cct), max(cct))
        if self.kelvin_range == (2700, 6500) and self.is_rgb:
            self.kelvin_range = (2200, 6500)
        self._identified = True

    def _came_online(self) -> None:
        self._online = True
        self.poll_interval = ONLINE_POLL_S

    def _went_offline(self) -> None:
        self._online = False
        # Poll fast while it is off, so a flipped wall switch shows within seconds.
        self.poll_interval = OFFLINE_POLL_S
        self.mark_unavailable()

    async def async_poll(self) -> None:
        if not self.entities:
            return
        try:
            self.pilot = await self._request("getPilot")
        except DeviceUnavailable as exc:
            if self._online:
                self.log.warning("WiZ %s (%s) unreachable: %s", self.entry.title, self.host, exc)
            self._went_offline()
            return
        if not self._online:
            self.log.info("WiZ %s (%s) is back", self.entry.title, self.host)
            await self._identify()
        self._came_online()
        await self._publish_all()

    # -- states ---------------------------------------------------------------
    async def _publish_all(self) -> None:
        self._publish_light()
        if self.speed is not None:
            speed = self.pilot.get("speed")
            self.publish(self.speed, speed if speed is not None else "unknown",
                         {"min": 10, "max": 200, "step": 1, "mode": "slider"})
        if self.power is not None:
            try:
                res = await self._request("getPower")
                watts = res.get("power")
                value = "unknown" if watts is None else round(float(watts) / 1000, 1)
            except DeviceUnavailable:
                value = "unknown"
            self.publish(self.power, value, {"unit_of_measurement": "W", "device_class": "power",
                                             "state_class": "measurement"})

    def _light_attributes(self) -> dict[str, Any]:
        p = self.pilot
        modes = self._color_modes()
        on = bool(p.get("state"))
        scene_ids = self._scene_ids()
        attrs: dict[str, Any] = {
            "supported_color_modes": modes,
            "min_color_temp_kelvin": self.kelvin_range[0],
            "max_color_temp_kelvin": self.kelvin_range[1],
            "effect_list": [SCENES[s] for s in scene_ids if s in SCENES],
            "supported_features": FEATURE_EFFECT,
            "color_mode": None, "brightness": None, "color_temp_kelvin": None,
            "rgb_color": None, "hs_color": None, "effect": None,
        }
        if not on:
            return attrs
        dimming = p.get("dimming")
        attrs["brightness"] = round(max(0, min(100, int(dimming))) * 255 / 100) if dimming is not None else 255
        scene = p.get("sceneId") or 0
        if scene:
            attrs["effect"] = SCENES.get(int(scene))
        if "r" in p and "g" in p and "b" in p and "rgb" in modes and not p.get("temp"):
            rgb = (int(p["r"]), int(p["g"]), int(p["b"]))
            attrs["color_mode"] = "rgb"
            attrs["rgb_color"] = list(rgb)
            h, s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
            attrs["hs_color"] = [round(h * 360, 3), round(s * 100, 3)]
        elif "color_temp" in modes:
            attrs["color_mode"] = "color_temp"
            if p.get("temp"):
                attrs["color_temp_kelvin"] = int(p["temp"])
        else:
            attrs["color_mode"] = "brightness"
        return attrs

    def _publish_light(self) -> None:
        if self.light is None:
            return
        self.publish(self.light, "on" if self.pilot.get("state") else "off", self._light_attributes())

    # -- services -------------------------------------------------------------
    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if not self._online:
            # The switch may have just been flipped on: try now rather than
            # wait for the next poll, and learn the bulb's type first.
            self.pilot = await self._request("getPilot")
            await self._identify()
            self._came_online()
        if entity is self.light or (self.light is not None and entity.entity_id == self.light.entity_id):
            if service == "toggle":
                service = "turn_off" if self.pilot.get("state") else "turn_on"
            if service == "turn_on":
                await self._set_pilot(self._turn_on_params(data))
            elif service == "turn_off":
                await self._set_pilot({"state": False})
            else:
                raise ServiceNotFound(f"wiz cannot run {domain}.{service}")
        elif self.speed is not None and entity.entity_id == self.speed.entity_id and service == "set_value":
            value = max(10, min(200, int(float(data.get("value", 100)))))
            await self._set_pilot({"speed": value})
        else:
            raise ServiceNotFound(f"wiz cannot run {domain}.{service} on {entity.entity_id}")
        return None

    def _turn_on_params(self, data: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {"state": True}
        bri = None
        if data.get("brightness") is not None:
            bri = float(data["brightness"]) / 255 * 100
        elif data.get("brightness_pct") is not None:
            bri = float(data["brightness_pct"])
        if bri is not None:
            if bri <= 0:
                return {"state": False}
            params["dimming"] = max(10, min(100, round(bri)))
        kelvin = data.get("color_temp_kelvin") or data.get("kelvin")
        if kelvin is None and data.get("color_temp") is not None:  # legacy mireds
            kelvin = 1_000_000 / float(data["color_temp"])
        rgb = data.get("rgb_color")
        if rgb is None and data.get("hs_color") is not None:
            h, s = data["hs_color"]
            r, g, b = colorsys.hsv_to_rgb(float(h) / 360, float(s) / 100, 1.0)
            rgb = [round(r * 255), round(g * 255), round(b * 255)]
        effect = data.get("effect")
        if effect:
            sid = SCENE_IDS.get(str(effect).lower())
            if sid is None:
                raise ServiceNotFound(f"WiZ has no effect {effect!r}")
            params["sceneId"] = sid
        elif rgb is not None and self.is_rgb:
            params.update({"r": int(rgb[0]), "g": int(rgb[1]), "b": int(rgb[2])})
        elif kelvin is not None and not self.is_dimmable_only:
            lo, hi = self.kelvin_range
            params["temp"] = max(lo, min(hi, round(float(kelvin))))
        return params

    async def _set_pilot(self, params: dict[str, Any]) -> None:
        try:
            await self._request("setPilot", params)
        except DeviceUnavailable:
            self._online = False
            self.mark_unavailable()
            raise
        self._online = True
        # Read back what the bulb actually did (it clamps and may ignore keys).
        try:
            self.pilot = await self._request("getPilot")
        except DeviceUnavailable:
            self.pilot = {**self.pilot, **{k: v for k, v in params.items() if k != "sceneId"},
                          "sceneId": params.get("sceneId", 0)}
        self._publish_light()
        if self.speed is not None and self.pilot.get("speed") is not None:
            self.publish(self.speed, self.pilot["speed"], {"min": 10, "max": 200, "step": 1, "mode": "slider"})
