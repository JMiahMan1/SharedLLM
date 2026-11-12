"""Tasmota devices over their HTTP command API.

HA found Tasmota devices through MQTT discovery, and the broker was an HA
add-on, so it went with HA. Every Tasmota firmware also answers
``http://<ip>/cm?cmnd=<command>`` with the same JSON it would publish, which
needs nothing but the device's address: ``Status 0`` reads everything in one
request, ``Power<n> On|Off|Toggle`` drives a relay.

The address is not in the HA config entry (it only holds the discovery
prefix). It comes from, in order: ``entry.options["host"]``, ``entry.data["host"]``,
``entry.data["hosts"][<MAC>]`` (several devices on one entry), or the device
registry entry's ``configuration_url`` (HA stored ``http://<ip>/`` there for the
Loft-Plug). Those are only where to reconnect (allowed to come from the import).

What the device is comes from its ``Status 0`` reply on every poll: firmware,
hardware, MAC and web UI are confirmed onto the device (``device_fields``,
shared with the flow that adds one), and the energy and status sensors publish
the class, unit and state class Tasmota's keys have (``ENERGY_META``).

Entities, by unique_id (HA's tasmota integration, ``{MAC}`` upper-case, no colons):
- ``{MAC}_switch_relay_{n}``                       relay n (Power{n+1})
- ``{MAC}_sensor_sensor_ENERGY_{Key}``              StatusSNS.ENERGY.Key
- ``{MAC}_status_sensor_status_sensor_{name}``      status sensors (uptime, Wi-Fi, ...)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, ServiceNotFound, utcnow

HTTP_TIMEOUT = 5.0

_RELAY_RE = re.compile(r"^([0-9A-Fa-f]{12})_switch_relay_(\d+)$")
_ENERGY_RE = re.compile(r"^([0-9A-Fa-f]{12})_sensor_sensor_ENERGY_(\w+)$")
_STATUS_RE = re.compile(r"^([0-9A-Fa-f]{12})_status_sensor_status_sensor_(\w+)$")

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
        self.energy: dict[str, str] = {}       # entity_id -> ENERGY key
        self.status_sensors: dict[str, str] = {}
        self.mac = ""
        for e in self.entities:
            uid = e.unique_id or ""
            if m := _RELAY_RE.match(uid):
                self.relays[e.entity_id] = int(m.group(2))
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
            if url:
                host = urlparse(str(url)).hostname
                if host:
                    return host
        return ""

    # -- transport (tests replace this) ---------------------------------------
    async def _command(self, command: str) -> dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT))
        url = f"http://{self.host}/cm?cmnd={quote(command)}"
        auth = None
        user, pw = self.entry.data.get("username"), self.entry.data.get("password")
        if pw:
            auth = aiohttp.BasicAuth(str(user or "admin"), str(pw))
        try:
            async with self._session.get(url, auth=auth) as resp:
                if resp.status != 200:
                    raise DeviceUnavailable(f"Tasmota {self.host} answered HTTP {resp.status} to {command!r}")
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise DeviceUnavailable(f"Tasmota {self.host} unreachable: {exc or type(exc).__name__}") from exc
        if not isinstance(body, dict):
            raise DeviceUnavailable(f"Tasmota {self.host} sent an unexpected reply to {command!r}")
        return body

    # -- lifecycle ------------------------------------------------------------
    async def async_setup(self) -> None:
        if not self.entities:
            return
        if not self.host:
            raise DeviceUnavailable(
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

    def _publish_relays(self) -> None:
        for eid, idx in self.relays.items():
            val = self.power.get(idx)
            state = "unknown" if val is None else ("on" if val.upper() == "ON" else "off")
            self.publish(eid, state, {})

    def _publish_all(self) -> None:
        fields = device_fields(self.status, self.host)
        for dev_id in {e.device_id for e in self.entities if e.device_id}:
            self.confirm_device(dev_id, **fields)
        self.power = _parse_power(self.status.get("StatusSTS") or {})
        self._publish_relays()
        energy = (self.status.get("StatusSNS") or {}).get("ENERGY") or {}
        for eid, key in self.energy.items():
            val = energy.get(key)
            if key == "TotalStartTime":
                val = self._tasmota_time(val)
            elif isinstance(val, list):  # per-phase arrays on multi-channel meters: report the sum
                val = round(sum(float(x) for x in val), 3)
            self.publish(eid, "unknown" if val is None else val, dict(ENERGY_META.get(key, {})))
        for eid, name in self.status_sensors.items():
            val, attrs = _status_value(name, self.status)
            self.publish(eid, val, attrs)

    # -- services -------------------------------------------------------------
    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        idx = self.relays.get(entity.entity_id)
        if idx is None or service not in ("turn_on", "turn_off", "toggle"):
            raise ServiceNotFound(f"tasmota cannot run {domain}.{service} on {entity.entity_id}")
        verb = {"turn_on": "On", "turn_off": "Off", "toggle": "Toggle"}[service]
        try:
            reply = await self._command(f"Power{idx + 1} {verb}")
        except DeviceUnavailable:
            self._online = False
            self.mark_unavailable()
            raise
        self._online = True
        # The reply carries the relay's new state: {"POWER2": "ON"} (or "POWER" on one-relay plugs).
        self.power.update(_parse_power(reply))
        self._publish_relays()
        return None

