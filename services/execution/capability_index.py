"""Which devices can do what: the device hub's capability index, for every tool that picks a device.

The hub (``services/homehub/capabilities.py``) derives each entity's abilities
from its driver, its protocol and the state it publishes, and serves them at
``GET /api/homehub/capabilities``. Tools ask here instead of matching entity ids
or names, or decoding ``supported_features`` signatures themselves:

    idx = await capability_index.load(ha_url, ha_token)
    if idx is not None:
        speakers = idx.with_ability("announce", area="Den")
        owner = idx.integration("media_player.example")

``load`` answers ``None`` when the URL is a Home Assistant that has no index
(HTTP 404: an old HA, not the hub), and raises ``CapabilityIndexError`` when
the hub cannot be reached or answers anything else. A caller that has an older
way to decide logs which path it took; nothing here guesses.

A device added on the hub is in the index on the next load (``TTL_S``), with no
restart of anything.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

log = logging.getLogger("execution.capability_index")

PATH = "/api/homehub/capabilities"
#: The index is cached this long per hub URL (a device added on the hub shows up after at most this).
TTL_S = 30.0
_TIMEOUT = aiohttp.ClientTimeout(total=15.0, connect=5.0)

#: The hub's integration -> the platform name execution's media and TV handlers use.
TV_PLATFORM = {"webostv": "webos", "samsungtv": "samsung", "roku": "roku", "androidtv_remote": "android_tv",
               "cast": "cast", "music_assistant": "music_assistant", "dlna_dmr": "dlna", "esphome": "esphome"}

_cache: dict[str, tuple[float, CapabilityIndex | None]] = {}


class CapabilityIndexError(RuntimeError):
    """The hub did not answer the capability index."""


class CapabilityIndex:
    """One snapshot of the hub's index."""

    def __init__(self, entities: list[dict[str, Any]], abilities: dict[str, str] | None = None) -> None:
        self.by_entity: dict[str, dict[str, Any]] = {e["entity_id"]: e for e in entities if e.get("entity_id")}
        self.abilities = abilities or {}

    def get(self, entity_id: str) -> dict[str, Any] | None:
        return self.by_entity.get(entity_id)

    def has(self, entity_id: str, ability: str) -> bool:
        cap = self.by_entity.get(entity_id)
        return bool(cap) and ability in (cap.get("abilities") or [])

    def integration(self, entity_id: str) -> str | None:
        cap = self.by_entity.get(entity_id)
        return (cap.get("integration") or cap.get("platform")) if cap else None

    def tv_platform(self, entity_id: str) -> str | None:
        """The media/TV handler platform for an entity, from the integration that owns it."""
        return TV_PLATFORM.get(self.integration(entity_id) or "")

    def with_ability(self, *abilities: str, area_id: str | None = None, domain: str | None = None,
                     available_only: bool = False) -> list[str]:
        """Entity ids having every ability, optionally in one area (its id) or domain."""
        out = []
        for eid, cap in self.by_entity.items():
            have = set(cap.get("abilities") or [])
            if not set(abilities) <= have:
                continue
            if area_id and cap.get("area_id") != area_id:
                continue
            if domain and cap.get("domain") != domain:
                continue
            if available_only and not cap.get("available"):
                continue
            out.append(eid)
        return sorted(out)


async def load(ha_url: str, ha_token: str, *, refresh: bool = False) -> CapabilityIndex | None:
    """The hub's index (cached ``TTL_S``); None when the URL is a Home Assistant without one."""
    if not ha_url or not ha_token:
        raise CapabilityIndexError("no hub URL or token for this account")
    key = ha_url.rstrip("/")
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and not refresh and now - hit[0] < TTL_S:
        return hit[1]
    from services.execution.http_client import get_session, host_of

    session = await get_session(host_of(ha_url))
    try:
        async with session.get(f"{key}{PATH}", headers={"Authorization": f"Bearer {ha_token}"},
                               timeout=_TIMEOUT) as resp:
            if resp.status == 404:
                _cache[key] = (now, None)
                return None
            if resp.status != 200:
                raise CapabilityIndexError(f"the hub answered HTTP {resp.status} for its capability index")
            body = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError) as exc:
        raise CapabilityIndexError(f"the hub's capability index did not answer: {type(exc).__name__}") from exc
    if not isinstance(body, dict) or not isinstance(body.get("entities"), list):
        raise CapabilityIndexError("the hub's capability index answered in an unexpected shape")
    idx = CapabilityIndex(body["entities"], body.get("abilities"))
    _cache[key] = (now, idx)
    return idx


async def load_or_none(ha_url: str, ha_token: str, purpose: str) -> CapabilityIndex | None:
    """``load``, with a failure logged (naming what will be used instead) rather than raised."""
    try:
        return await load(ha_url, ha_token)
    except CapabilityIndexError as exc:
        log.warning("[capabilities] %s; %s falls back to reading entity attributes", exc, purpose)
        return None


def clear_cache() -> None:
    _cache.clear()
