# services/execution/esphome_client.py
"""Direct native-API client for ESPHome devices.

Talks straight to devices over the ESPHome native TCP protocol
(aioesphomeapi) instead of routing through Home Assistant, so automation
commands keep working when HA is down and skip one network hop on the
FastPath.

Device registry lives in the Identity service as the GlobalSetting
``esphome_devices`` — a JSON list:

    [{"name": "office-light", "host": "192.168.2.87",
      "port": 6053, "noise_psk": "base64-key-from-device-yaml",
      "ha_entity_id": "light.office_light"}]

``port`` defaults to 6053 and ``noise_psk`` is optional (only needed when
the device YAML enables ``api: encryption``). ``ha_entity_id`` is the
optional Home Assistant entity that exposes this device; when set, the
hardware router knows the device is reachable via both paths. Per repo
rules there is no hardcoded fallback device list: an empty/unset setting
raises immediately.
"""

import asyncio
import base64
import contextlib
import json
import logging
import os
import time

import aiohttp

try:
    import aioesphomeapi
except ImportError as e:  # pragma: no cover - dependency missing
    raise ImportError(
        "aioesphomeapi is required for direct ESPHome control. "
        "Add it to services/execution requirements."
    ) from e

from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET, JARVIS_HOST

log = logging.getLogger("execution.esphome")

DEFAULT_ESPHOME_PORT = 6053
_SETTINGS_TTL_SECONDS = 60.0
_CONNECT_TIMEOUT_SECONDS = 10.0

# Cached device configs: {"name": {"host": ..., "port": ..., "noise_psk": ..., "ha_entity_id": ...}}
_device_cache: dict[str, dict] = {}
_cache_loaded_at: float = 0.0

# Cached per-device entity summaries from list_entities: name -> (loaded_at, entities)
_entity_list_cache: dict[str, tuple[float, list[dict]]] = {}
_ENTITY_LIST_TTL_SECONDS = 300.0


class EsphomeConfigError(RuntimeError):
    """Raised when the esphome_devices setting is missing or malformed."""


async def _identity_post(path: str, body: dict, *, bearer: str | None = None) -> tuple[int, dict]:
    """POST to Identity, as this service (internal secret) or as a user."""
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {"X-Internal-Secret": INTERNAL_SECRET}
    async with aiohttp.ClientSession() as client:
        resp = await client.post(f"{IDENTITY_SVC_URL}{path}", headers=headers, json=body,
                                 timeout=aiohttp.ClientTimeout(total=5.0))
        try:
            data = await resp.json()
        except (aiohttp.ContentTypeError, json.JSONDecodeError):
            data = {}
        return resp.status, data if isinstance(data, dict) else {}


async def _read_global_setting(key: str) -> str | None:
    """Fetch one Identity GlobalSetting's raw value (None when unset)."""
    async with aiohttp.ClientSession() as client:
        resp = await client.get(
            f"{IDENTITY_SVC_URL}/api/settings",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status != 200:
            raise EsphomeConfigError(
                f"Identity returned {resp.status} reading settings; "
                f"cannot resolve {key}."
            )
        settings = await resp.json()
    for entry in settings:
        if entry.get("key") == key:
            return entry.get("value")
    return None


async def _load_devices_from_identity() -> dict[str, dict]:
    """Fetch the esphome_devices GlobalSetting from the Identity service."""
    raw_value = await _read_global_setting("esphome_devices")
    if not raw_value or not str(raw_value).strip():
        raise EsphomeConfigError(
            "Setting 'esphome_devices' is not configured. Add it in "
            "Settings (Identity GlobalSettings) as a JSON list like: "
            '[{"name": "office-light", "host": "192.168.2.87", '
            '"port": 6053, "noise_psk": "<key from device YAML>"}]'
        )
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as e:
        raise EsphomeConfigError(
            f"'esphome_devices' is not valid JSON: {e}"
        ) from e
    if not isinstance(parsed, list):
        raise EsphomeConfigError("'esphome_devices' must be a JSON list.")
    devices: dict[str, dict] = {}
    for item in parsed:
        if not isinstance(item, dict) or not item.get("name") or not item.get("host"):
            raise EsphomeConfigError(
                "Every 'esphome_devices' entry needs at least 'name' and 'host'."
            )
        name = str(item["name"]).strip().lower()
        devices[name] = {
            "host": str(item["host"]).strip(),
            "port": int(item.get("port") or DEFAULT_ESPHOME_PORT),
            "noise_psk": (str(item["noise_psk"]).strip() or None)
            if item.get("noise_psk")
            else None,
            "ha_entity_id": (str(item["ha_entity_id"]).strip().lower() or None)
            if item.get("ha_entity_id")
            else None,
            # Set by pairing: the user the device was linked to, and the
            # address it was given for Jarvis (reused when it is re-sent).
            "owner": (str(item["owner"]).strip().lower() or None) if item.get("owner") else None,
            "jarvis_url": (str(item["jarvis_url"]).strip() or None) if item.get("jarvis_url") else None,
        }
    return devices


async def get_devices(force_refresh: bool = False) -> dict[str, dict]:
    """Resolve device configs from Identity with a short TTL cache."""
    global _cache_loaded_at
    now = time.monotonic()
    if force_refresh or not _device_cache or (now - _cache_loaded_at) > _SETTINGS_TTL_SECONDS:
        _device_cache.clear()
        _device_cache.update(await _load_devices_from_identity())
        _cache_loaded_at = now
        log.info(f"[esphome] loaded {len(_device_cache)} device(s) from Identity")
    return _device_cache


async def get_device(name: str) -> dict:
    devices = await get_devices()
    cfg = devices.get(str(name).strip().lower())
    if cfg is None:
        known = ", ".join(sorted(devices)) or "(none)"
        raise EsphomeConfigError(
            f"No ESPHome device named '{name}'. Configured devices: {known}."
        )
    return cfg


def _entity_domain(entity) -> str | None:
    """Map an EntityInfo object back to its domain string ('light', ...)."""
    for domain, info_cls in aioesphomeapi.COMPONENT_TYPE_TO_INFO.items():
        if isinstance(entity, info_cls):
            return domain
    return None


def _match_entity(entities: list, wanted: str):
    target = wanted.strip().lower()
    for entity in entities:
        names = {
            str(getattr(entity, "name", "") or "").lower(),
            str(getattr(entity, "object_id", "") or "").lower(),
        }
        unique_id = str(getattr(entity, "unique_id", "") or "").lower()
        if unique_id:
            names.add(unique_id)
            names.add(unique_id.split("-")[-1])
        if target in names:
            return entity
    return None


async def _with_connection(cfg: dict, coro_factory):
    """Open a connection, run coro_factory(client), always disconnect."""
    client = await _connect(cfg["host"], cfg["port"], cfg.get("noise_psk"))
    try:
        return await coro_factory(client)
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


async def list_entities(device_name: str) -> dict:
    """Return device info plus a summary of every exposed entity."""
    cfg = await get_device(device_name)

    async def op(client):
        info, entities, _services = await client.device_info_and_list_entities()
        out = []
        for entity in entities:
            domain = _entity_domain(entity)
            out.append(
                {
                    "domain": domain,
                    "name": getattr(entity, "name", ""),
                    "object_id": getattr(entity, "object_id", ""),
                    "key": entity.key,
                }
            )
        device = {
            "name": info.friendly_name or info.name,
            "esphome_version": info.esphome_version,
            "model": info.model,
        }
        return device, out

    device, entities = await _with_connection(cfg, op)
    return {"device": device, "entities": entities}


async def get_device_entities_cached(device_name: str) -> list[dict]:
    """Entity summaries for a device with a short TTL cache.

    Used by the hardware router for HA-entity correlation so repeated
    routing decisions don't reconnect to every device on every call.
    """
    now = time.monotonic()
    cached = _entity_list_cache.get(device_name)
    if cached and (now - cached[0]) <= _ENTITY_LIST_TTL_SECONDS:
        return cached[1]
    data = await list_entities(device_name)
    entities = data["entities"]
    _entity_list_cache[device_name] = (now, entities)
    return entities


async def find_device_for_ha_entity(ha_entity_id: str) -> str | None:
    """Map an HA entity_id to a configured ESPHome device name.

    Order: explicit ``ha_entity_id`` mapping in the esphome_devices
    setting first; then object-id/domain correlation against each
    configured device's entity list (HA names ESPHome-backed entities
    ``<domain>.<object_id>``). Returns None when no device matches.
    """
    wanted = str(ha_entity_id or "").strip().lower()
    if not wanted or "." not in wanted:
        return None
    ha_domain, ha_object_id = wanted.split(".", 1)
    devices = await get_devices()
    for name, cfg in devices.items():
        if (cfg.get("ha_entity_id") or "").lower() == wanted:
            return name
    for name in devices:
        try:
            entities = await get_device_entities_cached(name)
        except Exception as e:
            # Correlation is best-effort: an unreachable/misconfigured device
            # must be skipped, never break routing for the others.
            log.debug(f"[esphome] entity correlation skipped for '{name}': {e}")
            continue
        for entity in entities:
            if (
                entity.get("domain") == ha_domain
                and str(entity.get("object_id", "")).lower() == ha_object_id.lower()
            ):
                return name
    return None


def _climate_mode(value: str):
    try:
        return aioesphomeapi.ClimateMode[value.strip().upper()]
    except KeyError:
        valid = [m.name for m in aioesphomeapi.ClimateMode]
        raise ValueError(f"Invalid climate mode '{value}'. Valid: {valid}") from None


def _media_command(value: str):
    cmd = value.strip().upper()
    aliases = {"play_pause": "PLAY"}
    try:
        return aioesphomeapi.MediaPlayerCommand[aliases.get(cmd, cmd)]
    except KeyError:
        valid = [c.name for c in aioesphomeapi.MediaPlayerCommand]
        raise ValueError(f"Invalid media player command '{value}'. Valid: {valid}") from None


async def call_entity(device_name: str, entity_name: str, params: dict | None = None) -> dict:
    """Send a command to one entity on a device; returns a result summary."""
    cfg = await get_device(device_name)
    params = params or {}

    def build_op():
        async def op(client):
            _info, entities, _services = await client.device_info_and_list_entities()
            entity = _match_entity(entities, entity_name)
            if entity is None:
                available = [
                    f"{_entity_domain(e)}/{getattr(e, 'object_id', '')}" for e in entities
                ]
                raise ValueError(
                    f"Entity '{entity_name}' not found on '{device_name}'. "
                    f"Available: {available}"
                )
            domain = _entity_domain(entity)
            key = entity.key

            if domain == "button":
                client.button_command(key)
            elif domain == "light":
                state = params.get("state")
                brightness = params.get("brightness_pct")
                client.light_command(
                    key,
                    state=(bool(state) if state is not None else None),
                    brightness=(float(brightness) / 100.0 if brightness is not None else None),
                    rgb=tuple(params["rgb"]) if params.get("rgb") else None,
                )
            elif domain == "switch":
                state = params.get("state")
                client.switch_command(key, state=bool(state)) if state is not None else client.switch_command(key, True)
            elif domain == "fan":
                state = params.get("state")
                speed_level = params.get("speed_level")
                client.fan_command(
                    key,
                    state=(bool(state) if state is not None else None),
                    speed_level=int(speed_level) if speed_level is not None else None,
                    oscillating=params.get("oscillating"),
                )
            elif domain == "cover":
                if params.get("stop"):
                    client.cover_command(key, stop=True)
                elif params.get("position") is not None:
                    client.cover_command(key, position=float(params["position"]) / 100.0)
                else:
                    client.cover_command(key, position=1.0 if params.get("state", "open") != "closed" else 0.0)
            elif domain == "climate":
                client.climate_command(
                    key,
                    mode=_climate_mode(params["mode"]) if params.get("mode") else None,
                    target_temperature=(
                        float(params["target_temperature"])
                        if params.get("target_temperature") is not None
                        else None
                    ),
                )
            elif domain == "media_player":
                client.media_player_command(
                    key,
                    command=_media_command(params["command"]) if params.get("command") else None,
                    volume=(float(params["volume"]) if params.get("volume") is not None else None),
                    media_url=params.get("media_url"),
                    announcement=bool(params["announcement"]) if params.get("announcement") is not None else None,
                )
            elif domain == "select":
                option = params.get("option")
                if not option:
                    raise ValueError("select entities need params.option")
                client.select_command(key, str(option))
            elif domain == "number":
                value = params.get("value")
                if value is None:
                    raise ValueError("number entities need params.value")
                client.number_command(key, float(value))
            elif domain == "siren":
                tone = params.get("tone")
                if tone is not None:
                    client.siren_command(key, tone=str(tone), state=True)
                else:
                    state = bool(params.get("state", True))
                    client.siren_command(key, state=state)
            else:
                raise ValueError(
                    f"Direct commands for domain '{domain}' are not supported yet."
                )
            return {"domain": domain, "name": getattr(entity, "name", ""), "params": params}
        return op

    result = await _with_connection(cfg, build_op())
    return result


# ─── Handing a device Jarvis's own endpoint ────────────────────────────────────
#
# Devices that call Jarvis themselves (the watch syncs steps and asks
# questions over HTTP) need Jarvis's address and an API key. Rather than have
# someone type those on a 240 px screen, Jarvis pushes them over the native
# API by calling the device's `configure_jarvis` action (declared under
# `api: actions:` in the device YAML) with:
#   url      Jarvis as the device should reach it: JARVIS_HOST (its public
#            address, from the environment) when set, else the address the user
#            reached Jarvis at while pairing (the gateway passes it along). With
#            neither, pairing fails and says so.
#   api_key  a key minted for the requesting user, labelled "device:<name>",
#            so whatever the device sends is attributed to that user.

CONFIGURE_JARVIS_ACTION = "configure_jarvis"


async def get_jarvis_device_url(seen_at: str | None = None) -> str:
    """Jarvis's address as devices should reach it.

    JARVIS_HOST (the public address, from the environment) when set, else
    ``seen_at``: the address the user reached Jarvis at (from the gateway).
    """
    configured = str(JARVIS_HOST or "").strip().rstrip("/")
    url = configured or str(seen_at or "").strip().rstrip("/")
    source = "JARVIS_HOST" if configured else "the address Jarvis was reached at"
    if not url:
        raise EsphomeConfigError(
            "Jarvis could not tell which address the device should use: pair from "
            "Jarvis's own web address, or set JARVIS_HOST."
        )
    if not url.startswith(("http://", "https://")):
        raise EsphomeConfigError(f"{source} must be an http:// or https:// URL, got {url!r}.")
    return url


async def mint_device_api_key(user_api_key: str | None, device_name: str) -> str:
    """Create an API key for the requesting user, labelled for the device."""
    if not user_api_key:
        raise EsphomeConfigError(
            "No API key in the user context, so a device key cannot be minted "
            "for this user. Call this as an authenticated user."
        )
    status, body = await _identity_post("/api/users/me/keys", {"label": f"device:{device_name}"}, bearer=user_api_key)
    if status != 200:
        raise EsphomeConfigError(f"Identity returned {status} minting a key for device '{device_name}'.")
    key = body.get("key")
    if not key:
        raise EsphomeConfigError("Identity minted a key but returned no key value.")
    return key


async def _send_endpoint(client, services, url: str, mint_key) -> bool:
    """Hand the device Jarvis's URL and a freshly minted key, if it takes them.

    Returns False when the device has no configure_jarvis action (it never
    calls Jarvis itself). The key is minted only once the action is known to
    exist, so a device that cannot take it leaves no orphaned key.
    """
    service = next((s for s in services if s.name == CONFIGURE_JARVIS_ACTION), None)
    if service is None:
        return False
    wanted, have = {"url", "api_key"}, {a.name for a in service.args}
    if not wanted <= have:
        raise EsphomeConfigError(f"'{CONFIGURE_JARVIS_ACTION}' takes {sorted(have)}, expected {sorted(wanted)}.")
    await _respond(client, services, CONFIGURE_JARVIS_ACTION, {"url": url, "api_key": await mint_key()})
    return True


async def push_jarvis_endpoint(device_name: str, url: str, mint_key) -> dict:
    """Re-send Jarvis's URL and a new key to an already-registered device."""
    cfg = await get_device(device_name)

    async def op(client):
        _entities, services = await client.list_entities_services()
        if not await _send_endpoint(client, services, url, mint_key):
            names = ", ".join(sorted(s.name for s in services)) or "(none)"
            raise EsphomeConfigError(
                f"Device '{device_name}' has no '{CONFIGURE_JARVIS_ACTION}' action "
                f"(it exposes: {names}). Flash firmware that declares it under "
                "`api: actions:`."
            )
        return {"device": device_name, "url": url}

    return await _with_connection(cfg, op)


# ─── Companion pairing ─────────────────────────────────────────────────────────
#
# A user adds a device from Jarvis; the method follows what the device can do:
#   code   it declares the API actions pair_begin and pair_confirm (it has a
#          screen): it shows a 6-digit code and the user types it into Jarvis.
#   adopt  anything else (a voice assistant, a relay): it cannot prove
#          possession, so it is adopted by whoever adds it, and Identity will
#          not move an already-owned device on an unverified claim.
# Either way Jarvis sets the device's API encryption key (so from then on
# only Jarvis can talk to it), mints an API key for the user, hands over its
# own URL if the device takes configure_jarvis, records the connection in
# esphome_devices and the ownership in Identity's device registry.

PAIR_ACTIONS = ("pair_begin", "pair_confirm")
ESPHOME_MDNS_TYPE = "_esphomelib._tcp.local."


async def _connect(host: str, port: int, psk: str | None = None):
    client = aioesphomeapi.APIClient(host, port, None, noise_psk=psk)
    try:
        await asyncio.wait_for(client.connect(login=True), timeout=_CONNECT_TIMEOUT_SECONDS)
    except aioesphomeapi.RequiresEncryptionAPIError as e:
        raise EsphomeConfigError(
            "This device already has an encryption key (from its YAML, or it is paired with "
            "someone). Unpair it on the device, or enter its key in Admin → Hardware."
        ) from e
    return client


def pair_method(services) -> str:
    names = {s.name for s in services}
    return "code" if set(PAIR_ACTIONS) <= names else "adopt"


async def _respond(client, services, name: str, data: dict):
    service = next((s for s in services if s.name == name), None)
    if service is None:
        raise EsphomeConfigError(f"The device has no '{name}' action.")
    resp = await client.execute_service(service, data, return_response=True)
    if resp is not None and not resp.success:
        raise EsphomeConfigError(resp.error_message or f"The device refused '{name}'.")
    return resp


async def discover_devices(seconds: float = 3.0) -> list[dict]:
    """ESPHome devices announcing themselves on the LAN (mDNS), not yet registered."""
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    found: dict[str, dict] = {}
    names: set[str] = set()

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            names.add(name)

    azc = AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(azc.zeroconf, [ESPHOME_MDNS_TYPE], handlers=[on_change])
        await asyncio.sleep(seconds)
        for name in names:
            info = AsyncServiceInfo(ESPHOME_MDNS_TYPE, name)
            if not await info.async_request(azc.zeroconf, 1500):
                continue
            addrs = info.parsed_addresses()
            if not addrs:
                continue
            props = {k.decode(): (v.decode() if isinstance(v, bytes) else v) for k, v in (info.properties or {}).items()}
            short = name.removesuffix("." + ESPHOME_MDNS_TYPE)
            found[short] = {
                "name": short,
                "friendly_name": props.get("friendly_name") or short,
                "host": addrs[0],
                "port": info.port or DEFAULT_ESPHOME_PORT,
                "mac": props.get("mac"),
                "provisioned": props.get("api_encryption") is not None,
            }
        await browser.async_cancel()
    finally:
        await azc.async_close()
    try:
        registered = {cfg["host"] for cfg in (await get_devices(force_refresh=True)).values()}
    except EsphomeConfigError:
        registered = set()
    return sorted((d for d in found.values() if d["host"] not in registered), key=lambda d: d["friendly_name"].lower())


async def pair_start(host: str, port: int = DEFAULT_ESPHOME_PORT) -> dict:
    """Look at the device and, if it can show a code, ask it to."""
    client = await _connect(host, port)
    try:
        info = await client.device_info()
        _entities, services = await client.list_entities_services()
        method = pair_method(services)
        if method == "code":
            await _respond(client, services, "pair_begin", {})
        return {
            "method": method,
            "name": info.name,
            "friendly_name": info.friendly_name or info.name,
            "mac": info.mac_address,
            "model": info.model,
            "esphome_version": info.esphome_version,
            "host": host,
            "port": port,
        }
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


async def _device_list() -> list[dict]:
    """The esphome_devices setting as a list (empty when unset)."""
    raw = await _read_global_setting("esphome_devices")
    try:
        current = json.loads(raw) if raw and str(raw).strip() else []
    except json.JSONDecodeError as e:
        raise EsphomeConfigError(f"'esphome_devices' is not valid JSON: {e}") from e
    return [d for d in current if isinstance(d, dict)] if isinstance(current, list) else []


async def _write_device_list(devices: list[dict]) -> None:
    status, _ = await _identity_post("/api/settings", {"esphome_devices": json.dumps(devices)})
    if status != 200:
        raise EsphomeConfigError(f"Identity returned {status} saving esphome_devices.")
    _device_cache.clear()


def _without(devices: list[dict], name: str) -> list[dict]:
    return [d for d in devices if str(d.get("name", "")).lower() != name.lower()]


async def _save_device(name: str, entry: dict) -> None:
    """Upsert one device in the esphome_devices setting."""
    await _write_device_list([*_without(await _device_list(), name), {"name": name, **entry}])


async def _remove_device(name: str) -> None:
    await _write_device_list(_without(await _device_list(), name))


async def _claim(body: dict) -> dict:
    status, data = await _identity_post("/api/internal/devices/claim", body)
    if status != 200:
        raise EsphomeConfigError(data.get("detail") or f"Identity returned {status} linking the device.")
    return data


async def pair_finish(host: str, port: int, code: str | None, user: str, user_api_key: str | None,
                      kind: str | None = None, jarvis_url: str | None = None) -> dict:
    """Set Jarvis's key on the device, confirm the code (if it shows one), hand over
    a key and URL, and link the device to `user`."""
    url = await get_jarvis_device_url(jarvis_url)  # fail before touching the device
    if not user_api_key:
        raise EsphomeConfigError("No API key in the user context; sign in again.")
    psk = base64.b64encode(os.urandom(32)).decode()

    client = await _connect(host, port)
    try:
        info = await client.device_info()
        _entities, services = await client.list_entities_services()
        method = pair_method(services)
        if method == "code" and not (code or "").strip():
            raise EsphomeConfigError(f"Enter the code shown on {info.friendly_name or info.name}.")
        if not await client.noise_encryption_set_key(psk.encode()):
            raise EsphomeConfigError("The device would not take an encryption key.")
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
    # Recorded at once: from here only this key reaches the device, so it must
    # not be lost to a later failure (a screenless device has no guard that
    # would wipe it).
    await _save_device(info.name, {"host": host, "port": port, "noise_psk": psk, "owner": user, "jarvis_url": url})

    client = await _connect(host, port, psk)
    minted = False
    try:
        _entities, services = await client.list_entities_services()
        if method == "code":
            try:
                await _respond(client, services, "pair_confirm", {"code": code.strip(), "owner": user})
            except EsphomeConfigError:
                # wrong code: give the device back its unclaimed state now
                with contextlib.suppress(Exception):
                    await client.noise_encryption_set_key(b"")
                await _remove_device(info.name)
                raise
        minted = await _send_endpoint(client, services, url,
                                      lambda: mint_device_api_key(user_api_key, info.name))
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()

    device = await _claim({
        "device_key": f"esphome:{(info.mac_address or info.name).replace(':', '').lower()}",
        "kind": kind or ("watch" if method == "code" else "assistant"),
        "label": info.friendly_name or info.name,
        "owner_username": user,
        "verified": method == "code",
        "esphome_version": info.esphome_version,
        "app_version": info.project_version or None,
        "hardware": info.model,
        "ip_address": host,
    })
    return {"method": method, "name": info.name, "friendly_name": info.friendly_name or info.name,
            "url_sent": minted, "device": device}
