"""Error spike alerts from the central log (category ``admin``, to ``@admins``;
docs/PUSH.md).

Once a minute the logging service counts ERROR entries from the last five
minutes. Admins get a notice when the total exceeds
``alert_error_spike_threshold`` (20) or one error repeats more than
``alert_error_repeat_threshold`` (10) times. Each notice has a fingerprint (the
service plus the readable message with numbers and ids blanked) and is sent at
most once per ``COOLDOWN_SECONDS`` per fingerprint; the cooldown is a Redis key,
so a restart does not repeat it.

``readable_message`` is the server-side twin of the dashboard's
``readableLogMessage`` (services/ui/src/pages/Dashboard.tsx): a uvicorn access
line becomes "GET /x: 500 Internal Server Error".
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import Counter
from typing import Any

import aiohttp

from services.common.http import get_client
from services.config import INTERNAL_SECRET, TELEMETRY_SVC_URL

log = logging.getLogger("logging.error_alerts")

WINDOW_SECONDS = 300
CHECK_SECONDS = 60
COOLDOWN_SECONDS = 1800
COOLDOWN_KEY = "admin:alerts:errors:{fingerprint}"
DASHBOARD_URL = "/"
ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL"}
SPIKE_FINGERPRINT = "spike"

_ACCESS_LINE = re.compile(r'"([A-Z]+) (\S+) HTTP/[\d.]+" (\d{3})\s*(.*)$')
_VOLATILE = re.compile(r"\b[0-9a-f]{8,}\b|\d+", re.IGNORECASE)


def readable_message(message: str) -> str:
    """Uvicorn access lines ('INFO: 1.2.3.4:5 - "GET /x HTTP/1.1" 500 Internal
    Server Error') as 'GET /x: 500 Internal Server Error'; anything else as is."""
    m = _ACCESS_LINE.search(message or "")
    if not m:
        return message or ""
    return f"{m.group(1)} {m.group(2)}: {m.group(3)}" + (f" {m.group(4)}" if m.group(4) else "")


def fingerprint(entry: dict[str, Any]) -> str:
    """Same service, same message once numbers and ids are blanked."""
    text = _VOLATILE.sub("#", readable_message(str(entry.get("message") or "")))[:300]
    return hashlib.sha1(f"{entry.get('service')}|{text}".encode()).hexdigest()[:16]


def _open_dashboard() -> dict[str, Any]:
    return {
        "data": {"url": DASHBOARD_URL},
        "actions": [{"label": "Open", "type": "view", "url": DASHBOARD_URL}],
    }


def spike_notices(entries: list[dict[str, Any]], total_threshold: int,
                  repeat_threshold: int) -> list[tuple[str, dict[str, Any]]]:
    """(fingerprint, notice) for every spike in ``entries`` (one window)."""
    errors = [e for e in entries if str(e.get("level") or "").upper() in ERROR_LEVELS]
    counts: Counter[str] = Counter()
    sample: dict[str, dict[str, Any]] = {}
    for e in errors:
        fp = fingerprint(e)
        counts[fp] += 1
        sample.setdefault(fp, e)
    notices = []
    for fp, n in counts.most_common():
        if n <= repeat_threshold:
            break
        e = sample[fp]
        notices.append((fp, {
            "title": f"Repeated error: {e.get('service') or 'unknown'}",
            "body": f"{readable_message(str(e.get('message') or ''))} ({n} times in 5 min)",
            "priority": 4,
            "tag": f"admin:errors:{fp}",
            **_open_dashboard(),
        }))
    if len(errors) > total_threshold:
        fp, n = counts.most_common(1)[0]
        e = sample[fp]
        notices.append((SPIKE_FINGERPRINT, {
            "title": "Error spike",
            "body": (f"{len(errors)} errors in the last 5 minutes. Most frequent "
                     f"({e.get('service') or 'unknown'}, {n}x): {readable_message(str(e.get('message') or ''))}"),
            "priority": 4,
            "tag": f"admin:errors:{SPIKE_FINGERPRINT}",
            **_open_dashboard(),
        }))
    return notices


async def notify_admins(notice: dict[str, Any]) -> bool:
    """Send one admin notice through telemetry; True once telemetry accepted it."""
    payload = {"kind": "admin", **notice, "user": "@admins", "category": "admin"}
    try:
        async with get_client() as client, client.post(
            f"{TELEMETRY_SVC_URL.rstrip('/')}/api/telemetry/notify",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                return True
            log.warning(f"[error-alerts] telemetry refused {notice.get('tag')}: HTTP {resp.status}")
    except Exception as e:
        log.warning(f"[error-alerts] telemetry unreachable: {type(e).__name__}: {e}")
    return False


async def check_once(r, total_threshold: int, repeat_threshold: int, now: float | None = None) -> int:
    """One pass over the last five minutes of the log; returns notices sent."""
    now = time.time() if now is None else now
    entries = []
    for raw in await r.zrangebyscore("logs:entries", now - WINDOW_SECONDS, now):
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    sent = 0
    for fp, notice in spike_notices(entries, total_threshold, repeat_threshold):
        key = COOLDOWN_KEY.format(fingerprint=fp)
        if not await r.set(key, "1", nx=True, ex=COOLDOWN_SECONDS):
            continue
        if await notify_admins(notice):
            sent += 1
        else:
            await r.delete(key)  # not delivered: try again next pass
    return sent
