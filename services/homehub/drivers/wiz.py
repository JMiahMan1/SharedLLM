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

What the bulb is comes from the bulb: ``getSystemConfig`` gives its module
(RGB, tunable white or dimmable only), firmware and MAC, ``getModelConfig`` its
colour-temperature range, and ``getPower`` whether it meters. Those are
confirmed onto the device and the entities (``confirm_device`` /
``confirm_entity``). Nothing is guessed from a name: a bulb that has not
answered ``getSystemConfig`` yet is driven as tunable white and asked again on
the next poll.

A bulb switched off at the wall simply stops answering. Setup raises in that
case so the runtime marks the entry ``setup_retry`` and tries again with
backoff, which is what HA does; once loaded, a bulb that misses
``MISSES_BEFORE_UNAVAILABLE`` polls in a row is marked unavailable and comes back
on the next poll (or command) that it answers.
"""
from __future__ import annotations

import asyncio
import colorsys
import contextlib
import ipaddress
import json
import socket
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
    def __init__(self, method: str, host: str | None = None) -> None:
        self.method = method
        self.host = host
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        # The socket is not connected (connecting would resolve again), so only the bulb's own replies count.
        if self.host is not None and isinstance(addr, tuple) and addr and addr[0] != self.host:
            return
        try:
            msg = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return
        if isinstance(msg, dict) and msg.get("method", self.method) == self.method and not self.future.done():
            self.future.set_result(msg)

    def error_received(self, exc: Exception) -> None:
        # ICMP port unreachable and friends: keep resending until the timeout.
        pass


class WizTransportError(DeviceUnavailable):
    """The hub could not open a socket for the request: nothing reached the bulb, so it says nothing about it."""


def _bulb_address(host: str, port: int) -> tuple[socket.socket, tuple[str, int]]:
    """A non-blocking UDP socket and the bulb's address, with no name resolution.

    ``loop.create_datagram_endpoint(remote_addr=...)`` resolves the address, and under uvloop (which
    uvicorn runs the hub on) that is always a ``uv_getaddrinfo`` job on libuv's four-thread pool, even for
    a numeric address. That pool also runs every DNS lookup in the process, and on the server a lookup of a
    single-label name (aioesphomeapi tries an offline node's ``x.local`` as ``x`` too) takes ~4 s to fail.
    Four of those at once and opening a socket to a bulb waited past its 3 s timeout, so working bulbs
    were called unreachable (measured 2026-11-18). A socket we open ourselves skips the pool entirely.
    """
    ip = ipaddress.ip_address(host)  # ValueError for a name; the caller resolves it first
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
    except OSError as exc:
        raise WizTransportError(f"no UDP socket for WiZ bulb {host}: {exc}") from exc
    sock.setblocking(False)
    return sock, (str(ip), port)


async def _numeric_host(host: str, port: int, timeout: float) -> str:
    """``host`` as an IP address. Entries hold one; a name is resolved once per request (rare)."""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM), timeout)
    except (OSError, TimeoutError) as exc:
        raise DeviceUnavailable(f"WiZ bulb {host} does not resolve: {type(exc).__name__} {exc}".rstrip()) from exc
    return str(infos[0][4][0])


async def udp_request(host: str, method: str, params: dict[str, Any] | None = None, *,
                      port: int = PORT, timeout: float = REQUEST_TIMEOUT) -> dict[str, Any]:
    """Send one WiZ request and return the reply's ``result``. Raises DeviceUnavailable.

    ``WizTransportError`` (a DeviceUnavailable) means the hub could not send at all.
    """
    loop = asyncio.get_running_loop()
    payload = json.dumps({"method": method, "params": params or {}}).encode()
    address = await _numeric_host(host, port, timeout)
    sock, addr = _bulb_address(address, port)
    proto = _UDPReply(method, addr[0])
    try:
        transport, _ = await loop.create_datagram_endpoint(lambda: proto, sock=sock)
    except OSError as exc:
        sock.close()
        raise WizTransportError(f"WiZ bulb {host}: could not open a socket: {type(exc).__name__} {exc}".rstrip()) \
            from exc
    try:
        deadline = loop.time() + timeout
        while True:
            with contextlib.suppress(OSError):
                transport.sendto(payload, addr)
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
#: A bulb that was answering is asked once more, this long after a miss, before the poll counts as missed.
RECHECK_AFTER_S = 1.0
#: Missed polls in a row before a bulb that was answering is called unavailable. Bulbs flicked to
#: unavailable and back every minute (2026-11-14), and a re-check within the poll did not stop it: the
#: misses were the hub's own (a socket that could not be opened in time, 2026-11-18), several bulbs at once.
#: A bulb switched off at the wall is unavailable after about 20 s instead of 8.
MISSES_BEFORE_UNAVAILABLE = 2
#: A command's whole budget: setPilot (with its resends) and the read-back. A bulb marked unavailable is
#: still sent the command (it may have just come back), but a silent one cannot hold a room's reply.
COMMAND_BUDGET_S = 5.0
#: The read-back after a command is a courtesy; the command already answered.
READBACK_TIMEOUT_S = 1.5


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
        self._misses = 0
        self._identified = False
        self._bg_tasks: set[asyncio.Task] = set()
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
        if not self.host:
            raise DeviceUnavailable("WiZ entry has no host")
        if not any(e.config_entry_id == self.entry.entry_id for e in self.hub.entities.values()):
            # HA creates a bulb's entities when it sets the entry up; an entry that came over with none
            # (HA never reached the bulb, or they were deleted) gets them once the bulb answers. Entities
            # a person disabled are still in the registry and are left alone.
            await self._register_entities()
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

    async def _register_entities(self) -> None:
        """The light, its effect speed and (metered bulbs) its power, as the flow that adds a bulb makes them.

        Raises ``DeviceUnavailable`` while the bulb does not answer, so the entry shows ``setup_retry``
        (the bulb is missing) instead of loading with nothing to drive.
        """
        from services.homehub.discovery import norm_mac, wiz_type_label

        try:
            cfg = await self._request("getSystemConfig")
        except DeviceUnavailable as exc:
            raise DeviceUnavailable(f"WiZ {self.entry.title} ({self.host}) has no entities yet and does not "
                                    f"answer, so they cannot be made: {exc}") from exc
        mac = norm_mac(cfg.get("mac")) or norm_mac(self.entry.unique_id)
        if mac is None:
            raise DeviceUnavailable(f"WiZ {self.host} did not report its MAC address")
        module = str(cfg.get("moduleName") or "")
        metered = False
        with contextlib.suppress(DeviceUnavailable):
            metered = (await self._request("getPower")).get("power") is not None
        dev = self.ensure_device(self.entry.title or f"WiZ {wiz_type_label(module)} {mac[-6:].upper()}",
                                 connections=[["mac", ":".join(mac[i:i + 2] for i in range(0, 12, 2))]],
                                 manufacturer="WiZ", model=module or None)
        self.light = self.ensure_entity("light", mac, None, device=dev, supported_features=FEATURE_EFFECT)
        self.speed = self.ensure_entity("number", f"{mac}_effect_speed", "Effect speed", device=dev,
                                        entity_category="config",
                                        capabilities={"min": 10, "max": 200, "step": 1, "mode": "slider"})
        if metered:
            self.power = self.ensure_entity("sensor", f"{mac}_power", "Power", device=dev,
                                            original_device_class="power", unit_of_measurement="W",
                                            capabilities={"state_class": "measurement"})

    async def _identify(self) -> None:
        """Model details, read from the bulb until it has answered them; all best effort."""
        if self._identified:
            return
        cfg: dict[str, Any] = {}
        try:
            cfg = await self._request("getSystemConfig")
            self.module = str(cfg.get("moduleName") or "")
            self.mac = str(cfg.get("mac") or "")
        except DeviceUnavailable:
            self.log.info("WiZ %s: no system config yet, driving it as tunable white until it answers", self.host)
            return  # _identified stays False: asked again on the next poll
        with contextlib.suppress(DeviceUnavailable):
            model = await self._request("getModelConfig")
            cct = [int(x) for x in model.get("cctRange") or [] if isinstance(x, int | float) and x > 0]
            if cct:
                self.kelvin_range = (min(cct), max(cct))
        if self.kelvin_range == (2700, 6500) and self.is_rgb:
            self.kelvin_range = (2200, 6500)
        self._identified = True
        self._confirm(cfg)

    def _confirm(self, cfg: dict[str, Any]) -> None:
        """What the bulb said about itself, onto its registry records."""
        from services.homehub.discovery import norm_mac, wiz_type_label

        mac = norm_mac(self.mac)
        anchor = self.light or self.speed or self.power
        if anchor is not None:
            self.confirm_device(anchor, manufacturer="WiZ", model=self.module or None,
                                sw_version=str(cfg["fwVersion"]) if cfg.get("fwVersion") else None,
                                connections=[["mac", ":".join(mac[i:i + 2] for i in range(0, 12, 2))]] if mac else [])
        if self.light is not None:
            self.confirm_entity(self.light, supported_features=FEATURE_EFFECT, capabilities={
                "supported_color_modes": self._color_modes(), "min_color_temp_kelvin": self.kelvin_range[0],
                "max_color_temp_kelvin": self.kelvin_range[1]})
        if self.speed is not None:
            self.confirm_entity(self.speed, entity_category="config", original_name="Effect speed",
                                capabilities={"min": 10, "max": 200, "step": 1, "mode": "slider"})
        if self.power is not None:
            self.confirm_entity(self.power, original_device_class="power", unit_of_measurement="W",
                                capabilities={"state_class": "measurement"})
        self.log.debug("WiZ %s is a %s", self.host, wiz_type_label(self.module))

    def _came_online(self) -> None:
        self._online = True
        self._misses = 0
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
        except WizTransportError as exc:
            # The hub could not send: that says nothing about the bulb, so its state stands.
            self.log.warning("WiZ %s (%s): poll not sent: %s", self.entry.title, self.host, exc)
            return
        except DeviceUnavailable as exc:
            if not self._online:
                self._went_offline()
                return
            self.log.debug("WiZ %s (%s) missed a poll, asking again: %s", self.entry.title, self.host, exc)
            await asyncio.sleep(RECHECK_AFTER_S)
            try:
                self.pilot = await self._request("getPilot")
            except DeviceUnavailable as again:
                self._misses += 1
                if self._misses < MISSES_BEFORE_UNAVAILABLE:
                    # Ask again soon rather than in 30 s, but keep showing the last state until it is clear.
                    self.poll_interval = OFFLINE_POLL_S
                    self.log.info("WiZ %s (%s) missed %d poll(s) in a row; still shown as it was: %s",
                                  self.entry.title, self.host, self._misses, again)
                    return
                self.log.warning("WiZ %s (%s) unreachable after %d missed polls: %s",
                                 self.entry.title, self.host, self._misses, again)
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
        """Run a service within ``COMMAND_BUDGET_S``.

        A bulb shown unavailable is still sent the command: it may have just been switched back on, and the
        command itself is the quickest way to find out (no getPilot first). Only a toggle needs to read the
        bulb first, and only colour needs its type.
        """
        try:
            async with asyncio.timeout(COMMAND_BUDGET_S):
                return await self._call(entity, domain, service, data)
        except TimeoutError as exc:
            self._online = False
            self.mark_unavailable()
            raise DeviceUnavailable(f"WiZ bulb {self.host} did not answer within {COMMAND_BUDGET_S:.0f}s") from exc

    async def _call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if entity is self.light or (self.light is not None and entity.entity_id == self.light.entity_id):
            if service == "toggle":
                if not self._online:
                    self.pilot = await self._request("getPilot")
                service = "turn_off" if self.pilot.get("state") else "turn_on"
            if service == "turn_on":
                if not self._identified and any(data.get(k) is not None for k in ("rgb_color", "hs_color", "effect")):
                    await self._identify()  # colour and scenes depend on what the bulb is
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
        except WizTransportError:
            raise  # nothing was sent: the bulb's state stands
        except DeviceUnavailable:
            self._online = False
            self.mark_unavailable()
            raise
        was_online = self._online
        self._came_online()
        if not was_online:
            self.log.info("WiZ %s (%s) is back (it answered a command)", self.entry.title, self.host)
        # Read back what the bulb actually did (it clamps and may ignore keys).
        try:
            self.pilot = await asyncio.wait_for(self._request("getPilot"), READBACK_TIMEOUT_S)
        except (DeviceUnavailable, TimeoutError):
            self.pilot = {**self.pilot, **{k: v for k, v in params.items() if k != "sceneId"},
                          "sceneId": params.get("sceneId", 0)}
        self._publish_light()
        if self.speed is not None and self.pilot.get("speed") is not None:
            self.publish(self.speed, self.pilot["speed"], {"min": 10, "max": 200, "step": 1, "mode": "slider"})
        if not was_online and not self._identified:
            # It just came back: learn its type off the command's path (the next poll would too).
            self._spawn_identify()

    def _spawn_identify(self) -> None:
        task = asyncio.get_running_loop().create_task(self._identify_quietly())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _identify_quietly(self) -> None:
        with contextlib.suppress(Exception):
            await self._identify()
