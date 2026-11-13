"""New-device discovery: find devices on the LAN that are not set up yet.

This replaces Home Assistant's "Discovered" panel. Three sources feed one list
of findings:

- **mDNS**, continuously (python-zeroconf; the hub runs on the host network):
  ESPHome nodes, commissionable Matter devices, unpaired HomeKit accessories
  (``sf=1``), Google Cast, Android TV Remote, and Tasmota on ``_http._tcp``.
- **SSDP** M-SEARCH, on an interval and on demand: Roku, Samsung and LG TVs,
  DLNA renderers. Replies to a multicast M-SEARCH are unicast datagrams from
  the device, which a stateful firewall does not match to the outgoing
  multicast and drops, so the same search is also sent unicast to every
  address of the subnet; those replies are tracked and get through.
- **WiZ**, on an interval and on demand: ``getSystemConfig`` to UDP 38899, as a
  broadcast and as a unicast sweep of the subnet (same firewall reason).
- **DHCP/ARP**, with every active scan: the kernel's neighbour table
  (``/proc/net/arp``, ``HOMEHUB_ARP_TABLE``) gives each live address its MAC,
  and a reverse lookup its DHCP host name. Both are matched against the
  ``dhcp`` rules HA's integration manifests carry (``DHCP_MATCHERS``), which is
  how HA finds Reolink cameras and Roborock vacuums. The same table fills in the
  MAC of any finding that came without one, so a device set up by MAC is
  recognised whichever protocol found it.

A finding the registry knows whose address is not the one its entry uses has
**moved** (the router gave it a new lease). It is listed with ``moved``
(``entry_id``, ``old_host``, ``new_host``) and the API offers the update, as
HA's discovery updates an entry's host through its unique_id.

A finding has a stable id (``{integration}:{mac|uuid|serial}``), a name, a
model, an address, first/last seen, and whether the registry already knows it
(any config entry, device MAC, identifier, host or unique_id matching it).
Findings live in memory and in the store's kv (``discovery``); one not seen
for a day is dropped. Admins get one notice per new device (``once``-tagged,
so telemetry does not repeat it either).

Adding a finding is ``services/homehub/flows.py``; the API is
``services/homehub/discovery_api.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import socket
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID
from xml.etree import ElementTree as ET

from services.homehub.core import Hub

log = logging.getLogger("homehub.discovery")

KV_KEY = "discovery"
#: A finding not seen for this long is dropped.
EXPIRE_S = 24 * 3600
#: Active scans (SSDP and the WiZ sweep) run this often...
SCAN_INTERVAL_S = float(os.getenv("HOMEHUB_DISCOVERY_INTERVAL_S", "3600"))
#: ...and an on-demand scan at most this often; a sooner request gets the last results.
MIN_SCAN_GAP_S = 60.0
#: Housekeeping (expiry, saving, mDNS freshness) runs every this many seconds.
HOUSEKEEPING_S = 60.0
#: A device the hub follows by MAC that stopped answering gets a sweep this soon, not at the hourly scan.
FOLLOW_SCAN_S = 600.0
#: The first active scan waits this long after start, so drivers connect first.
STARTUP_DELAY_S = 20.0
#: New findings from mDNS are batched for this long before admins are told.
NOTIFY_DELAY_S = 15.0
#: More new devices than this in one batch become one summary notice.
NOTIFY_SUMMARY_OVER = 3
#: At most this many addresses are swept.
MAX_SWEEP_HOSTS = 1024

WIZ_PORT = 38899
SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_LISTEN_S = 4.0
WIZ_LISTEN_S = 3.0
#: Fixed source ports for the active scans (0 = any). A firewall rule can then
#: let the replies to the multicast M-SEARCH and the WiZ broadcast through.
SSDP_SOURCE_PORT = int(os.getenv("HOMEHUB_SSDP_PORT", "0"))
WIZ_SOURCE_PORT = int(os.getenv("HOMEHUB_WIZ_DISCOVERY_PORT", "0"))

SSDP_TARGETS = (
    "roku:ecp",
    "urn:samsung.com:device:RemoteControlReceiver:1",
    "urn:lge-com:service:webos-second-screen:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
)

MDNS_TYPES = (
    "_esphomelib._tcp.local.",
    "_matterc._udp.local.",
    "_hap._tcp.local.",
    "_googlecast._tcp.local.",
    "_androidtvremote2._tcp.local.",
    "_http._tcp.local.",
    "_hap._udp.local.",   # HomeKit over Thread
    "_mass._tcp.local.",  # Music Assistant servers
)

LABELS = {
    "esphome": "ESPHome", "wiz": "WiZ", "cast": "Google Cast", "roku": "Roku", "matter": "Matter",
    "homekit_controller": "HomeKit", "androidtv_remote": "Android TV", "samsungtv": "Samsung TV",
    "webostv": "LG webOS TV", "dlna_dmr": "DLNA renderer", "tasmota": "Tasmota", "reolink": "Reolink",
    "music_assistant": "Music Assistant", "roborock": "Roborock",
    # Bluetooth (services/homehub/bluetooth.py): BLE sensors by their parser, and iBeacon tags.
    "bthome": "BTHome", "xiaomi_ble": "Xiaomi BLE", "qingping": "Qingping", "sensorpush": "SensorPush",
    "thermopro": "ThermoPro", "inkbird": "INKBIRD", "ruuvitag_ble": "Ruuvi", "govee_ble": "Govee BLE",
    "ibeacon": "iBeacon",
}
#: Integrations a finding can be added for (services/homehub/flows.py).
ADOPTABLE = {"esphome", "wiz", "cast", "roku", "matter", "tasmota", "homekit_controller", "androidtv_remote",
             "samsungtv", "webostv", "dlna_dmr", "reolink", "music_assistant", "roborock", "bthome", "xiaomi_ble",
             "qingping", "sensorpush", "thermopro", "inkbird", "ruuvitag_ble", "govee_ble", "ibeacon"}
#: Integrations nobody is told about (too many, and nothing to do with them yet).
QUIET = {"dlna_dmr", "ibeacon"}  # iBeacons: every passing phone or tag; listed, never announced

_MAC12 = re.compile(r"^[0-9a-f]{12}$")
_SAFE_ID = re.compile(r"[^a-z0-9_.:-]+")


def now() -> float:
    return time.time()


def norm_mac(value: Any) -> str | None:
    """Any MAC spelling -> 12 lower-case hex digits, or None."""
    s = re.sub(r"[^0-9a-f]", "", str(value or "").lower())
    return s if _MAC12.match(s) else None


def token(value: Any) -> str:
    """An id reduced for matching: lower case, no ``uuid:`` prefix, letters and digits only."""
    s = str(value or "").strip().lower()
    s = s.removeprefix("uuid:")
    return re.sub(r"[^0-9a-z]", "", s)


def make_id(integration: str, key: str) -> str:
    return f"{integration}:{_SAFE_ID.sub('-', str(key).lower()).strip('-')}"


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts else None


# -- parsing (pure; the tests feed these the shapes real devices send) -----------

def _props(raw: dict[Any, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (raw or {}).items():
        key = k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)
        if v is None:
            out[key] = ""
        else:
            out[key] = v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
    return out


def parse_mdns(service_type: str, name: str, host: str | None, port: int | None,
               properties: dict[Any, Any] | None, server: str | None = None) -> dict[str, Any] | None:
    """One resolved mDNS service -> a finding, or None when it is not a device we add."""
    if not host:
        return None
    props = _props(properties)
    stype = service_type if service_type.endswith(".") else service_type + "."
    instance = name.removesuffix("." + stype).removesuffix(stype).rstrip(".")
    server_host = (server or "").rstrip(".").removesuffix(".local")

    if stype == "_esphomelib._tcp.local.":
        mac = norm_mac(props.get("mac"))
        encrypted = bool(props.get("api_encryption"))
        return {
            "id": make_id("esphome", mac or instance), "integration": "esphome",
            "name": props.get("friendly_name") or instance, "model": props.get("project_name") or props.get("board"),
            "manufacturer": "Espressif" if props.get("platform", "").upper().startswith("ESP") else None,
            "host": host, "port": port or 6053, "mac": mac, "ids": [x for x in (mac, instance) if x],
            "source": "mdns", "props": {"device_name": instance, "encrypted": encrypted,
                                        "version": props.get("version"), "platform": props.get("platform")},
        }
    if stype == "_matterc._udp.local.":
        vp = props.get("VP", "")
        vendor, _, product = vp.partition("+")
        mac = norm_mac(server_host)
        cm = props.get("CM", "0")
        key = mac or f"{vendor or 'x'}-{product or 'x'}-{props.get('D', instance)}"
        return {
            "id": make_id("matter", key), "integration": "matter",
            "name": props.get("DN") or (f"Matter device ({vendor}/{product})" if vp else "Matter device"),
            "model": product or None, "manufacturer": f"vendor {vendor}" if vendor else None,
            "host": host, "port": port, "mac": mac, "ids": [x for x in (mac,) if x], "source": "mdns",
            # CM=0: advertised but its pairing window is closed (often already on another fabric).
            "props": {"commissioning_open": cm in ("1", "2"), "discriminator": props.get("D"),
                      "vendor_id": vendor or None, "product_id": product or None},
        }
    if stype == "_mass._tcp.local.":
        server_id = props.get("server_id") or instance
        base_url = props.get("base_url") or f"http://{host}:{port or 8095}"
        return {
            "id": make_id("music_assistant", server_id), "integration": "music_assistant",
            "name": f"Music Assistant {props.get('server_version') or ''}".strip(), "model": None,
            "manufacturer": "Music Assistant", "host": host, "port": port or 8095, "mac": None,
            "ids": [x for x in (server_id,) if x], "source": "mdns",
            "props": {"server_id": server_id, "url": base_url, "version": props.get("server_version")},
        }
    if stype in ("_hap._tcp.local.", "_hap._udp.local."):
        if props.get("sf") != "1":
            return None  # paired already (with us or another controller): nothing to add
        pairing_id = props.get("id", "").lower()
        if not pairing_id:
            return None
        return {
            "id": make_id("homekit_controller", pairing_id), "integration": "homekit_controller",
            "name": instance, "model": props.get("md"), "manufacturer": None, "host": host, "port": port,
            "mac": None, "ids": [pairing_id], "source": "mdns",
            # What aiohomekit needs to pair without browsing again: feature flags
            # (MFi or software auth), config/state numbers and protocol version.
            "props": {"category": props.get("ci"), "pairing_id": pairing_id, "ff": props.get("ff"),
                      "c#": props.get("c#"), "s#": props.get("s#"), "pv": props.get("pv"),
                      "transport": "thread" if stype == "_hap._udp.local." else "ip"},
        }
    if stype == "_googlecast._tcp.local.":
        raw_id = props.get("id", "")
        try:
            uuid = str(UUID(raw_id))
        except ValueError:
            return None
        return {
            "id": make_id("cast", uuid), "integration": "cast", "name": props.get("fn") or instance,
            "model": props.get("md"), "manufacturer": None, "host": host, "port": port or 8009, "mac": None,
            "ids": [uuid], "source": "mdns", "props": {"uuid": uuid},
        }
    if stype == "_androidtvremote2._tcp.local.":
        mac = norm_mac(props.get("bt"))
        return {
            "id": make_id("androidtv_remote", mac or host), "integration": "androidtv_remote", "name": instance,
            "model": None, "manufacturer": None, "host": host, "port": port, "mac": mac,
            "ids": [x for x in (mac,) if x], "source": "mdns", "props": {},
        }
    if stype == "_http._tcp.local.":
        low = f"{instance} {server_host}".lower()
        if low.startswith("tasmota") or " tasmota" in low:
            return {
                "id": make_id("tasmota", server_host or instance), "integration": "tasmota", "name": instance,
                "model": None, "manufacturer": None, "host": host, "port": port or 80, "mac": None,
                "ids": [], "source": "mdns", "props": {},
            }
        if "reolink" in low:
            return {
                "id": make_id("reolink", server_host or instance), "integration": "reolink", "name": instance,
                "model": None, "manufacturer": "Reolink", "host": host, "port": port, "mac": None, "ids": [],
                "source": "mdns", "props": {},
            }
    return None


def parse_ssdp_headers(text: str) -> dict[str, str]:
    lines = text.split("\r\n") if "\r\n" in text else text.split("\n")
    out: dict[str, str] = {}
    for line in lines[1:]:
        k, sep, v = line.partition(":")
        if sep:
            out[k.strip().upper()] = v.strip()
    return out


#: When one address answers as several things, the more specific integration wins.
_SSDP_RANK = {"roku": 0, "samsungtv": 1, "webostv": 2, "dlna_dmr": 3}


def classify_ssdp(headers: dict[str, str]) -> str | None:
    st = (headers.get("ST") or headers.get("NT") or "").lower()
    server = (headers.get("SERVER") or "").lower()
    usn = (headers.get("USN") or "").lower()
    if "roku" in st or "roku" in server or "roku:ecp" in usn:
        return "roku"
    if "samsung.com:device:remotecontrolreceiver" in st:
        return "samsungtv"
    if "lge-com:service:webos" in st or "webos" in server:
        return "webostv"
    if "mediarenderer" in st:
        return "dlna_dmr"
    return None


def parse_ssdp(text: str, host: str) -> dict[str, Any] | None:
    """One M-SEARCH reply -> a finding (without the description's details), or None."""
    headers = parse_ssdp_headers(text)
    integration = classify_ssdp(headers)
    if integration is None:
        return None
    usn = headers.get("USN") or ""
    serial = None
    uuid = None
    if integration == "roku":
        m = re.search(r"roku:ecp:([A-Za-z0-9]+)", usn)
        serial = m.group(1) if m else None
    m = re.search(r"uuid:([0-9A-Fa-f][0-9A-Fa-f-]{7,})", usn)
    if m:
        uuid = m.group(1).lower()
    key = serial or uuid or host
    return {
        "id": make_id(integration, key), "integration": integration, "name": None, "model": None,
        "manufacturer": None, "host": host, "port": None, "mac": None,
        "ids": [x for x in (serial, uuid) if x], "source": "ssdp",
        "props": {"location": headers.get("LOCATION"), "server": headers.get("SERVER"), "uuid": uuid,
                  "serial": serial, "st": headers.get("ST")},
    }


def apply_description(finding: dict[str, Any], xml_text: str) -> None:
    """Fill name, model and ids from a UPnP device description."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return
    dev = None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "device":
            dev = el
            break
    if dev is None:
        return
    fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in dev}
    finding["name"] = finding.get("name") or fields.get("friendlyName") or None
    finding["model"] = finding.get("model") or fields.get("modelName") or None
    finding["manufacturer"] = finding.get("manufacturer") or fields.get("manufacturer") or None
    serial = fields.get("serialNumber")
    udn = fields.get("UDN", "").lower()
    ids = set(finding.get("ids") or [])
    if serial:
        ids.add(serial)
        props = finding.setdefault("props", {})
        props["serial"] = props.get("serial") or serial
    if udn:
        ids.add(udn.removeprefix("uuid:"))
    finding["ids"] = sorted(ids)


#: pywizlight's bulb classes, as HA names them in a WiZ device name.
def wiz_type_label(module: str) -> str:
    m = (module or "").upper()
    if "SOCKET" in m:
        return "Socket"
    if "FANDIM" in m:
        return "Fan"
    if "RGB" in m:
        return "RGB Tunable"
    if "DW" in m and "TW" not in m:
        return "Dimmable White"
    return "Tunable White"


def parse_wiz(reply: dict[str, Any], host: str) -> dict[str, Any] | None:
    result = reply.get("result") if isinstance(reply, dict) else None
    if not isinstance(result, dict):
        return None
    mac = norm_mac(result.get("mac"))
    if mac is None:
        return None
    module = str(result.get("moduleName") or "")
    return {
        "id": make_id("wiz", mac), "integration": "wiz",
        "name": f"WiZ {wiz_type_label(module)} {mac[-6:].upper()}", "model": module or None,
        "manufacturer": "WiZ", "host": host, "port": WIZ_PORT, "mac": mac, "ids": [mac], "source": "wiz",
        "props": {"module": module, "fw": result.get("fwVersion")},
    }


# -- DHCP / ARP ---------------------------------------------------------------------

#: The ``dhcp`` rules of HA's integration manifests, for the integrations the hub
#: has a flow for: a MAC prefix (``macaddress``, upper-case hex with ``*``) or a
#: DHCP host name pattern (``hostname``, lower case with ``*``). These are the
#: vendors' own prefixes and default host names, not anything of this household.
DHCP_MATCHERS: tuple[dict[str, str], ...] = (
    {"integration": "reolink", "macaddress": "EC71DB*"},
    {"integration": "reolink", "hostname": "reolink*"},
    {"integration": "roborock", "hostname": "roborock-*"},
    {"integration": "wiz", "macaddress": "A8BB50*"},
    {"integration": "wiz", "macaddress": "D8A011*"},
    {"integration": "wiz", "macaddress": "444F8E*"},
    {"integration": "wiz", "macaddress": "6C2990*"},
    {"integration": "wiz", "macaddress": "6C29D2*"},
    {"integration": "wiz", "hostname": "wiz_*"},
    {"integration": "tasmota", "hostname": "tasmota-*"},
    {"integration": "tasmota", "hostname": "tasmota_*"},  # firmware before 9.x names itself tasmota_XXXXXX-NNNN
)
#: Integrations a DHCP match alone is enough to offer (the others are found by
#: their own protocol, which also tells what the device is).
DHCP_OFFER = {"reolink", "roborock", "tasmota"}
#: Reverse lookups for DHCP host names (``HOMEHUB_DHCP_HOSTNAMES=0`` turns them off).
DHCP_HOSTNAMES = os.getenv("HOMEHUB_DHCP_HOSTNAMES", "1") not in ("0", "false", "no")
#: One login covers every device of these (a cloud account): a device of one is
#: set up when the integration is.
ACCOUNT_WIDE = {"roborock"}


def _glob(pattern: str, value: str) -> bool:
    import fnmatch

    return bool(value) and fnmatch.fnmatchcase(value, pattern)


def dhcp_match(mac: str | None, hostname: str | None) -> str | None:
    """The integration HA's manifest rules name for this MAC / host name, or None."""
    m = (norm_mac(mac) or "").upper()
    h = (hostname or "").split(".")[0].lower()
    for rule in DHCP_MATCHERS:
        if "macaddress" in rule and m and _glob(rule["macaddress"], m):
            return rule["integration"]
        if "hostname" in rule and h and _glob(rule["hostname"], h):
            return rule["integration"]
    return None


def parse_arp(text: str) -> dict[str, str]:
    """``/proc/net/arp`` -> ``{ip: mac}`` (complete entries only, no zero MACs)."""
    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        ip, flags, mac = parts[0], parts[2], norm_mac(parts[3])
        try:
            complete = int(flags, 16) & 0x2
        except ValueError:
            continue
        if complete and mac and mac != "000000000000":
            out[ip] = mac
    return out


def parse_dhcp(host: str, mac: str, hostname: str | None) -> dict[str, Any] | None:
    """One neighbour (address, MAC, DHCP host name) -> a finding when a manifest rule names it."""
    integration = dhcp_match(mac, hostname)
    if integration is None or integration not in DHCP_OFFER:
        return None
    short = (hostname or "").split(".")[0] or None
    return {
        "id": make_id(integration, mac), "integration": integration,
        "name": short or f"{LABELS.get(integration, integration)} at {host}", "model": None,
        "manufacturer": LABELS.get(integration), "host": host, "port": None, "mac": mac, "ids": [mac],
        "source": "dhcp", "props": {"hostname": short},
    }


# -- what the registry already knows ----------------------------------------------

class KnownIndex:
    """Every id, MAC and host in the registry -> the config entry it belongs to."""

    #: Entry data/option keys that hold an id or an address.
    ID_KEYS = ("mac", "AccessoryPairingID", "device_name", "serial", "uuid", "unique_id")
    HOST_KEYS = ("host", "url")

    def __init__(self, hub: Hub) -> None:
        self.tokens: dict[str, str] = {}
        self.hosts: dict[str, str] = {}
        self.sources: dict[str, str] = {}
        for entry in hub.config_entries.values():
            self.sources[entry.entry_id] = entry.source
            self._tok(entry.unique_id, entry.entry_id)
            for src in (entry.data or {}, entry.options or {}):
                for k in self.ID_KEYS:
                    self._tok(src.get(k), entry.entry_id)
                for k in self.HOST_KEYS:
                    self._host(src.get(k), entry.entry_id)
                for h in src.get("known_hosts") or []:
                    self._host(h, entry.entry_id)
                hosts = src.get("hosts")
                if isinstance(hosts, dict):
                    for mac, h in hosts.items():
                        self._tok(mac, entry.entry_id)
                        self._host(h, entry.entry_id)
        for dev in hub.devices.values():
            entry_id = dev.config_entries[0] if dev.config_entries else None
            if entry_id is None:
                continue
            for pair in [*dev.identifiers, *dev.connections]:
                if len(pair) >= 2:
                    self._tok(pair[1], entry_id)
            if dev.configuration_url:
                self._host(dev.configuration_url, entry_id)
        for ent in hub.entities.values():
            if not ent.config_entry_id or not ent.unique_id:
                continue
            self._tok(ent.unique_id, ent.config_entry_id)
            # ``{mac}_power``, ``{MAC}/0/light/...``, ``{serial}_active_app``: the device part.
            self._tok(re.split(r"[/_]", ent.unique_id, maxsplit=1)[0], ent.config_entry_id)

    def _tok(self, value: Any, entry_id: str) -> None:
        if value in (None, ""):
            return
        t = token(value)
        if len(t) >= 6:
            self.tokens.setdefault(t, entry_id)

    def _host(self, value: Any, entry_id: str) -> None:
        if not value:
            return
        raw = str(value)
        host = urlparse(raw).hostname if "://" in raw else raw.split(":")[0]
        if host and host not in ("localhost", "127.0.0.1"):
            self.hosts.setdefault(host.lower(), entry_id)

    def match(self, finding: dict[str, Any]) -> str | None:
        return self.match_how(finding)[0]

    def match_how(self, finding: dict[str, Any]) -> tuple[str | None, str | None]:
        """``(entry_id, "id" | "host")``: by an id the device reports, or only by its address."""
        for value in [finding.get("mac"), *(finding.get("ids") or [])]:
            t = token(value)
            if len(t) >= 6 and t in self.tokens:
                return self.tokens[t], "id"
        host = str(finding.get("host") or "").lower()
        entry_id = self.hosts.get(host) if host else None
        return entry_id, ("host" if entry_id else None)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_loopback(value: str) -> bool:
    """The hub reaching a service on its own machine (127.0.0.1, ::1): never a stale address."""
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def shared_macs(neighbours: dict[str, str]) -> set[str]:
    """MACs the neighbour table shows at more than one address.

    A Wi-Fi range extender (or any MAC-translating bridge) answers ARP for every
    device behind it with its own MAC, so that MAC names the extender, not a
    device. Used as an identity it merged unrelated devices into one and
    reported them as a single device that "moved".
    """
    seen: dict[str, int] = {}
    for mac in neighbours.values():
        seen[mac] = seen.get(mac, 0) + 1
    return {mac for mac, count in seen.items() if count > 1}


def current_host(entry: Any) -> str | None:
    """The address the driver uses now: an options override wins, as in the drivers."""
    for src in (entry.options or {}, entry.data or {}):
        raw = str(src.get("host") or src.get("url") or "")
        if raw:
            return (urlparse(raw).hostname if "://" in raw else raw.split(":")[0]) or None
    return None


# -- the manager -------------------------------------------------------------------

Notifier = Callable[[dict[str, Any]], Awaitable[bool]]


async def _send_notice(payload: dict[str, Any]) -> bool:
    from services.common import notify

    return await notify.send(payload)


class DiscoveryManager:
    """Owns the findings, the mDNS browser and the periodic scans."""

    def __init__(self, hub: Hub, store: Any, *, notifier: Notifier | None = None) -> None:
        self.hub = hub
        self.store = store
        self.notifier = notifier or _send_notice
        self.findings: dict[str, dict[str, Any]] = {}
        self.ignored: dict[str, float] = {}
        self.notified: list[str] = []
        self.last_scan: dict[str, Any] = {}
        self._last_active = 0.0
        #: The hub's runtime, set by the service: following a device to a new address reloads its entry.
        self.runtime: Any = None
        self._dirty = False
        self._tasks: list[asyncio.Task] = []
        self._scan_lock = asyncio.Lock()
        self._azc: Any = None
        self._browser: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mdns_seen: dict[str, str] = {}   # finding id -> "type|name" still announced
        self._mdns_pending: set[tuple[str, str]] = set()
        self._notify_task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------------
    def load(self) -> None:
        saved = self.store.get_kv(KV_KEY, {}) or {}
        self.findings = {str(k): dict(v) for k, v in (saved.get("findings") or {}).items() if isinstance(v, dict)}
        self.ignored = {str(k): float(v) for k, v in (saved.get("ignored") or {}).items()}
        self.notified = [str(x) for x in saved.get("notified") or []]

    def save(self) -> None:
        self._dirty = False
        try:
            self.store.set_kv(KV_KEY, {"findings": self.findings, "ignored": self.ignored,
                                       "notified": self.notified[-1000:]})
        except Exception:
            log.exception("saving discovery findings failed")

    async def start(self, *, mdns: bool = True, periodic: bool = True) -> None:
        self._loop = asyncio.get_running_loop()
        self.load()
        if mdns:
            try:
                await self._start_mdns()
            except Exception:
                log.exception("mDNS discovery could not start")
        if periodic:
            self._tasks.append(asyncio.create_task(self._periodic(), name="discovery"))

    async def stop(self) -> None:
        for t in [*self._tasks, *([self._notify_task] if self._notify_task else [])]:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()
        self._notify_task = None
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.async_cancel()
            self._browser = None
        if self._azc is not None:
            with contextlib.suppress(Exception):
                await self._azc.async_close()
            self._azc = None
        if self._dirty:
            self.save()

    async def _periodic(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_S)
        while True:
            if now() - self._last_active >= SCAN_INTERVAL_S:
                try:
                    await self.scan(reason="periodic")
                except Exception:
                    log.exception("discovery scan failed")
            try:
                await self.housekeeping()
            except Exception:
                log.exception("discovery housekeeping failed")
            await asyncio.sleep(HOUSEKEEPING_S)

    async def housekeeping(self) -> None:
        try:
            await self.follow_addresses()
        except Exception:
            log.exception("following devices to new addresses failed")
        t = now()
        for fid in list(self._mdns_seen):
            if fid in self.findings:
                self.findings[fid]["last_seen"] = t  # still announced on the LAN
        expired = [fid for fid, f in self.findings.items() if t - float(f.get("last_seen") or 0) > EXPIRE_S]
        for fid in expired:
            self.findings.pop(fid, None)
            self._mdns_seen.pop(fid, None)
            self._dirty = True
        if self._dirty:
            self.save()

    # -- findings ------------------------------------------------------------------
    def record(self, finding: dict[str, Any]) -> dict[str, Any]:
        """Merge one sighting into the findings; returns the stored finding."""
        t = now()
        fid = finding["id"]
        cur = self.findings.get(fid)
        if cur is None:
            cur = {"first_seen": t, "notified": False}
            self.findings[fid] = cur
            self._dirty = True
            log.info("discovered %s %s at %s", finding.get("integration"), fid, finding.get("host"))
        elif cur.get("host") != finding.get("host"):
            self._dirty = True
        keep_ids = set(cur.get("ids") or [])
        for k, v in finding.items():
            if v is None and cur.get(k) is not None:
                continue  # an SSDP reply without a name must not erase the description's name
            cur[k] = v
        cur["ids"] = sorted(keep_ids | set(finding.get("ids") or []))
        cur["last_seen"] = t
        return cur

    def public(self, f: dict[str, Any], index: KnownIndex | None = None) -> dict[str, Any]:
        index = index or KnownIndex(self.hub)
        entry_id, how = index.match_how(f)
        if entry_id is not None and entry_id not in self.hub.config_entries:
            entry_id = None
        integration = f.get("integration") or ""
        if entry_id is None and integration in ACCOUNT_WIDE:
            entry_id = next((e.entry_id for e in self.hub.config_entries.values()
                             if e.domain == integration and e.source != "ignore"), None)
            how = "account" if entry_id else None
        ignored_by_ha = entry_id is not None and index.sources.get(entry_id) == "ignore"
        known = entry_id is not None and not ignored_by_ha
        ignored = f["id"] in self.ignored or ignored_by_ha
        props = f.get("props") or {}
        from services.homehub import flows  # flows imports this module; import here to avoid a cycle

        needs = flows.needs_for(f)
        unsupported = flows.unsupported_reason(f)
        entry = self.hub.config_entries.get(entry_id or "")
        moved = None
        new_host = str(f.get("host") or "").lower()
        if known and entry is not None and how == "id" and new_host:
            cur = current_host(entry)
            # Only an IP address goes stale; a host name follows the device by itself.
            if cur and _is_ip(cur) and _is_ip(new_host) and cur.lower() != new_host and not _is_loopback(cur):
                moved = {"entry_id": entry.entry_id, "entry_title": entry.title, "old_host": cur,
                         "new_host": f.get("host")}
        return {
            "moved": moved,
            "id": f["id"], "integration": integration, "label": LABELS.get(integration, integration),
            "name": f.get("name") or f.get("host"), "model": f.get("model"), "manufacturer": f.get("manufacturer"),
            "host": f.get("host"), "port": f.get("port"), "mac": f.get("mac"), "source": f.get("source"),
            "first_seen": _iso(f.get("first_seen")), "last_seen": _iso(f.get("last_seen")),
            "known": known, "entry_id": entry_id if known else None,
            "entry_title": entry.title if (known and entry is not None) else None,
            "ignored": ignored, "new": not known and not ignored,
            "adoptable": not known and unsupported is None, "unsupported": unsupported,
            "needs": needs, "details": {k: v for k, v in props.items() if k not in ("location",)},
        }

    def list(self, *, new_only: bool = False) -> list[dict[str, Any]]:
        index = KnownIndex(self.hub)
        out = [self.public(f, index) for f in self.findings.values()]
        if new_only:
            out = [f for f in out if f["new"]]
        out.sort(key=lambda f: (not f["new"], f["label"].lower(), str(f["name"] or "").lower()))
        return out

    def get(self, finding_id: str) -> dict[str, Any] | None:
        return self.findings.get(finding_id)

    def set_ignored(self, finding_id: str, ignored: bool = True) -> dict[str, Any] | None:
        f = self.findings.get(finding_id)
        if f is None:
            return None
        if ignored:
            self.ignored[finding_id] = now()
        else:
            self.ignored.pop(finding_id, None)
        self.save()
        return self.public(f)

    def mark_adopted(self, finding_id: str, entry_id: str) -> None:
        f = self.findings.get(finding_id)
        if f is not None:
            f["adopted_entry_id"] = entry_id
            self.ignored.pop(finding_id, None)
            self.save()

    # -- telling the admins ---------------------------------------------------------
    def _should_notify(self, pub: dict[str, Any], raw: dict[str, Any]) -> bool:
        if not pub["new"] or pub["id"] in self.notified or pub["integration"] in QUIET:
            return False
        return not (pub["integration"] == "matter" and not (raw.get("props") or {}).get("commissioning_open"))

    async def flush_notices(self) -> int:
        """Tell the admins about every new finding not told yet; returns how many."""
        index = KnownIndex(self.hub)
        fresh = []
        moved = []
        for f in list(self.findings.values()):
            pub = self.public(f, index)
            if self._should_notify(pub, f):
                fresh.append(pub)
            elif pub.get("moved") and f"moved:{pub['id']}:{pub['moved']['new_host']}" not in self.notified:
                moved.append(pub)
        if moved:
            await self._notify_moved(moved)
        if not fresh:
            return 0
        from services.common import notify

        def one(p: dict[str, Any]) -> dict[str, Any]:
            where = f" at {p['host']}" if p.get("host") else ""
            action = "Add it" if p["adoptable"] else "See it"
            return notify.notice(
                "@admins", "admin", f"New device found: {p['name']}",
                f"A {p['label']} device{where} is on the network and not set up yet. {action} on the Devices page.",
                url="/devices", priority=2, tag=f"admin:new-device:{p['id']}", once=f"homehub:new-device:{p['id']}",
                kind="admin", audience="parents")

        if len(fresh) > NOTIFY_SUMMARY_OVER:
            names = ", ".join(f"{p['name']} ({p['label']})" for p in fresh[:5])
            more = f" and {len(fresh) - 5} more" if len(fresh) > 5 else ""
            payloads = [notify.notice(
                "@admins", "admin", f"{len(fresh)} new devices found",
                f"Not set up yet: {names}{more}. Add or ignore them on the Devices page.",
                url="/devices", priority=2, tag="admin:new-devices",
                once="homehub:new-devices:" + ",".join(sorted(p["id"] for p in fresh))[:150],
                kind="admin", audience="parents")]
        else:
            payloads = [one(p) for p in fresh]
        sent = 0
        for payload in payloads:
            with contextlib.suppress(Exception):
                sent += int(bool(await self.notifier(payload)))
        if sent:
            for p in fresh:
                self.notified.append(p["id"])
                self.findings[p["id"]]["notified"] = True
            self.save()
        return len(fresh) if sent else 0

    async def follow_addresses(self) -> None:
        """Learn the MAC of devices set up by IP, and follow them when DHCP moves them (address_follow.py)."""
        from services.homehub import address_follow

        try:
            neighbours = parse_arp(self.read_arp())
        except RuntimeError:
            return
        proxied = shared_macs(neighbours)
        learn, moves = address_follow.plan(self.hub, neighbours, proxied)
        for device, mac in learn:
            address_follow.learn_mac(self.hub, device, mac)
        for move in moves:
            entry = address_follow.apply_move(self.hub, move)
            if self.runtime is not None:
                await self.runtime.stop_entry(entry.entry_id)
                if not entry.disabled_by:
                    self.runtime.start_entry(entry)
            await self._notify_followed(move)
        # A followed device that stopped answering and is not in the table: sweep now, so the kernel
        # learns where it went, instead of waiting for the hourly scan.
        if not moves and now() - self._last_active >= FOLLOW_SCAN_S and address_follow.lost(self.hub, neighbours):
            self._last_active = now()  # one early sweep per FOLLOW_SCAN_S
            asyncio.get_running_loop().create_task(self.scan(reason="a followed device stopped answering"))

    async def _notify_followed(self, move: Any) -> None:
        from services.common import notify

        payload = notify.notice(
            "@admins", "admin", f"{move.title} has a new address",
            f"The router gave {move.title} a new address ({move.old_ip} to {move.new_ip}). "
            "Jarvis followed it by its hardware address; nothing needs doing.",
            url="/devices", priority=3, tag=f"admin:device-followed:{move.entry_id}",
            once=f"homehub:followed:{move.entry_id}:{move.new_ip}", kind="admin", audience="parents")
        with contextlib.suppress(Exception):
            await self.notifier(payload)

    async def _notify_moved(self, moved: list[dict[str, Any]]) -> None:
        """One notice per device whose address changed (until it changes again)."""
        from services.common import notify

        for p in moved:
            m = p["moved"]
            key = f"moved:{p['id']}:{m['new_host']}"
            payload = notify.notice(
                "@admins", "admin", f"{m['entry_title']} has a new address",
                f"{m['entry_title']} ({p['label']}) now answers at {m['new_host']} instead of {m['old_host']}. "
                "Update it on the Devices page so the hub can reach it.",
                url="/devices", priority=2, tag=f"admin:device-moved:{p['id']}", once=f"homehub:{key}",
                kind="admin", audience="parents")
            sent = False
            with contextlib.suppress(Exception):
                sent = bool(await self.notifier(payload))
            if sent:
                self.notified.append(key)
                self._dirty = True

    def _schedule_notices(self) -> None:
        if self._notify_task is not None and not self._notify_task.done():
            return

        async def later() -> None:
            await asyncio.sleep(NOTIFY_DELAY_S)
            await self.flush_notices()

        self._notify_task = asyncio.ensure_future(later())

    # -- mDNS ----------------------------------------------------------------------
    @staticmethod
    def zeroconf_factory() -> Any:
        from zeroconf.asyncio import AsyncZeroconf

        return AsyncZeroconf()

    async def _start_mdns(self) -> None:
        from zeroconf.asyncio import AsyncServiceBrowser

        self._azc = self.zeroconf_factory()
        self._browser = AsyncServiceBrowser(self._azc.zeroconf, list(MDNS_TYPES), handlers=[self._on_mdns])
        log.info("mDNS discovery browsing %d service types", len(MDNS_TYPES))

    def _on_mdns(self, zeroconf: Any = None, service_type: str = "", name: str = "",
                 state_change: Any = None) -> None:
        change = getattr(state_change, "name", str(state_change))
        if change == "Removed":
            for fid, key in list(self._mdns_seen.items()):
                if key == f"{service_type}|{name}":
                    self._mdns_seen.pop(fid, None)
            return
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._spawn_resolve, service_type, name)

    def _spawn_resolve(self, service_type: str, name: str) -> None:
        key = (service_type, name)
        if key in self._mdns_pending:
            return
        self._mdns_pending.add(key)
        task = asyncio.ensure_future(self._resolve(service_type, name))
        task.add_done_callback(lambda _t: self._mdns_pending.discard(key))

    async def _resolve(self, service_type: str, name: str) -> None:
        from zeroconf.asyncio import AsyncServiceInfo

        if self._azc is None:
            return
        info = AsyncServiceInfo(service_type, name)
        try:
            if not await info.async_request(self._azc.zeroconf, 3000):
                return
        except Exception as exc:
            log.debug("mDNS %s did not resolve: %s", name, exc)
            return
        addrs = [a for a in info.parsed_addresses() if ":" not in a] or info.parsed_addresses()
        self.handle_mdns(service_type, name, addrs[0] if addrs else None, info.port, info.properties, info.server)

    def handle_mdns(self, service_type: str, name: str, host: str | None, port: int | None,
                    properties: dict[Any, Any] | None, server: str | None) -> dict[str, Any] | None:
        """One resolved service (the browser's callback, and the tests' entry point)."""
        try:
            finding = parse_mdns(service_type, name, host, port, properties, server)
        except Exception:
            log.exception("could not read mDNS service %s", name)
            return None
        if finding is None:
            return None
        is_new = finding["id"] not in self.findings
        stored = self.record(finding)
        self._mdns_seen[finding["id"]] = f"{service_type}|{name}"
        if is_new and self._loop is not None:
            self._schedule_notices()
        return stored

    # -- active scans ----------------------------------------------------------------
    async def scan(self, *, reason: str = "on demand") -> dict[str, Any]:
        """SSDP and the WiZ sweep now (at most once a minute), and re-resolve mDNS."""
        async with self._scan_lock:
            if reason != "periodic" and now() - self._last_active < MIN_SCAN_GAP_S:
                return {**self.last_scan, "skipped": "a scan ran less than a minute ago"}
            self._last_active = now()
            started = time.monotonic()
            before = set(self.findings)
            counts: dict[str, Any] = {}
            # DHCP/ARP last: the sweeps above make the kernel resolve every live address.
            for label, job in (("ssdp", self._scan_ssdp), ("wiz", self._scan_wiz), ("dhcp", self._scan_dhcp)):
                try:
                    counts[label] = await job()
                except Exception as exc:
                    log.warning("%s scan failed: %s", label, exc)
                    counts[label] = f"failed: {exc}"
            for key in list(self._mdns_seen.values()):
                stype, _, name = key.partition("|")
                self._spawn_resolve(stype, name)
            counts["mdns"] = len(self._mdns_seen)
            self.last_scan = {"reason": reason, "at": datetime.now(UTC).isoformat(),
                              "seconds": round(time.monotonic() - started, 1), "found": counts,
                              "new_ids": sorted(set(self.findings) - before)}
            self.save()
        await self.flush_notices()
        return self.last_scan

    def subnet_hosts(self) -> list[str]:
        """Addresses to sweep: ``HOMEHUB_SCAN_SUBNET`` or this host's /24."""
        raw = os.getenv("HOMEHUB_SCAN_SUBNET") or os.getenv("SCAN_SUBNET")
        if raw:
            net = ipaddress.IPv4Network(raw, strict=False)
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("10.255.255.255", 1))  # no packet is sent; this only picks the LAN interface
                local = s.getsockname()[0]
            finally:
                s.close()
            net = ipaddress.IPv4Network(f"{local}/24", strict=False)
        return [str(h) for h in list(net.hosts())[:MAX_SWEEP_HOSTS]]

    # SSDP --------------------------------------------------------------------------
    async def _scan_ssdp(self) -> int:
        replies = await self.ssdp_search(self.subnet_hosts())
        best: dict[str, dict[str, Any]] = {}
        for text, host in replies:
            f = parse_ssdp(text, host)
            if f is None:
                continue
            cur = best.get(host)
            if cur is None or _SSDP_RANK[f["integration"]] < _SSDP_RANK[cur["integration"]]:
                best[host] = f
            elif cur["integration"] == f["integration"]:
                cur["ids"] = sorted(set(cur["ids"]) | set(f["ids"]))
                for k, v in (f.get("props") or {}).items():
                    if v and not cur["props"].get(k):
                        cur["props"][k] = v
                if f["props"].get("serial") and f["props"].get("location"):
                    cur["props"]["location"] = f["props"]["location"]  # the device's root, not the DIAL one
        for host, f in best.items():
            location = (f.get("props") or {}).get("location")
            if location and urlparse(location).hostname == host:
                xml = await self.fetch_description(location)
                if xml:
                    apply_description(f, xml)
            serial = (f.get("props") or {}).get("serial")
            if f["integration"] == "roku" and serial:
                f["id"] = make_id("roku", serial)  # the serial is HA's unique_id for a Roku
            if not f.get("name"):
                f["name"] = f"{LABELS.get(f['integration'], f['integration'])} at {host}"
            self.record(f)
        return len(best)

    async def ssdp_search(self, hosts: list[str]) -> list[tuple[str, str]]:
        """M-SEARCH by multicast and unicast; returns ``(reply text, sender address)``."""
        loop = asyncio.get_running_loop()
        replies: list[tuple[str, str]] = []

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: Any) -> None:
                replies.append((data.decode("utf-8", "replace"), addr[0]))

            def error_received(self, exc: Exception) -> None:
                pass

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind(("0.0.0.0", SSDP_SOURCE_PORT))
        sock.setblocking(False)
        transport, _ = await loop.create_datagram_endpoint(Proto, sock=sock)
        try:
            for st in SSDP_TARGETS:
                msg = (f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
                       f"MX: 2\r\nST: {st}\r\n\r\n").encode()
                with contextlib.suppress(OSError):
                    transport.sendto(msg, SSDP_ADDR)
            for host in hosts:
                msg = (f'M-SEARCH * HTTP/1.1\r\nHOST: {host}:1900\r\nMAN: "ssdp:discover"\r\n'
                       f"ST: ssdp:all\r\n\r\n").encode()
                with contextlib.suppress(OSError):
                    transport.sendto(msg, (host, 1900))
                await asyncio.sleep(0.005)  # about 1.3 s for a /24
            await asyncio.sleep(SSDP_LISTEN_S)
        finally:
            transport.close()
        return replies

    async def fetch_description(self, url: str) -> str | None:
        import aiohttp

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session, \
                    session.get(url) as resp:
                if resp.status != 200:
                    return None
                return (await resp.content.read(256 * 1024)).decode("utf-8", "replace")
        except Exception as exc:
            log.debug("UPnP description %s: %s", url, exc)
            return None

    # DHCP / ARP --------------------------------------------------------------------
    @staticmethod
    def read_arp() -> str:
        path = os.getenv("HOMEHUB_ARP_TABLE", "/proc/net/arp")
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            raise RuntimeError(f"the neighbour table {path} cannot be read: {exc}") from exc

    async def reverse_name(self, host: str) -> str | None:
        """The DHCP host name the LAN's DNS has for an address, or None."""
        loop = asyncio.get_running_loop()
        try:
            name = await asyncio.wait_for(loop.run_in_executor(None, socket.gethostbyaddr, host), 2.0)
        except (OSError, TimeoutError):
            return None
        return str(name[0]) if name and name[0] else None

    async def _scan_dhcp(self) -> int:
        """Neighbours by MAC and host name -> findings, and MACs for findings that had none."""
        neighbours = parse_arp(self.read_arp())
        proxied = shared_macs(neighbours)
        if proxied:
            log.debug("dhcp: %d MAC(s) answer for several addresses (an extender or bridge); not used as ids",
                      len(proxied))
            # Undo what an earlier scan learned from such a MAC: its own findings go, and
            # other findings lose it as an id (it was filled in from the extender).
            for fid, f in list(self.findings.items()):
                if f.get("mac") not in proxied:
                    continue
                if f.get("source") == "dhcp":
                    del self.findings[fid]
                else:
                    f["ids"] = sorted(set(f.get("ids") or []) - {f["mac"]})
                    f["mac"] = None
                self._dirty = True
        by_host: dict[str, dict[str, Any]] = {}
        for f in self.findings.values():
            if f.get("host"):
                by_host.setdefault(str(f["host"]), f)
        filled = 0
        for host, mac in neighbours.items():
            f = by_host.get(host)
            if f is not None and not f.get("mac") and mac not in proxied:
                f["mac"] = mac
                f["ids"] = sorted(set(f.get("ids") or []) | {mac})
                self._dirty = True
                filled += 1
        names: dict[str, str | None] = {}
        if DHCP_HOSTNAMES:
            sem = asyncio.Semaphore(16)

            async def one(h: str) -> None:
                async with sem:
                    names[h] = await self.reverse_name(h)

            await asyncio.gather(*(one(h) for h in neighbours))
        found = 0
        for host, mac in neighbours.items():
            if mac in proxied:
                continue  # the extender's MAC, not this device's: no id can be made from it
            f = parse_dhcp(host, mac, names.get(host))
            if f is None:
                continue
            other = by_host.get(host)
            if other is not None and other.get("integration") == f["integration"] and other["id"] != f["id"]:
                continue  # already found by its own protocol
            self.record(f)
            found += 1
        log.debug("dhcp: %d neighbours, %d matched, %d MACs filled in", len(neighbours), found, filled)
        return found

    # WiZ ---------------------------------------------------------------------------
    async def _scan_wiz(self) -> int:
        found = 0
        for reply, host in await self.wiz_sweep(self.subnet_hosts()):
            f = parse_wiz(reply, host)
            if f is not None:
                self.record(f)
                found += 1
        return found

    async def wiz_sweep(self, hosts: list[str]) -> list[tuple[dict[str, Any], str]]:
        """``getSystemConfig`` by broadcast and to every host; returns ``(reply, sender)``, one per sender."""
        loop = asyncio.get_running_loop()
        replies: dict[str, dict[str, Any]] = {}

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: Any) -> None:
                try:
                    msg = json.loads(data.decode("utf-8", "replace"))
                except ValueError:
                    return
                if isinstance(msg, dict) and msg.get("method") == "getSystemConfig":
                    replies.setdefault(addr[0], msg)

            def error_received(self, exc: Exception) -> None:
                pass

        transport, _ = await loop.create_datagram_endpoint(
            Proto, local_addr=("0.0.0.0", WIZ_SOURCE_PORT), allow_broadcast=True)
        payload = json.dumps({"method": "getSystemConfig", "params": {}}).encode()
        try:
            for _ in range(2):  # Wi-Fi drops a datagram now and then
                with contextlib.suppress(OSError):
                    transport.sendto(payload, ("255.255.255.255", WIZ_PORT))
                for host in hosts:
                    if host in replies:
                        continue
                    with contextlib.suppress(OSError):
                        transport.sendto(payload, (host, WIZ_PORT))
                    await asyncio.sleep(0.004)
            await asyncio.sleep(WIZ_LISTEN_S)
        finally:
            transport.close()
        return [(r, h) for h, r in replies.items()]
