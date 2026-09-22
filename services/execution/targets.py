"""
Resolve announcement/broadcast targets without hardcoded entity IDs.

Precedence:
  1. Explicit entity IDs / target devices
  2. room_speakers map (Identity GlobalSetting)
  3. Media group member_entity_ids
  4. HA areas (entity area name == room) filtered to media_player
"""
from __future__ import annotations

import json
import logging

import aiohttp

from services.execution import ha_client

log = logging.getLogger("execution/targets")


async def _call_identity(method: str, path: str, json_data: dict | None = None) -> dict | list | None:
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    url = f"{IDENTITY_SVC_URL.rstrip('/')}{path}"
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with get_client() as client, client.request(
        method, url, json=json_data, headers=headers, timeout=aiohttp.ClientTimeout(total=10.0)
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def fetch_room_speakers() -> dict[str, list[str]]:
    """Load room→speaker entity_ids map from Identity. Empty dict if unset."""
    try:
        data = await _call_identity("GET", "/api/intercom/room-speakers")
        if isinstance(data, dict):
            raw = data.get("room_speakers") or data.get("rooms") or data
            return _normalize_room_map(raw)
    except Exception as e:
        log.warning(f"[targets] room_speakers load failed: {e}")
    return {}


def _normalize_room_map(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for room, val in raw.items():
        if not isinstance(room, str) or not room:
            continue
        if isinstance(val, str):
            ids = [val]
        elif isinstance(val, list):
            ids = [str(x) for x in val if x]
        else:
            continue
        if ids:
            out[room.strip().lower().replace(" ", "_")] = ids
    return out


def room_key(room: str) -> str:
    return room.strip().lower().replace(" ", "_")


async def fetch_media_group(group_id: str) -> list[str]:
    try:
        groups = await _call_identity("GET", "/api/groups/media")
        if isinstance(groups, list):
            for g in groups:
                if str(g.get("group_id") or g.get("name") or g.get("key") or "").endswith(group_id) or g.get("group_id") == group_id:
                    return [str(x) for x in (g.get("member_entity_ids") or []) if x]
        elif isinstance(groups, dict):
            g = groups.get(group_id) or {}
            return [str(x) for x in (g.get("member_entity_ids") or []) if x]
    except Exception as e:
        log.warning(f"[targets] media group '{group_id}' load failed: {e}")
    return []


async def _speakers_for_rooms(
    rooms: list[str],
    room_map: dict[str, list[str]],
    ha_url: str,
    ha_token: str,
) -> list[str]:
    """Resolve rooms via config map first, then HA areas (media_player only)."""
    ids: list[str] = []
    unresolved: list[str] = []
    for room in rooms:
        key = room_key(room)
        mapped = room_map.get(key) or room_map.get(room.strip().lower())
        if mapped:
            ids.extend(mapped)
        else:
            unresolved.append(room)

    if unresolved and ha_url and ha_token:
        try:
            areas = await ha_client.get_areas(ha_url, ha_token)
            # areas: entity_id -> area_name
            for eid, area in areas.items():
                if not eid.startswith("media_player."):
                    continue
                area_key = room_key(area or "")
                if area_key in {room_key(r) for r in unresolved}:
                    ids.append(eid)
        except Exception as e:
            log.warning(f"[targets] HA area fallback failed: {e}")

    return _dedupe(ids)


def _dedupe(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def normalize_entity_id(entity_id: str) -> str:
    e = entity_id.strip()
    if e and "." not in e:
        return f"media_player.{e}"
    return e


async def resolve_targets(
    *,
    entity_ids: list[str] | None = None,
    rooms: list[str] | None = None,
    group_id: str | None = None,
    ha_url: str = "",
    ha_token: str = "",
) -> list[str]:
    """
    Resolve final media_player entity IDs.
    Explicit entities always win; never invents hardcoded speakers.
    """
    explicit = [normalize_entity_id(e) for e in (entity_ids or []) if e]
    if explicit:
        return _dedupe(explicit)

    room_list = [r for r in (rooms or []) if r]
    if room_list:
        room_map = await fetch_room_speakers()
        return _dedupe(await _speakers_for_rooms(room_list, room_map, ha_url, ha_token))

    if group_id:
        return _dedupe(await fetch_media_group(group_id))

    return []
