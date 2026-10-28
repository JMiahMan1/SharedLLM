# services/execution/esphome_client.py
"""Direct native-API client for ESPHome devices.

Talks straight to devices over the ESPHome native TCP protocol
(aioesphomeapi) instead of routing through Home Assistant, so automation
commands keep working when HA is down and skip one network hop on the
FastPath.

Device registry lives in the Identity service as the GlobalSetting
``esphome_devices``, a JSON list:

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
import gzip
import hashlib
import json
import logging
import math
import os
import re
import secrets
import socket
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

# A companion watch sleeps between wakes, so a connection attempt that fails is
# the expected answer rather than a problem, and the caller already treats it as
# one: watch_glance logs it at debug ("asleep or out of range: next round").
# aioesphomeapi logs its own WARNING for every attempt, which for a watch that
# is re-polled every two minutes filled the log with hundreds of lines a day and
# buried anything real. Its errors still surface -- the client raises them and
# the callers report them.
logging.getLogger("aioesphomeapi").setLevel(logging.ERROR)

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
            "device_key": (str(item["device_key"]).strip().lower() or None) if item.get("device_key") else None,
            "friendly_name": (str(item["friendly_name"]).strip() or None) if item.get("friendly_name") else None,
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


def _resolve_device_cfg(devices: dict[str, dict], name: str) -> dict | None:
    """Resolve a configured ESPHome device by exact name, slugified friendly
    label ('Jarvis Watch' -> 'jarvis-watch'), stripped 'esphome:' prefix, or
    stored device_key / friendly_name."""
    raw = str(name or "").strip().lower()
    if not raw:
        return None
    if raw in devices:
        return devices[raw]
    slug = re.sub(r"[\s_]+", "-", raw)
    if slug in devices:
        return devices[slug]
    without_prefix = re.sub(r"^esphome:", "", raw)
    if without_prefix in devices:
        return devices[without_prefix]
    slug_without_prefix = re.sub(r"[\s_]+", "-", without_prefix)
    if slug_without_prefix in devices:
        return devices[slug_without_prefix]
    for cfg in devices.values():
        dk = (cfg.get("device_key") or "").strip().lower()
        fn = (cfg.get("friendly_name") or "").strip().lower()
        if raw in (dk, fn) or (fn and re.sub(r"[\s_]+", "-", fn) == slug):
            return cfg
    return None


async def get_device(name: str) -> dict:
    devices = await get_devices()
    cfg = _resolve_device_cfg(devices, name)
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


_STATE_SETTLE_SECONDS = 0.35


def _str_or_none(val) -> str | None:
    return val if isinstance(val, str) and val else None


def _num_or_none(val) -> float | int | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def infer_esphome_kind(info, entities: list, services: list, method: str = "adopt") -> str:
    """Infer an ESPHome companion device's kind from its exposed services, entities, and name."""
    svc_names = {str(getattr(s, "name", "") or "").lower() for s in (services or [])}
    domains = {_entity_domain(e) for e in (entities or []) if _entity_domain(e)}
    dev_name = f"{_str_or_none(getattr(info, 'name', None)) or ''} {_str_or_none(getattr(info, 'friendly_name', None)) or ''}".lower()
    if method == "code" or "find_watch" in svc_names:
        if "show_page" in svc_names or "set_rotation" in svc_names:
            return "screen"
        return "watch"
    if "show_page" in svc_names or "set_rotation" in svc_names or any(
        tok in dev_name for tok in ("screen", "cyd", "waveshare", "fnk0104", "display", "panel")
    ):
        return "screen"
    if "media_player" in domains or "speaker" in dev_name:
        return "speaker"
    non_diag_domains = domains - {"button", "sensor", "text_sensor", "binary_sensor"}
    if non_diag_domains == {"light"} and "light" in dev_name:
        return "light"
    return "assistant"


def _entity_metadata(domain: str | None, entity) -> dict:
    """Extract domain-specific configuration metadata from an EntityInfo object."""
    meta: dict = {}
    icon = _str_or_none(getattr(entity, "icon", None))
    if icon:
        meta["icon"] = icon
    cat = getattr(entity, "entity_category", None)
    if isinstance(cat, int) and not isinstance(cat, bool) and cat > 0:
        meta["entity_category"] = int(cat)
    if domain == "sensor":
        unit = _str_or_none(getattr(entity, "unit_of_measurement", None))
        if unit:
            meta["unit"] = unit
        acc = getattr(entity, "accuracy_decimals", None)
        if isinstance(acc, int) and not isinstance(acc, bool):
            meta["accuracy_decimals"] = acc
        dcls = _str_or_none(getattr(entity, "device_class", None))
        if dcls:
            meta["device_class"] = dcls
    elif domain == "number":
        for attr in ("min_value", "max_value", "step"):
            v = _num_or_none(getattr(entity, attr, None))
            if v is not None:
                meta[attr] = v
        unit = _str_or_none(getattr(entity, "unit_of_measurement", None))
        if unit:
            meta["unit"] = unit
    elif domain == "select":
        opts = getattr(entity, "options", None)
        if isinstance(opts, (list, tuple)):
            meta["options"] = [str(o) for o in opts]
    elif domain == "text":
        for attr in ("min_length", "max_length"):
            v = _num_or_none(getattr(entity, attr, None))
            if v is not None:
                meta[attr] = int(v)
    elif domain == "light":
        effects = getattr(entity, "effects", None)
        if isinstance(effects, (list, tuple)) and effects:
            meta["effects"] = [str(e) for e in effects]
    elif domain == "media_player":
        sp = getattr(entity, "supports_pause", None)
        if isinstance(sp, bool):
            meta["supports_pause"] = sp
    return meta


def _serialize_entity_state(domain: str | None, entity, state_obj) -> dict:
    """Convert an aioesphomeapi EntityState into JSON-serializable state fields."""
    if state_obj is None or getattr(state_obj, "missing_state", False) is True:
        return {}
    if domain == "sensor":
        val = _num_or_none(getattr(state_obj, "state", None))
        if val is None:
            return {}
        acc = getattr(entity, "accuracy_decimals", None)
        if isinstance(val, float) and isinstance(acc, int) and not isinstance(acc, bool) and acc >= 0:
            val = round(val, acc) if acc > 0 else int(round(val))
        return {"state": val}
    if domain in ("binary_sensor", "switch"):
        raw = getattr(state_obj, "state", None)
        return {"state": bool(raw)} if isinstance(raw, bool) else {}
    if domain in ("text_sensor", "select", "text"):
        raw = getattr(state_obj, "state", None)
        return {"state": str(raw)} if isinstance(raw, str) else {}
    if domain == "number":
        val = _num_or_none(getattr(state_obj, "state", None))
        return {"state": val} if val is not None else {}
    if domain == "light":
        out: dict = {"state": bool(getattr(state_obj, "state", False))}
        bri = _num_or_none(getattr(state_obj, "brightness", None))
        if bri is not None:
            out["brightness_pct"] = int(round(float(bri) * 100))
        eff = _str_or_none(getattr(state_obj, "effect", None))
        if eff:
            out["effect"] = eff
        return out
    if domain == "media_player":
        raw_state = getattr(state_obj, "state", None)
        state_name = _str_or_none(getattr(raw_state, "name", None))
        if not state_name and isinstance(raw_state, int) and not isinstance(raw_state, bool):
            with contextlib.suppress(ValueError):
                state_name = aioesphomeapi.MediaPlayerState(raw_state).name
        media_state = (state_name or "idle").lower()
        out = {"state": media_state, "muted": bool(getattr(state_obj, "muted", False))}
        vol = _num_or_none(getattr(state_obj, "volume", None))
        if vol is not None:
            out["volume_pct"] = int(round(float(vol) * 100))
        return out
    if domain == "update":
        cur = _str_or_none(getattr(state_obj, "current_version", None)) or ""
        lat = _str_or_none(getattr(state_obj, "latest_version", None)) or ""
        return {
            "state": lat or cur or "idle",
            "current_version": cur,
            "latest_version": lat,
            "in_progress": bool(getattr(state_obj, "in_progress", False)),
        }
    val = getattr(state_obj, "state", None)
    if isinstance(val, (str, int, float, bool)):
        return {"state": val}
    return {}


async def _collect_live_states(client, entities: list) -> dict[int, object]:
    """Subscribe to live entity states and wait briefly for the initial snapshot."""
    stateful_keys = {
        getattr(e, "key", None)
        for e in (entities or [])
        if _entity_domain(e) not in ("button", "camera", "event", None)
    } - {None}
    if not stateful_keys:
        return {}
    sub_fn = getattr(client, "subscribe_states", None)
    if not callable(sub_fn):
        return {}
    states: dict[int, object] = {}
    all_done = asyncio.Event()

    def on_state(state):
        key = getattr(state, "key", None)
        if key is not None:
            states[key] = state
            if stateful_keys <= states.keys():
                all_done.set()

    try:
        res = sub_fn(on_state)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:
        log.debug(f"[esphome] subscribe_states skipped: {exc}")
        return states

    # Avoid sleeping in unit tests where APIClient is a plain MagicMock without side_effect
    is_unconfigured_mock = (
        type(sub_fn).__module__.startswith("unittest.mock")
        and getattr(sub_fn, "side_effect", None) is None
    )
    if not is_unconfigured_mock and not all_done.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(all_done.wait(), timeout=_STATE_SETTLE_SECONDS)
    return states


async def list_entities(device_name: str) -> dict:
    """Return device info plus a summary of every exposed entity (with live state) and service."""
    cfg = await get_device(device_name)

    async def op(client):
        info, entities, services = await client.device_info_and_list_entities()
        states = await _collect_live_states(client, entities)
        out = []
        for entity in entities:
            domain = _entity_domain(entity)
            row = {
                "domain": domain,
                "name": getattr(entity, "name", ""),
                "object_id": getattr(entity, "object_id", ""),
                "key": entity.key,
                **_entity_metadata(domain, entity),
                **_serialize_entity_state(domain, entity, states.get(entity.key)),
            }
            out.append(row)
        svc_out = [
            {
                "name": getattr(s, "name", ""),
                "args": [getattr(a, "name", "") for a in (getattr(s, "args", None) or [])],
            }
            for s in (services or [])
        ]
        webserver_port = getattr(info, "webserver_port", None)
        device = {
            "name": info.friendly_name or info.name,
            "node_name": _str_or_none(getattr(info, "name", None)),
            "friendly_name": _str_or_none(getattr(info, "friendly_name", None)) or _str_or_none(getattr(info, "name", None)),
            "esphome_version": info.esphome_version,
            "model": info.model,
            "mac_address": _str_or_none(getattr(info, "mac_address", None)),
            "project_name": _str_or_none(getattr(info, "project_name", None)),
            "project_version": _str_or_none(getattr(info, "project_version", None)),
            "has_deep_sleep": bool(getattr(info, "has_deep_sleep", False))
            if isinstance(getattr(info, "has_deep_sleep", None), bool)
            else False,
            "webserver_port": int(webserver_port)
            if isinstance(webserver_port, int) and not isinstance(webserver_port, bool) and webserver_port > 0
            else None,
            "host": cfg.get("host"),
            "inferred_kind": infer_esphome_kind(info, entities, services, pair_method(services or [])),
        }
        return device, out, svc_out

    device, entities, services = await _with_connection(cfg, op)
    has_speaker = any(e.get("domain") in ("media_player", "siren") for e in entities)
    return {
        "device": device,
        "entities": entities,
        "services": services,
        "has_speaker": has_speaker,
    }


def _match_service(services: list, wanted: str):
    """Match an ESPHome UserService by name (tolerating script./service. prefixes)."""
    target = wanted.strip().lower()
    for prefix in ("script.", "service.", "esphome."):
        if target.startswith(prefix):
            target = target[len(prefix):]
    for svc in services or []:
        name = str(getattr(svc, "name", "") or "").lower()
        if name == target or name.endswith(f"_{target}"):
            return svc
    return None


def _coerce_service_args(service, raw_params: dict | None) -> dict:
    """Build the argument dict required by aioesphomeapi.execute_service for `service`."""
    params = dict(raw_params or {})
    out: dict = {}
    arg_type_cls = getattr(aioesphomeapi, "UserServiceArgType", None)
    for arg_desc in getattr(service, "args", None) or []:
        aname = getattr(arg_desc, "name", "")
        atype = getattr(arg_desc, "type", None)
        if aname in params and params[aname] is not None:
            val = params[aname]
        else:
            # Sensible defaults so execute_service never raises KeyError
            if arg_type_cls is not None:
                if atype == getattr(arg_type_cls, "BOOL", -1):
                    val = False
                elif atype == getattr(arg_type_cls, "INT", -1):
                    val = 0
                elif atype == getattr(arg_type_cls, "FLOAT", -1):
                    val = 0.0
                elif atype in (
                    getattr(arg_type_cls, "BOOL_ARRAY", -1),
                    getattr(arg_type_cls, "INT_ARRAY", -1),
                    getattr(arg_type_cls, "FLOAT_ARRAY", -1),
                    getattr(arg_type_cls, "STRING_ARRAY", -1),
                ):
                    val = []
                else:
                    val = ""
            else:
                val = ""
        if arg_type_cls is not None:
            if atype == getattr(arg_type_cls, "BOOL", -1):
                val = bool(val)
            elif atype == getattr(arg_type_cls, "INT", -1):
                val = int(val)
            elif atype == getattr(arg_type_cls, "FLOAT", -1):
                val = float(val)
            elif atype == getattr(arg_type_cls, "STRING", -1):
                val = str(val)
        out[aname] = val
    return out


async def _execute_user_service(client, service, raw_params: dict | None = None):
    """Execute an ESPHome UserService/action, safely handling response flags."""
    args_data = _coerce_service_args(service, raw_params)
    supports_resp = getattr(service, "supports_response", None)
    none_type = getattr(getattr(aioesphomeapi, "SupportsResponseType", None), "NONE", None)
    want_resp = True if (supports_resp and supports_resp != none_type) else None
    return await client.execute_service(service, args_data, return_response=want_resp)


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


async def find_device_for_ha_entity(
    ha_entity_id: str,
    devices: dict[str, dict] | None = None,
) -> str | None:
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
    if devices is None:
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
    aliases = {"PLAY_PAUSE": "PLAY", "RESUME": "PLAY"}
    try:
        return aioesphomeapi.MediaPlayerCommand[aliases.get(cmd, cmd)]
    except KeyError:
        valid = [c.name for c in aioesphomeapi.MediaPlayerCommand]
        raise ValueError(f"Invalid media player command '{value}'. Valid: {valid}") from None


async def call_entity(device_name: str, entity_name: str, params: dict | None = None) -> dict:
    """Send a command to one entity or script/service on a device; returns a result summary."""
    cfg = await get_device(device_name)
    params = params or {}

    def build_op():
        async def op(client):
            _info, entities, services = await client.device_info_and_list_entities()
            entity = _match_entity(entities, entity_name)
            if entity is None:
                svc = _match_service(services, entity_name)
                if svc is not None:
                    await _execute_user_service(client, svc, params)
                    return {"domain": "service", "name": getattr(svc, "name", entity_name), "params": params}
                available = [
                    f"{_entity_domain(e)}/{getattr(e, 'object_id', '')}" for e in entities
                ] + [f"service/{getattr(s, 'name', '')}" for s in (services or [])]
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
                light_kwargs = {
                    "state": (bool(state) if state is not None else None),
                    "brightness": (float(brightness) / 100.0 if brightness is not None else None),
                    "rgb": tuple(params["rgb"]) if params.get("rgb") else None,
                }
                if params.get("effect") is not None:
                    light_kwargs["effect"] = str(params["effect"])
                client.light_command(key, **light_kwargs)
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
                vol = params.get("volume")
                if vol is None and params.get("volume_pct") is not None:
                    vol = float(params["volume_pct"]) / 100.0
                client.media_player_command(
                    key,
                    command=_media_command(params["command"]) if params.get("command") else None,
                    volume=(float(vol) if vol is not None else None),
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
            elif domain == "text":
                value = params.get("value")
                if value is None:
                    raise ValueError("text entities need params.value")
                client.text_command(key, str(value))
            elif domain == "update":
                cmd_name = str(params.get("command") or "install").strip().upper()
                update_cmd = (
                    aioesphomeapi.UpdateCommand.CHECK
                    if cmd_name == "CHECK"
                    else aioesphomeapi.UpdateCommand.INSTALL
                )
                client.update_command(key, update_cmd)
            elif domain == "lock":
                cmd_name = str(params.get("command") or ("LOCK" if params.get("state", True) else "UNLOCK")).strip().upper()
                lock_cmd = getattr(aioesphomeapi.LockCommand, cmd_name, aioesphomeapi.LockCommand.LOCK)
                client.lock_command(key, lock_cmd)
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


# Standard ESPHome onscreen / script actions checked when a timer or alarm fires
SCREEN_ALERT_SERVICES = (
    "flash_screen",
    "alarm_flash",
    "screen_flash",
    "alarm_trigger",
    "timer_alert",
    "show_alarm",
    "trigger_alarm",
    "notify_screen",
    "play_rtttl",
    "set_glance",
)

STOP_ALERT_SERVICES = (
    "stop_flash",
    "alarm_stop",
    "stop_alarm",
    "clear_alarm",
    "dismiss_alarm",
)


async def trigger_device_alert(
    device_name: str,
    *,
    title: str,
    message: str,
    media_url: str | None = None,
    emoji: str | None = None,
    sound: str | None = None,
    rtttl: str | None = None,
    script_name: str | None = None,
    flash_screen: bool = True,
    until_button: bool = True,
) -> dict:
    """Trigger an alarm/timer on an ESPHome device.

    - If the device has a speaker (`media_player` or `siren`), plays `media_url`
      or triggers the siren.
    - If `script_name` is specified, OR if the device has NO speaker (or
      `flash_screen` is requested), triggers the device's onscreen flash /
      alarm script (until a button is pressed) or falls back to a flash
      button/switch/light entity on the device.
    """
    cfg = await get_device(device_name)

    async def op(client):
        _info, entities, services = await client.device_info_and_list_entities()
        services = services or []
        triggered: list[str] = []
        has_speaker = False

        display_emoji = emoji or "⏰"
        glance_text = f"{display_emoji} {title}"[:60]
        service_payload = {
            "title": title,
            "name": title,
            "label": title,
            "message": message,
            "text": message,
            "body": message,
            "line": glance_text,
            "emoji": display_emoji,
            "icon": display_emoji,
            "sound": sound or "classic_alarm",
            "tone": sound or "classic_alarm",
            "rtttl": rtttl or "alarm:d=4,o=5,b=180:c6,p,c6,p,g5,p",
            "song_str": rtttl or "alarm:d=4,o=5,b=180:c6,p,c6,p,g5,p",
            "until_button": bool(until_button),
            "wait_for_button": bool(until_button),
            "flash": bool(flash_screen),
            "loop": bool(until_button),
            "duration": 60,
            "duration_s": 60,
            "seconds": 60,
        }

        # 1. Explicit script/service or entity requested
        if script_name:
            svc = _match_service(services, script_name)
            if svc is not None:
                await _execute_user_service(client, svc, service_payload)
                triggered.append(f"service:{svc.name}")
            else:
                ent = _match_entity(entities, script_name)
                if ent is not None:
                    dom = _entity_domain(ent)
                    if dom == "button":
                        client.button_command(ent.key)
                        triggered.append(f"button:{getattr(ent, 'object_id', script_name)}")
                    elif dom == "switch":
                        client.switch_command(ent.key, state=True)
                        triggered.append(f"switch:{getattr(ent, 'object_id', script_name)}")
                    elif dom == "light":
                        client.light_command(ent.key, state=True, brightness=1.0)
                        triggered.append(f"light:{getattr(ent, 'object_id', script_name)}")

        # 2. Speaker playback (media_player / siren) if present on the device
        for ent in entities:
            dom = _entity_domain(ent)
            if dom == "media_player" and media_url:
                has_speaker = True
                client.media_player_command(ent.key, media_url=media_url, announcement=True)
                triggered.append(f"media_player:{getattr(ent, 'object_id', 'speaker')}")
            elif dom == "siren":
                has_speaker = True
                if sound:
                    client.siren_command(ent.key, tone=str(sound), state=True)
                else:
                    client.siren_command(ent.key, state=True)
                triggered.append(f"siren:{getattr(ent, 'object_id', 'siren')}")

        # 3. If no speaker (or flash_screen requested), trigger onscreen flash / script
        if not has_speaker or flash_screen:
            for candidate in SCREEN_ALERT_SERVICES:
                svc = _match_service(services, candidate)
                if svc is not None and f"service:{svc.name}" not in triggered:
                    await _execute_user_service(client, svc, service_payload)
                    triggered.append(f"service:{svc.name}")
                    # Stop after the first primary screen-alert service (unless it's play_rtttl, which can pair with a screen flash)
                    if candidate != "play_rtttl":
                        break

        # 4. Fallback for speakerless devices with no matching service: check buttons/switches/lights
        if not triggered:
            for ent in entities:
                dom = _entity_domain(ent)
                oid = str(getattr(ent, "object_id", "") or getattr(ent, "name", "")).lower()
                if dom == "button" and any(k in oid for k in ("flash", "alarm", "alert", "screen", "buzzer", "ring")):
                    client.button_command(ent.key)
                    triggered.append(f"button:{oid}")
                    break
                if dom == "switch" and any(k in oid for k in ("flash", "alarm", "alert", "screen", "buzzer", "ring")):
                    client.switch_command(ent.key, state=True)
                    triggered.append(f"switch:{oid}")
                    break
            if not triggered:
                for ent in entities:
                    if _entity_domain(ent) == "light":
                        client.light_command(ent.key, state=True, brightness=1.0)
                        triggered.append(f"light:{getattr(ent, 'object_id', 'light')}")
                        break

        return {
            "device": device_name,
            "has_speaker": has_speaker,
            "triggered": triggered,
        }

    return await _with_connection(cfg, op)


async def stop_device_alert(device_name: str) -> dict:
    """Stop any active alarm/screen flash on an ESPHome device."""
    cfg = await get_device(device_name)

    async def op(client):
        _info, entities, services = await client.device_info_and_list_entities()
        services = services or []
        stopped: list[str] = []

        for candidate in STOP_ALERT_SERVICES:
            svc = _match_service(services, candidate)
            if svc is not None:
                await _execute_user_service(client, svc, {})
                stopped.append(f"service:{svc.name}")

        # Also clear glance line if the device uses set_glance
        glance_svc = _match_service(services, "set_glance")
        if glance_svc is not None:
            await _execute_user_service(client, glance_svc, {"line": ""})
            stopped.append("service:set_glance")

        for ent in entities:
            dom = _entity_domain(ent)
            oid = str(getattr(ent, "object_id", "") or getattr(ent, "name", "")).lower()
            if dom == "media_player":
                with contextlib.suppress(Exception):
                    client.media_player_command(ent.key, command=_media_command("STOP"))
                    stopped.append(f"media_player:{oid}")
            elif dom == "siren":
                client.siren_command(ent.key, state=False)
                stopped.append(f"siren:{oid}")
            elif dom == "switch" and any(k in oid for k in ("flash", "alarm", "alert", "screen", "buzzer", "ring")):
                client.switch_command(ent.key, state=False)
                stopped.append(f"switch:{oid}")
            elif dom == "button" and any(k in oid for k in ("stop", "dismiss", "ack", "clear")):
                client.button_command(ent.key)
                stopped.append(f"button:{oid}")

        return {"device": device_name, "stopped": stopped}

    return await _with_connection(cfg, op)



# Handing a device Jarvis's own endpoint
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


# Companion pairing
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
    entities = []
    try:
        entities, services = await client.list_entities_services()
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
        "kind": kind or infer_esphome_kind(info, entities, services, method),
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


# ---------------------------------------------------------------------------
# Remote Firmware Update (OTA)
#
# All sibling ESPHome devices (watches, smart speakers, LVGL smart screens,
# lights) declare `ota: - platform: esphome` (native TCP protocol on port 3232),
# and devices with `web_server:` also accept `POST http://<host>/update` with
# multipart field `update`. We try the native ESPHome OTA v1/v2 TCP protocol
# first and fall back to the HTTP web_server `/update` endpoint if TCP port 3232
# is refused.
# ---------------------------------------------------------------------------

OTA_DEFAULT_PORT = 3232
OTA_MAGIC_BYTES = b"\x6c\x26\xf7\x5c\x45"
OTA_TYPE_UPDATE_APP = 0x00

OTA_RESPONSE_OK = 0x00
OTA_RESPONSE_REQUEST_AUTH = 0x01
OTA_RESPONSE_REQUEST_SHA256_AUTH = 0x02
OTA_RESPONSE_HEADER_OK = 0x40
OTA_RESPONSE_AUTH_OK = 0x41
OTA_RESPONSE_UPDATE_PREPARE_OK = 0x42
OTA_RESPONSE_BIN_MD5_OK = 0x43
OTA_RESPONSE_RECEIVE_OK = 0x44
OTA_RESPONSE_UPDATE_END_OK = 0x45
OTA_RESPONSE_SUPPORTS_COMPRESSION = 0x46
OTA_RESPONSE_CHUNK_OK = 0x47
OTA_RESPONSE_FEATURE_FLAGS = 0x48

OTA_CLIENT_FEATURE_COMPRESSION = 0x01
OTA_CLIENT_FEATURE_SHA256_AUTH = 0x02
OTA_CLIENT_FEATURE_EXTENDED_PROTOCOL = 0x04
OTA_SERVER_FEATURE_COMPRESSION = 0x01

OTA_BLOCK_SIZE = 8192

_OTA_ERROR_MESSAGES: dict[int, str] = {
    0x80: "Invalid magic byte",
    0x81: "Device could not prepare flash memory for update (binary too big or flash busy).",
    0x82: "OTA authentication failed (wrong OTA password).",
    0x83: "Writing OTA data to flash memory failed.",
    0x84: "Finishing OTA update failed on the device.",
    0x85: "Invalid bootstrapping state; press the reset button on the ESP once after USB flashing.",
    0x88: "Not enough flash space on ESP8266 for this firmware image.",
    0x89: "The OTA partition on the ESP32 is too small for this firmware image.",
    0x8A: "No OTA partition found on the ESP32.",
    0x8B: "Firmware MD5 checksum mismatch during transfer.",
    0x8D: "Firmware signature verification failed.",
    0x93: "Device rejected the update due to OTA version downgrade protection.",
    0x94: "Device requires encrypted OTA.",
    0xFF: "Unknown OTA error reported by ESPHome device.",
}


def _ota_recv_exact(sock: socket.socket, count: int, step_desc: str, expect: int | tuple[int, ...] | None = None) -> bytes:
    buf = b""
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise EsphomeConfigError(f"ESPHome device closed connection during OTA ({step_desc}).")
        buf += chunk
        if len(buf) == len(chunk):
            first = buf[0]
            if first in _OTA_ERROR_MESSAGES:
                raise EsphomeConfigError(f"ESPHome OTA error ({step_desc}): {_OTA_ERROR_MESSAGES[first]}")
            if expect is not None:
                allowed = (expect,) if isinstance(expect, int) else expect
                if first not in allowed:
                    raise EsphomeConfigError(
                        f"Unexpected ESPHome OTA response during {step_desc}: 0x{first:02X}"
                    )
    return buf


def _esphome_native_ota_sync(
    host: str,
    port: int,
    firmware: bytes,
    password: str | None = None,
    timeout: float = 90.0,
) -> dict:
    """Push a firmware binary over ESPHome's native TCP OTA v1/v2 protocol."""
    sock = socket.create_connection((host, port), timeout=15.0)
    with contextlib.closing(sock):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(OTA_MAGIC_BYTES)

        _, version = _ota_recv_exact(sock, 2, "version handshake", OTA_RESPONSE_OK)
        if version not in (1, 2):
            raise EsphomeConfigError(f"Unsupported ESPHome OTA protocol version {version}.")

        features_to_send = (
            OTA_CLIENT_FEATURE_COMPRESSION
            | OTA_CLIENT_FEATURE_SHA256_AUTH
            | OTA_CLIENT_FEATURE_EXTENDED_PROTOCOL
        )
        sock.sendall(bytes([features_to_send]))
        feat_byte = _ota_recv_exact(sock, 1, "feature negotiation")[0]

        extended_proto = False
        if feat_byte == OTA_RESPONSE_FEATURE_FLAGS:
            extended_proto = True
            server_features = _ota_recv_exact(sock, 1, "extended feature flags")[0]
        elif feat_byte == OTA_RESPONSE_SUPPORTS_COMPRESSION:
            server_features = OTA_SERVER_FEATURE_COMPRESSION
        elif feat_byte == OTA_RESPONSE_HEADER_OK:
            server_features = 0
        else:
            server_features = 0

        if server_features & OTA_SERVER_FEATURE_COMPRESSION:
            upload_payload = gzip.compress(firmware, compresslevel=9)
            compressed = True
        else:
            upload_payload = firmware
            compressed = False

        auth_byte = _ota_recv_exact(
            sock,
            1,
            "auth challenge",
            (OTA_RESPONSE_AUTH_OK, OTA_RESPONSE_REQUEST_AUTH, OTA_RESPONSE_REQUEST_SHA256_AUTH),
        )[0]
        if auth_byte != OTA_RESPONSE_AUTH_OK:
            if not password:
                raise EsphomeConfigError("This ESPHome device requires an OTA password, but none was provided.")
            if auth_byte == OTA_RESPONSE_REQUEST_SHA256_AUTH:
                hash_fn, nonce_len = hashlib.sha256, 64
            else:
                hash_fn, nonce_len = hashlib.md5, 32
            nonce = _ota_recv_exact(sock, nonce_len, "auth nonce").decode("ascii", errors="replace")
            cnonce = secrets.token_hex(nonce_len // 2)
            sock.sendall(cnonce.encode("ascii"))
            h = hash_fn()
            h.update(password.encode("utf-8"))
            h.update(nonce.encode("ascii"))
            h.update(cnonce.encode("ascii"))
            sock.sendall(h.hexdigest().encode("ascii"))
            _ota_recv_exact(sock, 1, "auth verification", OTA_RESPONSE_AUTH_OK)

        sock.settimeout(timeout)
        if extended_proto:
            sock.sendall(bytes([OTA_TYPE_UPDATE_APP]))

        upload_size = len(upload_payload)
        size_bytes = bytes([
            (upload_size >> 24) & 0xFF,
            (upload_size >> 16) & 0xFF,
            (upload_size >> 8) & 0xFF,
            upload_size & 0xFF,
        ])
        sock.sendall(size_bytes)
        _ota_recv_exact(sock, 1, "flash prepare", OTA_RESPONSE_UPDATE_PREPARE_OK)

        upload_md5 = hashlib.md5(upload_payload).hexdigest()
        sock.sendall(upload_md5.encode("ascii"))
        _ota_recv_exact(sock, 1, "MD5 checksum check", OTA_RESPONSE_BIN_MD5_OK)

        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
        offset = 0
        while offset < upload_size:
            chunk = upload_payload[offset : offset + OTA_BLOCK_SIZE]
            offset += len(chunk)
            sock.sendall(chunk)
            if version >= 2:
                _ota_recv_exact(sock, 1, "chunk ack", OTA_RESPONSE_CHUNK_OK)

        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        _ota_recv_exact(sock, 1, "receive complete", OTA_RESPONSE_RECEIVE_OK)
        _ota_recv_exact(sock, 1, "update finalize", OTA_RESPONSE_UPDATE_END_OK)
        with contextlib.suppress(OSError):
            sock.sendall(bytes([OTA_RESPONSE_OK]))

    return {
        "method": "esphome_ota",
        "ota_version": version,
        "compressed": compressed,
        "uploaded_bytes": upload_size,
    }


async def _esphome_http_ota_push(
    host: str,
    web_port: int,
    firmware: bytes,
    filename: str = "firmware.bin",
    password: str | None = None,
    timeout: float = 90.0,
) -> dict:
    """Fallback OTA upload via ESPHome's built-in `web_server` (`POST /update`)."""
    form = aiohttp.FormData()
    form.add_field("update", firmware, filename=filename, content_type="application/octet-stream")
    auth = aiohttp.BasicAuth("admin", password) if password else None
    url = f"http://{host}:{web_port}/update"
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            url,
            data=form,
            auth=auth,
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        body = await resp.text()
        if resp.status >= 400:
            raise EsphomeConfigError(
                f"ESPHome web_server OTA at {url} returned HTTP {resp.status}: {body[:200]}"
            )
    return {
        "method": "web_server",
        "uploaded_bytes": len(firmware),
    }


async def ota_update(
    device_name: str,
    firmware: bytes,
    *,
    filename: str = "firmware.bin",
    password: str | None = None,
    ota_port: int = OTA_DEFAULT_PORT,
    web_port: int = 80,
) -> dict:
    """Push a compiled ESPHome `.bin` firmware image to a device over the air.

    Tries the native ESPHome OTA TCP protocol (`ota: platform: esphome`, default
    port 3232) first, and falls back to `web_server` `POST /update` on port 80
    if the TCP OTA port refuses the connection.
    """
    if not firmware:
        raise EsphomeConfigError("Firmware image is empty.")
    cfg = await get_device(device_name)
    host = cfg["host"]
    sha256 = hashlib.sha256(firmware).hexdigest()
    try:
        res = await asyncio.to_thread(
            _esphome_native_ota_sync, host, int(ota_port or OTA_DEFAULT_PORT), firmware, password
        )
    except (ConnectionRefusedError, TimeoutError, OSError) as tcp_err:
        log.info(
            f"[esphome] native OTA on {host}:{ota_port} failed ({tcp_err}); "
            f"trying web_server HTTP /update on port {web_port}"
        )
        try:
            res = await _esphome_http_ota_push(
                host, int(web_port or 80), firmware, filename=filename, password=password
            )
        except Exception as http_err:
            raise EsphomeConfigError(
                f"Could not push OTA firmware to '{device_name}' ({host}): "
                f"native OTA port {ota_port} failed ({tcp_err}) and web_server port {web_port} failed ({http_err})."
            ) from http_err

    return {
        "device": device_name,
        "host": host,
        "filename": filename,
        "bytes": len(firmware),
        "sha256": sha256,
        **res,
    }
