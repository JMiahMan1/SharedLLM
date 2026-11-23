"""Record the latest User-Agent per LAN client IP for device identification.

The control plane reads ``network:ua`` (Redis hash, IP -> {ua, at}) and maps
IPs to MACs with the current leases. Only private LAN client addresses are
recorded, and User-Agents are never logged. The Jarvis app adds
``X-Device-Hint: touch|no-touch``, kept as a ``[touch]`` suffix on the UA, so
an iPad (whose Safari sends a Mac UA) is not mistaken for a Mac.

This runs for every gateway request, so the decision is an in-memory check
(``due``) and Redis is touched only when an address shows a new User-Agent or
its last write is older than ``THROTTLE_SECONDS`` (``write``, off the event loop).
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
from typing import Any

from services.gateway import client_origin

UA_KEY = "network:ua"
MAX_UA_ENTRIES = 500
THROTTLE_SECONDS = 300
MAX_CACHE_ENTRIES = 1000
CAP_CHECK_INTERVAL = 60

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()
_last_cap_check = 0.0


def get_redis():
    from services.gateway.history import get_redis

    return get_redis()


def reset() -> None:
    """Forget the throttle state (tests)."""
    global _last_cap_check
    with _lock:
        _cache.clear()
        _last_cap_check = 0.0


def client_ip(request: Any) -> str | None:
    peer = request.client.host if getattr(request, "client", None) else None
    return client_origin.caller_address(peer, request.headers.get("X-Forwarded-For"))


def due(request: Any, now: float | None = None) -> tuple[str, str, float] | None:
    """Return (ip, ua, now) when this request's User-Agent should be written."""
    ip = client_ip(request)
    if not ip or not client_origin._is_local(ip):
        return None
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    hint = (request.headers.get("x-device-hint") or "").strip().lower()
    if hint in ("touch", "no-touch"):
        ua = f"{ua} [{hint}]"
    now = time.time() if now is None else now
    with _lock:
        seen = _cache.get(ip)
        if seen is not None and seen[0] == ua and now - seen[1] < THROTTLE_SECONDS:
            return None
        if len(_cache) >= MAX_CACHE_ENTRIES and ip not in _cache:
            oldest = min(_cache, key=lambda k: _cache[k][1])
            _cache.pop(oldest, None)
        _cache[ip] = (ua, now)
    return ip, ua, now


def write(ip: str, ua: str, now: float, redis_client: Any = None) -> None:
    global _last_cap_check
    r = redis_client if redis_client is not None else get_redis()
    with contextlib.suppress(Exception):
        r.hset(UA_KEY, ip, json.dumps({"ua": ua, "at": now}))
    with _lock:
        if now - _last_cap_check < CAP_CHECK_INTERVAL:
            return
        _last_cap_check = now
    with contextlib.suppress(Exception):
        if r.hlen(UA_KEY) <= MAX_UA_ENTRIES:
            return
        ages = []
        for key, value in (r.hgetall(UA_KEY) or {}).items():
            try:
                ages.append((json.loads(value).get("at", 0), key))
            except (TypeError, ValueError, AttributeError):
                ages.append((0, key))
        ages.sort()
        stale = [key for _, key in ages[: len(ages) - MAX_UA_ENTRIES]]
        if stale:
            r.hdel(UA_KEY, *stale)


def record_user_agent(request: Any, redis_client: Any = None) -> None:
    hit = due(request)
    if hit:
        write(*hit, redis_client=redis_client)
