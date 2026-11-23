"""What devices announce about themselves over mDNS (Bonjour), as HomeHub hears it.

HomeHub runs on the host network and already browses mDNS; it keeps an in-memory
table of every LAN address's announced names, services and model fields and
serves it at ``GET /api/network/mdns`` (admins, or the stack's INTERNAL_SECRET).
This needs no app or login on the device, so it identifies guests' phones too.

With no ``HOMEHUB_URL`` this source is off. A hub that does not answer is logged
once per outage and identification carries on with the other sources.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from services import config

log = logging.getLogger("control_plane.mdns_peers")

TIMEOUT_S = 5
_failing = False


def enabled() -> bool:
    return bool(config.HOMEHUB_URL)


def fetch() -> dict[str, dict[str, Any]]:
    """{ipv4: {"names", "host", "services", "txt", "last"}}; {} when off or unreachable."""
    global _failing
    if not enabled():
        return {}
    url = f"{config.HOMEHUB_URL}/api/network/mdns"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {config.INTERNAL_SECRET}"}, timeout=TIMEOUT_S)
        resp.raise_for_status()
        devices = (resp.json() or {}).get("devices") or {}
    except (requests.RequestException, ValueError) as exc:
        if not _failing:
            log.warning("[mdns] HomeHub's mDNS table is unavailable (%s); identifying without it", type(exc).__name__)
        _failing = True
        return {}
    if _failing:
        log.info("[mdns] HomeHub's mDNS table is back")
    _failing = False
    return {str(ip): entry for ip, entry in devices.items() if isinstance(entry, dict)}
