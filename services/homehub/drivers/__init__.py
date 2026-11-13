"""Driver registry: config entry domain -> driver class, imported lazily.

Lazy so a missing or broken device library costs only its own integration,
never the hub.
"""
from __future__ import annotations

import importlib
import logging

from services.homehub.core import Driver

log = logging.getLogger("homehub.drivers")

DRIVERS: dict[str, str] = {
    "wiz": "services.homehub.drivers.wiz:WizDriver",
    "esphome": "services.homehub.drivers.esphome:ESPHomeDriver",
    "esphome_builder": "services.homehub.drivers.esphome_builder:ESPHomeBuilderDriver",
    "tasmota": "services.homehub.drivers.tasmota:TasmotaDriver",
    "switch_as_x": "services.homehub.drivers.switch_as_x:SwitchAsXDriver",
    "matter": "services.homehub.drivers.matter:MatterDriver",
    "cast": "services.homehub.drivers.cast:CastDriver",
    "roku": "services.homehub.drivers.roku:RokuDriver",
    "webostv": "services.homehub.drivers.webostv:WebOSDriver",
    "samsungtv": "services.homehub.drivers.samsungtv:SamsungTVDriver",
    "androidtv_remote": "services.homehub.drivers.androidtv_remote:AndroidTVRemoteDriver",
    "dlna_dmr": "services.homehub.drivers.dlna_dmr:DlnaDmrDriver",
    "music_assistant": "services.homehub.drivers.music_assistant:MusicAssistantDriver",
    "homekit_controller": "services.homehub.drivers.homekit:HomeKitDriver",
    "roborock": "services.homehub.drivers.roborock:RoborockDriver",
    "emporia_vue": "services.homehub.drivers.emporia:EmporiaDriver",
    "srp_energy": "services.homehub.drivers.srp_energy:SrpEnergyDriver",
    "mjpeg": "services.homehub.drivers.mjpeg:MjpegDriver",
    "reolink": "services.homehub.drivers.reolink:ReolinkDriver",
    "go2rtc": "services.homehub.live_video:Go2rtcDriver",
    "camera_recording": "services.homehub.camera_recording:CameraRecordingDriver",
    "open_meteo": "services.homehub.drivers.weather:OpenMeteoDriver",
    "nws": "services.homehub.drivers.weather:NWSDriver",
    "sun": "services.homehub.drivers.sun:SunDriver",
    "mobile_app": "services.homehub.drivers.mobile_app:MobileAppDriver",
    "local_todo": "services.homehub.drivers.todo:LocalTodoDriver",
    "shopping_list": "services.homehub.drivers.todo:ShoppingListDriver",
    # Bluetooth: one driver for every BLE sensor parser (services/homehub/bluetooth_parsers.py), and iBeacon tags.
    **{d: "services.homehub.drivers.ble:BLESensorDriver" for d in (
        "bthome", "xiaomi_ble", "qingping", "sensorpush", "thermopro", "inkbird", "ruuvitag_ble", "govee_ble")},
    "ibeacon": "services.homehub.drivers.ble:IBeaconDriver",
}


#: Home Assistant's own integrations that came over with its data but have nothing to drive here,
#: because the hub does the job itself or the job does not exist outside Home Assistant. They show
#: as not loaded with this reason, instead of "no driver yet" or a setup retried forever.
REPLACED: dict[str, str] = {
    "analytics": "Home Assistant's usage reports to its developers; the hub sends none",
    "backup": "Home Assistant's own backups; the hub keeps its data in its own database",
    "hassio": "Home Assistant OS's Supervisor and add-ons; the hub runs as a container and has none",
    "google_translate": "Home Assistant's Google text to speech; the hub speaks with Jarvis's voice",
    "wyoming": "Home Assistant's link to speech services; the hub's voice pipeline talks to them directly",
    "ollama": "a Home Assistant conversation agent; voice and chat go to Jarvis",
    "openai_conversation": "a Home Assistant conversation agent; voice and chat go to Jarvis",
    "bluetooth": "Home Assistant's Bluetooth adapter; the hub scans Bluetooth itself",
}
#: Home Assistant's iBeacon integration is one entry with no data; here each beacon is its own entry.
IBEACON_UMBRELLA = "Replaced by the hub: Home Assistant's iBeacon integration; the hub's Bluetooth presence tracks each beacon itself"


def replaced_reason(domain: str, data: dict | None) -> str | None:
    """Why an entry has nothing to drive here (``REPLACED``), or None when it should load."""
    if domain == "ibeacon" and not data:
        return IBEACON_UMBRELLA
    reason = REPLACED.get(domain)
    return f"Replaced by the hub: {reason}" if reason else None


def driver_class(domain: str) -> type[Driver] | None:
    target = DRIVERS.get(domain)
    if target is None:
        return None
    module_name, _, cls_name = target.partition(":")
    try:
        module = importlib.import_module(module_name)
        return getattr(module, cls_name)
    except Exception as exc:  # a driver that cannot import is reported, not fatal
        log.error("driver %s unavailable: %s", domain, exc)
        return None
