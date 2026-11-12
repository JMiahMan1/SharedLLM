"""Room detection on the gateway: phone beacons, the room map, and who may see a room.

Three pieces, all against the hub's (services/homehub) Bluetooth room detection:

* ``PUT /api/phone/ble_beacon`` (the signed-in phone): the Jarvis app reports its
  iBeacon (``uuid``, ``major``, ``minor``, ``tx_power``) and whether it is on the
  air. The Jarvis app has no companion-app webhook of its own (its reports reach
  the hub through geo), so the gateway does the companion app's
  ``update_registration`` part: it finds the user's phone on the hub and merges
  ``app_data.ble_beacon`` into that entry with the hub's admin API
  (``PATCH /hub/mobile_app/phones/<entry_id>`` ``{"app_data": {"ble_beacon": ...}}``).
  The last report is kept (Redis, ``phone_beacon:<user>``), so Devices > Phones
  can say "Room detection unavailable: <reason>" even when the hub never heard
  of the beacon. A hub that refuses is reported back, never swallowed.
* ``/api/homehub/bluetooth/map...`` (admins): the "Map rooms" wizard, the hub's
  ``/hub/bluetooth/map`` API passed through 1:1.
* ``filter_people_rooms``: ``person.*`` features carry ``room``/``room_area_id``;
  each is kept only for a viewer who may see that person's room. The rule is the
  one the gateway applies to a member's collected data (identity's
  ``may_view_raw_data``): a child's room is their parents' and an admin's, the
  father and mother see each other's, any other adult's is their own; everyone
  sees their own. Identity not answering hides the room (fail closed).
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("gateway.room_presence")

router = APIRouter()

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_STATES = ("advertising", "bluetooth_off", "disabled", "unavailable")
_TX_LEVELS = ("ultra_low", "low", "medium", "high")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_AREA_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
#: A beacon id the wizard may map: a phone's sensor entity, or ``<uuid>-<major>-<minor>``.
_TRACKER = re.compile(r"^(?:sensor\.[a-z0-9_]{1,120}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-\d{1,5}-\d{1,5})$")
_MAP_PASS_THROUGH = {200, 201, 400, 404, 409}
#: Room attributes a ``person.*`` feature may carry; all of them go together.
ROOM_KEYS = ("room", "room_area_id", "room_last_seen", "room_stale")
_NO_ROOM = {"", "unknown", "unavailable", "none", "not_home"}
_REDIS_KEY = "phone_beacon:{user}"
#: Kept for a month: a phone that stopped reporting still shows its last word.
_REDIS_TTL_S = 30 * 24 * 3600


# -- validation -----------------------------------------------------------------------------------

def beacon_report(body: Any) -> dict[str, Any]:
    """The validated beacon report. Raises 422 naming the first bad field."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="The body must be a JSON object")

    def int_in(key: str, lo: int, hi: int) -> int:
        v = body.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise HTTPException(status_code=422, detail=f"{key} must be a whole number from {lo} to {hi}")
        return v

    uuid = str(body.get("uuid") or "").strip().lower()
    if not _UUID.match(uuid):
        raise HTTPException(status_code=422, detail="uuid must be a UUID like 00000000-0000-4000-8000-000000000000")
    state = body.get("state")
    if state not in _STATES:
        raise HTTPException(status_code=422, detail=f"state must be one of {', '.join(_STATES)}")
    level = body.get("tx_power_level")
    if level is not None and level not in _TX_LEVELS:
        raise HTTPException(status_code=422, detail=f"tx_power_level must be one of {', '.join(_TX_LEVELS)}")
    reason = body.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise HTTPException(status_code=422, detail="reason must be text")
    reason = (reason or "").strip()[:200] or None
    if state == "unavailable" and not reason:
        raise HTTPException(status_code=422, detail="An unavailable beacon must say why (reason)")
    device = body.get("device_name")
    device = str(device).strip()[:80] if isinstance(device, str) and device.strip() else None
    return {"uuid": uuid, "major": int_in("major", 0, 65535), "minor": int_in("minor", 0, 65535),
            "tx_power": int_in("tx_power", -127, 20), "tx_power_level": level, "state": state,
            "reason": reason, "device_name": device}


def hub_beacon(report: dict[str, Any]) -> dict[str, Any] | None:
    """What goes into the hub entry's ``app_data.ble_beacon`` (None when switched off)."""
    if report["state"] == "disabled":
        return None
    out = {k: report[k] for k in ("uuid", "major", "minor", "tx_power")}
    out["state"] = report["state"]
    if report["reason"]:
        out["reason"] = report["reason"]
    return out


def pick_phone(phones: list[dict[str, Any]], user: str, device_name: str | None) -> tuple[dict | None, str]:
    """The user's phone on the hub, or ``(None, why)``. Several phones: the one whose model
    is this device; still several, the one Jarvis feeds (``jarvis`` kind) if it is the only one."""
    mine = [p for p in phones if str(p.get("jarvis_user") or "").lower() == user
            and p.get("jarvis_fallback", True) is not False]
    if not mine:
        return None, f"the device hub has no phone for {user}; add one in Devices > Phones"
    if len(mine) == 1:
        return mine[0], ""
    if device_name:
        same = [p for p in mine if str((p.get("app") or {}).get("model") or "").lower() == device_name.lower()]
        if len(same) == 1:
            return same[0], ""
    jarvis = [p for p in mine if p.get("kind") == "jarvis"]
    if len(jarvis) == 1:
        return jarvis[0], ""
    return None, (f"{user} has {len(mine)} phones on the device hub and none is clearly this one; "
                  "turn Jarvis off on the old ones in Devices > Phones")


# -- the stored report --------------------------------------------------------------------------

async def _save(user: str, record: dict[str, Any]) -> None:
    from services.gateway.notices import _redis

    async with _redis() as conn:
        await conn.set(_REDIS_KEY.format(user=user), json.dumps(record), ex=_REDIS_TTL_S)


async def load_beacons(users: list[str]) -> dict[str, dict[str, Any]]:
    """The last beacon report per user (only users who sent one). Raises when Redis cannot be read."""
    from services.gateway.notices import _redis

    out: dict[str, dict[str, Any]] = {}
    if not users:
        return out
    async with _redis() as conn:
        raw = await conn.mget([_REDIS_KEY.format(user=u) for u in users])
    for user, value in zip(users, raw, strict=True):
        if value:
            try:
                out[user] = json.loads(value)
            except ValueError:
                log.warning("[beacon] unreadable stored report for %s", user)
    return out


def attach_beacons(phones: list[dict[str, Any]], beacons: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Each phone with ``beacon``: its user's last report, or None."""
    out = []
    for p in phones:
        user = str(p.get("jarvis_user") or "").lower()
        out.append({**p, "beacon": beacons.get(user)})
    return out


# -- routes ---------------------------------------------------------------------------------------

@router.put("/api/phone/ble_beacon")
async def report_beacon(request: Request):
    """The Jarvis app's beacon, from the signed-in phone. ``{"stored", "hub": {"registered", "detail"}}``."""
    from services.gateway import main as gateway
    from services.gateway import phone_routes

    user = (await gateway._require_authenticated(request)).strip().lower()
    try:
        body = await request.json()
    except Exception:
        body = None
    report = beacon_report(body)

    registered, detail = False, ""
    try:
        status, listing = await phone_routes._hub("GET", "/hub/mobile_app/phones")
        if status != 200 or not isinstance(listing, dict):
            raise HTTPException(status_code=502, detail="The device hub did not answer")
        phone, detail = pick_phone(listing.get("phones") or [], user, report["device_name"])
        if phone is not None:
            status, answer = await phone_routes._hub(
                "PATCH", f"/hub/mobile_app/phones/{phone['entry_id']}",
                json={"app_data": {"ble_beacon": hub_beacon(report)}})
            if status == 200:
                registered, detail = True, f"registered on {phone.get('name') or 'the phone'}"
            else:
                message = str((answer or {}).get("message") or (answer or {}).get("detail") or f"HTTP {status}") \
                    if isinstance(answer, dict) else f"HTTP {status}"
                detail = f"the device hub refused the beacon: {message}"
    except HTTPException as exc:
        detail = str(exc.detail)
    if not registered:
        log.warning("[beacon] %s's beacon not registered on the hub: %s", user, detail)

    record = {**report, "reported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "hub_registered": registered, "hub_detail": detail or None}
    try:
        await _save(user, record)
    except Exception as exc:
        log.error("[beacon] could not store %s's beacon report: %s", user, type(exc).__name__)
        raise HTTPException(status_code=503, detail="The beacon report could not be stored") from exc
    return {"stored": True, "hub": {"registered": registered, "detail": detail}}


async def _admin(request: Request) -> str:
    from services.gateway import main as gateway

    return await gateway._require_admin(request)


async def _map_call(method: str, path: str, *, json: Any = None) -> Any:
    from services.gateway import phone_routes

    status, body = await phone_routes._hub(method, path, json=json)
    if status == 200:
        return body
    if status in _MAP_PASS_THROUGH and isinstance(body, dict):
        message = str(body.get("message") or body.get("detail") or "")
        return JSONResponse({**body, "detail": message} if message else body, status_code=status)
    log.warning("[room map] hub answered HTTP %s for %s %s", status, method, path)
    raise HTTPException(status_code=502, detail="The device hub did not answer")


@router.get("/api/homehub/bluetooth/map")
async def room_map(request: Request):
    await _admin(request)
    return await _map_call("GET", "/hub/bluetooth/map")


@router.delete("/api/homehub/bluetooth/map")
async def clear_room_map(request: Request, confirm: str | None = None):
    await _admin(request)
    if confirm != "1":
        raise HTTPException(status_code=400, detail="Clearing every room needs confirm=1")
    return await _map_call("DELETE", "/hub/bluetooth/map?confirm=1")


@router.delete("/api/homehub/bluetooth/map/{area_id}")
async def clear_room(area_id: str, request: Request):
    await _admin(request)
    if not _AREA_ID.match(area_id or ""):
        raise HTTPException(status_code=404, detail="No such room")
    return await _map_call("DELETE", f"/hub/bluetooth/map/{area_id}")


@router.post("/api/homehub/bluetooth/map/sessions")
async def start_mapping(request: Request):
    """``{"area_id", "tracker", "duration_s"}``: record a pass through one room."""
    await _admin(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="The body must be a JSON object")
    area_id, tracker, duration = body.get("area_id"), body.get("tracker"), body.get("duration_s")
    if not isinstance(area_id, str) or not _AREA_ID.match(area_id):
        raise HTTPException(status_code=400, detail="Choose a room")
    if not isinstance(tracker, str) or not _TRACKER.match(tracker.strip().lower()):
        raise HTTPException(status_code=400, detail="Choose a phone or a beacon (uuid-major-minor)")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 10 <= duration <= 600:
        raise HTTPException(status_code=400, detail="duration_s must be 10 to 600 seconds")
    return await _map_call("POST", "/hub/bluetooth/map/sessions",
                           json={"area_id": area_id, "tracker": tracker.strip().lower(), "duration_s": duration})


@router.get("/api/homehub/bluetooth/map/sessions/{session_id}")
async def mapping_session(session_id: str, request: Request):
    await _admin(request)
    if not _SESSION_ID.match(session_id or ""):
        raise HTTPException(status_code=404, detail="No such recording")
    return await _map_call("GET", f"/hub/bluetooth/map/sessions/{session_id}")


@router.delete("/api/homehub/bluetooth/map/sessions/{session_id}")
async def cancel_mapping(session_id: str, request: Request):
    await _admin(request)
    if not _SESSION_ID.match(session_id or ""):
        raise HTTPException(status_code=404, detail="No such recording")
    return await _map_call("DELETE", f"/hub/bluetooth/map/sessions/{session_id}")


# -- who may see a room ------------------------------------------------------------------------

def person_subject(entity_id: str) -> str | None:
    """The username a ``person.*`` entity stands for (``person.alex`` -> ``alex``), else None."""
    if not entity_id.startswith("person."):
        return None
    obj = entity_id.split(".", 1)[1].lower().replace("-", "_")
    first = obj.split("_", 1)[0]
    return first or None


async def filter_people_rooms(payload: Any, viewer: str,
                              allowed: Callable[[str, str], Awaitable[bool]]) -> Any:
    """``payload`` (geo's FeatureCollection) with every room the viewer may not see removed.

    ``allowed(viewer, subject)`` is identity's raw-data rule; it is asked once per subject.
    A room that says nothing (unknown, not home) is removed for everyone.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        return payload
    v = (viewer or "").strip().lower()
    verdicts: dict[str, bool] = {}
    features = []
    for f in payload["features"]:
        props = f.get("properties") if isinstance(f, dict) else None
        if not isinstance(props, dict) or not any(k in props for k in ROOM_KEYS):
            features.append(f)
            continue
        subject = person_subject(str(props.get("entity_id") or ""))
        keep = False
        if subject and str(props.get("room") or "").strip().lower() not in _NO_ROOM:
            if subject not in verdicts:
                verdicts[subject] = subject == v or await _safe(allowed, v, subject)
            keep = verdicts[subject]
        if not keep:
            props = {k: val for k, val in props.items() if k not in ROOM_KEYS}
            f = {**f, "properties": props}
        features.append(f)
    return {**payload, "features": features}


async def _safe(allowed: Callable[[str, str], Awaitable[bool]], viewer: str, subject: str) -> bool:
    if not viewer:
        return False
    try:
        return bool(await allowed(viewer, subject))
    except Exception as exc:
        log.warning("[room] visibility check for %s failed (%s); room hidden", subject, type(exc).__name__)
        return False


async def identity_allows(viewer: str, subject: str) -> bool:
    """Identity's ``may-view-raw`` answer (the member-data rule)."""
    from services.gateway.config import INTERNAL_SECRET
    from services.gateway.main import IDENTITY_SVC, borrow_http_client

    async with borrow_http_client() as client:
        resp = await client.get(f"{IDENTITY_SVC}/api/internal/may-view-raw",
                                params={"viewer": viewer, "subject": subject},
                                headers={"X-Internal-Secret": INTERNAL_SECRET})
        if resp.status != 200:
            raise RuntimeError(f"identity answered HTTP {resp.status}")
        return bool((await resp.json(content_type=None) or {}).get("allowed"))
