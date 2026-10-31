"""Missed-update reminders: tell someone about a new Jarvis app only when a
phone or tablet of theirs is actually behind.

The gateway records each published APK (``app_updates:latest_apk``,
``services/gateway/notices.py``) without telling anyone. CI publishes one on
every UI push, and the old fan-out sent the whole family an "is available"
notice for each, several a day, whatever their phones ran. Here, on the
recap tick:

* a build counts only once it has been out ``GRACE_SECONDS`` (a burst of
  pushes is one update, not five);
* only phones and tablets seen in the last ``ACTIVE_DAYS`` and on an older
  build count (a drawer tablet does not nag anyone);
* a grown member hears about their own device; a child's device is told to
  their parents (update notices never reach a minor);
* at most once per ``REMIND_GAP_SECONDS`` per person whose device is behind.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from services.common import notify
from services.telemetry import notices

log = logging.getLogger("telemetry.app_updates")

LATEST_APK_KEY = "app_updates:latest_apk"
REMINDED_PREFIX = "app_updates:reminded:"
GRACE_SECONDS = 3 * 3600
REMIND_GAP_SECONDS = 24 * 3600
ACTIVE_DAYS = 30

Deliver = Callable[[dict[str, Any]], Awaitable[Any]]


def behind(details: list[dict[str, Any]], latest: int, now: datetime) -> list[dict[str, Any]]:
    """The devices on a build older than ``latest`` that are still in use."""
    out = []
    for d in details or []:
        if not isinstance(d, dict) or d.get("revoked"):
            continue
        build = str(d.get("app_build") or "").strip()
        if not build.isdigit() or int(build) >= latest:
            continue
        seen = d.get("last_seen_at")
        try:
            when = datetime.fromisoformat(str(seen))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            # Identity stores naive local server time; a day's slack covers the zone.
            when = when.replace(tzinfo=UTC)
        if now - when > timedelta(days=ACTIVE_DAYS + 1):
            continue
        out.append(d)
    return out


def _device_line(devices: list[dict[str, Any]]) -> str:
    return ", ".join(f"{d.get('label') or d.get('kind') or 'device'} (build {d.get('app_build')})" for d in devices)


async def remind_missed_updates(rc, session, deliver: Deliver, children: dict[str, dict[str, Any]],
                                now_ts: float | None = None) -> int:
    """One pass; returns how many notices went out."""
    raw = await rc.get(LATEST_APK_KEY)
    if not raw:
        return 0
    try:
        latest = json.loads(raw)
        code = int(latest.get("version_code") or 0)
        published = float(latest.get("published_at") or 0)
    except (TypeError, ValueError):
        return 0
    now_ts = time.time() if now_ts is None else now_ts
    if code <= 0 or now_ts - published < GRACE_SECONDS:
        return 0
    now = datetime.fromtimestamp(now_ts, UTC)
    name = str(latest.get("version_name") or "")
    label = f"{name} (build {code})" if name else f"build {code}"
    notes = str(latest.get("notes") or "")

    sent = 0
    for user in await notify.family(client=session):
        try:
            data = await notices._identity_get(session, f"/api/internal/owned-devices/{user}")
        except Exception as exc:
            log.warning("devices for %s could not be read: %s", user, exc)
            continue
        late = behind((data or {}).get("details") or [], code, now)
        if not late:
            continue
        child = children.get(user)
        recipients = list(child.get("parents") or []) if child else [user]
        if not recipients:
            continue
        # Claimed before sending, so two ticks can never both remind.
        if not await rc.set(f"{REMINDED_PREFIX}{user}", str(code), nx=True, ex=REMIND_GAP_SECONDS):
            continue
        who = (child.get("display_name") or user) if child else None
        title = f"{who}'s Jarvis app needs updating" if who else "Your Jarvis app needs updating"
        body = f"{_device_line(late)} {'is' if len(late) == 1 else 'are'} behind; {label} is ready."
        if notes:
            body += f" {notes}"
        for recipient in recipients:
            try:
                await deliver({
                    "user": recipient,
                    "category": "app_updates",
                    "kind": "app_update",
                    "title": title,
                    "body": body,
                    "tag": f"app_update:{user}",
                    "audience": "parents",
                    "data": {"url": "/settings"},
                })
                sent += 1
            except Exception as exc:
                log.warning("update reminder to %s failed: %s", recipient, exc)
    return sent
