"""Admin notices from the control plane's monitors (category ``admin``, to
``@admins``; docs/PUSH.md). Disk space (disk_alerts.py) and the router
(router_monitor.py) both send through here."""
from __future__ import annotations

import logging
from typing import Any

import requests

from services.config import INTERNAL_SECRET, TELEMETRY_SVC_URL

log = logging.getLogger("control_plane.admin_notice")


def notify_admins(notice: dict[str, Any]) -> bool:
    """Send one admin notice through telemetry; True once telemetry accepted it."""
    payload = {"kind": "admin", **notice, "user": "@admins", "category": "admin"}
    try:
        resp = requests.post(
            f"{TELEMETRY_SVC_URL.rstrip('/')}/api/telemetry/notify",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=15,
        )
        if resp.status_code == 200:
            return True
        log.warning(f"[admin-notice] telemetry refused {notice.get('tag')}: HTTP {resp.status_code}")
    except Exception as e:
        log.warning(f"[admin-notice] telemetry unreachable: {type(e).__name__}: {e}")
    return False
