"""Pairing flows: devices that need a code or a person's "Allow" before the hub may drive them.

Each flow uses the library HA's own integration uses, stores what HA stored in
the config entry, and creates the entities with HA's unique_id formats, so a
device paired here looks exactly like one HA paired:

| Integration | Library | What the person does | Entry data (as HA) | Entities (unique_id) |
|---|---|---|---|---|
| homekit_controller | aiohomekit | types the 8-digit setup code | aiohomekit's pairing dict | one per HAP service, ``{pairing_id}_{aid}_{iid}`` (``drivers/homekit_services.py``) |
| androidtv_remote | androidtvremote2 | types the code the TV shows | ``host, name, mac, cert_pem, key_pem`` | media_player and remote ``{mac}`` |
| samsungtv | samsungtvws | presses Allow (websocket, legacy) or types the PIN (2014-15 encrypted) | ``host, method, port, token[, session_id]`` | media_player and remote ``{uuid}`` |
| webostv | aiowebostv | presses Accept | ``host, client_secret`` | media_player ``{deviceUUID}`` |
| dlna_dmr | (UPnP description) | nothing | ``url, device_id, device_type`` | media_player ``{udn}`` |
| reolink | reolink-aio | types the login | ``host, port, use_https, username, password, ...`` | per channel, ``{host_uid}_{camera_uid}_{key}`` |
| esphome_builder | (HTTP) | types the URL (and login) | ``url[, username, password]`` | none |

A pairing that spans requests (the TV shows a code and waits for it; the TV
asks "Allow?" and nobody has answered yet) is a **session**: an id the caller
sends back as ``input.session``. Sessions live in memory for
``SESSION_TTL_S`` and are bound to the finding they started from.

Nothing here names a model, a vendor product or an address: entities come
from what the device reports (HAP services, Reolink capabilities, UPnP
descriptions), and every address comes from discovery or the admin.

The device transports are module functions so the tests can replace them.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, NoReturn
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from services.homehub.core import ConfigEntry
from services.homehub.discovery import norm_mac
from services.homehub.flows import (
    FlowError,
    NeedsInput,
    Pending,
    Plan,
    _existing_entry,
    _new_entry_id,
    commit,
)

log = logging.getLogger("homehub.pairing")

#: A session not finished within this long is dropped (the TV's own prompt times out sooner).
SESSION_TTL_S = 300.0
#: How long a TV's Allow prompt is waited for in the background.
ACCEPT_TIMEOUT_S = 120.0
HOMEKIT_TIMEOUT_S = 60.0

HOMEKIT_CODE = {"key": "code", "label": "HomeKit setup code", "type": "text",
                "help": "The 8-digit code on the accessory, its manual or its box (123-45-678)."}
TV_CODE = {"key": "pin", "label": "Code on the TV", "type": "text",
           "help": "The code the TV is showing now."}
USERNAME = {"key": "username", "label": "Username", "type": "text"}
PASSWORD = {"key": "password", "label": "Password", "type": "password"}
HOST = {"key": "host", "label": "Address", "type": "text", "help": "The device's IP address or host name."}
URL = {"key": "url", "label": "URL", "type": "text"}
DLNA_URL = {**URL, "help": "The renderer's UPnP description URL (http://.../description.xml)."}
BUILDER_URL = {**URL, "help": "Where admins open the ESPHome Device Builder, e.g. http://<server>:6052/"}

ATV_ID = "androidtv_remote"


# -- sessions ---------------------------------------------------------------------------

@dataclass
class Session:
    id: str
    integration: str
    finding_id: str
    created: float = field(default_factory=time.monotonic)
    #: A background job waiting for a person (the TV's Allow prompt).
    task: asyncio.Task | None = None
    #: A library object holding an open pairing (waiting for the code on the screen).
    obj: Any = None
    data: dict[str, Any] = field(default_factory=dict)


SESSIONS: dict[str, Session] = {}


def _expire() -> None:
    now = time.monotonic()
    for sid, s in list(SESSIONS.items()):
        if now - s.created > SESSION_TTL_S:
            drop_session(sid)


def drop_session(sid: str) -> None:
    s = SESSIONS.pop(sid, None)
    if s is None:
        return
    if s.task is not None and not s.task.done():
        s.task.cancel()
    obj = s.obj
    if obj is not None:
        for name in ("disconnect", "close"):
            fn = getattr(obj, name, None)
            if fn is None:
                continue
            with contextlib.suppress(Exception):
                result = fn()
                if asyncio.iscoroutine(result):
                    task = asyncio.ensure_future(result)
                    task.add_done_callback(lambda t: t.cancelled() or t.exception())
            break
    tmp = s.data.get("tmpdir")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


def new_session(integration: str, finding: dict[str, Any], *, obj: Any = None, job: Any = None,
                data: dict[str, Any] | None = None) -> Session:
    _expire()
    s = Session(id=secrets.token_urlsafe(12), integration=integration, finding_id=str(finding.get("id") or ""),
                obj=obj, data=dict(data or {}))
    if job is not None:
        s.task = asyncio.ensure_future(job)
        # Read the outcome so an unawaited failure is not logged as "never retrieved".
        s.task.add_done_callback(lambda t: t.cancelled() or t.exception())
    SESSIONS[s.id] = s
    return s


def session_for(integration: str, finding: dict[str, Any], user_input: dict[str, Any]) -> Session | None:
    """The session the caller sent back, or None when it did not send one. 410 when it is gone."""
    sid = str(user_input.get("session") or "")
    if not sid:
        return None
    _expire()
    s = SESSIONS.get(sid)
    if s is None or s.integration != integration or s.finding_id != str(finding.get("id") or ""):
        raise FlowError("That pairing has expired or was cancelled. Start again.", 410)
    return s


def waiting(s: Session, message: str, step: str = "accept_on_tv") -> None:
    """Raise Pending while the session's job runs; return when it has finished."""
    if s.task is not None and not s.task.done():
        raise Pending(s.id, message, step)


# -- needs (what discovery can tell up front) ----------------------------------------------

def needs_for(finding: dict[str, Any]) -> list[dict[str, Any]]:
    integration = finding.get("integration")
    if integration == "homekit_controller":
        return [HOMEKIT_CODE]
    if integration == "reolink":
        return [USERNAME, PASSWORD]
    if integration == "dlna_dmr" and not (finding.get("props") or {}).get("location"):
        return [DLNA_URL]
    return []


#: Integrations that can be added by address, with what the admin types (``POST /hub/flows/{domain}``).
MANUAL: dict[str, dict[str, Any]] = {
    "androidtv_remote": {"label": "Android TV / Google TV", "fields": [HOST]},
    "samsungtv": {"label": "Samsung TV", "fields": [HOST]},
    "webostv": {"label": "LG webOS TV", "fields": [HOST]},
    "dlna_dmr": {"label": "DLNA renderer", "fields": [DLNA_URL]},
    "reolink": {"label": "Reolink camera, NVR or Home Hub", "fields": [HOST, USERNAME, PASSWORD]},
    "roku": {"label": "Roku", "fields": [HOST]},
    "wiz": {"label": "WiZ light", "fields": [HOST]},
    "tasmota": {"label": "Tasmota", "fields": [HOST]},
    "esphome": {"label": "ESPHome node", "fields": [HOST]},
    "esphome_builder": {"label": "ESPHome Device Builder", "fields": [
        BUILDER_URL, {**USERNAME, "optional": True}, {**PASSWORD, "optional": True}]},
}


def manual_finding(domain: str, body: dict[str, Any]) -> dict[str, Any]:
    """A finding for a device added by address rather than discovered."""
    if domain not in MANUAL:
        raise FlowError(f"{domain} devices cannot be added by address", 501)
    user_input = body.get("input") if isinstance(body.get("input"), dict) else {}
    host = str(body.get("host") or user_input.get("host") or "").strip()
    url = str(body.get("url") or user_input.get("url") or "").strip()
    if url and not host:
        host = urlparse(url).hostname or ""
    if not host and not url:
        raise FlowError("Enter the device's address" if domain not in ("dlna_dmr", "esphome_builder")
                        else "Enter the URL", 400)
    if host and not re.fullmatch(r"[A-Za-z0-9._:\-\[\]]{1,253}", host):
        raise FlowError("That does not look like an address", 400)
    props: dict[str, Any] = {}
    if url:
        props["location"] = url
    key = re.sub(r"[^a-z0-9_.:-]+", "-", (host or url).lower()).strip("-")
    return {"id": f"manual:{domain}:{key}", "integration": domain, "name": None, "model": None,
            "manufacturer": None, "host": host or None, "port": None, "mac": None, "ids": [], "source": "user",
            "props": props}


def _fmt_mac(raw: Any) -> str | None:
    m = norm_mac(raw)
    return ":".join(m[i:i + 2] for i in range(0, 12, 2)) if m else None


# -- HomeKit ----------------------------------------------------------------------------

def normalize_homekit_code(raw: Any) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) != 8:
        raise FlowError("A HomeKit setup code has 8 digits (123-45-678).", 400)
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


async def homekit_pair(finding: dict[str, Any], code: str, alias: str) -> tuple[dict[str, Any], list[Any]]:
    """Pair over IP with the setup code; returns ``(pairing data, accessories)``.

    Built from the mDNS record discovery already holds, so pairing needs no
    second browse (and works where multicast does not reach the container).
    """
    from aiohomekit.characteristic_cache import CharacteristicCacheMemory
    from aiohomekit.controller.ip.controller import IpController
    from aiohomekit.controller.ip.discovery import IpDiscovery
    from aiohomekit.model import Categories
    from aiohomekit.model.feature_flags import FeatureFlags
    from aiohomekit.model.status_flags import StatusFlags
    from aiohomekit.zeroconf import HomeKitService

    props = finding.get("props") or {}
    host = str(finding.get("host") or "")

    def num(key: str, default: int) -> int:
        try:
            return int(props.get(key) or default)
        except (TypeError, ValueError):
            return default

    try:
        category = Categories(num("category", 1))
    except ValueError:
        category = Categories(1)
    desc = HomeKitService(
        name=str(finding.get("name") or host), id=str(props.get("pairing_id") or (finding.get("ids") or [""])[0]),
        model=str(finding.get("model") or ""), feature_flags=FeatureFlags(num("ff", 0)),
        status_flags=StatusFlags(1), config_num=num("c#", 1), state_num=num("s#", 1), category=category,
        protocol_version=str(props.get("pv") or "1.1"), type="_hap._tcp.local.", address=host, addresses=[host],
        port=int(finding.get("port") or 80))
    controller = IpController(char_cache=CharacteristicCacheMemory(), zeroconf_instance=SimpleNamespace(zeroconf=None))
    discovery = IpDiscovery(controller, desc)
    try:
        finish = await asyncio.wait_for(discovery.async_start_pairing(alias), HOMEKIT_TIMEOUT_S)
        pairing = await asyncio.wait_for(finish(code), HOMEKIT_TIMEOUT_S)
    finally:
        with contextlib.suppress(Exception):
            await discovery.close()
    try:
        accessories = await asyncio.wait_for(pairing.list_accessories_and_characteristics(), HOMEKIT_TIMEOUT_S)
    finally:
        with contextlib.suppress(Exception):
            await pairing.close()
    return dict(pairing.pairing_data), list(accessories)


def _homekit_failure(exc: Exception) -> Exception:
    name = type(exc).__name__
    if name in ("AuthenticationError", "MalformedPinError", "InvalidAuthTagError"):
        return NeedsInput([HOMEKIT_CODE], "Enter the accessory's HomeKit setup code.", "That code was not accepted.")
    if name == "MaxPeersError":
        return FlowError("The accessory cannot take another controller. Remove one in the Home app, or reset "
                         "the accessory, then try again.", 409)
    if name == "MaxTriesError":
        return FlowError("Too many wrong codes. Restart the accessory and try again.", 409)
    if name in ("UnavailableError", "AlreadyPairedError"):
        return FlowError("The accessory is already paired with another controller. Remove it there (or reset it) "
                         "first.", 409)
    if name == "BusyError":
        return FlowError("The accessory is pairing with something else right now. Try again in a minute.", 409)
    return FlowError(f"The accessory did not answer: {exc or name}", 502)


async def adopt_homekit(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                        user_input: dict[str, Any]) -> dict[str, Any]:
    from services.homehub.drivers import homekit_services as hks

    hub = runtime.hub
    props = finding.get("props") or {}
    pairing_id = str(props.get("pairing_id") or (finding.get("ids") or [""])[0]).lower()
    if not pairing_id:
        raise FlowError("This HomeKit accessory did not announce its id", 400)
    if _existing_entry(hub, "homekit_controller", pairing_id):
        raise FlowError("This HomeKit accessory is already set up", 409)
    raw = user_input.get("code")
    if not raw:
        raise NeedsInput([HOMEKIT_CODE], "Enter the accessory's HomeKit setup code.")
    try:
        code = normalize_homekit_code(raw)
    except FlowError as exc:
        raise NeedsInput([HOMEKIT_CODE], "Enter the accessory's HomeKit setup code.", str(exc)) from exc
    entry_id = _new_entry_id()
    try:
        data, accessories = await homekit_pair(finding, code, entry_id)
    except Exception as exc:
        raise _homekit_failure(exc) from exc
    pid = str(data.get("AccessoryPairingID") or pairing_id)
    infos = {int(acc["aid"]): hks.accessory_info(acc) for acc in accessories}
    main = infos.get(1) or next(iter(infos.values()), {})
    title = main.get("name") or finding.get("name") or "HomeKit accessory"
    entry = ConfigEntry(entry_id=entry_id, domain="homekit_controller", title=title, data=data, source="zeroconf",
                        unique_id=pid.lower())
    plan = Plan(hub, entry)
    devices = {}
    for aid in sorted(infos):
        info = infos[aid]
        primary = plan.device
        devices[aid] = plan.add_device(
            info.get("name") or (title if aid == 1 else f"{title} {aid}"),
            name_by_user=name if primary is None else None, area_id=area_id,
            manufacturer=info.get("manufacturer"), model=info.get("model"),
            serial_number=info.get("serial_number"), sw_version=info.get("sw_version"),
            hw_version=info.get("hw_version"),
            identifiers=[["homekit_controller:accessory-id", f"{pid}:aid:{aid}"]],
            via_device_id=primary.id if primary is not None else None)
    for spec in hks.entity_specs(pid, accessories):
        spec = dict(spec)
        aid, domain, uid, own = spec.pop("aid"), spec.pop("domain"), spec.pop("unique_id"), spec.pop("name")
        plan.add_entity(domain, uid, own, device=devices.get(aid), **spec)
    return await commit(runtime, plan)


async def unpair_homekit(runtime: Any, entry: ConfigEntry) -> None:
    """Remove our pairing from the accessory (HA's "delete" does the same)."""
    driver = runtime.hub.drivers.get(entry.entry_id)
    if driver is not None and hasattr(driver, "async_unpair"):
        await driver.async_unpair()
        return
    from services.homehub.drivers.homekit import HomeKitDriver

    temp = HomeKitDriver(runtime.hub, entry)
    try:
        await temp.async_unpair()
    finally:
        await temp.async_unload()


# -- Android TV Remote ------------------------------------------------------------------

def shared_atv_cert(hub: Any) -> tuple[str | None, str | None]:
    """The client certificate the TVs already trust (HA used one pair for every Android TV)."""
    for e in hub.config_entries.values():
        if e.domain == ATV_ID and e.data.get("cert_pem") and e.data.get("key_pem"):
            return str(e.data["cert_pem"]), str(e.data["key_pem"])
    return None, None


async def atv_start(host: str, cert_pem: str | None, key_pem: str | None) -> dict[str, Any]:
    """Open a pairing; the TV then shows a code. Returns ``remote, name, mac, cert_pem, key_pem, tmpdir``."""
    from androidtvremote2 import AndroidTVRemote

    from services.homehub.drivers.androidtv_remote import CLIENT_NAME

    tmpdir = tempfile.mkdtemp(prefix="homehub-atv-pair-")
    os.chmod(tmpdir, 0o700)
    certfile, keyfile = os.path.join(tmpdir, "cert.pem"), os.path.join(tmpdir, "key.pem")
    try:
        if cert_pem and key_pem:
            for path, pem in ((certfile, cert_pem), (keyfile, key_pem)):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(pem)
        remote = AndroidTVRemote(CLIENT_NAME, certfile, keyfile, host)
        await remote.async_generate_cert_if_missing()
        with open(certfile) as fh:
            cert_pem = fh.read()
        with open(keyfile) as fh:
            key_pem = fh.read()
        tv_name, mac = await asyncio.wait_for(remote.async_get_name_and_mac(), 10)
        await asyncio.wait_for(remote.async_start_pairing(), 10)
    except BaseException:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    return {"remote": remote, "name": tv_name, "mac": mac, "cert_pem": cert_pem, "key_pem": key_pem,
            "tmpdir": tmpdir}


async def atv_finish(remote: Any, pin: str) -> None:
    await asyncio.wait_for(remote.async_finish_pairing(pin), 15)


async def _atv_open(runtime: Any, finding: dict[str, Any], error: str | None = None) -> NoReturn:
    hub, host = runtime.hub, str(finding.get("host") or "")
    cert, key = shared_atv_cert(hub)
    try:
        started = await atv_start(host, cert, key)
    except Exception as exc:
        raise FlowError(f"The Android TV at {host} did not answer: {exc or type(exc).__name__}", 502) from exc
    mac = _fmt_mac(started["mac"])
    if mac and _existing_entry(hub, ATV_ID, mac):
        shutil.rmtree(started["tmpdir"], ignore_errors=True)
        with contextlib.suppress(Exception):
            started["remote"].disconnect()
        raise FlowError("This Android TV is already set up", 409)
    s = new_session(ATV_ID, finding, obj=started["remote"],
                    data={k: started[k] for k in ("name", "cert_pem", "key_pem", "tmpdir")} | {"mac": mac})
    raise NeedsInput([TV_CODE], "Enter the code shown on the TV.", error, session=s.id)


async def adopt_androidtv_remote(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                                 user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    s = session_for(ATV_ID, finding, user_input)
    if s is None:
        await _atv_open(runtime, finding)
    pin = str(user_input.get("pin") or "").strip()
    if not pin:
        raise NeedsInput([TV_CODE], "Enter the code shown on the TV.", session=s.id)
    try:
        await atv_finish(s.obj, pin)
    except Exception as exc:
        kind = type(exc).__name__
        if kind == "InvalidAuth":
            raise NeedsInput([TV_CODE], "Enter the code shown on the TV.", "That code was not accepted.",
                             session=s.id) from exc
        # The TV closed the pairing (cancelled, or its code expired): start a new one, as HA does.
        drop_session(s.id)
        if kind in ("ConnectionClosed", "TimeoutError"):
            await _atv_open(runtime, finding, "The TV closed the pairing; enter the new code it shows.")
        raise FlowError(f"Pairing with the TV failed: {exc or kind}", 502) from exc
    data = dict(s.data)
    drop_session(s.id)
    host = str(finding.get("host") or "")
    mac = data.get("mac")
    if not mac:
        raise FlowError("The TV did not report its MAC address", 502)
    title = str(data.get("name") or finding.get("name") or "Android TV")
    entry = ConfigEntry(entry_id=_new_entry_id(), domain=ATV_ID, title=title, source="zeroconf", unique_id=mac,
                        data={"host": host, "name": title, "mac": mac, "cert_pem": data["cert_pem"],
                              "key_pem": data["key_pem"]})
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=name, area_id=area_id, identifiers=[[ATV_ID, mac]],
                    connections=[["mac", mac]], manufacturer=finding.get("manufacturer"),
                    model=finding.get("model"))
    plan.add_entity("media_player", mac, None, original_device_class="tv")
    plan.add_entity("remote", mac, None)
    return await commit(runtime, plan)


# -- Samsung ----------------------------------------------------------------------------

SAMSUNG_NAME = "HomeAssistant"  # the client name HA used; TVs that allowed it do not ask again
SAMSUNG_LEGACY_ID = "ha.component.samsung"
ACCEPT_TV = "Accept the connection on the TV (choose Allow when it asks)."


async def _port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError):
        return False
    w.close()
    with contextlib.suppress(Exception):
        await w.wait_closed()
    return True


async def samsung_probe(host: str) -> dict[str, Any]:
    """Which protocol the TV speaks: websocket (REST info on 8001), encrypted (8000) or legacy (55000)."""
    import aiohttp
    from samsungtvws.async_rest import SamsungTVAsyncRest

    try:
        async with aiohttp.ClientSession() as session:
            info = await SamsungTVAsyncRest(host, session=session, port=8001, timeout=5).rest_device_info()
        dev = info.get("device") or {}
        port = 8002 if str(dev.get("TokenAuthSupport", "true")).lower() == "true" else 8001
        return {"method": "websocket", "port": port, "info": info}
    except Exception as exc:
        log.debug("Samsung %s: no REST API (%r)", host, exc)
    if await _port_open(host, 8000):
        return {"method": "encrypted", "port": 8000, "info": {}}
    if await _port_open(host, 55000):
        return {"method": "legacy", "port": 55000, "info": {}}
    raise FlowError(f"The Samsung TV at {host} did not answer. Turn it on and try again.", 502)


async def samsung_ws_pair(host: str, port: int) -> str | None:
    """Connect until the TV is allowed; returns its token (None on TVs that do not issue one)."""
    from samsungtvws.async_remote import SamsungTVWSAsyncRemote

    remote = SamsungTVWSAsyncRemote(host=host, port=port, token=None, timeout=ACCEPT_TIMEOUT_S, name=SAMSUNG_NAME,
                                    key_press_delay=0)
    try:
        await remote.open()
        return remote.token
    finally:
        with contextlib.suppress(Exception):
            await remote.close()


async def samsung_legacy_pair(host: str, port: int) -> str | None:
    from samsungtvws.legacy.remote import SamsungTVLegacyAsyncRemote

    remote = SamsungTVLegacyAsyncRemote(host, port=port, name=SAMSUNG_NAME, description=SAMSUNG_NAME,
                                        remote_id=SAMSUNG_LEGACY_ID, timeout=ACCEPT_TIMEOUT_S)
    try:
        await remote.open()
        return None
    finally:
        with contextlib.suppress(Exception):
            await remote.close()


class EncryptedPairing:
    """A 2014-2015 TV's PIN pairing: it keeps the HTTP session the TV's PIN page belongs to."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.session: Any = None
        self.auth: Any = None

    async def start(self) -> None:
        import aiohttp
        from samsungtvws.encrypted.authenticator import SamsungTVEncryptedWSAsyncAuthenticator

        self.session = aiohttp.ClientSession()
        self.auth = SamsungTVEncryptedWSAsyncAuthenticator(self.host, web_session=self.session, timeout=10)
        await self.auth.start_pairing()

    async def finish(self, pin: str) -> tuple[str, str] | None:
        tok = await self.auth.try_pin(pin)
        if not tok:
            return None
        return tok, await self.auth.get_session_id_and_close()

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()


async def samsung_encrypted_start(host: str) -> Any:
    pairing = EncryptedPairing(host)
    await pairing.start()
    return pairing


def _samsung_uid(probe: dict[str, Any], finding: dict[str, Any]) -> str | None:
    dev = (probe.get("info") or {}).get("device") or {}
    raw = dev.get("duid") or dev.get("id") or (finding.get("props") or {}).get("uuid")
    if not raw:
        raw = next((i for i in finding.get("ids") or [] if re.fullmatch(r"[0-9a-f-]{32,36}", str(i))), None)
    return str(raw).lower().removeprefix("uuid:") if raw else None


def _renderer_location(runtime: Any, host: str) -> str | None:
    """The UPnP MediaRenderer description the TV announced (volume and URL playback, as HA)."""
    mgr = getattr(runtime, "discovery", None)
    for f in (getattr(mgr, "findings", None) or {}).values():
        if f.get("integration") == "dlna_dmr" and f.get("host") == host:
            loc = (f.get("props") or {}).get("location")
            if loc:
                return str(loc)
    return None


async def adopt_samsungtv(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                          user_input: dict[str, Any]) -> dict[str, Any]:
    hub, host = runtime.hub, str(finding.get("host") or "")
    s = session_for("samsungtv", finding, user_input)
    if s is None:
        probe = await samsung_probe(host)
        uid = _samsung_uid(probe, finding)
        if uid and _existing_entry(hub, "samsungtv", uid):
            raise FlowError("This Samsung TV is already set up", 409)
        if probe["method"] == "encrypted":
            try:
                pairing = await samsung_encrypted_start(host)
            except Exception as exc:
                raise FlowError(f"The Samsung TV at {host} did not start pairing: {exc or type(exc).__name__}",
                                502) from exc
            s = new_session("samsungtv", finding, obj=pairing, data={"probe": probe, "uid": uid})
            raise NeedsInput([TV_CODE], "Enter the PIN shown on the TV.", session=s.id)
        job = (samsung_ws_pair if probe["method"] == "websocket" else samsung_legacy_pair)(host, probe["port"])
        s = new_session("samsungtv", finding, job=job, data={"probe": probe, "uid": uid})
        raise Pending(s.id, ACCEPT_TV, "accept_on_tv")
    probe, uid = s.data["probe"], s.data.get("uid")
    extra: dict[str, Any] = {}
    if probe["method"] == "encrypted":
        pin = str(user_input.get("pin") or "").strip()
        if not pin:
            raise NeedsInput([TV_CODE], "Enter the PIN shown on the TV.", session=s.id)
        try:
            got = await s.obj.finish(pin)
        except Exception as exc:
            drop_session(s.id)
            raise FlowError(f"Pairing with the TV failed: {exc or type(exc).__name__}. Start again.", 502) from exc
        if got is None:
            raise NeedsInput([TV_CODE], "Enter the PIN shown on the TV.", "That PIN was not accepted.", session=s.id)
        extra = {"token": got[0], "session_id": got[1]}
    else:
        waiting(s, ACCEPT_TV)
        exc = s.task.exception() if s.task is not None and not s.task.cancelled() else None
        if exc is not None or s.task is None or s.task.cancelled():
            drop_session(s.id)
            kind = type(exc).__name__ if exc is not None else "Cancelled"
            if kind == "UnauthorizedError":
                raise FlowError("The TV refused the connection (Deny was chosen). Allow it under the TV's "
                                "external device manager, then try again.", 409) from exc
            if kind in ("TimeoutError", "Cancelled"):
                raise FlowError("Nobody accepted on the TV in time. Try again.", 409) from exc
            raise FlowError(f"The Samsung TV did not pair: {exc or kind}", 502) from exc
        tok = s.task.result()
        if tok:
            extra = {"token": tok}
    drop_session(s.id)
    dev = (probe.get("info") or {}).get("device") or {}
    uid = uid or _fmt_mac(dev.get("wifiMac")) or host
    if _existing_entry(hub, "samsungtv", uid):
        raise FlowError("This Samsung TV is already set up", 409)
    title = str(dev.get("name") or finding.get("name") or "Samsung TV")
    model = dev.get("modelName") or finding.get("model")
    mac = _fmt_mac(dev.get("wifiMac"))
    data: dict[str, Any] = {"host": host, "method": probe["method"], "port": probe["port"], "name": title,
                            "manufacturer": "Samsung", "model": model, **extra}
    if mac:
        data["mac"] = mac
    location = _renderer_location(runtime, host)
    if location:
        data["ssdp_rendering_control_location"] = location
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="samsungtv", title=title, data=data, source="ssdp",
                        unique_id=uid)
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=name, area_id=area_id, manufacturer="Samsung", model=model,
                    identifiers=[["samsungtv", uid]], connections=[["mac", mac]] if mac else [])
    plan.add_entity("media_player", uid, None, original_device_class="tv")
    plan.add_entity("remote", uid, None)
    return await commit(runtime, plan)


# -- LG webOS ---------------------------------------------------------------------------

async def webos_pair(host: str) -> tuple[str, dict[str, Any]]:
    """Connect without a key; the TV asks to accept. Returns ``(client_key, {uuid, model, name, sw_version})``."""
    from aiowebostv import WebOsClient

    client = WebOsClient(host, client_key=None, connect_timeout=10)
    try:
        await asyncio.wait_for(client.connect(), ACCEPT_TIMEOUT_S)
        if not client.client_key:
            raise FlowError("The TV did not give a pairing key", 502)
        hello = client.tv_info.hello or {}
        system = client.tv_info.system or {}
        software = client.tv_info.software or {}
        sw = ".".join(str(software[k]) for k in ("major_ver", "minor_ver") if software.get(k) is not None)
        return str(client.client_key), {"uuid": hello.get("deviceUUID"), "model": system.get("modelName"),
                                        "name": system.get("modelName"), "sw_version": sw or None}
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


async def adopt_webostv(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                        user_input: dict[str, Any]) -> dict[str, Any]:
    hub, host = runtime.hub, str(finding.get("host") or "")
    s = session_for("webostv", finding, user_input)
    if s is None:
        s = new_session("webostv", finding, job=webos_pair(host))
        raise Pending(s.id, "Accept the connection request on the TV.", "accept_on_tv")
    waiting(s, "Accept the connection request on the TV.")
    task = s.task
    drop_session(s.id)
    if task is None or task.cancelled():
        raise FlowError("Nobody accepted on the TV in time. Try again.", 409)
    exc = task.exception()
    if exc is not None:
        if isinstance(exc, FlowError):
            raise exc
        if type(exc).__name__ == "WebOsTvPairError":
            raise FlowError("The TV refused the connection. Try again and choose Accept.", 409) from exc
        if isinstance(exc, TimeoutError):
            raise FlowError("Nobody accepted on the TV in time. Try again.", 409) from exc
        raise FlowError(f"The LG TV at {host} did not answer: {exc or type(exc).__name__}. Is it on?", 502) from exc
    key, info = task.result()
    uid = str(info.get("uuid") or "").lower() or None
    if uid is None:
        raise FlowError("The TV did not report its id", 502)
    if _existing_entry(hub, "webostv", uid):
        raise FlowError("This LG TV is already set up", 409)
    model = info.get("model")
    title = f"LG webOS TV {model}" if model else (finding.get("name") or "LG webOS TV")
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="webostv", title=title, source="ssdp", unique_id=uid,
                        data={"host": host, "client_secret": key})
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=name, area_id=area_id, manufacturer="LG", model=model,
                    sw_version=info.get("sw_version"), identifiers=[["webostv", uid]])
    plan.add_entity("media_player", uid, None, original_device_class="tv")
    return await commit(runtime, plan)


# -- DLNA renderers ---------------------------------------------------------------------

async def dlna_describe(url: str) -> str:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session, session.get(url) as resp:
        if resp.status != 200:
            raise FlowError(f"The renderer answered HTTP {resp.status}", 502)
        return await resp.text()


def parse_renderer(xml_text: str) -> dict[str, str] | None:
    """The first MediaRenderer device in a UPnP description (root or embedded)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for dev in root.iter():
        if dev.tag.rsplit("}", 1)[-1] != "device":
            continue
        fields = {c.tag.rsplit("}", 1)[-1]: (c.text or "").strip() for c in dev}
        if "MediaRenderer" in fields.get("deviceType", ""):
            return fields
    return None


async def adopt_dlna_dmr(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                         user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    url = str(user_input.get("url") or (finding.get("props") or {}).get("location") or "").strip()
    if not url:
        raise NeedsInput([DLNA_URL], "Enter the renderer's description URL.")
    if urlparse(url).scheme not in ("http", "https"):
        raise NeedsInput([DLNA_URL], "Enter the renderer's description URL.", "That is not an http URL.")
    try:
        xml_text = await dlna_describe(url)
    except FlowError:
        raise
    except Exception as exc:
        raise FlowError(f"The renderer at {url} did not answer: {exc or type(exc).__name__}", 502) from exc
    dev = parse_renderer(xml_text)
    if dev is None:
        raise FlowError("That device is not a DLNA media renderer", 400)
    udn = dev.get("UDN") or ""
    if not udn:
        raise FlowError("The renderer did not report its id (UDN)", 502)
    if _existing_entry(hub, "dlna_dmr", udn):
        raise FlowError("This renderer is already set up", 409)
    title = dev.get("friendlyName") or finding.get("name") or "DLNA renderer"
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="dlna_dmr", title=title, source="ssdp", unique_id=udn,
                        data={"url": url, "device_id": udn, "device_type": dev.get("deviceType")})
    plan = Plan(hub, entry)
    plan.set_device(title, name_by_user=name, area_id=area_id, manufacturer=dev.get("manufacturer") or None,
                    model=dev.get("modelName") or None, serial_number=dev.get("serialNumber") or None,
                    identifiers=[["dlna_dmr", udn]])
    plan.add_entity("media_player", udn, None)
    return await commit(runtime, plan)


# -- Reolink ----------------------------------------------------------------------------

#: switch key -> capability (keys are the driver's, ``drivers/reolink.py`` SWITCHES).
REOLINK_SWITCHES = {"email": ("email", "Email on event"), "ftp_upload": ("ftp", "FTP upload"),
                    "buzzer": ("buzzer", "Buzzer on event"), "ir_lights": ("ir_lights", "Infrared lights in night mode"),
                    "manual_record": ("manual_record", "Manual record"),
                    "push_notifications": ("push", "Push notifications"), "record": ("recording", "Record"),
                    "siren_on_event": ("siren", "Siren on event")}
#: binary sensor key -> reolink-aio AI type.
REOLINK_AI = {"person": ("people", "Person"), "vehicle": ("vehicle", "Vehicle"), "pet": ("dog_cat", "Pet"),
              "face": ("face", "Face"), "package": ("package", "Package")}


async def reolink_connect(host: str, username: str, password: str) -> Any:
    from reolink_aio.api import Host

    api = Host(host, username, password, timeout=15)
    await api.get_host_data()
    return api


async def reolink_close(api: Any) -> None:
    with contextlib.suppress(Exception):
        await api.logout()


def _sup(api: Any, ch: int | None, cap: str) -> bool:
    try:
        return bool(api.supported(ch, cap))
    except Exception:
        return False


def reolink_entities(api: Any, plan: Plan, host_uid: str, hub_dev: Any, area_id: str | None) -> None:
    """Every entity the driver can serve, chosen by what the device reports it supports."""
    is_nvr = bool(getattr(api, "is_nvr", False))
    for ch in list(getattr(api, "channels", []) or []):
        cam_uid = str(api.camera_uid(ch) or "")
        part = cam_uid if cam_uid and cam_uid != "Unknown" else str(ch)
        if is_nvr:
            dev = plan.add_device(api.camera_name(ch) or f"Camera {ch + 1}", area_id=area_id,
                                  manufacturer="Reolink", model=api.camera_model(ch),
                                  identifiers=[["reolink", f"{host_uid}_{part}"]], via_device_id=hub_dev.id)
        else:
            dev = hub_dev
        pre = f"{host_uid}_{part}"

        def add(domain: str, key: str, label: str | None, dev: Any = dev, pre: str = pre, **fields: Any) -> None:
            plan.add_entity(domain, f"{pre}_{key}", label, device=dev, **fields)

        add("camera", "sub", "Fluent")
        add("camera", "main", "Clear", disabled_by="integration")
        if _sup(api, ch, "motion_detection"):
            add("binary_sensor", "motion", "Motion", original_device_class="motion")
        for key, (ai_type, label) in REOLINK_AI.items():
            with contextlib.suppress(Exception):
                if api.ai_supported(ch, ai_type):
                    add("binary_sensor", key, label)
        if _sup(api, ch, "sleep"):
            add("binary_sensor", "sleep", "Sleep status", entity_category="diagnostic")
        if _sup(api, ch, "battery"):
            add("sensor", "battery_percent", "Battery", original_device_class="battery", unit_of_measurement="%",
                capabilities={"state_class": "measurement"})
        if _sup(api, ch, "status_led"):
            add("light", "status_led", "Status LED", entity_category="config")
        if _sup(api, ch, "siren_play"):
            add("siren", "siren", "Siren")
        for key, (cap, label) in REOLINK_SWITCHES.items():
            if _sup(api, ch, cap):
                add("switch", key, label, entity_category="config")
        if _sup(api, ch, "volume"):
            add("number", "volume", "Volume", entity_category="config")
        if _sup(api, ch, "PIR_sensitivity"):
            add("number", "pir_sensitivity", "PIR sensitivity", entity_category="config")
        if _sup(api, ch, "md_sensitivity"):
            add("number", "motion_sensitivity", "Motion sensitivity", entity_category="config")
        if _sup(api, ch, "ai_sensitivity"):
            for key, (ai_type, label) in REOLINK_AI.items():
                with contextlib.suppress(Exception):
                    if key in ("person", "vehicle", "pet") and api.ai_supported(ch, ai_type):
                        add("number", f"ai_{key}_sensititvity", f"AI {label.lower()} sensitivity",
                            entity_category="config")
        if _sup(api, ch, "dayNight"):
            add("select", "day_night_mode", "Day night mode", entity_category="config")
    if is_nvr:
        if _sup(api, None, "state_light"):
            plan.add_entity("light", f"{host_uid}_hub_status_led", "Status LED", device=hub_dev,
                            entity_category="config")
        if _sup(api, None, "siren_play"):
            plan.add_entity("siren", f"{host_uid}_siren", "Siren", device=hub_dev)
        if _sup(api, None, "hub_audio"):
            plan.add_entity("number", f"{host_uid}_alarm_volume", "Alarm volume", device=hub_dev,
                            entity_category="config")
            plan.add_entity("number", f"{host_uid}_message_volume", "Message volume", device=hub_dev,
                            entity_category="config")


async def adopt_reolink(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                        user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    host = str(user_input.get("host") or finding.get("host") or "").strip()
    username = str(user_input.get("username") or "").strip()
    password = str(user_input.get("password") or "")
    if not host:
        raise NeedsInput([HOST, USERNAME, PASSWORD], "Enter the address and login.")
    if not username or not password:
        raise NeedsInput([USERNAME, PASSWORD], "Enter the login of the camera, NVR or Home Hub.")
    try:
        api = await reolink_connect(host, username, password)
    except Exception as exc:
        kind = type(exc).__name__
        if kind in ("CredentialsInvalidError", "LoginError", "LoginPrivacyModeError"):
            raise NeedsInput([USERNAME, PASSWORD], "Enter the login of the camera, NVR or Home Hub.",
                             "That login was not accepted.") from exc
        raise FlowError(f"The Reolink device at {host} did not answer: {exc or kind}", 502) from exc
    try:
        uid = str(getattr(api, "uid", "") or "")
        try:
            mac = _fmt_mac(api.mac_address)
        except Exception:
            mac = None
        host_uid = uid if uid and uid != "Unknown" else (mac or "")
        if not host_uid:
            raise FlowError("The Reolink device reported neither its UID nor its MAC address", 502)
        entry_uid = mac or host_uid
        if _existing_entry(hub, "reolink", entry_uid):
            raise FlowError("This Reolink device is already set up", 409)
        title = str(getattr(api, "nvr_name", "") or finding.get("name") or "Reolink")
        data: dict[str, Any] = {"host": host, "port": getattr(api, "port", None),
                                "use_https": getattr(api, "use_https", None), "username": username,
                                "password": password, "baichuan_port": getattr(getattr(api, "baichuan", None),
                                                                               "port", None) or 9000,
                                "baichuan_only": False}
        if uid and uid != "Unknown":
            data["uid"] = uid
        entry = ConfigEntry(entry_id=_new_entry_id(), domain="reolink", title=title, data=data, source="user",
                            unique_id=entry_uid, options={"protocol": "rtsp"})
        plan = Plan(hub, entry)
        hub_dev = plan.set_device(title, name_by_user=name, area_id=area_id, manufacturer="Reolink",
                                  model=getattr(api, "model", None), sw_version=getattr(api, "sw_version", None),
                                  identifiers=[["reolink", host_uid]],
                                  connections=[["mac", mac]] if mac else [])
        reolink_entities(api, plan, host_uid, hub_dev, area_id)
    finally:
        await reolink_close(api)
    return await commit(runtime, plan)


# -- the ESPHome Device Builder ---------------------------------------------------------

async def builder_check(url: str, username: str | None, password: str | None) -> dict[str, Any]:
    from services.homehub.drivers.esphome_builder import check_dashboard

    return await check_dashboard(url, username, password)


async def adopt_esphome_builder(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                                user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    url = str(user_input.get("url") or (finding.get("props") or {}).get("location") or "").strip()
    if not url:
        raise NeedsInput([BUILDER_URL], "Enter the ESPHome Device Builder's address.")
    if urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname:
        raise NeedsInput([BUILDER_URL], "Enter the ESPHome Device Builder's address.", "That is not an http URL.")
    if any(e.domain == "esphome_builder" for e in hub.config_entries.values()):
        raise FlowError("The ESPHome Device Builder is already set up; change or remove it under Integrations.", 409)
    username = str(user_input.get("username") or "").strip() or None
    password = str(user_input.get("password") or "") or None
    try:
        result = await builder_check(url, username, password)
    except Exception as exc:
        raise FlowError(f"The ESPHome Device Builder at {url} did not answer: {exc or type(exc).__name__}",
                        502) from exc
    if result.get("login"):
        raise NeedsInput([{**USERNAME}, {**PASSWORD}], "The builder asks for a login.",
                         "That login was not accepted." if username else None)
    data: dict[str, Any] = {"url": url}
    if username:
        data.update({"username": username, "password": password or ""})
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="esphome_builder", title=name or "ESPHome Device Builder",
                        data=data, source="user", unique_id="esphome_builder")
    return await commit(runtime, Plan(hub, entry))


FLOWS = {
    "homekit_controller": adopt_homekit,
    "androidtv_remote": adopt_androidtv_remote,
    "samsungtv": adopt_samsungtv,
    "webostv": adopt_webostv,
    "dlna_dmr": adopt_dlna_dmr,
    "reolink": adopt_reolink,
    "esphome_builder": adopt_esphome_builder,
}

#: Called before an entry is removed, to release the device's side of the pairing.
UNPAIR = {
    "homekit_controller": unpair_homekit,
}
