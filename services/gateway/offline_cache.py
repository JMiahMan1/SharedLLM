"""The household's latest information, for phones that cannot reach the server.

An online phone asks ``GET /api/offline/snapshot`` now and then. The answer is
a small set of items for **every** family member, each one built by asking this
gateway's own routes *as that member* (so every existing sharing rule decides
what is in it: location sharing, Bible sharing, whose calendar...), then
compressed and sealed with that member's offline seal key (the private chat's
AES-256-GCM envelope, under the key derived from their own API key, as in
offline_relay). The filtering happens here, before anything leaves the server:
an item is only ever made for a member who may see it, and only that member's
phone can open it.

Phones pass the sealed items to each other over Bluetooth (FamilyMesh), newest
first by the server's version, so the freshest phone updates the rest hop by
hop whether or not any of them is online. A relay carries other members' items
as ciphertext it cannot read, and cannot make or alter one (AES-GCM under a key
only the server and that member hold).

Each item's ``version`` is the time (ms) the server first produced its current
content, so it changes only when the content does and newest wins everywhere.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request

from services.gateway import offline_relay

log = logging.getLogger(__name__)

router = APIRouter()

#: Largest item kept (after compression); anything bigger is left out, never cut.
ITEM_MAX_BYTES = 12 * 1024
#: Items are offered again after this long even if unchanged (their age then shows how fresh).
VERSION_TTL_S = 30 * 86400
#: A member's items, once built, serve every phone asking within this long.
BUILD_CACHE_S = 60


def _today_window(now: float) -> tuple[str, str]:
    start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def sources(now: float) -> dict[str, tuple[str, str, dict | None]]:
    """Item key -> (method, path, body) of the route that fills it, asked as the member."""
    start, end = _today_window(now)
    return {
        "locations": ("GET", "/api/users/location/all", None),
        "near": ("GET", "/api/geo/proximity/near", None),
        "trips": ("GET", "/api/geo/trips?limit=10", None),
        "shopping": ("POST", "/api/communication/notes/read", dict(offline_relay.SHOPPING_NOTE)),
        "calendar": ("GET", f"/api/communication/calendar/events?start={start}&end={end}", None),
        "reminders": ("GET", "/api/communication/timers", None),
        "bible": ("GET", "/api/bible/daily", None),
    }


def trim(key: str, data: Any) -> Any:
    """Only what an offline screen shows, so items stay small."""
    if key == "trips" and isinstance(data, dict):
        keep = ("id", "user_id", "user_name", "start_time", "end_time", "start_location", "end_location",
                "vehicle_name", "vehicle_source", "together", "distance_miles")
        return {"trips": [{k: t.get(k) for k in keep} for t in (data.get("trips") or [])[:10] if isinstance(t, dict)]}
    if key == "shopping" and isinstance(data, dict):
        return {"text": str(data.get("message") or "") if data.get("status") == "SUCCESS" else ""}
    return data


def pack(data: Any) -> str | None:
    """Compressed JSON, base64, or None when too big to carry."""
    raw = gzip.compress(json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8"), mtime=0)
    return base64.b64encode(raw).decode() if len(raw) <= ITEM_MAX_BYTES else None


async def version_of(conn, member: str, key: str, digest: str, now_ms: int) -> int:
    """When the server first produced this content (ms): unchanged content keeps its version."""
    rkey = f"offline_cache:ver:{member}:{key}"
    stored = await conn.get(rkey)
    if stored:
        old_digest, _, old_version = stored.partition("|")
        if old_digest == digest and old_version.isdigit():
            return int(old_version)
    await conn.set(rkey, f"{digest}|{now_ms}", ex=VERSION_TTL_S)
    return now_ms


async def build_for(conn, member: str, api_key: str, now: float) -> list[dict]:
    """Every item ``member`` may see, sealed for them."""
    _, seal_key = offline_relay.derive_keys(api_key)
    out: list[dict] = []
    for key, (method, path, body) in sources(now).items():
        try:
            code, data = await offline_relay._call_self(method, path, api_key, body or {})
        except Exception as e:
            log.info(f"[offline-cache] {key} for {member}: {type(e).__name__}")
            continue
        if code != 200 or data is None:
            continue
        packed = pack(trim(key, data))
        if packed is None:
            continue
        digest = hashlib.sha256(packed.encode()).hexdigest()
        version = await version_of(conn, member, key, digest, int(now * 1000))
        env = offline_relay.seal(seal_key, {"to": member, "key": key, "version": version, "data": packed})
        out.append({"to": member, "key": key, "version": version, "env": env})
    return out


async def _household() -> list[str]:
    from services.gateway.main import IDENTITY_SVC, INTERNAL_SECRET, shared_http_client

    async with shared_http_client() as client:
        resp = await client.get(f"{IDENTITY_SVC}/api/internal/activity-sharing",
                                headers={"X-Internal-Secret": INTERNAL_SECRET})
        if resp.status != 200:
            return []
        data = await resp.json()
    return sorted({str(u.get("username") or "").lower() for u in (data or {}).get("users", []) if u.get("username")})


@router.get("/api/offline/snapshot")
async def snapshot(request: Request):
    """The household's latest information, sealed per member, for a phone to carry."""
    from services.gateway.main import _require_authenticated
    from services.gateway.notices import _redis

    me = str(await _require_authenticated(request)).split(".")[-1].lower()
    now = time.time()
    members = await _household()
    if me not in members:
        members.append(me)
    items: list[dict] = []
    async with _redis() as conn:
        for member in members:
            api_key = await offline_relay._sender_key(member)
            if not api_key:
                continue
            # Several phones refreshing together build each member's items once a minute at most.
            cached = await conn.get(f"offline_cache:items:{member}")
            if cached:
                items.extend(json.loads(cached))
                continue
            built = await build_for(conn, member, api_key, now)
            await conn.set(f"offline_cache:items:{member}", json.dumps(built), ex=BUILD_CACHE_S)
            items.extend(built)
    return {"generated_at": int(now * 1000), "items": items}

