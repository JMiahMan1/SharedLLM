"""A made-up Tasmota device for the tests: ``Status 0`` and the commands the hub sends, over real HTTP.

Kinds: ``plug`` (two relays and an energy meter), ``ct`` (a dimmable cold/warm white bulb), ``rgb`` (an RGB
bulb), ``rgbcw`` (RGB with cold/warm white). With ``password`` set it answers as Tasmota does without the
right ``user``/``password`` arguments: HTTP 401 with the ``Need user=`` warning and its ``Server: Tasmota/...`` header. Every
name, address and MAC is made up.
"""
from __future__ import annotations

import colorsys
from typing import Any

from aiohttp import web

SERVER = "Tasmota/15.6.0 (ESP8266EX)"
CHANNELS = {"ct": 2, "rgb": 3, "rgbw": 4, "rgbcw": 5}


class FakeTasmota:
    def __init__(self, kind: str = "plug", *, mac: str = "02:00:00:00:0a:01", name: str = "Shelf Bulb",
                 password: str | None = None, ip: str = "192.0.2.51") -> None:
        self.kind, self.mac, self.name, self.password, self.ip = kind, mac, name, password, ip
        self.commands: list[str] = []
        self.auth_seen: list[str | None] = []
        if kind == "plug":
            self.sts: dict[str, Any] = {"POWER1": "ON", "POWER2": "OFF"}
        else:
            n = CHANNELS[kind]
            self.sts = {"POWER": "ON", "Dimmer": 60, "Fade": "OFF", "Speed": 1, "LedTable": "ON"}
            if kind in ("ct", "rgbcw"):
                self.sts["CT"] = 300
            if n >= 3:
                self.sts["HSBColor"] = "120,100,60"
            if n == 4:
                self.sts["White"] = 0
            self.sts["Channel"] = [0] * n
            self.white = kind == "ct"
            self._sync_channels()
        self.sts.update({"UptimeSec": 3600, "MqttCount": 0, "Wifi": {"SSId": "lan", "LinkCount": 1, "RSSI": 70,
                                                                       "Signal": -65}})
        self.runner: web.AppRunner | None = None
        self.port = 0

    # -- the device's own state ----------------------------------------------------------------
    def _sync_channels(self) -> None:
        """Channel (percentages) and Color from Dimmer/HSBColor/CT, as the firmware keeps them in step."""
        n = len(self.sts["Channel"])
        d = int(self.sts["Dimmer"])
        if n == 4 and self.white:
            chans = [0, 0, 0, d]
        elif n == 2 or (n == 5 and self.white):
            ct = int(self.sts.get("CT", 300))
            warm = round(d * (ct - 153) / (500 - 153))
            chans = [0, 0, 0, d - warm, warm] if n == 5 else [d - warm, warm]
        elif n >= 3:
            h, s, _b = (float(x) for x in self.sts["HSBColor"].split(","))
            r, g, b = colorsys.hsv_to_rgb(h / 360, s / 100, d / 100)
            chans = [round(r * 100), round(g * 100), round(b * 100)] + [0] * (n - 3)
        else:
            chans = [d]
        self.sts["Channel"] = chans
        self.sts["Color"] = "".join(f"{round(c * 255 / 100):02X}" for c in chans)

    def status0(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "Status": {"Module": 0, "DeviceName": self.name, "FriendlyName": [self.name], "Topic": "tasmota_0A01"},
            "StatusPRM": {"RestartReason": "Power On"},
            "StatusFWR": {"Version": "15.6.0(release-tasmota)", "Hardware": "ESP8266EX"},
            "StatusNET": {"Hostname": self.name, "IPAddress": self.ip, "Mac": self.mac.upper()},
            "StatusSTS": dict(self.sts),
            "StatusSNS": {"Time": "2026-11-11T10:00:00"},
        }
        if self.kind == "plug":
            out["StatusSNS"]["ENERGY"] = {"Total": 12.5, "Today": 0.3, "Power": 40, "Voltage": 120, "Current": 0.33}
        return out

    def _power_key(self) -> str:
        return "POWER" if "POWER" in self.sts else "POWER1"

    def _light_reply(self) -> dict[str, Any]:
        keys = ("POWER", "Dimmer", "Color", "HSBColor", "White", "CT", "Channel")
        return {k: self.sts[k] for k in keys if k in self.sts}

    def run(self, command: str) -> dict[str, Any]:
        self.commands.append(command)
        if command == "Status 0":
            return self.status0()
        name, _, arg = command.partition(" ")
        low = name.lower()
        if low.startswith("power"):
            key = "POWER" if low == "power1" and "POWER" in self.sts else f"POWER{low[5:] or 1}"
            cur = self.sts.get(key, "OFF")
            new = {"on": "ON", "off": "OFF", "toggle": "OFF" if cur == "ON" else "ON"}[arg.lower()]
            self.sts[key] = new
            return {key: new}
        self.sts[self._power_key()] = "ON"  # a light change turns it on
        if low == "dimmer":
            self.sts["Dimmer"] = int(arg)
            h, s, _b = (self.sts.get("HSBColor") or "0,0,0").split(",")
            if "HSBColor" in self.sts:
                self.sts["HSBColor"] = f"{h},{s},{arg}"
            self._sync_channels()
        elif low == "ct":
            self.sts["CT"] = int(arg)
            self.white = True
            self._sync_channels()
        elif low in ("hsbcolor1", "hsbcolor2"):
            h, s, b = self.sts["HSBColor"].split(",")
            h, s = (arg, s) if low == "hsbcolor1" else (h, arg)
            self.sts["HSBColor"] = f"{h},{s},{b}"
            self.white = False
            self._sync_channels()
        elif low == "white":
            self.sts["White"] = int(arg)
            self.sts["Dimmer"] = int(arg)
            self.white = True
            self._sync_channels()
        else:
            return {"Command": "Unknown"}
        return self._light_reply()

    # -- HTTP ------------------------------------------------------------------------------------
    def _authorised(self, request: web.Request) -> bool:
        """Tasmota's ``/cm`` takes its password only as the ``user`` and ``password`` arguments."""
        if not self.password:
            return True
        self.auth_seen.append(request.query.get("password"))
        return request.query.get("user") == "admin" and request.query.get("password") == self.password

    async def _cm(self, request: web.Request) -> web.Response:
        if not self._authorised(request):
            return web.json_response({"WARNING": "Need user=<username>&password=<password>"}, status=401,
                                     headers={"Server": SERVER})
        return web.json_response(self.run(request.query.get("cmnd", "")), headers={"Server": SERVER})

    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/cm", self._cm)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        return f"127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
