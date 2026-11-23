"""Tasmota devices over their HTTP command API.

HA found Tasmota devices through MQTT discovery, and the broker was an HA
add-on, so it went with HA. Every Tasmota firmware also answers
``http://<ip>/cm?cmnd=<command>`` with the same JSON it would publish, which
needs nothing but the device's address: ``Status 0`` reads everything in one
request, ``Power<n> On|Off|Toggle`` drives a relay.

The address is not in the HA config entry (it only holds the discovery
prefix). It comes from, in order: ``entry.options["host"]``, ``entry.data["host"]``,
``entry.data["hosts"][<MAC>]`` (several devices on one entry), or the device
registry entry's ``configuration_url`` (HA stored ``http://<ip>/`` there for an
imported plug). Those are only where to reconnect (allowed to come from the import).

What the device is comes from its ``Status 0`` reply on every poll: firmware,
hardware, MAC and web UI are confirmed onto the device (``device_fields``,
shared with the flow that adds one), and the energy and status sensors publish
the class, unit and state class Tasmota's keys have (``ENERGY_META``).

Entities, by unique_id (HA's tasmota integration, ``{MAC}`` upper-case, no colons):
- ``{MAC}_switch_relay_{n}``                       relay n (Power{n+1})
- ``{MAC}_sensor_sensor_ENERGY_{Key}``              StatusSNS.ENERGY.Key
- ``{MAC}_sensor_sensor_ENERGY_{Key}_{i}``          channel i of a multi-channel meter's Key (a list)

A multi-channel meter reports some keys as one value per channel. Each channel
has its own sensor (``_{i}``); the key's own sensor is the sum where a sum
means something (power, current, energy: ``ADDITIVE_ENERGY``) and never for
voltage, power factor or frequency, whose own sensor shows the value only when
every channel reads the same (and each channel in its ``channels`` attribute).
- ``{MAC}_status_sensor_status_sensor_{name}``      status sensors (uptime, Wi-Fi, ...)
- ``{MAC}_light_light_{n}``                         the light on Power{n+1} (a bulb's is ``_light_light_0``)

A light is read from the keys Tasmota's ``StatusSTS`` has for it: ``Dimmer``
(0-100), ``CT`` (mireds 153-500), ``HSBColor`` (``h,s,b``), ``White`` and
``Channel`` (one percentage per PWM channel). The number of channels is the
light type, as in HA's tasmota integration: 1 dimmable, 2 cold/warm white,
3 RGB, 4 RGBW, 5 RGB with cold/warm white. Commands are ``Dimmer``, ``CT``,
``HsbColor1``/``HsbColor2`` and ``White``; each reply carries the light's new
state.

A device with a web password answers ``/cm`` with HTTP 401 (or, on older
firmware, ``{"WARNING": "Need user=..."}``); that is ``AuthRequired``, so the
entry shows it needs its web password rather than that it is unreachable. The
password goes as Tasmota's own ``user``/``password`` arguments (user ``admin``),
the only way its ``/cm`` takes it; no log or error text carries the request URL.
"""
from __future__ import annotations

import colorsys
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from services.homehub.core import (
    AuthRequired,
    DeviceUnavailable,
    Driver,
    EntityEntry,
    ServiceNotFound,
    SetupError,
    utcnow,
)

HTTP_TIMEOUT = 5.0

_RELAY_RE = re.compile(r"^([0-9A-Fa-f]{12})_switch_relay_(\d+)$")
_ENERGY_RE = re.compile(r"^([0-9A-Fa-f]{12})_sensor_sensor_ENERGY_(\w+)$")
_STATUS_RE = re.compile(r"^([0-9A-Fa-f]{12})_status_sensor_status_sensor_(\w+)$")
_LIGHT_RE = re.compile(r"^([0-9A-Fa-f]{12})_light_light_(\d+)$")

#: Tasmota's colour temperature range in mireds (CT 153..500), as HA's tasmota integration has it.
CT_MIN, CT_MAX = 153, 500
#: The StatusSTS keys only a light has.
LIGHT_KEYS = ("Dimmer", "Color", "HSBColor", "CT", "White", "Channel")

# HA's units and classes for the ENERGY keys Tasmota reports.
ENERGY_META: dict[str, dict[str, Any]] = {
    "Total": {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    "Today": {"unit_of_measurement": "kWh", "device_class": "energy", "state_class": "total_increasing"},
    "Yesterday": {"unit_of_measurement": "kWh", "device_class": "energy"},
    "Power": {"unit_of_measurement": "W", "device_class": "power", "state_class": "measurement"},
    "ApparentPower": {"unit_of_measurement": "VA", "device_class": "apparent_power",
                      "state_class": "measurement"},
    "ReactivePower": {"unit_of_measurement": "var", "device_class": "reactive_power",
                      "state_class": "measurement"},
    "Factor": {"device_class": "power_factor", "state_class": "measurement"},
    "Voltage": {"unit_of_measurement": "V", "device_class": "voltage", "state_class": "measurement"},
    "Current": {"unit_of_measurement": "A", "device_class": "current", "state_class": "measurement"},
    "TotalStartTime": {"device_class": "timestamp"},
}
#: Keys whose per-channel values add up to the device's (power, current, energy). Voltage, power factor and
#: frequency do not: two 230 V channels are not 460 V.
ADDITIVE_ENERGY = frozenset({
    "Power", "ActivePower", "ApparentPower", "ReactivePower", "Current", "Total", "Today", "Yesterday", "Period",
    "ImportActive", "ExportActive", "ImportReactive", "ExportReactive", "TotalTariff", "ExportTariff",
})
_CHANNEL_KEY = re.compile(r"^(\w+?)_(\d+)$")


def energy_value(energy: dict[str, Any], key: str) -> tuple[Any, dict[str, Any]]:
    """(state value, extra attributes) of ``ENERGY.key``: a channel (``Power_1``), a sum, or a plain value."""
    val = energy.get(key)
    if val is None and (m := _CHANNEL_KEY.match(key)) and isinstance(energy.get(m.group(1)), list):
        channels, idx = energy[m.group(1)], int(m.group(2))
        return (channels[idx] if idx < len(channels) else None), {}
    if not isinstance(val, list):
        return val, {}
    try:
        numbers = [float(x) for x in val]
    except (TypeError, ValueError):
        return None, {"channels": val}
    if key in ADDITIVE_ENERGY:
        return round(sum(numbers), 3), {"channels": val}
    return (val[0] if numbers and len(set(numbers)) == 1 else None), {"channels": val}


def device_fields(status: dict[str, Any], host: str | None) -> dict[str, Any]:
    """The DeviceEntry fields a ``Status 0`` reply gives."""
    from services.homehub.discovery import norm_mac

    fwr = status.get("StatusFWR") or {}
    mac = norm_mac((status.get("StatusNET") or {}).get("Mac"))
    return {"manufacturer": "Tasmota", "model": fwr.get("Hardware") or None, "sw_version": fwr.get("Version") or None,
            "connections": [["mac", ":".join(mac[i:i + 2] for i in range(0, 12, 2))]] if mac else [],
            "configuration_url": f"http://{host}/" if host else None}


def _status_value(name: str, st: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """One of HA's tasmota status sensors from a ``Status 0`` reply."""
    sts = st.get("StatusSTS") or {}
    wifi = sts.get("Wifi") or {}
    if name == "last_restart_time":
        up = sts.get("UptimeSec")
        if up is None:
            return "unknown", {"device_class": "timestamp"}
        started = (utcnow() - timedelta(seconds=int(up))).replace(microsecond=0)
        return started.isoformat(), {"device_class": "timestamp"}
    if name == "status_link_count":
        return wifi.get("LinkCount", "unknown"), {"state_class": "total"}
    if name == "status_mqtt_count":
        return sts.get("MqttCount", "unknown"), {"state_class": "total"}
    if name == "status_restart_reason":
        return (st.get("StatusPRM") or {}).get("RestartReason", "unknown"), {}
    if name == "status_ssid":
        return wifi.get("SSId", "unknown"), {}
    if name == "status_signal":
        return wifi.get("Signal", "unknown"), {"unit_of_measurement": "dBm", "device_class": "signal_strength"}
    if name == "status_rssi":
        return wifi.get("RSSI", "unknown"), {"unit_of_measurement": "%"}
    if name == "status_ip":
        return (st.get("StatusNET") or {}).get("IPAddress", "unknown"), {}
    return "unknown", {}


def _parse_power(d: dict[str, Any]) -> dict[int, str]:
    """Relay index -> "ON"/"OFF" from a reply or StatusSTS (``POWER`` alone on one-relay devices)."""
    out: dict[int, str] = {}
    for k, v in d.items():
        if k == "POWER":
            out[0] = str(v)
        elif k.startswith("POWER") and k[5:].isdigit():
            out[int(k[5:]) - 1] = str(v)
    return out


def cm_params(command: str, password: str | None) -> dict[str, str]:
    """The query of a ``/cm`` request. Tasmota takes its web password only as ``user``/``password``
    arguments (its documented ``/cm?user=admin&password=...&cmnd=...``); the user is always ``admin``."""
    params = {"cmnd": command}
    if password:
        params = {"user": "admin", "password": password, **params}
    return params


def basic_auth(password: str | None) -> Any:
    """HTTP Basic credentials for the web password (user ``admin``), or None.

    Stock Tasmota reads the password from ``/cm``'s ``user``/``password`` arguments, but some
    builds and Tasmota-compatible firmwares put every page, ``/cm`` included, behind HTTP Basic
    login and close the connection on a ``/cm`` without it. Sending both works for either.
    """
    return aiohttp.BasicAuth("admin", password) if password else None


async def asks_for_login(session: Any, host: str) -> bool:
    """Whether the device's web page wants a login (401 with a Basic challenge).

    Used after ``/cm`` closed the connection without a reply: a password-protected
    device does that on some firmwares, where an unreachable one never answers at all.
    """
    try:
        async with session.get(f"http://{host}/") as resp:
            return resp.status == 401 and "basic" in resp.headers.get("WWW-Authenticate", "").lower()
    except (aiohttp.ClientError, TimeoutError):
        return False


def safe_error(exc: BaseException, password: str | None) -> str:
    """An error's text for logs and the entry's reason, without the password (a URL in it carries the query)."""
    text = str(exc) or type(exc).__name__
    if (password and password in text) or "password=" in text:
        return type(exc).__name__
    return text


def needs_password(status: int, body: Any) -> bool:
    """Whether a ``/cm`` reply is Tasmota asking for its web password (401, or the older WARNING reply)."""
    if status == 401:
        return True
    return isinstance(body, dict) and "need user=" in str(body.get("WARNING") or "").lower()


# -- lights (pure; the tests feed these the StatusSTS shapes real bulbs send) -----------------------

def has_light(sts: dict[str, Any]) -> bool:
    return any(k in sts for k in ("Dimmer", "Color", "HSBColor", "CT"))


def light_channels(sts: dict[str, Any]) -> int:
    """How many PWM channels the light has (its type): from ``Channel``, else the ``Color`` it reports."""
    ch = sts.get("Channel")
    if isinstance(ch, list) and ch:
        return len(ch)
    color = sts.get("Color")
    if isinstance(color, str) and color:
        return len(color.split(",")) if "," in color else len(color) // 2
    return 1 if "Dimmer" in sts else 0


def light_index(sts: dict[str, Any]) -> int:
    """The relay (0-based) the light is on: Tasmota numbers a light after the device's relays."""
    powers = _parse_power(sts)
    return max(powers) if powers else 0


def color_modes(sts: dict[str, Any]) -> list[str]:
    """HA's ``supported_color_modes`` for the light, from its channels (HA's tasmota integration's rules)."""
    n = light_channels(sts)
    modes: set[str] = set()
    if n >= 3 or "HSBColor" in sts:
        modes.add("hs")
    if n in (2, 5) or ("CT" in sts and n not in (3, 4)):
        modes.add("color_temp")
    if n == 4:
        modes.add("white")
    if not modes and ("Dimmer" in sts or n == 1):
        modes.add("brightness")
    return sorted(modes) or ["onoff"]


def _hsb(sts: dict[str, Any]) -> tuple[float, float, float] | None:
    try:
        h, s, b = (float(x) for x in str(sts.get("HSBColor")).split(","))
    except (TypeError, ValueError):
        return None
    return h, s, b


def light_attributes(sts: dict[str, Any], on: bool) -> dict[str, Any]:
    """The light's HA attributes from StatusSTS (or a command's reply, which has the same keys)."""
    modes = color_modes(sts)
    attrs: dict[str, Any] = {"supported_color_modes": modes, "color_mode": None, "brightness": None,
                             "hs_color": None, "rgb_color": None, "color_temp_kelvin": None, "color_temp": None}
    if "color_temp" in modes:
        attrs.update({"min_mireds": CT_MIN, "max_mireds": CT_MAX,
                      "min_color_temp_kelvin": round(1_000_000 / CT_MAX),
                      "max_color_temp_kelvin": round(1_000_000 / CT_MIN)})
    if not on:
        return attrs
    dimmer = sts.get("Dimmer")
    if isinstance(dimmer, (int, float)) and modes != ["onoff"]:
        attrs["brightness"] = round(max(0, min(100, float(dimmer))) * 255 / 100)
    channels = [c for c in sts.get("Channel") or [] if isinstance(c, (int, float))]
    rgb_dark = len(channels) >= 4 and not any(channels[:3])  # only the white channels are lit
    if "hs" in modes and not rgb_dark:
        attrs["color_mode"] = "hs"
    elif "white" in modes:
        attrs["color_mode"] = "white"
    elif "color_temp" in modes:
        attrs["color_mode"] = "color_temp"
    else:
        attrs["color_mode"] = modes[0]
    hsb = _hsb(sts)
    if hsb is not None and "hs" in modes:
        h, s, _b = hsb
        attrs["hs_color"] = [round(h, 3), round(s, 3)]
        r, g, b = colorsys.hsv_to_rgb(h / 360, s / 100, 1.0)
        attrs["rgb_color"] = [round(r * 255), round(g * 255), round(b * 255)]
    ct = sts.get("CT")
    if isinstance(ct, (int, float)) and ct > 0 and "color_temp" in modes:
        attrs["color_temp"] = int(ct)
        attrs["color_temp_kelvin"] = round(1_000_000 / float(ct))
    return attrs


def light_commands(data: dict[str, Any], modes: list[str]) -> list[str] | None:
    """What a ``light.turn_on`` asks for, colour first and brightness last; [] is a plain on, None an off
    (a brightness of 0)."""
    cmds: list[str] = []
    hs = data.get("hs_color")
    if hs is None and data.get("rgb_color") is not None:
        h, s, _v = colorsys.rgb_to_hsv(*(float(c) / 255 for c in list(data["rgb_color"])[:3]))
        hs = (h * 360, s * 100)
    mireds = data.get("color_temp")
    kelvin = data.get("color_temp_kelvin") or data.get("kelvin")
    if kelvin:
        mireds = 1_000_000 / float(kelvin)
    if hs is not None and "hs" in modes:
        cmds += [f"HsbColor1 {round(float(hs[0])) % 360}", f"HsbColor2 {max(0, min(100, round(float(hs[1]))))}"]
    elif mireds is not None and "color_temp" in modes:
        cmds.append(f"CT {max(CT_MIN, min(CT_MAX, round(float(mireds))))}")
    if data.get("white") is not None and "white" in modes:
        cmds.append(f"White {max(1, min(100, round(float(data['white']) * 100 / 255)))}")
    pct: float | None = None
    if data.get("brightness") is not None:
        pct = float(data["brightness"]) * 100 / 255
    elif data.get("brightness_pct") is not None:
        pct = float(data["brightness_pct"])
    if pct is not None:
        if pct <= 0:
            return None
        if modes != ["onoff"]:
            cmds.append(f"Dimmer {max(1, min(100, round(pct)))}")
    return cmds


class TasmotaDriver(Driver):
    platform = "tasmota"
    poll_interval = 30.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self._session: aiohttp.ClientSession | None = None
        self._online = False
        self.status: dict[str, Any] = {}
        self.power: dict[int, str] = {}
        self.relays: dict[str, int] = {}       # entity_id -> relay index (0-based)
        self.lights: dict[str, int] = {}       # entity_id -> the relay index the light is on
        self.energy: dict[str, str] = {}       # entity_id -> ENERGY key
        self.status_sensors: dict[str, str] = {}
        self.mac = ""
        for e in self.entities:
            uid = e.unique_id or ""
            if m := _RELAY_RE.match(uid):
                self.relays[e.entity_id] = int(m.group(2))
            elif m := _LIGHT_RE.match(uid):
                self.lights[e.entity_id] = int(m.group(2))
            elif m := _ENERGY_RE.match(uid):
                self.energy[e.entity_id] = m.group(2)
            elif m := _STATUS_RE.match(uid):
                self.status_sensors[e.entity_id] = m.group(2)
            else:
                continue
            self.mac = self.mac or m.group(1).upper()
        self.host = self._find_host()

    def _find_host(self) -> str:
        for src in (self.entry.options, self.entry.data):
            if src.get("host"):
                return str(src["host"])
            hosts = src.get("hosts")
            if isinstance(hosts, dict) and self.mac:
                for k, v in hosts.items():
                    if k.replace(":", "").upper() == self.mac:
                        return str(v)
        for e in self.entities:
            dev = self.hub.devices.get(e.device_id or "")
            url = dev.configuration_url if dev else None
            parts = urlparse(str(url)) if url else None
            if parts is not None and parts.hostname:
                return parts.hostname + (f":{parts.port}" if parts.port else "")
        return ""

    def _auth_failure(self) -> AuthRequired:
        if self.entry.data.get("password"):
            return AuthRequired(f"Tasmota {self.host} did not accept its web password")
        return AuthRequired(f"Tasmota {self.host} needs its web password")

    # -- transport (tests replace this) ---------------------------------------
    async def _command(self, command: str) -> dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT))
        pw = str(self.entry.data.get("password") or "")
        try:
            async with self._session.get(f"http://{self.host}/cm", params=cm_params(command, pw),
                                         auth=basic_auth(pw)) as resp:
                if resp.status == 401:
                    raise self._auth_failure()
                if resp.status != 200:
                    raise DeviceUnavailable(f"Tasmota {self.host} answered HTTP {resp.status} to {command!r}")
                body = await resp.json(content_type=None)
        except aiohttp.ServerDisconnectedError as exc:
            if await asks_for_login(self._session, self.host):
                raise self._auth_failure() from None
            raise DeviceUnavailable(f"Tasmota {self.host} unreachable: {safe_error(exc, pw)}") from None
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise DeviceUnavailable(f"Tasmota {self.host} unreachable: {safe_error(exc, pw)}") from None
        if needs_password(200, body):
            raise self._auth_failure()
        if not isinstance(body, dict):
            raise DeviceUnavailable(f"Tasmota {self.host} sent an unexpected reply to {command!r}")
        return body

    # -- lifecycle ------------------------------------------------------------
    async def async_setup(self) -> None:
        if not self.entities:
            return
        if not self.host:
            raise SetupError(
                f"Tasmota {self.mac or self.entry.title}: no address; set entry data or options 'host'")
        self.status = await self._command("Status 0")
        self._online = True
        self._publish_all()

    async def async_poll(self) -> None:
        if not self.entities or not self.host:
            return
        try:
            self.status = await self._command("Status 0")
        except DeviceUnavailable as exc:
            if self._online:
                self.log.warning("Tasmota %s (%s) unreachable: %s", self.mac, self.host, exc)
            self._online = False
            self.mark_unavailable()
            if isinstance(exc, AuthRequired):
                # The password was changed on the device: the runtime parks the entry until it is entered.
                raise
            return
        if not self._online:
            self.log.info("Tasmota %s (%s) is back", self.mac, self.host)
        self._online = True
        self._publish_all()

    async def async_unload(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # -- states ---------------------------------------------------------------
    def _tasmota_time(self, value: Any) -> Any:
        """Tasmota timestamps are naive local times; HA reports them as ISO with the offset."""
        if not isinstance(value, str):
            return "unknown" if value is None else value
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
        if dt.tzinfo is None:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(self.hub.time_zone))
            except (ZoneInfoNotFoundError, ValueError):
                return value
        return dt.isoformat()

    @property
    def _sts(self) -> dict[str, Any]:
        sts = self.status.get("StatusSTS")
        if not isinstance(sts, dict):
            sts = {}
            self.status["StatusSTS"] = sts
        return sts

    def _publish_relays(self) -> None:
        for eid, idx in self.relays.items():
            val = self.power.get(idx)
            state = "unknown" if val is None else ("on" if val.upper() == "ON" else "off")
            self.publish(eid, state, {})

    def _publish_lights(self) -> None:
        for eid, idx in self.lights.items():
            val = self.power.get(idx)
            if val is None:
                self.publish(eid, "unknown", light_attributes(self._sts, False))
                continue
            on = val.upper() == "ON"
            self.publish(eid, "on" if on else "off", light_attributes(self._sts, on))

    def _publish_all(self) -> None:
        fields = device_fields(self.status, self.host)
        for dev_id in {e.device_id for e in self.entities if e.device_id}:
            self.confirm_device(dev_id, **fields)
        self.power = _parse_power(self._sts)
        self._publish_relays()
        self._publish_lights()
        energy = (self.status.get("StatusSNS") or {}).get("ENERGY") or {}
        for eid, key in self.energy.items():
            val, extra = energy_value(energy, key)
            if key == "TotalStartTime":
                val = self._tasmota_time(val)
            base = m.group(1) if (m := _CHANNEL_KEY.match(key)) and key not in ENERGY_META else key
            self.publish(eid, "unknown" if val is None else val, {**ENERGY_META.get(base, {}), **extra})
        for eid, name in self.status_sensors.items():
            val, attrs = _status_value(name, self.status)
            self.publish(eid, val, attrs)

    # -- services -------------------------------------------------------------
    async def _send(self, command: str) -> dict[str, Any]:
        try:
            reply = await self._command(command)
        except DeviceUnavailable:
            self._online = False
            self.mark_unavailable()
            raise
        self._online = True
        # A reply carries the new state: {"POWER2": "ON"} (or "POWER" on one-relay plugs), and a light's
        # command also its Dimmer, Color, HSBColor, CT and Channel.
        self.power.update(_parse_power(reply))
        sts = self._sts
        for k, v in reply.items():
            if k in LIGHT_KEYS or k == "POWER" or (k.startswith("POWER") and k[5:].isdigit()):
                sts[k] = v
        return reply

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if entity.entity_id in self.lights:
            await self._light_call(entity, service, data)
            return None
        idx = self.relays.get(entity.entity_id)
        if idx is None or service not in ("turn_on", "turn_off", "toggle"):
            raise ServiceNotFound(f"tasmota cannot run {domain}.{service} on {entity.entity_id}")
        verb = {"turn_on": "On", "turn_off": "Off", "toggle": "Toggle"}[service]
        await self._send(f"Power{idx + 1} {verb}")
        self._publish_relays()
        return None

    async def _light_call(self, entity: EntityEntry, service: str, data: dict[str, Any]) -> None:
        idx = self.lights[entity.entity_id]
        power = f"Power{idx + 1}"
        if service not in ("turn_on", "turn_off", "toggle"):
            raise ServiceNotFound(f"tasmota cannot run light.{service} on {entity.entity_id}")
        cmds = light_commands(data, color_modes(self._sts)) if service == "turn_on" else None
        if service == "turn_off" or (service == "turn_on" and cmds is None):
            await self._send(f"{power} Off")
        elif service == "toggle":
            await self._send(f"{power} Toggle")
        else:
            for cmd in cmds or []:
                await self._send(cmd)
            if str(self.power.get(idx, "")).upper() != "ON":
                await self._send(f"{power} On")  # a plain on, or a device set not to power on with a change
        self._publish_lights()
