"""Whether a phone is still collecting: what it says about itself, and what the
server can see for itself.

A child's phone once stopped sending calls, texts and screen time while its
notification activity (social, driving, media) kept arriving: a sign-out and
sign-in inside the running app cancelled the background workers and nothing
scheduled them again. The server accepted every upload it got, so nothing
looked wrong; the parents' Calls & messages tab was simply empty.

Two signals, so it works for phones that have not updated yet:

* The phone's own health report (``POST /api/telemetry/device_health``): its
  app version, whether each permission is granted, whether each background
  worker is scheduled, and how each collector's last run went. It never
  carries a message, a number or a name from the phone, only states.
* The server's own view (``staleness``): when each kind of data last arrived.
  If other activity keeps arriving long after the last call or text (and the
  child had calls or texts before), or after the last screen-time day, the
  collector has most likely stopped.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from services.telemetry.config import COMMS_STALE_HOURS, USAGE_STALE_HOURS

#: Calls and texts: what the phone's comms collector sends.
COMMS_FEATURES = frozenset({"calls", "sms"})
#: The kinds the server keeps an arrival time for (usage is not an activity event).
USAGE_KIND = "usage"

#: Redis: ``<prefix><user>`` hash of device id -> the device's latest report.
HEALTH_PREFIX = "tel:devhealth:"
#: Redis: ``<prefix><user>`` hash of kind (a feature, or "usage") -> when it last arrived.
ARRIVALS_PREFIX = "tel:arrivals:"
#: Most devices kept per person; the oldest report goes first.
MAX_DEVICES = 10

#: ``bluetooth`` is Android 12's Nearby devices (BLUETOOTH_CONNECT): without it
#: car detection (Settings > Nearby & offline) cannot tell which linked vehicle a
#: trip was in. ``bluetooth_scan`` is the same permission's scan half, reported
#: once any Nearby & offline part is on.
PERMISSIONS = ("read_sms", "read_call_log", "usage_access", "notification_listener", "location", "bluetooth",
               "bluetooth_scan")
WORKERS = ("comms_sync", "usage_sync", "photo_scan")
#: ``listener`` is a comms run the notification listener started when a text or call came in.
#: ``proximity`` is the Nearby & offline engine (Bluetooth LE, Settings > Nearby &
#: offline); ``cannot_advertise``
#: is how a device that only scans (some tablets) says so.
COLLECTORS = ("comms", "usage", "listener", "proximity")
#: WorkManager states that mean the work is still scheduled.
SCHEDULED_STATES = frozenset({"ENQUEUED", "RUNNING", "BLOCKED"})
_WORKER_STATES = SCHEDULED_STATES | {"SUCCEEDED", "FAILED", "CANCELLED", "NONE"}
_DEVICE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")


def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _millis(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def clean_report(body: dict[str, Any]) -> dict[str, Any]:
    """The fields of a phone's health report that are kept, and nothing else.

    Everything is an allow-listed key with a bounded value, so a report can
    never carry a message's text or a contact into storage.
    Raises ``ValueError`` when the device cannot be told apart.
    """
    device = _text(body.get("device_id"), 128)
    if not _DEVICE_ID.fullmatch(device):
        raise ValueError("device_id is required")
    perms_in = body.get("permissions") if isinstance(body.get("permissions"), dict) else {}
    permissions = {k: bool(perms_in[k]) for k in PERMISSIONS if isinstance(perms_in.get(k), bool)}
    workers_in = body.get("workers") if isinstance(body.get("workers"), dict) else {}
    workers: dict[str, str] = {}
    for name in WORKERS:
        state = _text(workers_in.get(name), 16).upper()
        if state in _WORKER_STATES:
            workers[name] = state
    collectors_in = body.get("collectors") if isinstance(body.get("collectors"), dict) else {}
    collectors: dict[str, dict[str, Any]] = {}
    for name in COLLECTORS:
        run = collectors_in.get(name)
        if not isinstance(run, dict):
            continue
        outcome = _text(run.get("outcome"), 24).lower()
        collectors[name] = {
            "at": _millis(run.get("at")),
            "ok": bool(run.get("ok")),
            "outcome": outcome if re.fullmatch(r"[a-z_]{1,24}", outcome) else "",
            "message": _text(run.get("message"), 200),
            "last_ok_at": _millis(run.get("last_ok_at")),
        }
    return {
        "device_id": device,
        "device_name": _text(body.get("device_name"), 80),
        "app_version": _text(body.get("app_version"), 40),
        "app_build": _millis(body.get("app_build")),
        "signed_in": bool(body.get("signed_in", True)),
        "permissions": permissions,
        "workers": workers,
        "collectors": collectors,
        "reported_at": _millis(body.get("reported_at")),
    }


def problems(report: dict[str, Any]) -> list[dict[str, str]]:
    """What is wrong on one device, in the order a parent should fix it."""
    out: list[dict[str, str]] = []
    for name, granted in (report.get("permissions") or {}).items():
        if granted is False:
            out.append({"kind": "permission", "what": name})
    for name, state in (report.get("workers") or {}).items():
        if state not in SCHEDULED_STATES:
            out.append({"kind": "worker", "what": name, "state": state})
    for name, run in (report.get("collectors") or {}).items():
        if isinstance(run, dict) and run.get("ok") is False:
            out.append({"kind": "collector", "what": name, "outcome": str(run.get("outcome") or ""),
                        "message": str(run.get("message") or "")})
    return out


async def save_report(rc, user: str, report: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Keep a device's latest report (the server's receive time is ``received_at``)."""
    stored = dict(report)
    stored["received_at"] = (now or datetime.now(UTC)).isoformat()
    key = f"{HEALTH_PREFIX}{user}"
    await rc.hset(key, report["device_id"], json.dumps(stored))
    if await rc.hlen(key) > MAX_DEVICES:
        devices = await list_reports(rc, user)
        for old in devices[MAX_DEVICES:]:
            await rc.hdel(key, old["device_id"])
    return stored


async def list_reports(rc, user: str) -> list[dict[str, Any]]:
    """Every device's latest report, the newest first."""
    out: list[dict[str, Any]] = []
    for raw in (await rc.hgetall(f"{HEALTH_PREFIX}{user}")).values():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("device_id"):
            out.append(parsed)
    out.sort(key=lambda r: str(r.get("received_at") or ""), reverse=True)
    return out


async def note_arrival(rc, user: str, kind: str, now: datetime | None = None) -> None:
    """Remember when a kind of data last reached the server for ``user``."""
    if not user or not kind:
        return
    await rc.hset(f"{ARRIVALS_PREFIX}{user}", kind, (now or datetime.now(UTC)).isoformat())


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def last_seen(arrivals: dict[str, Any], events: list[dict[str, Any]], usage_days: list[dict[str, Any]]) -> dict[str, datetime]:
    """When each kind of data last arrived: the arrival record where there is
    one, else the newest event of that kind (data from before arrivals were
    kept), and for screen time the newest day's update time."""
    seen: dict[str, datetime] = {}

    def keep(kind: str, when: datetime | None) -> None:
        if when is not None and (kind not in seen or when > seen[kind]):
            seen[kind] = when

    for kind, at in arrivals.items():
        keep(str(kind), _when(at))
    for event in events:
        keep(str(event.get("feature") or "other"), _when(event.get("at")))
    for day in usage_days:
        when = _when(day.get("updated_at"))
        if when is None and day.get("day"):
            # A day without an update time was sent no earlier than its end.
            when = _when(f"{day['day']}T00:00:00")
            when = when + timedelta(days=1) if when else None
        keep(USAGE_KIND, when)
    return seen


def staleness(
    seen: dict[str, datetime],
    comms_hours: float = COMMS_STALE_HOURS,
    usage_hours: float = USAGE_STALE_HOURS,
) -> dict[str, Any]:
    """Whether calls/texts or screen time stopped while other activity kept
    coming. Only a kind the phone sent before can go stale: a child with no
    phone calls ever, or no usage access ever, is not flagged here."""
    comms = [seen[k] for k in COMMS_FEATURES if k in seen]
    comms_last = max(comms) if comms else None
    others = [when for kind, when in seen.items() if kind not in COMMS_FEATURES and kind != USAGE_KIND]
    other_last = max(others) if others else None
    usage_last = seen.get(USAGE_KIND)
    anything_but_usage = max([w for k, w in seen.items() if k != USAGE_KIND], default=None)

    def iso(when: datetime | None) -> str | None:
        return when.isoformat() if when else None

    comms_stale = bool(
        comms_last and other_last and other_last - comms_last > timedelta(hours=comms_hours)
    )
    usage_stale = bool(
        usage_last and anything_but_usage and anything_but_usage - usage_last > timedelta(hours=usage_hours)
    )
    return {
        "comms": {"stale": comms_stale, "last_at": iso(comms_last),
                  "calls_last_at": iso(seen.get("calls")), "sms_last_at": iso(seen.get("sms"))},
        "usage": {"stale": usage_stale, "last_at": iso(usage_last)},
        "other_last_at": iso(other_last),
        "comms_stale_hours": comms_hours,
        "usage_stale_hours": usage_hours,
    }
