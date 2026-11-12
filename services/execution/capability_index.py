"""Which devices can do what: the device hub's capability index, for every tool that picks a device.

The hub (``services/homehub/capabilities.py``) derives each entity's abilities
from its driver, its protocol and the state it publishes, and serves them at
``GET /api/homehub/capabilities``. Tools ask here instead of matching entity ids
or names, or decoding ``supported_features`` signatures themselves:

    idx = await capability_index.load(ha_url, ha_token)
    if idx is not None:
        speakers = idx.in_area(idx.require_area("Den"), "announce")
        owner = idx.integration("media_player.example")

``load`` answers ``None`` when the URL is a Home Assistant that has no index
(HTTP 404: an old HA, not the hub), and raises ``CapabilityIndexError`` when
the hub cannot be reached or answers anything else. A caller that has an older
way to decide logs which path it took; nothing here guesses.

Rooms are the hub's **areas**, read from its area registry with their names and
aliases (``area_id_for``, ``area_in_text``); a room is never read out of an
entity id or a friendly name. Every tool that picks a device by room asks
``in_area`` for the entities there with the ability it needs.

A device added on the hub is in the index on the next load (``TTL_S``), with no
restart of anything.

``routing_index`` is what a tool that routes calls: it answers the index, or
``None`` with a logged reason when the URL is a plain Home Assistant (HTTP 404),
and raises ``CapabilityIndexError`` for anything else, so nothing falls back
silently.
"""
from __future__ import annotations

import json
import logging
import re
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


class UnknownAreaError(LookupError):
    """A room was named that is not one of the hub's areas (by name, alias or id)."""


#: Abilities that make an entity a place to send sound to (a speaker, a TV, a Cast device).
MEDIA_ABILITIES = ("play_media", "announce")
#: Abilities that mean the entity drives a screen (a TV's browser or apps).
SCREEN_ABILITIES = ("show_url", "launch_app")


#: Words that say what kind of device was asked for ("the TV in the den"), never which one.
SCREEN_WORDS = frozenset({"tv", "television", "screen", "telly"})
SPEAKER_WORDS = frozenset({"speaker", "speakers", "radio", "stereo"})


def state_rank(state: str | None) -> int:
    """Playing before idle before anything else, for choosing among players."""
    return {"playing": 0, "paused": 0, "buffering": 0, "idle": 1, "on": 1}.get(str(state or ""), 2)


def norm(text: str | None) -> str:
    """Lowercase words separated by single spaces: ``Living_Room`` and ``living room`` compare equal."""
    return " ".join(re.split(r"[^0-9a-z]+", str(text or "").lower())).strip()


class CapabilityIndex:
    """One snapshot of the hub's index, with its area registry."""

    def __init__(self, entities: list[dict[str, Any]], abilities: dict[str, str] | None = None,
                 areas: list[dict[str, Any]] | None = None) -> None:
        self.by_entity: dict[str, dict[str, Any]] = {e["entity_id"]: e for e in entities if e.get("entity_id")}
        self.abilities = abilities or {}
        self.areas: dict[str, dict[str, Any]] = {
            str(a["area_id"]): {"area_id": str(a["area_id"]), "name": str(a.get("name") or a["area_id"]),
                                "aliases": [str(x) for x in (a.get("aliases") or []) if x]}
            for a in (areas or []) if isinstance(a, dict) and a.get("area_id")}
        # An entity may name an area the registry list did not carry; it is still a room.
        for cap in self.by_entity.values():
            aid = cap.get("area_id")
            if aid and aid not in self.areas:
                self.areas[aid] = {"area_id": aid, "name": aid, "aliases": []}

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self.by_entity

    # -- areas --------------------------------------------------------------
    def _phrases(self) -> list[tuple[str, str]]:
        """``(normalized phrase, area_id)`` for every area id, name and alias."""
        out = []
        for aid, a in self.areas.items():
            for phrase in {norm(aid), norm(a["name"]), *(norm(x) for x in a["aliases"])}:
                if phrase:
                    out.append((phrase, aid))
        return out

    def area_names(self) -> list[str]:
        return sorted(a["name"] for a in self.areas.values())

    def area_name(self, area_id: str | None) -> str | None:
        a = self.areas.get(area_id or "")
        return a["name"] if a else None

    def area_id_for(self, room: str | None) -> str | None:
        """The area a room name means: its id, its name or one of its aliases (case and spacing ignored)."""
        want = norm(room)
        if not want:
            return None
        for phrase, aid in self._phrases():
            if phrase == want:
                return aid
        return None

    def require_area(self, room: str) -> str:
        """``area_id_for``, raising ``UnknownAreaError`` naming the hub's areas when the room is not one."""
        aid = self.area_id_for(room)
        if aid is None:
            known = ", ".join(self.area_names()) or "none"
            raise UnknownAreaError(f"There is no area {room!r} on the hub (its areas: {known})")
        return aid

    def area_in_text(self, text: str | None) -> str | None:
        """The area a phrase like "the speaker in the den" names: the longest area name or alias in it, as words."""
        hay = f" {norm(text)} "
        best: tuple[int, str] | None = None
        for phrase, aid in self._phrases():
            if f" {phrase} " in hay and (best is None or len(phrase) > best[0]):
                best = (len(phrase), aid)
        return best[1] if best else None

    def area_of(self, entity_id: str) -> str | None:
        cap = self.by_entity.get(entity_id)
        return (cap.get("area_id") or None) if cap else None

    def device_of(self, entity_id: str) -> str | None:
        cap = self.by_entity.get(entity_id)
        return (cap.get("device_id") or None) if cap else None

    def in_area(self, area_id: str, *abilities: str, any_of: tuple[str, ...] = (), domain: str | None = None,
                integration: str | None = None, available_only: bool = False) -> list[str]:
        """Entities in one area having every ability in ``abilities`` and at least one of ``any_of``."""
        return [eid for eid in self.with_ability(*abilities, area_id=area_id, domain=domain,
                                                 available_only=available_only)
                if (not any_of or self.has_any(eid, *any_of))
                and (integration is None or self.integration(eid) == integration)]

    def nearby(self, entity_id: str, *, any_of: tuple[str, ...] = (), domain: str | None = None,
               integration: str | None = None) -> list[str]:
        """Other entities on the same device first, then the rest of the same area, filtered like ``in_area``."""
        dev, area = self.device_of(entity_id), self.area_of(entity_id)

        def ok(eid: str) -> bool:
            cap = self.by_entity[eid]
            return (eid != entity_id and (not any_of or self.has_any(eid, *any_of))
                    and (domain is None or cap.get("domain") == domain)
                    and (integration is None or self.integration(eid) == integration))

        same_dev = sorted(e for e, c in self.by_entity.items() if dev and c.get("device_id") == dev and ok(e))
        same_area = sorted(e for e, c in self.by_entity.items()
                           if area and c.get("area_id") == area and e not in same_dev and ok(e))
        return same_dev + same_area

    # -- abilities ----------------------------------------------------------
    def has_any(self, entity_id: str, *abilities: str) -> bool:
        cap = self.by_entity.get(entity_id)
        return bool(cap) and bool(set(abilities) & set(cap.get("abilities") or []))

    def is_media_target(self, entity_id: str) -> bool:
        """It can be sent sound (a speaker, a TV, a Cast device)."""
        return self.has_any(entity_id, *MEDIA_ABILITIES)

    def is_screen(self, entity_id: str) -> bool:
        """It drives a screen: the hub calls it a TV, or it can open a page or launch an app."""
        cap = self.by_entity.get(entity_id)
        if not cap:
            return False
        return (cap.get("details") or {}).get("device_class") == "tv" or self.has_any(entity_id, *SCREEN_ABILITIES)

    def media_targets(self, area_id: str | None = None, *, available_only: bool = False) -> list[str]:
        """Every entity that can be sent sound, optionally in one area."""
        return sorted(eid for eid in self.by_entity
                      if self.is_media_target(eid)
                      and (area_id is None or self.area_of(eid) == area_id)
                      and (not available_only or self.by_entity[eid].get("available")))

    def pick_player(self, area_id: str, phrase: str = "", purpose: str | None = None,
                    states: list[dict[str, Any]] | None = None) -> str | None:
        """The one player in an area a request means, chosen by what each can do (never by its name).

        ``phrase`` is what the person said ("the TV in the den"): a kind word
        (``SCREEN_WORDS`` / ``SPEAKER_WORDS``) says a screen or a speaker is
        wanted. ``purpose`` "video" wants a screen, "music" a player that
        searches and queues, "announcement" one that announces over what it
        plays. Then available beats unavailable and playing beats idle.
        """
        cands = self.in_area(area_id, any_of=MEDIA_ABILITIES)
        if not cands:
            return None
        by_state = {s.get("entity_id"): s.get("state") for s in states or []}
        words = set(norm(phrase).split())
        wants_screen = purpose == "video" or bool(words & SCREEN_WORDS)
        wants_speaker = bool(words & SPEAKER_WORDS)

        def rank(eid: str) -> tuple:
            screen = self.is_screen(eid)
            kind_ok = (wants_screen and screen) or (wants_speaker and not screen) or not (wants_screen or wants_speaker)
            purpose_ok = ((purpose != "music" or self.has_any(eid, "search_media", "enqueue"))
                          and (purpose != "announcement" or self.has_any(eid, "announce")))
            return (0 if self.by_entity[eid].get("available") else 1, 0 if kind_ok else 1,
                    0 if purpose_ok else 1, state_rank(by_state.get(eid)), eid)

        return sorted(cands, key=rank)[0]

    def service_for(self, entity_id: str, service: str) -> tuple[str, str] | None:
        """``(domain, service)`` when the entity accepts ``<its domain>.<service>``, from the index's service list."""
        cap = self.by_entity.get(entity_id)
        if not cap:
            return None
        domain = cap.get("domain") or entity_id.split(".", 1)[0]
        return (domain, service) if f"{domain}.{service}" in (cap.get("services") or []) else None

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


async def _fetch_index(key: str, ha_token: str) -> dict[str, Any] | None:
    """The index body from the hub, or None when the URL answers 404 (a Home Assistant without one)."""
    from services.execution.http_client import get_session, host_of

    session = await get_session(host_of(key))
    try:
        async with session.get(f"{key}{PATH}", headers={"Authorization": f"Bearer {ha_token}"},
                               timeout=_TIMEOUT) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                raise CapabilityIndexError(f"the hub answered HTTP {resp.status} for its capability index")
            body = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError) as exc:
        raise CapabilityIndexError(f"the hub's capability index did not answer: {type(exc).__name__}") from exc
    if not isinstance(body, dict) or not isinstance(body.get("entities"), list):
        raise CapabilityIndexError("the hub's capability index answered in an unexpected shape")
    return body


async def _fetch_areas(key: str, ha_token: str) -> list[dict[str, Any]]:
    """The hub's area registry (names and aliases), over its HA-compatible WebSocket API."""
    from services.execution.http_client import get_session, host_of

    session = await get_session(host_of(key))
    try:
        async with session.ws_connect(f"{key}/api/websocket") as ws:
            hello = json.loads((await ws.receive(timeout=_TIMEOUT.total)).data)
            if hello.get("type") != "auth_required":
                raise CapabilityIndexError(f"the hub's WebSocket greeted with {hello.get('type')!r}")
            await ws.send_str(json.dumps({"type": "auth", "access_token": ha_token}))
            auth = json.loads((await ws.receive(timeout=_TIMEOUT.total)).data)
            if auth.get("type") != "auth_ok":
                raise CapabilityIndexError("the hub refused this account's token for its area registry")
            await ws.send_str(json.dumps({"id": 1, "type": "config/area_registry/list"}))
            reply = json.loads((await ws.receive(timeout=_TIMEOUT.total)).data)
    except (TimeoutError, aiohttp.ClientError, ValueError, TypeError) as exc:
        raise CapabilityIndexError(f"the hub's area registry did not answer: {type(exc).__name__}") from exc
    if not reply.get("success") or not isinstance(reply.get("result"), list):
        raise CapabilityIndexError(f"the hub's area registry answered {reply.get('error') or 'in an unexpected shape'}")
    return reply["result"]


async def load(ha_url: str, ha_token: str, *, refresh: bool = False) -> CapabilityIndex | None:
    """The hub's index with its areas (cached ``TTL_S``); None when the URL is a Home Assistant without one.

    The areas come with the index when the hub sends them (an ``areas`` list in
    the body), else from its area registry.
    """
    if not ha_url or not ha_token:
        raise CapabilityIndexError("no hub URL or token for this account")
    key = ha_url.rstrip("/")
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and not refresh and now - hit[0] < TTL_S:
        return hit[1]
    body = await _fetch_index(key, ha_token)
    if body is None:
        _cache[key] = (now, None)
        return None
    areas = body.get("areas")
    if not isinstance(areas, list):
        areas = await _fetch_areas(key, ha_token)
    idx = CapabilityIndex(body["entities"], body.get("abilities"), areas)
    _cache[key] = (now, idx)
    return idx


_fallback_logged: set[tuple[str, str]] = set()


async def routing_index(ha_url: str, ha_token: str, purpose: str) -> CapabilityIndex | None:
    """The index a tool routes by. ``None`` only for a plain Home Assistant (404), logged with the reason.

    Any other failure raises ``CapabilityIndexError``: a tool must not quietly
    guess at devices because the hub did not answer.
    """
    idx = await load(ha_url, ha_token)
    if idx is None:
        key = (ha_url.rstrip("/"), purpose)
        level = logging.WARNING if key not in _fallback_logged else logging.DEBUG
        _fallback_logged.add(key)
        log.log(level, "[capabilities] %s: %s has no capability index (HTTP 404: a Home Assistant, not the hub); "
                "falling back to entity ids and attributes", purpose, key[0])
    return idx


def clear_cache() -> None:
    _cache.clear()
    _fallback_logged.clear()
