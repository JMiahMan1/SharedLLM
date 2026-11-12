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
    "open_meteo": "services.homehub.drivers.weather:OpenMeteoDriver",
    "nws": "services.homehub.drivers.weather:NWSDriver",
    "sun": "services.homehub.drivers.sun:SunDriver",
    "mobile_app": "services.homehub.drivers.mobile_app:MobileAppDriver",
    "local_todo": "services.homehub.drivers.todo:LocalTodoDriver",
    "shopping_list": "services.homehub.drivers.todo:ShoppingListDriver",
}


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
