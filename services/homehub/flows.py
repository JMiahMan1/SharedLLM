"""Adding a discovered device: HA's config flows, for the integrations the hub drives.

Each flow turns a finding (``services/homehub/discovery.py``) plus what the
admin typed into the records the matching driver expects: a ConfigEntry with
the data keys HA stored, a DeviceEntry, and EntityEntry records with HA's
unique_id formats. They are saved through the store, added to the hub, and the
entry is started (``runtime.start_entry``).

| Integration | Input | What is created |
|---|---|---|
| wiz | none | entry ``{host}`` (unique_id = MAC); light ``{mac}``, ``{mac}_effect_speed``, ``{mac}_power`` when metered |
| cast | none | an entity ``media_player`` (unique_id = cast UUID) on the one ``cast`` entry, its host added to ``known_hosts`` |
| roku | none | entry ``{host}`` (unique_id = serial); media_player and remote ``{serial}``, diagnostics ``{serial}_{key}`` |
| esphome | ``noise_psk`` when the node is encrypted | entry ``{host, port, password, noise_psk, device_name}``; one entity per node entity, ``{MAC}/{device_id}/{platform}/{name}`` |
| tasmota | ``password`` when the web UI has one | entry ``{host}``; relays ``{MAC}_switch_relay_{n}``, energy ``{MAC}_sensor_sensor_ENERGY_{Key}`` |
| matter | the setup code | commissioned through the Matter Server; entities in HA's Matter scheme on the existing ``matter`` entry |

The flows that pair with the device (HomeKit, Android TV Remote, Samsung, LG
webOS) and DLNA, Reolink and the ESPHome builder are in
``services/homehub/pairing.py``. A flow answers in one of three ways: the
records it created (``adopted``), ``NeedsInput`` (fields to fill in, with a
``session`` when a pairing is open on the device), or ``Pending`` (the device is
waiting for a person, e.g. "Allow" on a TV; the caller asks again with the
``session``).

The device transports are module functions so the tests can replace them.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from typing import Any
from uuid import UUID

from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, HubError
from services.homehub.discovery import norm_mac, token, wiz_type_label

log = logging.getLogger("homehub.flows")

#: Integrations a finding names that no flow can add (none right now; kept for new discovery sources).
UNSUPPORTED: dict[str, str] = {}

MATTER_CODE_RE = re.compile(r"^MT:[0-9A-Z.\-]{10,}$")


class FlowError(HubError):
    """A flow that cannot go on; ``status`` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class NeedsInput(Exception):
    """The flow needs input from the admin (a key, a code)."""

    def __init__(self, needs: list[dict[str, Any]], message: str, error: str | None = None,
                 session: str | None = None) -> None:
        super().__init__(message)
        self.needs = needs
        self.message = message
        self.error = error
        #: A pairing open on the device (the TV is showing a code); sent back with the input.
        self.session = session


class Pending(Exception):
    """The device is waiting for a person (accept on the TV); ask again with ``session``."""

    def __init__(self, session: str, message: str, step: str = "accept_on_device", poll_ms: int = 2000) -> None:
        super().__init__(message)
        self.session = session
        self.message = message
        self.step = step
        self.poll_ms = poll_ms


NOISE_PSK = {"key": "noise_psk", "label": "Encryption key", "type": "password",
             "help": "The api: encryption: key from the node's ESPHome YAML (44 characters, base64)."}
SETUP_CODE = {"key": "code", "label": "Setup code", "type": "text",
              "help": "The QR code's text (MT:...) or the 11 or 21 digit pairing code on the device or its box."}
TASMOTA_PASSWORD = {"key": "password", "label": "Web password", "type": "password", "optional": True,
                    "help": "Only if the device's web UI asks for one."}


def needs_for(finding: dict[str, Any]) -> list[dict[str, Any]]:
    """The input a finding will need to be added, as far as discovery can tell."""
    integration = finding.get("integration")
    props = finding.get("props") or {}
    if integration == "esphome" and props.get("encrypted"):
        return [NOISE_PSK]
    if integration == "matter":
        return [SETUP_CODE]
    from services.homehub import pairing  # pairing imports this module

    return pairing.needs_for(finding)


def unsupported_reason(finding: dict[str, Any]) -> str | None:
    integration = finding.get("integration") or ""
    if integration in UNSUPPORTED:
        return UNSUPPORTED[integration]
    if integration == "wiz" and wiz_type_label(str((finding.get("props") or {}).get("module") or "")) in (
            "Socket", "Fan"):
        return "WiZ sockets and fans are not supported yet (the hub drives WiZ lights)."
    return None


# -- registry helpers ------------------------------------------------------------------

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s or "device"


def free_entity_id(hub: Any, domain: str, base: str, taken: set[str]) -> str:
    """``domain.base``, or ``domain.base_2`` and up when that is in use."""
    candidate = f"{domain}.{base}"
    n = 2
    while candidate in hub.entities or candidate in hub.states.entity_ids() or candidate in taken:
        candidate = f"{domain}.{base}_{n}"
        n += 1
    taken.add(candidate)
    return candidate


def _area(hub: Any, area: str | None) -> str | None:
    if not area:
        return None
    found = hub.area_id_by_name(str(area))
    if found is None:
        raise FlowError(f"There is no area {area!r}", 400)
    return found


def _existing_entry(hub: Any, domain: str, unique_id: str | None) -> ConfigEntry | None:
    if not unique_id:
        return None
    want = str(unique_id).lower()
    for e in hub.config_entries.values():
        if e.domain == domain and str(e.unique_id or "").lower() == want and e.source != "ignore":
            return e
    return None


def _new_entry_id() -> str:
    return uuid.uuid4().hex


class Plan:
    """The records one flow creates, committed together."""

    def __init__(self, hub: Any, entry: ConfigEntry, *, new_entry: bool = True) -> None:
        self.hub = hub
        self.entry = entry
        self.new_entry = new_entry
        self.device: DeviceEntry | None = None
        #: Every device the flow creates; the first is ``device`` (a bridge has one per accessory).
        self.devices: list[DeviceEntry] = []
        self.entities: list[EntityEntry] = []
        self._taken: set[str] = set()

    def set_device(self, name: str, *, name_by_user: str | None, area_id: str | None, **fields: Any) -> DeviceEntry:
        self.device = self.add_device(name, name_by_user=name_by_user, area_id=area_id, **fields)
        return self.device

    def add_device(self, name: str, *, name_by_user: str | None = None, area_id: str | None = None,
                   **fields: Any) -> DeviceEntry:
        dev = DeviceEntry(id=uuid.uuid4().hex, name=name, name_by_user=name_by_user or None,
                          area_id=area_id, config_entries=[self.entry.entry_id], **fields)
        self.devices.append(dev)
        if self.device is None:
            self.device = dev
        return dev

    def add_entity(self, domain: str, unique_id: str, own_name: str | None, *, device: DeviceEntry | None = None,
                   **fields: Any) -> EntityEntry:
        dev = device or self.device
        dev_name = (dev.display_name if dev else "") or self.entry.title
        base = slugify(f"{dev_name} {own_name}" if own_name else dev_name)
        ent = EntityEntry(entity_id=free_entity_id(self.hub, domain, base, self._taken), platform=self.entry.domain,
                          unique_id=unique_id, config_entry_id=self.entry.entry_id,
                          device_id=dev.id if dev else None, original_name=own_name,
                          has_entity_name=True, **fields)
        self.entities.append(ent)
        return ent


async def commit(runtime: Any, plan: Plan, *, restart: bool = True) -> dict[str, Any]:
    """Save everything, add it to the hub and (re)start the entry."""
    hub, store = runtime.hub, runtime.store
    try:
        store.save_config_entry(plan.entry)
    except RuntimeError as exc:  # no FERNET_KEY: the store refuses to keep credentials in the clear
        raise FlowError(str(exc), 503) from exc
    hub.config_entries[plan.entry.entry_id] = plan.entry
    for dev in plan.devices:
        store.save_device(dev)
        hub.devices[dev.id] = dev
    for ent in plan.entities:
        store.save_entity(ent)
        hub.entities[ent.entity_id] = ent
    if restart:
        if not plan.new_entry:
            await runtime.stop_entry(plan.entry.entry_id)
        runtime.start_entry(plan.entry)
    log.info("added %s %s (%s) with %d entities", plan.entry.domain, plan.entry.title, plan.entry.entry_id,
             len(plan.entities))
    return {
        "status": "adopted", "entry_id": plan.entry.entry_id, "domain": plan.entry.domain, "title": plan.entry.title,
        "name": (plan.device.display_name if plan.device else "") or plan.entry.title,
        "device_id": plan.device.id if plan.device else None,
        "entities": [e.entity_id for e in plan.entities],
    }


# -- device transports (the tests replace these) --------------------------------------

async def wiz_request(host: str, method: str) -> dict[str, Any]:
    from services.homehub.drivers.wiz import udp_request

    return await udp_request(host, method)


async def esphome_probe(host: str, port: int, noise_psk: str | None, password: str = "") -> tuple[Any, list[Any]]:
    """Connect once and return ``(device_info, entity infos)``."""
    from aioesphomeapi import APIClient

    client = APIClient(host, port, password, client_info="homehub", noise_psk=noise_psk or None)
    try:
        await asyncio.wait_for(client.connect(login=True), 10)
        device_info, infos, _services = await asyncio.wait_for(client.device_info_and_list_entities(), 10)
        return device_info, list(infos)
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(client.disconnect(), 3)


async def roku_device_info(host: str) -> dict[str, str]:
    import xml.etree.ElementTree as ET

    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session, \
            session.get(f"http://{host}:8060/query/device-info") as resp:
        if resp.status != 200:
            raise FlowError(f"The Roku at {host} answered HTTP {resp.status}", 502)
        root = ET.fromstring(await resp.text())
    return {child.tag: (child.text or "") for child in root}


async def tasmota_status(host: str, password: str | None) -> dict[str, Any]:
    import aiohttp

    auth = aiohttp.BasicAuth("admin", password) if password else None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session, \
            session.get(f"http://{host}/cm", params={"cmnd": "Status 0"}, auth=auth) as resp:
        if resp.status == 401:
            raise NeedsInput([TASMOTA_PASSWORD], "This Tasmota device asks for its web password.",
                             "The password was not accepted." if password else None)
        if resp.status != 200:
            raise FlowError(f"The Tasmota device at {host} answered HTTP {resp.status}", 502)
        body = await resp.json(content_type=None)
    if isinstance(body, dict) and body.get("WARNING"):
        raise NeedsInput([TASMOTA_PASSWORD], "This Tasmota device asks for its web password.",
                         "The password was not accepted." if password else None)
    if not isinstance(body, dict):
        raise FlowError("The Tasmota device sent an unexpected reply", 502)
    return body


# -- the flows -------------------------------------------------------------------------

async def adopt(runtime: Any, finding: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Add one discovered device. Raises NeedsInput or FlowError."""
    integration = finding.get("integration") or ""
    reason = unsupported_reason(finding)
    if reason is not None:
        raise FlowError(reason, 501)
    from services.homehub import pairing  # pairing imports this module

    flow = FLOWS.get(integration) or pairing.FLOWS.get(integration)
    if flow is None:
        raise FlowError(f"Adding {integration} devices is not supported yet.", 501)
    hub = runtime.hub
    name = str(body.get("name") or "").strip() or None
    area_id = _area(hub, body.get("area_id"))
    user_input = body.get("input") if isinstance(body.get("input"), dict) else {}
    return await flow(runtime, finding, name, area_id, user_input)


async def adopt_wiz(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                    user_input: dict[str, Any]) -> dict[str, Any]:
    from services.homehub.core import DeviceUnavailable

    hub, host = runtime.hub, str(finding.get("host") or "")
    try:
        cfg = await wiz_request(host, "getSystemConfig")
    except DeviceUnavailable as exc:
        raise FlowError(f"The WiZ light at {host} did not answer: {exc}", 502) from exc
    mac = norm_mac(cfg.get("mac")) or norm_mac(finding.get("mac"))
    if mac is None:
        raise FlowError("The WiZ light did not report its MAC address", 502)
    if _existing_entry(hub, "wiz", mac):
        raise FlowError("This WiZ light is already set up", 409)
    module = str(cfg.get("moduleName") or (finding.get("props") or {}).get("module") or "")
    if wiz_type_label(module) in ("Socket", "Fan"):
        raise FlowError("WiZ sockets and fans are not supported yet (the hub drives WiZ lights).", 501)
    metered = False
    with contextlib.suppress(Exception):
        metered = (await wiz_request(host, "getPower")).get("power") is not None
    title = f"WiZ {wiz_type_label(module)} {mac[-6:].upper()}"
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="wiz", title=title, data={"host": host},
                        source="zeroconf", unique_id=mac)
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=name, area_id=area_id, manufacturer="WiZ", model=module or None,
                    sw_version=str(cfg.get("fwVersion")) if cfg.get("fwVersion") else None,
                    connections=[["mac", ":".join(mac[i:i + 2] for i in range(0, 12, 2))]])
    plan.add_entity("light", mac, None, supported_features=4)
    plan.add_entity("number", f"{mac}_effect_speed", "Effect speed", entity_category="config",
                    capabilities={"min": 10, "max": 200, "step": 1, "mode": "slider"})
    if metered:
        plan.add_entity("sensor", f"{mac}_power", "Power", original_device_class="power", unit_of_measurement="W",
                        capabilities={"state_class": "measurement"})
    return await commit(runtime, plan)


async def adopt_cast(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                     user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    raw = (finding.get("props") or {}).get("uuid") or next(iter(finding.get("ids") or []), "")
    try:
        cast_uuid = str(UUID(str(raw)))
    except ValueError as exc:
        raise FlowError("This Cast device has no usable id", 400) from exc
    for e in hub.entities.values():
        if e.platform == "cast" and token(e.unique_id) == token(cast_uuid):
            raise FlowError("This Cast device is already set up", 409)
    entry = next((e for e in hub.config_entries.values() if e.domain == "cast" and e.source != "ignore"), None)
    host = str(finding.get("host") or "")
    if entry is None:
        entry = ConfigEntry(entry_id=_new_entry_id(), domain="cast", title="Google Cast",
                            data={"known_hosts": [], "ignore_cec": [], "uuid": None}, source="zeroconf",
                            unique_id="google_cast")
        new_entry = True
    else:
        new_entry = False
    hosts = [str(h) for h in entry.data.get("known_hosts") or []]
    if host and host not in hosts:
        entry.data = {**entry.data, "known_hosts": [*hosts, host]}
    title = finding.get("name") or "Cast device"
    plan = Plan(hub, entry, new_entry=new_entry)
    plan.set_device(title, name_by_user=name, area_id=area_id, manufacturer=finding.get("manufacturer") or "Google Inc.",
                    model=finding.get("model"), identifiers=[["cast", cast_uuid]])
    plan.add_entity("media_player", cast_uuid, None)
    return await commit(runtime, plan)


ROKU_BINARY = {"headphones_connected": "Headphones connected", "supports_airplay": "Supports AirPlay",
               "supports_ethernet": "Supports ethernet", "supports_find_remote": "Supports find remote"}


async def adopt_roku(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                     user_input: dict[str, Any]) -> dict[str, Any]:
    hub, host = runtime.hub, str(finding.get("host") or "")
    try:
        info = await roku_device_info(host)
    except FlowError:
        raise
    except Exception as exc:
        raise FlowError(f"The Roku at {host} did not answer: {exc or type(exc).__name__}", 502) from exc
    serial = info.get("serial-number") or (finding.get("props") or {}).get("serial")
    if not serial:
        raise FlowError("The Roku did not report its serial number", 502)
    if _existing_entry(hub, "roku", serial):
        raise FlowError("This Roku is already set up", 409)
    title = info.get("user-device-name") or info.get("friendly-device-name") or finding.get("name") or "Roku"
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="roku", title=title, data={"host": host},
                        source="ssdp", unique_id=serial)
    plan = Plan(hub, entry)
    macs = [norm_mac(info.get(k)) for k in ("wifi-mac", "ethernet-mac")]
    plan.set_device(title, name_by_user=name, area_id=area_id, manufacturer=info.get("vendor-name") or "Roku",
                    model=info.get("model-name") or finding.get("model"), serial_number=serial,
                    sw_version=info.get("software-version") or None, identifiers=[["roku", serial]],
                    connections=[["mac", ":".join(m[i:i + 2] for i in range(0, 12, 2))] for m in macs if m])
    plan.add_entity("media_player", serial, None,
                    original_device_class="tv" if str(info.get("is-tv")).lower() == "true" else "receiver")
    plan.add_entity("remote", serial, None)
    for key, label in ROKU_BINARY.items():
        plan.add_entity("binary_sensor", f"{serial}_{key}", label, entity_category="diagnostic")
    plan.add_entity("sensor", f"{serial}_active_app", "Active app", entity_category="diagnostic")
    plan.add_entity("sensor", f"{serial}_active_app_id", "Active app ID", entity_category="diagnostic")
    return await commit(runtime, plan)


# ESPHome platform word -> HA domain (text sensors are HA sensors).
_ESPHOME_DOMAINS = {"text_sensor": "sensor"}


async def adopt_esphome(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                        user_input: dict[str, Any]) -> dict[str, Any]:
    from aioesphomeapi import (
        APIConnectionError,
        EncryptionPlaintextAPIError,
        InvalidAuthAPIError,
        InvalidEncryptionKeyAPIError,
        RequiresEncryptionAPIError,
    )

    from services.homehub.drivers.esphome import platform_of

    hub, host = runtime.hub, str(finding.get("host") or "")
    port = int(finding.get("port") or 6053)
    props = finding.get("props") or {}
    psk = str(user_input.get("noise_psk") or "").strip() or None
    password = str(user_input.get("password") or "")
    mac_hint = norm_mac(finding.get("mac"))
    if mac_hint and _existing_entry(hub, "esphome", ":".join(mac_hint[i:i + 2] for i in range(0, 12, 2))):
        raise FlowError("This ESPHome node is already set up", 409)
    if props.get("encrypted") and not psk:
        raise NeedsInput([NOISE_PSK], f"{finding.get('name') or host} is encrypted: enter its key.")
    try:
        device_info, infos = await esphome_probe(host, port, psk, password)
    except RequiresEncryptionAPIError as exc:
        raise NeedsInput([NOISE_PSK], f"{finding.get('name') or host} is encrypted: enter its key.") from exc
    except InvalidEncryptionKeyAPIError as exc:
        raise NeedsInput([NOISE_PSK], "Enter the node's encryption key.", "That key was not accepted.") from exc
    except EncryptionPlaintextAPIError as exc:
        raise FlowError("This node is not encrypted; add it without a key.", 400) from exc
    except InvalidAuthAPIError as exc:
        raise NeedsInput([{"key": "password", "label": "API password", "type": "password"}],
                         "This node has an API password.", "That password was not accepted." if password else None
                         ) from exc
    except (APIConnectionError, OSError, TimeoutError) as exc:
        raise FlowError(f"The ESPHome node at {host} did not answer: {exc or type(exc).__name__}", 502) from exc
    mac_colon = str(device_info.mac_address or "").upper()
    mac = norm_mac(mac_colon)
    if mac is None:
        raise FlowError("The ESPHome node did not report its MAC address", 502)
    mac_lower = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
    if _existing_entry(hub, "esphome", mac_lower):
        raise FlowError("This ESPHome node is already set up", 409)
    title = device_info.friendly_name or device_info.name or finding.get("name") or host
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="esphome", title=title, source="zeroconf",
                        unique_id=mac_lower,
                        data={"host": host, "port": port, "password": password, "noise_psk": psk or "",
                              "device_name": device_info.name})
    plan = Plan(hub, entry)
    version = device_info.esphome_version
    plan.set_device(title, name_by_user=name, area_id=area_id,
                    manufacturer=device_info.manufacturer or "Espressif",
                    model=device_info.project_name or device_info.model or None,
                    sw_version=f"{device_info.project_version} (ESPHome {version})" if device_info.project_version
                    else (f"ESPHome {version}" if version else None),
                    connections=[["mac", mac_lower]],
                    configuration_url=f"http://{device_info.name}.local/" if device_info.webserver_port else None)
    mac_upper = ":".join(mac[i:i + 2] for i in range(0, 12, 2)).upper()
    for info in infos:
        platform = platform_of(info)
        domain = _ESPHOME_DOMAINS.get(platform, platform)
        category = {1: "config", 2: "diagnostic"}.get(int(getattr(info, "entity_category", 0) or 0))
        fields: dict[str, Any] = {"entity_category": category}
        if getattr(info, "device_class", ""):
            fields["original_device_class"] = info.device_class
        if getattr(info, "unit_of_measurement", ""):
            fields["unit_of_measurement"] = info.unit_of_measurement
        if getattr(info, "icon", ""):
            fields["original_icon"] = info.icon
        if getattr(info, "disabled_by_default", False):
            fields["disabled_by"] = "integration"
        uid = f"{mac_upper}/{getattr(info, 'device_id', 0) or 0}/{platform}/{info.name}"
        plan.add_entity(domain, uid, info.name or None, **fields)
    return await commit(runtime, plan)


TASMOTA_ENERGY = ("Power", "Voltage", "Current", "Total", "Today", "Yesterday", "ApparentPower",
                  "ReactivePower", "Factor", "TotalStartTime")


async def adopt_tasmota(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                        user_input: dict[str, Any]) -> dict[str, Any]:
    hub, host = runtime.hub, str(finding.get("host") or "")
    password = str(user_input.get("password") or "") or None
    try:
        status = await tasmota_status(host, password)
    except (NeedsInput, FlowError):
        raise
    except Exception as exc:
        raise FlowError(f"The Tasmota device at {host} did not answer: {exc or type(exc).__name__}", 502) from exc
    mac = norm_mac((status.get("StatusNET") or {}).get("Mac"))
    if mac is None:
        raise FlowError("The Tasmota device did not report its MAC address", 502)
    mac_up = mac.upper()
    if _existing_entry(hub, "tasmota", mac_up):
        raise FlowError("This Tasmota device is already set up", 409)
    st = status.get("Status") or {}
    friendly = st.get("FriendlyName") or []
    title = st.get("DeviceName") or (friendly[0] if friendly else None) or finding.get("name") or host
    data: dict[str, Any] = {"host": host}
    if password:
        data.update({"username": "admin", "password": password})
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="tasmota", title=title, data=data, source="zeroconf",
                        unique_id=mac_up)
    plan = Plan(hub, entry)
    fw = (status.get("StatusFWR") or {}).get("Version")
    plan.set_device(title, name_by_user=name, area_id=area_id, manufacturer="Tasmota",
                    model=(status.get("StatusFWR") or {}).get("Hardware"), sw_version=fw,
                    connections=[["mac", ":".join(mac[i:i + 2] for i in range(0, 12, 2))]],
                    configuration_url=f"http://{host}/")
    sts = status.get("StatusSTS") or {}
    relays = sorted(int(k[5:] or 1) for k in sts if re.fullmatch(r"POWER\d*", k))
    for n in relays:
        label = friendly[n - 1] if 0 < n <= len(friendly) and len(relays) > 1 else None
        plan.add_entity("switch", f"{mac_up}_switch_relay_{n - 1}", label)
    energy = (status.get("StatusSNS") or {}).get("ENERGY") or {}
    for key in TASMOTA_ENERGY:
        if key in energy:
            plan.add_entity("sensor", f"{mac_up}_sensor_sensor_ENERGY_{key}", f"Energy {key}")
    return await commit(runtime, plan)


def normalize_matter_code(code: Any) -> str:
    """A QR payload (``MT:...``) or an 11/21 digit manual code, cleaned; FlowError otherwise."""
    raw = str(code or "").strip()
    if raw.upper().startswith("MT:"):
        cleaned = "MT:" + raw[3:].strip().upper()
        if MATTER_CODE_RE.match(cleaned):
            return cleaned
        raise FlowError("That does not look like a Matter QR code (MT:...).", 400)
    digits = re.sub(r"[\s-]", "", raw)
    if digits.isdigit() and len(digits) in (11, 21):
        return digits
    raise FlowError("Enter the Matter QR code text (MT:...) or the 11 or 21 digit pairing code.", 400)


def matter_driver(runtime: Any) -> Any:
    for entry in runtime.hub.config_entries.values():
        if entry.domain == "matter" and entry.source != "ignore":
            drv = runtime.hub.drivers.get(entry.entry_id)
            if drv is not None and hasattr(drv, "commission_with_code"):
                return drv
    raise FlowError("The Matter Server is not connected, so Matter devices cannot be added right now.", 503)


async def commission_matter(runtime: Any, code: Any, *, name: str | None = None,
                            area_id: str | None = None) -> dict[str, Any]:
    """Commission a Matter device with its setup code and register its entities."""
    from services.homehub.drivers.matter import node_device_info, node_entity_specs

    hub = runtime.hub
    clean = normalize_matter_code(code)
    area = _area(hub, area_id)
    driver = matter_driver(runtime)
    fabric = driver.compressed_fabric_id
    if fabric is None:
        raise FlowError("The Matter Server has not reported its fabric yet; try again in a minute.", 503)
    node = await driver.commission_with_code(clean)
    node_id = int(node["node_id"])
    info = node_device_info(fabric, node)
    plan = Plan(hub, driver.entry, new_entry=False)
    title = info.pop("name")
    plan.set_device(title, name_by_user=name, area_id=area, **info)
    for spec in node_entity_specs(fabric, node):
        spec = dict(spec)
        domain, uid, own = spec.pop("domain"), spec.pop("unique_id"), spec.pop("name")
        if any(e.unique_id == uid for e in hub.entities.values()):
            continue
        plan.add_entity(domain, uid, own, **spec)
    # The driver is already connected: register the records and let it pick
    # them up, rather than restarting the whole fabric's connection.
    result = await commit(runtime, plan, restart=False)
    driver.reindex(node_id)
    return {**result, "node_id": node_id}


async def adopt_matter(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                       user_input: dict[str, Any]) -> dict[str, Any]:
    code = user_input.get("code")
    if not code:
        raise NeedsInput([SETUP_CODE], "Enter the device's Matter setup code.")
    try:
        return await commission_matter(runtime, code, name=name, area_id=area_id)
    except FlowError as exc:
        if exc.status == 400:
            raise NeedsInput([SETUP_CODE], "Enter the device's Matter setup code.", str(exc)) from exc
        raise


FLOWS = {
    "wiz": adopt_wiz,
    "cast": adopt_cast,
    "roku": adopt_roku,
    "esphome": adopt_esphome,
    "tasmota": adopt_tasmota,
    "matter": adopt_matter,
}


async def adopt_response(runtime: Any, finding: dict[str, Any], body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Run a flow and turn its outcome into ``(HTTP status, body)`` for the admin API."""
    try:
        result = await adopt(runtime, finding, body)
    except NeedsInput as need:
        out: dict[str, Any] = {"status": "needs_input", "message": need.message, "error": need.error,
                               "needs": need.needs}
        if need.session:
            out["session"] = need.session
        return 200, out
    except Pending as wait:
        return 200, {"status": "pending", "message": wait.message, "session": wait.session, "step": wait.step,
                     "poll_ms": wait.poll_ms}
    except HubError as exc:
        status = int(getattr(exc, "status", 400) or 400)
        return status, {"status": "unsupported" if status == 501 else "error", "message": str(exc)}
    return 200, result
