"""Admin > Network's data from the router monitor (docs/NETWORK.md).

The gateway proxies these to admins only. The router's login lives encrypted
in Identity (User 1's router_* credentials); this service fetches it over the
internal API and never returns it.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import threading
import time
from typing import Any

import redis
import requests
from fastapi import APIRouter, Depends, Header, HTTPException

from services.common.secrets import secret_matches
from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET, REDIS_URL
from services.control_plane import router_monitor as rm

log = logging.getLogger("control_plane.network")

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}
_lock = threading.Lock()
_monitor: rm.Monitor | None = None


def _require_internal(x_internal_secret: str = Header(..., alias="X-Internal-Secret")) -> None:
    if not secret_matches(x_internal_secret, INTERNAL_SECRET):
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


def _network_of(ip: str | None, networks: list[dict[str, Any]]) -> str | None:
    if not ip:
        return None
    for n in networks:
        try:
            if ipaddress.ip_address(ip) in ipaddress.ip_interface(n["cidr"]).network:
                return n["name"]
        except ValueError:
            continue
    return None


@router.get("/usage")
def usage(hours: int = 24):
    """Download and upload per device over the last ``hours``, biggest first;
    totals per network; and the household's traffic as a series (hourly up
    to two days, daily beyond)."""
    hours = max(1, min(hours, 24 * 35))
    r = monitor().r
    now = time.time()
    current = json.loads(r.get(rm.STATUS_KEY) or "{}")
    known = current.get("devices") or {}
    networks = current.get("networks") or []
    by_day = hours > 48
    per_device: dict[str, dict[str, int]] = {}
    series: dict[str, dict[str, int]] = {}
    for h in range(hours - 1, -1, -1):
        t = time.localtime(now - h * 3600)
        bucket = time.strftime("%Y-%m-%d" if by_day else "%Y-%m-%dT%H", t)
        point = series.setdefault(bucket, {"down": 0, "up": 0})
        for field, n in r.hgetall(rm.USAGE_PREFIX + time.strftime("%Y-%m-%dT%H", t)).items():
            # "<mac>:down" / "<mac>:up"; a MAC has colons, so split at the last.
            # A bare MAC is an hour written before the split, counted as download.
            mac, _, kind = field.rpartition(":")
            if kind not in ("down", "up"):
                mac, kind = field, "down"
            per_device.setdefault(mac, {"down": 0, "up": 0})[kind] += int(n)
            point[kind] += int(n)
    rows = []
    totals: dict[str, dict[str, int]] = {}
    for mac, d in per_device.items():
        info = known.get(mac) or {"name": mac, "ip": None}
        net = _network_of(info.get("ip"), networks)
        rows.append({"mac": mac, "name": info["name"], "ip": info.get("ip"), "network": net,
                     "down": d["down"], "up": d["up"], "bytes": d["down"] + d["up"]})
        if net:
            tot = totals.setdefault(net, {"down": 0, "up": 0})
            tot["down"] += d["down"]
            tot["up"] += d["up"]
    rows.sort(key=lambda x: -x["bytes"])
    return {
        "hours": hours,
        "source": current.get("usage_source", "wifi"),
        "devices": rows,
        "networks": [{"name": k, **v} for k, v in sorted(totals.items(), key=lambda kv: -(kv[1]["down"] + kv[1]["up"]))],
        "series": [{"t": k, **v} for k, v in series.items()],
        "bucket": "day" if by_day else "hour",
    }


@router.get("/outages")
def outages(days: int = 30):
    """Internet outages in the last ``days``, newest first; an outage still
    going on is first, with no end."""
    days = max(1, min(days, 365))
    r = monitor().r
    since = time.time() - days * 86400
    out = []
    down_since = (json.loads(r.get(rm.STATE_KEY) or "{}").get("internet") or {}).get("down_since")
    if down_since:
        out.append({"start": down_since, "end": None, "minutes": round((time.time() - down_since) / 60)})
    for raw in r.lrange(rm.OUTAGES_KEY, 0, rm.OUTAGES_MAX - 1):
        o = json.loads(raw)
        if o["end"] < since:
            break
        out.append({**o, "minutes": round((o["end"] - o["start"]) / 60)})
    return {"days": days, "outages": out}
