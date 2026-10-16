"""Admin > Network's data from the router monitor (docs/NETWORK.md).

The gateway proxies these to admins only. The router's login lives encrypted
in Identity (User 1's router_* credentials); this service fetches it over the
internal API and never returns it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import redis
import requests
from fastapi import APIRouter, Depends, Header, HTTPException

from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET, REDIS_URL
from services.control_plane import router_monitor as rm

log = logging.getLogger("control_plane.network")

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}
_lock = threading.Lock()
_monitor: rm.Monitor | None = None


def _require_internal(x_internal_secret: str = Header(..., alias="X-Internal-Secret")) -> None:
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


router = APIRouter(prefix="/api/network", dependencies=[Depends(_require_internal)])


def load_config() -> dict[str, Any] | None:
    """The router's URL, login and pinned certificate, from Identity."""
    try:
        resp = requests.get(f"{IDENTITY_SVC_URL.rstrip('/')}/api/internal/router", headers=_HEADERS, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except (requests.RequestException, ValueError) as e:
        log.warning(f"[network] cannot read the router settings: {type(e).__name__}")
        return None


def save_pin(fingerprint: str) -> None:
    """Remember the router's certificate the first time Jarvis connects."""
    requests.post(f"{IDENTITY_SVC_URL.rstrip('/')}/api/admin/seed-credential",
                  json={"field": "router_cert_sha256", "value": fingerprint}, headers=_HEADERS, timeout=10)


def monitor() -> rm.Monitor:
    global _monitor
    if _monitor is None:
        _monitor = rm.Monitor(redis.Redis.from_url(REDIS_URL, decode_responses=True), load_config, save_pin)
    return _monitor


def check_now() -> dict[str, Any]:
    with _lock:
        return monitor().tick()


def loop() -> None:
    while True:
        try:
            check_now()
        except Exception as e:  # never let the thread die
            log.error(f"[network] unexpected error: {type(e).__name__}: {e}")
        time.sleep(rm.CHECK_SECONDS)


def start() -> None:
    threading.Thread(target=loop, name="router-monitor", daemon=True).start()


def _events(r, limit: int) -> list[dict[str, Any]]:
    return [json.loads(e) for e in r.lrange(rm.EVENTS_KEY, 0, limit - 1)]


@router.get("/status")
def status():
    r = monitor().r
    current = json.loads(r.get(rm.STATUS_KEY) or '{"configured": false}')
    return {**current, "events": _events(r, 20)}


@router.post("/check")
def check():
    """Check now, e.g. right after the router settings change."""
    return {**check_now(), "events": _events(monitor().r, 20)}


@router.get("/log")
def router_log(limit: int = 200, level: str | None = None, q: str | None = None):
    """The router's log, newest first; ``level`` keeps that level and worse."""
    limit = max(1, min(limit, 2000))
    order = list(rm._SYSLOG_LEVELS.values())
    worst = order.index(level) if level in order else len(order) - 1
    needle = (q or "").lower()
    out = []
    for raw in monitor().r.lrange(rm.LOG_KEY, 0, rm.LOG_MAX - 1):
        e = json.loads(raw)
        if order.index(e["level"]) <= worst and (not needle or needle in e["msg"].lower()):
            out.append(e)
            if len(out) >= limit:
                break
    return {"lines": out}


@router.get("/usage")
def usage(hours: int = 24):
    """Bytes each Wi-Fi device moved over the last ``hours``, biggest first."""
    hours = max(1, min(hours, 24 * 35))
    r = monitor().r
    now = time.time()
    totals: dict[str, int] = {}
    for h in range(hours):
        key = rm.USAGE_PREFIX + time.strftime("%Y-%m-%dT%H", time.localtime(now - h * 3600))
        for mac, n in r.hgetall(key).items():
            totals[mac] = totals.get(mac, 0) + int(n)
    current = json.loads(r.get(rm.STATUS_KEY) or "{}")
    names = {c["mac"]: (c["name"], c.get("ip")) for radio in current.get("radios", []) for c in radio["clients"]}
    rows = [{"mac": mac, "name": names.get(mac, (mac, None))[0], "ip": names.get(mac, (mac, None))[1], "bytes": n}
            for mac, n in totals.items()]
    rows.sort(key=lambda x: -x["bytes"])
    return {"hours": hours, "devices": rows}
