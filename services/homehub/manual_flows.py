"""Adding by hand: a config flow for every integration the hub drives that ``pairing.MANUAL`` does not cover.

``pairing.MANUAL`` adds devices by address (TVs, cameras, ESPHome...). This
module covers the rest, so every driver in ``drivers/__init__.py`` can be set
up from the Devices page without HA:

| Integration | What the admin enters | What is created (HA's data keys and unique_ids) |
|---|---|---|
| cast | address | the device's Cast UUID is read from ``:8008/setup/eureka_info``; then as discovery's Cast flow |
| homekit_controller | address | the accessory's own mDNS record (discovery's); then the HomeKit pairing flow |
| matter | setup code | commissioned through the Matter Server |
| mjpeg | stream URL, still URL, login | entry ``{mjpeg_url, still_image_url, username, password, authentication, verify_ssl}``; camera ``{entry_id}`` |
| music_assistant | server URL, token | entry ``{url, token}`` (unique_id = server id); one media_player per MA player, read once it connects |
| go2rtc | API URL, login | entry ``{url, username, password}`` (one per hub) |
| emporia_vue | email, password | entry ``{email, password, enable_*}``; per circuit ``sensor.emporia_vue.{scale}.{gid}-{channel}`` |
| srp_energy | account, login, time-of-use | entry ``{id, is_tou, name, username, password}``; sensor ``{entry_id}_total_usage`` |
| roborock | email, then the emailed code | entry ``{username, user_data, base_url}``; one vacuum ``{duid}`` per vacuum, read once it connects |
| sun | nothing | ``sun.sun`` and HA's sun sensors ``{entry_id}-{key}`` (one per hub) |
| open_meteo | a zone | weather ``{zone}`` for that zone |
| nws | contact email, location, station | weather ``{lat}_{lon}`` and the observation sensors ``{lat}_{lon}_{key}`` |
| local_todo | a list name | todo ``{slug}`` |
| shopping_list | nothing | todo ``shopping_list`` (one per hub) |
| switch_as_x | a switch, a kind | the switch shown as a light, fan, cover, lock, siren or valve (unique_id = entry_id) |

The device transports are module functions so the tests can replace them.
``mobile_app`` is not here on purpose: a phone adds itself when its app signs in.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

from services.homehub.core import ConfigEntry
from services.homehub.flows import (
    FlowError,
    NeedsInput,
    Plan,
    _existing_entry,
    _new_entry_id,
    commit,
    slugify,
)

log = logging.getLogger("homehub.manual_flows")

HOST = {"key": "host", "label": "Address", "type": "text", "help": "The device's IP address or host name."}
USERNAME_OPT = {"key": "username", "label": "Username", "type": "text", "optional": True}
PASSWORD_OPT = {"key": "password", "label": "Password", "type": "password", "optional": True}
EMAIL = {"key": "email", "label": "Email", "type": "text"}
PASSWORD = {"key": "password", "label": "Password", "type": "password"}

SWITCH_AS_X_DOMAINS = ("light", "fan", "cover", "lock", "siren", "valve")


def _zone_options(hub: Any) -> list[dict[str, str]]:
    return [{"value": eid, "label": hub.states.get(eid).name} for eid in sorted(hub.states.entity_ids())
            if eid.startswith("zone.")]


def _switch_options(hub: Any) -> list[dict[str, str]]:
    return [{"value": eid, "label": hub.states.get(eid).name if hub.states.get(eid) else eid}
            for eid in sorted(hub.entities) if eid.startswith("switch.")]


#: ``{domain: {label, kind, fields, single?}}``. ``fields`` may be a function of the hub
#: (choices read from the registry: zones, switches).
SPECS: dict[str, dict[str, Any]] = {
    "cast": {"label": "Google Cast device", "kind": "local", "fields": [HOST]},
    "homekit_controller": {"label": "HomeKit accessory", "kind": "local", "fields": [HOST]},
    "matter": {"label": "Matter device", "kind": "local", "fields": [
        {"key": "code", "label": "Setup code", "type": "text",
         "help": "The QR code's text (MT:...) or the 11 or 21 digit pairing code on the device or its box."}]},
    "mjpeg": {"label": "MJPEG IP camera", "kind": "local", "fields": [
        {"key": "mjpeg_url", "label": "Stream URL", "type": "text", "help": "The camera's MJPEG stream (http://...)."},
        {"key": "still_image_url", "label": "Snapshot URL", "type": "text", "optional": True,
         "help": "A single JPEG, if the camera has one."},
        USERNAME_OPT, PASSWORD_OPT]},
    "music_assistant": {"label": "Music Assistant server", "kind": "service", "fields": [
        {"key": "url", "label": "Server URL", "type": "text", "help": "Where Music Assistant runs, e.g. http://<server>:8095"},
        {"key": "token", "label": "Token", "type": "password", "optional": True,
         "help": "A long-lived token from Music Assistant's settings, when its server asks for one."}]},
    "go2rtc": {"label": "go2rtc (live camera video)", "kind": "service", "single": True, "fields": [
        {"key": "url", "label": "API URL", "type": "text", "help": "go2rtc's API, e.g. http://127.0.0.1:1984"},
        USERNAME_OPT, PASSWORD_OPT]},
    "emporia_vue": {"label": "Emporia Vue energy monitor", "kind": "cloud", "fields": [EMAIL, PASSWORD]},
    "srp_energy": {"label": "SRP energy usage", "kind": "cloud", "fields": [
        {"key": "id", "label": "Billing account", "type": "text", "help": "The 9-digit account number on the bill."},
        {"key": "username", "label": "Username", "type": "text"}, PASSWORD,
        {"key": "is_tou", "label": "Time-of-use plan", "type": "select", "optional": True,
         "options": [{"value": "", "label": "No"}, {"value": "1", "label": "Yes"}]}]},
    "roborock": {"label": "Roborock account", "kind": "cloud", "fields": [EMAIL]},
    "sun": {"label": "Sun", "kind": "helper", "single": True, "fields": []},
    "open_meteo": {"label": "Open-Meteo weather", "kind": "helper", "fields": lambda hub: [
        {"key": "zone", "label": "Zone", "type": "select", "options": _zone_options(hub) or
         [{"value": "zone.home", "label": "Home"}]}]},
    "nws": {"label": "National Weather Service", "kind": "helper", "fields": [
        {"key": "api_key", "label": "Contact email", "type": "text",
         "help": "The NWS asks every client for a contact; it is sent in the User-Agent."},
        {"key": "latitude", "label": "Latitude", "type": "text", "optional": True, "help": "Empty: the home's."},
        {"key": "longitude", "label": "Longitude", "type": "text", "optional": True, "help": "Empty: the home's."},
        {"key": "station", "label": "Station", "type": "text", "optional": True,
         "help": "An observation station code; empty picks the nearest."}]},
    "local_todo": {"label": "To-do list", "kind": "helper", "fields": [
        {"key": "todo_list_name", "label": "List name", "type": "text"}]},
    "shopping_list": {"label": "Shopping list", "kind": "helper", "single": True, "fields": []},
    "switch_as_x": {"label": "Switch as another device", "kind": "helper", "fields": lambda hub: [
        {"key": "entity_id", "label": "Switch", "type": "select", "options": _switch_options(hub)},
        {"key": "target_domain", "label": "Show it as", "type": "select",
         "options": [{"value": d, "label": d.title()} for d in SWITCH_AS_X_DOMAINS]},
        {"key": "invert", "label": "Inverted", "type": "select", "optional": True,
         "options": [{"value": "", "label": "No"}, {"value": "1", "label": "Yes"}]}]},
}
#: Domains whose devices need no address (the finding is a placeholder).
NO_ADDRESS = {"matter", "emporia_vue", "srp_energy", "roborock", "sun", "open_meteo", "nws", "local_todo",
              "shopping_list", "switch_as_x", "music_assistant", "go2rtc", "mjpeg"}
#: Discovery labels its findings with these when it has one.
KINDS = {"local": "On the network", "service": "Servers and services", "cloud": "Cloud accounts", "helper": "Helpers"}


def fields_for(hub: Any, domain: str) -> list[dict[str, Any]]:
    spec = SPECS[domain]
    fields = spec["fields"]
    return list(fields(hub) if callable(fields) else fields)


def all_manual() -> set[str]:
    from services.homehub import pairing

    return set(pairing.MANUAL) | set(SPECS)


def manual_list(hub: Any) -> list[dict[str, Any]]:
    """Every integration that can be added by hand, with its fields, for ``GET /hub/flows``."""
    from services.homehub import pairing

    out = [{"domain": d, "kind": "service" if d == "esphome_builder" else "local", **spec}
           for d, spec in pairing.MANUAL.items()]
    for d, spec in SPECS.items():
        out.append({"domain": d, "label": spec["label"], "kind": spec["kind"], "fields": fields_for(hub, d),
                    "single": bool(spec.get("single")),
                    "configured": bool(spec.get("single")) and any(
                        e.domain == d and e.source != "ignore" for e in hub.config_entries.values())})
    order = list(KINDS)
    out.sort(key=lambda f: (order.index(f["kind"]) if f["kind"] in order else 9, str(f["label"]).lower()))
    return out


def manual_finding(domain: str, body: dict[str, Any]) -> dict[str, Any]:
    """A placeholder finding for an integration added by hand; ``pairing.manual_finding`` for the rest."""
    from services.homehub import pairing

    if domain not in SPECS:
        return pairing.manual_finding(domain, body)
    user_input = body.get("input") if isinstance(body.get("input"), dict) else {}
    host = str(body.get("host") or user_input.get("host") or "").strip()
    if domain not in NO_ADDRESS:
        if not host:
            raise FlowError("Enter the device's address", 400)
        if not re.fullmatch(r"[A-Za-z0-9._:\-\[\]]{1,253}", host):
            raise FlowError("That does not look like an address", 400)
    key = re.sub(r"[^a-z0-9_.:-]+", "-", (host or domain).lower()).strip("-")
    return {"id": f"manual:{domain}:{key}", "integration": domain, "name": None, "model": None,
            "manufacturer": None, "host": host or None, "port": None, "mac": None, "ids": [], "source": "user",
            "props": {}}


def needs_for(finding: dict[str, Any]) -> list[dict[str, Any]] | None:
    """What a discovered device of these integrations needs, or None when this module has no opinion."""
    integration = finding.get("integration")
    if integration == "roborock":
        return [EMAIL]
    if integration == "music_assistant":
        return []
    return None


def _required(user_input: dict[str, Any], fields: list[dict[str, Any]], message: str) -> dict[str, str]:
    values = {f["key"]: str(user_input.get(f["key"]) or "").strip() for f in fields}
    missing = [f for f in fields if not f.get("optional") and not values[f["key"]]]
    if missing:
        raise NeedsInput(fields, message, "Fill in " + ", ".join(f["label"] for f in missing) + ".")
    return values


def _single(hub: Any, domain: str, label: str) -> None:
    if any(e.domain == domain and e.source != "ignore" for e in hub.config_entries.values()):
        raise FlowError(f"{label} is already set up (one per hub)", 409)


def _url(value: str, what: str) -> str:
    u = urlparse(value)
    if u.scheme not in ("http", "https", "rtsp") or not u.hostname:
        raise FlowError(f"Enter the {what} as a full URL (http://...)", 400)
    return value.rstrip("/") if u.scheme != "rtsp" else value


# -- transports (the tests replace these) ------------------------------------------------

async def http_get(url: str, *, auth: tuple[str, str] | None = None, read: int = 64 * 1024,
                   timeout: float = 8.0) -> tuple[int, str, bytes]:
    """``(status, content type, first bytes)``."""
    import aiohttp

    basic = aiohttp.BasicAuth(*auth) if auth else None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session, \
            session.get(url, auth=basic) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), await resp.content.read(read)


async def cast_info(host: str) -> dict[str, Any]:
    import json

    status, _ctype, body = await http_get(f"http://{host}:8008/setup/eureka_info?params=name,device_info")
    if status != 200:
        raise FlowError(f"The Cast device at {host} answered HTTP {status}", 502)
    return json.loads(body.decode("utf-8", "replace"))


async def srp_login(account: str, username: str, password: str) -> bool:
    from srpenergy.client import SrpEnergyClient

    client = SrpEnergyClient(account, username, password)
    return bool(await asyncio.to_thread(client.validate))


async def emporia_login(email: str, password: str) -> list[Any]:
    """Log in and return the account's devices (pyemvue's), or raise FlowError."""
    from pyemvue import PyEmVue

    vue = PyEmVue()
    ok = await asyncio.to_thread(vue.login, email, password)
    if not ok:
        raise NeedsInput([EMAIL, PASSWORD], "Sign in to Emporia.", "Emporia did not accept that login.")
    return list(await asyncio.to_thread(vue.get_devices))


async def roborock_request_code(email: str) -> None:
    from roborock.web_api import RoborockApiClient

    await RoborockApiClient(username=email).request_code()


async def roborock_code_login(email: str, code: str) -> tuple[dict[str, Any], str | None]:
    from roborock.web_api import RoborockApiClient

    client = RoborockApiClient(username=email)
    user_data = await client.code_login(code)
    base_url = await client.base_url  # an async property: the region the account lives in
    return user_data.as_dict(), base_url


# -- the flows ---------------------------------------------------------------------------

async def adopt_cast_by_host(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                             user_input: dict[str, Any]) -> dict[str, Any]:
    from services.homehub.flows import adopt_cast

    host = str(finding.get("host") or "")
    try:
        info = await cast_info(host)
    except FlowError:
        raise
    except Exception as exc:
        raise FlowError(f"The Cast device at {host} did not answer: {exc or type(exc).__name__}", 502) from exc
    uuid = str(info.get("ssdp_udn") or "").removeprefix("uuid:")
    if not uuid:
        raise FlowError(f"The device at {host} did not report a Cast id; is it a Cast device?", 502)
    dev = info.get("device_info") if isinstance(info.get("device_info"), dict) else {}
    found = {**finding, "name": info.get("name") or finding.get("name"), "model": dev.get("model_name"),
             "manufacturer": dev.get("manufacturer"), "ids": [uuid], "props": {"uuid": uuid}}
    return await adopt_cast(runtime, found, name, area_id, user_input)


async def adopt_homekit_by_host(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                                user_input: dict[str, Any]) -> dict[str, Any]:
    """HomeKit pairs with the accessory's own announcement; find it by address among discovery's findings."""
    from services.homehub import pairing

    host = str(finding.get("host") or "").lower()
    mgr = getattr(runtime, "discovery", None)
    match = None
    if mgr is not None:
        match = next((f for f in mgr.findings.values() if f.get("integration") == "homekit_controller"
                      and str(f.get("host") or "").lower() == host), None)
    if match is None:
        raise FlowError(f"No HomeKit accessory ready to pair is announcing itself at {host}. Reset it or put it "
                        "in pairing mode on this network, wait a minute, then try again.", 404)
    return await pairing.adopt_homekit(runtime, match, name, area_id, user_input)


async def adopt_matter_by_code(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                               user_input: dict[str, Any]) -> dict[str, Any]:
    from services.homehub.flows import adopt_matter

    return await adopt_matter(runtime, finding, name, area_id, user_input)


async def adopt_mjpeg(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                      user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    fields = fields_for(hub, "mjpeg")
    v = _required(user_input, fields, "Enter the camera's stream URL.")
    stream = _url(v["mjpeg_url"], "stream URL")
    still = _url(v["still_image_url"], "snapshot URL") if v["still_image_url"] else ""
    auth = (v["username"], v["password"]) if v["username"] else None
    probe = still or stream
    try:
        status, ctype, body = await http_get(probe, auth=auth, read=4096)
    except Exception as exc:
        raise FlowError(f"The camera did not answer at {urlparse(probe).hostname}: {exc or type(exc).__name__}",
                        502) from exc
    if status == 401:
        raise NeedsInput(fields, "The camera asks for a login.",
                         "That login was not accepted." if auth else None)
    if status != 200:
        raise FlowError(f"The camera answered HTTP {status}", 502)
    if "image" not in ctype and "multipart" not in ctype and not body.startswith(b"\xff\xd8"):
        raise FlowError(f"That URL answered with {ctype or 'something'} rather than a picture or an MJPEG stream",
                        400)
    title = name or urlparse(stream).hostname or "Camera"
    data: dict[str, Any] = {"mjpeg_url": stream, "still_image_url": still or None, "authentication": "basic",
                            "verify_ssl": True}
    if auth:
        data.update({"username": auth[0], "password": auth[1]})
    if _existing_entry(hub, "mjpeg", stream):
        raise FlowError("This camera is already set up", 409)
    entry_id = _new_entry_id()
    entry = ConfigEntry(entry_id=entry_id, domain="mjpeg", title=title, data=data, source="user", unique_id=stream)
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=None, area_id=area_id, manufacturer=None, model="MJPEG camera",
                    identifiers=[["mjpeg", entry_id]])
    plan.add_entity("camera", entry_id, None)
    return await commit(runtime, plan)


async def adopt_music_assistant(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                                user_input: dict[str, Any]) -> dict[str, Any]:
    import json

    from services.homehub import hardware

    hub = runtime.hub
    url = str(user_input.get("url") or (finding.get("props") or {}).get("url") or "").strip()
    if not url:
        raise NeedsInput(fields_for(hub, "music_assistant"), "Enter the Music Assistant server's URL.")
    url = _url(url, "server URL")
    try:
        status, _ctype, body = await http_get(f"{url}/info")
    except Exception as exc:
        raise FlowError(f"Music Assistant did not answer at {url}: {exc or type(exc).__name__}", 502) from exc
    if status != 200:
        raise FlowError(f"{url} answered HTTP {status}; is it a Music Assistant server?", 502)
    try:
        info = json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise FlowError(f"{url} did not answer like a Music Assistant server", 502) from exc
    server_id = str(info.get("server_id") or "")
    if not server_id:
        raise FlowError(f"{url} did not report a Music Assistant server id", 502)
    if _existing_entry(hub, "music_assistant", server_id):
        raise FlowError("This Music Assistant server is already set up", 409)
    data: dict[str, Any] = {"url": url}
    token = str(user_input.get("token") or "").strip()
    if token:
        data["token"] = token
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="music_assistant", title=name or "Music Assistant",
                        data=data, source="user" if finding.get("source") == "user" else "zeroconf",
                        unique_id=server_id)
    result = await commit(runtime, Plan(hub, entry))
    sync = await hardware.sync_after_load(runtime, entry.entry_id)
    return {**result, "entities": [*result["entities"], *sync.get("entities", [])], "sync": sync}


async def adopt_go2rtc(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                       user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    _single(hub, "go2rtc", "go2rtc")
    v = _required(user_input, fields_for(hub, "go2rtc"), "Enter go2rtc's API URL.")
    url = _url(v["url"], "API URL")
    auth = (v["username"], v["password"]) if v["username"] else None
    try:
        status, _ctype, _body = await http_get(f"{url}/api", auth=auth)
    except Exception as exc:
        raise FlowError(f"go2rtc did not answer at {url}: {exc or type(exc).__name__}", 502) from exc
    if status == 401:
        raise NeedsInput(fields_for(hub, "go2rtc"), "go2rtc asks for a login.",
                         "That login was not accepted." if auth else None)
    if status != 200:
        raise FlowError(f"{url} answered HTTP {status}; is it go2rtc's API?", 502)
    data: dict[str, Any] = {"url": url}
    if auth:
        data.update({"username": auth[0], "password": auth[1]})
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="go2rtc", title=name or "go2rtc", data=data,
                        source="user", unique_id="go2rtc")
    return await commit(runtime, Plan(hub, entry))


async def adopt_emporia(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                        user_input: dict[str, Any]) -> dict[str, Any]:
    from services.homehub import hardware

    hub = runtime.hub
    v = _required(user_input, [EMAIL, PASSWORD], "Sign in to Emporia.")
    if _existing_entry(hub, "emporia_vue", v["email"].lower()):
        raise FlowError("This Emporia account is already set up", 409)
    try:
        devices = await emporia_login(v["email"], v["password"])
    except (NeedsInput, FlowError):
        raise
    except Exception as exc:
        raise FlowError(f"Emporia's cloud did not answer: {exc or type(exc).__name__}", 502) from exc
    if not devices:
        raise FlowError("This Emporia account has no devices", 400)
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="emporia_vue", title=name or v["email"],
                        data={"email": v["email"], "password": v["password"], "enable_1m": True, "enable_1d": True,
                              "enable_1mon": True}, source="user", unique_id=v["email"].lower())
    merged: dict[int, Any] = {}
    for dev in devices:
        gid = int(dev.device_gid)
        if gid in merged:
            merged[gid].channels += dev.channels
        else:
            merged[gid] = dev
    plan = hardware._sync_emporia(runtime, entry, type("Listing", (), {"devices": merged})())
    plan.new_entry = True
    for dev in plan.devices:
        if area_id and not dev.area_id:
            dev.area_id = area_id
    return await commit(runtime, plan)


async def adopt_srp(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                    user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    fields = fields_for(hub, "srp_energy")
    v = _required(user_input, fields, "Sign in to SRP.")
    if not re.fullmatch(r"\d{9}", v["id"]):
        raise NeedsInput(fields, "Sign in to SRP.", "The billing account is the 9-digit number on the bill.")
    if _existing_entry(hub, "srp_energy", v["id"]):
        raise FlowError("This SRP account is already set up", 409)
    try:
        ok = await srp_login(v["id"], v["username"], v["password"])
    except Exception as exc:
        raise FlowError(f"SRP did not answer: {exc or type(exc).__name__}", 502) from exc
    if not ok:
        raise NeedsInput(fields, "Sign in to SRP.", "SRP did not accept that login for this account.")
    title = name or f"SRP account {v['id'][-4:]}"
    entry_id = _new_entry_id()
    entry = ConfigEntry(entry_id=entry_id, domain="srp_energy", title=title, source="user", unique_id=v["id"],
                        data={"id": v["id"], "is_tou": bool(v["is_tou"]), "name": title, "username": v["username"],
                              "password": v["password"]})
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=None, area_id=area_id, manufacturer="SRP", model="Energy usage",
                    entry_type="service", identifiers=[["srp_energy", entry_id]])
    plan.add_entity("sensor", f"{entry_id}_total_usage", "Energy usage", original_device_class="energy",
                    unit_of_measurement="kWh", capabilities={"state_class": "total_increasing"})
    return await commit(runtime, plan)


async def adopt_roborock(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                         user_input: dict[str, Any]) -> dict[str, Any]:
    """Email, then the code Roborock emails (HA's flow); the vacuums are read once the account connects."""
    from services.homehub import hardware

    hub = runtime.hub
    email = str(user_input.get("email") or "").strip()
    code = str(user_input.get("code") or "").strip()
    if not email:
        raise NeedsInput([EMAIL], "Enter the email of the Roborock app account.")
    if _existing_entry(hub, "roborock", email.lower()):
        raise FlowError("This Roborock account is already set up", 409)
    code_field = {"key": "code", "label": "Code from the email", "type": "text",
                  "help": "Roborock emailed a code to this address."}
    if not code:
        try:
            await roborock_request_code(email)
        except Exception as exc:
            raise FlowError(f"Roborock could not send a code: {exc or type(exc).__name__}", 502) from exc
        raise NeedsInput([EMAIL, code_field], f"Roborock emailed a code to {email}. Enter it here.")
    try:
        user_data, base_url = await roborock_code_login(email, code)
    except Exception as exc:
        name_ = type(exc).__name__
        if "Code" in name_ or "Invalid" in name_:
            raise NeedsInput([EMAIL, code_field], "Enter the code Roborock emailed.",
                             "That code was not accepted.") from exc
        raise FlowError(f"Roborock did not answer: {exc or name_}", 502) from exc
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="roborock", title=name or email, source="user",
                        unique_id=email.lower(), data={"username": email, "user_data": user_data, "base_url": base_url})
    result = await commit(runtime, Plan(hub, entry))
    sync = await hardware.sync_after_load(runtime, entry.entry_id)
    return {**result, "entities": [*result["entities"], *sync.get("entities", [])], "sync": sync}


SUN_SENSORS = (("next_dawn", "Next dawn", "timestamp"), ("next_dusk", "Next dusk", "timestamp"),
               ("next_midnight", "Next midnight", "timestamp"), ("next_noon", "Next noon", "timestamp"),
               ("next_rising", "Next rising", "timestamp"), ("next_setting", "Next setting", "timestamp"),
               ("solar_elevation", "Solar elevation", None), ("solar_azimuth", "Solar azimuth", None),
               ("solar_rising", "Solar rising", None))


async def adopt_sun(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                    user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    _single(hub, "sun", "Sun")
    entry_id = _new_entry_id()
    entry = ConfigEntry(entry_id=entry_id, domain="sun", title="Sun", data={}, source="user")
    plan = Plan(hub, entry)
    plan.set_device("Sun", name_by_user=name, area_id=None, entry_type="service", identifiers=[["sun", entry_id]])
    for key, label, dclass in SUN_SENSORS:
        plan.add_entity("sensor", f"{entry_id}-{key}", label, entity_category="diagnostic",
                        **({"original_device_class": dclass} if dclass else {}))
    return await commit(runtime, plan)


async def adopt_open_meteo(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                           user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    zone = str(user_input.get("zone") or "zone.home").strip()
    if not zone.startswith("zone."):
        raise FlowError("Choose a zone", 400)
    if _existing_entry(hub, "open_meteo", zone):
        raise FlowError("Open-Meteo is already set up for this zone", 409)
    st = hub.states.get(zone)
    if st is None and not (hub.location or {}).get("latitude"):
        raise FlowError(f"{zone} has no location, and neither has the home", 400)
    title = name or (st.name if st else "Home")
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="open_meteo", title=title, data={"zone": zone},
                        source="user", unique_id=zone)
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=None, area_id=area_id, manufacturer="Open-Meteo", entry_type="service",
                    identifiers=[["open_meteo", zone]])
    plan.add_entity("weather", zone, None)
    return await commit(runtime, plan)


NWS_OBSERVATIONS = ("temperature", "dewpoint", "windChill", "heatIndex", "relativeHumidity", "windSpeed",
                    "windGust", "windDirection", "barometricPressure", "seaLevelPressure", "visibility")


async def adopt_nws(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                    user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    fields = fields_for(hub, "nws")
    v = _required(user_input, fields, "Enter a contact email for the National Weather Service.")
    loc = hub.location or {}
    try:
        lat = float(v["latitude"] or loc.get("latitude"))
        lon = float(v["longitude"] or loc.get("longitude"))
    except (TypeError, ValueError) as exc:
        raise NeedsInput(fields, "Enter the location.", "The home has no location; enter a latitude and longitude."
                         ) from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise NeedsInput(fields, "Enter the location.", "That is not a latitude and longitude.")
    base = f"{lat}_{lon}"
    if _existing_entry(hub, "nws", base):
        raise FlowError("The National Weather Service is already set up for this location", 409)
    data: dict[str, Any] = {"api_key": v["api_key"], "location": {"latitude": lat, "longitude": lon}}
    if v["station"]:
        data["station"] = v["station"].upper()
    title = name or "National Weather Service"
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="nws", title=title, data=data, source="user",
                        unique_id=base)
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=None, area_id=area_id, manufacturer="National Weather Service",
                    entry_type="service", identifiers=[["nws", base]])
    plan.add_entity("weather", base, None)
    for key in NWS_OBSERVATIONS:
        label = re.sub(r"(?<!^)([A-Z])", r" \1", key).capitalize()
        plan.add_entity("sensor", f"{base}_{key}", label, disabled_by=None if key in (
            "temperature", "relativeHumidity", "windSpeed") else "integration")
    return await commit(runtime, plan)


async def adopt_local_todo(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                           user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    list_name = str(user_input.get("todo_list_name") or name or "").strip()
    if not list_name:
        raise NeedsInput(fields_for(hub, "local_todo"), "Name the list.")
    key = slugify(list_name)
    if _existing_entry(hub, "local_todo", key):
        raise FlowError(f"There is already a list called {list_name}", 409)
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="local_todo", title=list_name,
                        data={"todo_list_name": list_name, "storage_key": key, "ics": ""}, source="user",
                        unique_id=key)
    plan = Plan(hub, entry)
    plan.set_device(list_name, name_by_user=None, area_id=area_id, entry_type="service",
                    identifiers=[["local_todo", key]])
    plan.add_entity("todo", key, None)
    return await commit(runtime, plan)


async def adopt_shopping_list(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                              user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    _single(hub, "shopping_list", "The shopping list")
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="shopping_list", title="Shopping list",
                        data={"items": []}, source="user", unique_id="shopping_list")
    plan = Plan(hub, entry)
    ent = plan.add_entity("todo", "shopping_list", None)
    ent.original_name, ent.has_entity_name = "Shopping list", False
    return await commit(runtime, plan)


async def adopt_switch_as_x(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                            user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    fields = fields_for(hub, "switch_as_x")
    v = _required(user_input, fields, "Choose the switch and what to show it as.")
    source = v["entity_id"]
    if source not in hub.entities or not source.startswith("switch."):
        raise NeedsInput(fields, "Choose the switch and what to show it as.", f"There is no switch {source}.")
    if v["target_domain"] not in SWITCH_AS_X_DOMAINS:
        raise NeedsInput(fields, "Choose the switch and what to show it as.", "Choose what to show it as.")
    for e in hub.config_entries.values():
        if e.domain == "switch_as_x" and {**e.data, **e.options}.get("entity_id") == source:
            raise FlowError(f"{source} is already shown as another device", 409)
    wrapped = hub.entities[source]
    entry_id = _new_entry_id()
    title = name or hub.friendly_name(wrapped)
    entry = ConfigEntry(entry_id=entry_id, domain="switch_as_x", title=title, source="user",
                        data={}, options={"entity_id": source, "target_domain": v["target_domain"],
                                          "invert": bool(v["invert"])})
    plan = Plan(hub, entry)
    dev = hub.devices.get(wrapped.device_id or "")
    ent = plan.add_entity(v["target_domain"], entry_id, None, device=dev)
    if dev is None:
        ent.original_name, ent.has_entity_name, ent.area_id = title, False, area_id
    return await commit(runtime, plan)


FLOWS = {
    "cast": adopt_cast_by_host,
    "homekit_controller": adopt_homekit_by_host,
    "matter": adopt_matter_by_code,
    "mjpeg": adopt_mjpeg,
    "music_assistant": adopt_music_assistant,
    "go2rtc": adopt_go2rtc,
    "emporia_vue": adopt_emporia,
    "srp_energy": adopt_srp,
    "roborock": adopt_roborock,
    "sun": adopt_sun,
    "open_meteo": adopt_open_meteo,
    "nws": adopt_nws,
    "local_todo": adopt_local_todo,
    "shopping_list": adopt_shopping_list,
    "switch_as_x": adopt_switch_as_x,
}

#: Discovered findings of these go through the flows above (the manual ones replace the address-only ones).
DISCOVERED_FLOWS = {"music_assistant": adopt_music_assistant, "roborock": adopt_roborock}


def flow_for(integration: str, source: str | None) -> Any:
    """The flow for a finding: a hand-added cast/homekit/matter uses this module's, discovered ones flows'."""
    if source == "user" and integration in FLOWS:
        return FLOWS[integration]
    return DISCOVERED_FLOWS.get(integration)

