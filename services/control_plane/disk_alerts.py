"""Disk nearly full alerts (category ``admin``, to ``@admins``; docs/PUSH.md).

Every hour the control plane reads free space on the disks that hold the
stack's data. Compose mounts them read-only under ``/hostfs`` (``./data``, the
workspaces and ``/var/lib/docker``); paths on the same filesystem are checked
once. Admins hear once when a disk drops below ``alert_disk_free_percent`` (10)
and once when it climbs back above it; the second notice replaces the first.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from services.control_plane.admin_notice import notify_admins

log = logging.getLogger("control_plane.disk_alerts")

CHECK_SECONDS = int(os.getenv("DISK_ALERT_INTERVAL", "3600"))
DEFAULT_PATHS = "/hostfs/data,/hostfs/workspaces,/hostfs/docker"
DASHBOARD_URL = "/"


def alert_paths() -> list[str]:
    return [p.strip() for p in os.getenv("DISK_ALERT_PATHS", DEFAULT_PATHS).split(",") if p.strip()]


def free_percent(path: str) -> float:
    st = os.statvfs(path)
    return 100.0 * st.f_bavail / st.f_blocks if st.f_blocks else 100.0


def readings(paths: list[str]) -> dict[str, float]:
    """Disk label -> free percent, one entry per filesystem.

    The label names every checked path on that filesystem ("data, docker"),
    without the ``/hostfs/`` prefix. Missing paths are skipped.
    """
    by_device: dict[int, list[str]] = {}
    first: dict[int, str] = {}
    for path in paths:
        try:
            dev = os.stat(path).st_dev
        except OSError as e:
            log.warning(f"[disk] cannot check {path}: {e}")
            continue
        by_device.setdefault(dev, []).append(path.removeprefix("/hostfs/"))
        first.setdefault(dev, path)
    return {", ".join(names): free_percent(first[dev]) for dev, names in by_device.items()}


def disk_transitions(low: set[str], free: dict[str, float], threshold: int) -> list[dict[str, Any]]:
    """Notices for disks that crossed ``threshold`` either way; updates ``low``."""
    notices = []
    for label, pct in sorted(free.items()):
        tag = f"admin:disk:{label}"
        if pct < threshold and label not in low:
            low.add(label)
            notices.append({
                "title": "Disk nearly full",
                "body": f"{label}: {pct:.1f}% free (alert below {threshold}%).",
                "priority": 4,
                "tag": tag,
                "data": {"url": DASHBOARD_URL},
            })
        elif pct >= threshold and label in low:
            low.discard(label)
            notices.append({
                "title": "Disk space recovered",
                "body": f"{label}: {pct:.1f}% free.",
                "priority": 2,
                "tag": tag,
                "data": {"url": DASHBOARD_URL},
            })
    return notices


def tick(low: set[str], threshold: int) -> int:
    """One check; returns notices sent. A crossing whose notice telemetry did
    not accept is not recorded, so the next check tries again."""
    new_low = set(low)
    sent = 0
    for notice in disk_transitions(new_low, readings(alert_paths()), threshold):
        label = notice["tag"].removeprefix("admin:disk:")
        if notify_admins(notice):
            sent += 1
        elif label in low:
            new_low.add(label)
        else:
            new_low.discard(label)
    low.clear()
    low.update(new_low)
    return sent
