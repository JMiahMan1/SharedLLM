# services/execution/device_registry.py
"""
Persistent device IP/MAC/hostname registry for TV/media devices.

Stores discovered network information per HA entity_id. Used by Roku ECP,
WebOS WOL, Samsung direct calls, Android TV ADB, Chromecast DIAL, and other
integrations that need network-level access to devices.

Storage: JSON file at /data/device_registry.json (volume-mounted as execution_data).

Schema:
{
  "devices": {
    "media_player.28_tcl_roku_tv": {
      "ip": "192.168.2.166",
      "mac": "cc:b0:da:c6:8f:21",
      "hostname": "28TCLRokuTV.local",
      "integration": "roku",
      "friendly_name": "Gracies TV",
      "device_class": "tv",
      "last_seen": 1715961234.5,
      "last_verified": 1715961234.5,
      "discovery_method": "ssdp",
      "metadata": {
        "manufacturer": "TCL",
        "model": "50S435",
        "serial": "2N0062385487"
      }
    }
  }
}
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("execution.device_registry")

REGISTRY_PATH = os.environ.get("DEVICE_REGISTRY_PATH", "/data/device_registry.json")


def _load() -> dict:
    try:
        path = Path(REGISTRY_PATH)
        if path.exists():
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"[device_registry] Failed to load: {e}")
    return {"devices": {}}


def _save(data: dict) -> None:
    try:
        path = Path(REGISTRY_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.error(f"[device_registry] Failed to save: {e}")


def get_device(entity_id: str) -> Optional[dict]:
    """Get stored device info for an entity."""
    data = _load()
    return data.get("devices", {}).get(entity_id)


def set_device(
    entity_id: str,
    ip: Optional[str] = None,
    mac: Optional[str] = None,
    hostname: Optional[str] = None,
    integration: Optional[str] = None,
    friendly_name: Optional[str] = None,
    device_class: Optional[str] = None,
    metadata: Optional[dict] = None,
    discovery_method: Optional[str] = None,
) -> dict:
    """Store or update device info. Returns the updated device record."""
    data = _load()
    devices = data.setdefault("devices", {})
    device = devices.setdefault(entity_id, {})

    if ip:
        device["ip"] = ip
        device["last_seen"] = time.time()
        device["last_verified"] = time.time()
    if mac:
        device["mac"] = mac
    if hostname:
        device["hostname"] = hostname
    if integration:
        device["integration"] = integration
    if friendly_name:
        device["friendly_name"] = friendly_name
    if device_class:
        device["device_class"] = device_class
    if metadata:
        device.setdefault("metadata", {}).update(metadata)
    if discovery_method:
        device["discovery_method"] = discovery_method

    device["last_updated"] = time.time()
    devices[entity_id] = device
    _save(data)
    log.info(f"[device_registry] Updated {entity_id}: ip={ip} mac={mac} method={discovery_method}")
    return device


def invalidate_device(entity_id: str, reason: str = "connection_failed") -> None:
    """Mark cached IP as stale (call on connection errors)."""
    data = _load()
    device = data.get("devices", {}).get(entity_id)
    if device and "ip" in device:
        old_ip = device["ip"]
        device["ip_stale"] = True
        device["ip_stale_reason"] = reason
        device["ip_stale_at"] = time.time()
        _save(data)
        log.info(f"[device_registry] Invalidated IP for {entity_id} (was {old_ip}, reason: {reason})")


def clear_stale(entity_id: str) -> None:
    """Remove stale flag after successful re-discovery."""
    data = _load()
    device = data.get("devices", {}).get(entity_id)
    if device:
        device.pop("ip_stale", None)
        device.pop("ip_stale_reason", None)
        device.pop("ip_stale_at", None)
        _save(data)


def list_devices() -> dict:
    """Return all registered devices."""
    return _load().get("devices", {})


def find_by_ip(ip: str) -> Optional[str]:
    """Find entity_id by IP address."""
    devices = _load().get("devices", {})
    for entity_id, info in devices.items():
        if info.get("ip") == ip:
            return entity_id
    return None


def find_by_mac(mac: str) -> Optional[str]:
    """Find entity_id by MAC address."""
    devices = _load().get("devices", {})
    mac_normalized = mac.lower().replace("-", ":")
    for entity_id, info in devices.items():
        if info.get("mac", "").lower().replace("-", ":") == mac_normalized:
            return entity_id
    return None


def remove_device(entity_id: str) -> bool:
    """Remove a device from the registry."""
    data = _load()
    if entity_id in data.get("devices", {}):
        del data["devices"][entity_id]
        _save(data)
        log.info(f"[device_registry] Removed {entity_id}")
        return True
    return False


def needs_rediscovery(entity_id: str, max_age_seconds: int = 86400) -> bool:
    """Check if a device needs re-discovery (stale or never discovered)."""
    device = get_device(entity_id)
    if not device:
        return True
    if device.get("ip_stale"):
        return True
    last_verified = device.get("last_verified", 0)
    return (time.time() - last_verified) > max_age_seconds
