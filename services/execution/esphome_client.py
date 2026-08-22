# services/execution/esphome_client.py
"""Direct native-API client for ESPHome devices.

Talks straight to devices over the ESPHome native TCP protocol
(aioesphomeapi) instead of routing through Home Assistant, so automation
commands keep working when HA is down and skip one network hop on the
FastPath.

Device registry lives in the Identity service as the GlobalSetting
``esphome_devices`` — a JSON list:

    [{"name": "office-light", "host": "192.168.2.87",
      "port": 6053, "noise_psk": "base64-key-from-device-yaml"}]

``port`` defaults to 6053 and ``noise_psk`` is optional (only needed when
the device YAML enables ``api: encryption``). Per repo rules there is no
hardcoded fallback device list: an empty/unset setting raises immediately.
"""

import asyncio
import contextlib
import json
import logging
import time

import aiohttp

try:
    import aioesphomeapi
except ImportError as e:  # pragma: no cover - dependency missing
    raise ImportError(
        "aioesphomeapi is required for direct ESPHome control. "
        "Add it to services/execution requirements."
    ) from e

from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

log = logging.getLogger("execution.esphome")

DEFAULT_ESPHOME_PORT = 6053
_SETTINGS_TTL_SECONDS = 60.0
_CONNECT_TIMEOUT_SECONDS = 10.0

# Cached device configs: {"name": {"host": ..., "port": ..., "noise_psk": ...}}
_device_cache: dict[str, dict] = {}
_cache_loaded_at: float = 0.0


class EsphomeConfigError(RuntimeError):
    """Raised when the esphome_devices setting is missing or malformed."""


async def _load_devices_from_identity() -> dict[str, dict]:
    """Fetch the esphome_devices GlobalSetting from the Identity service."""
    async with aiohttp.ClientSession() as client:
        resp = await client.get(
            f"{IDENTITY_SVC_URL}/api/settings",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        if resp.status != 200:
            raise EsphomeConfigError(
                f"Identity returned {resp.status} reading settings; "
                "cannot resolve esphome_devices."
            )
        settings = await resp.json()
    raw_value = None
    for entry in settings:
        if entry.get("key") == "esphome_devices":
            raw_value = entry.get("value")
            break
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
    client = aioesphomeapi.APIClient(
        cfg["host"],
        cfg["port"],
        None,
        noise_psk=cfg.get("noise_psk"),
    )
    await asyncio.wait_for(client.connect(), timeout=_CONNECT_TIMEOUT_SECONDS)
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
