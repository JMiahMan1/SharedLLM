"""Which speakers an announcement goes to, chosen from what is reachable right now.

An announcement used to need one named device, and a model that named none got
"entity_id or device_name is required"; a model that searched first saw stale
duplicates listed as "unavailable" and told the family nothing could announce.
Now every request has a target:

* a named device or room: that one (``main._announce_one``);
* ``person``: the satellites and speakers assigned to that person in identity
  (``GET /api/internal/person-devices``), plus a notice on their phone;
* ``everywhere`` (or a device name like "the house"): one speaker in every
  room that has a reachable one;
* nothing named: the device the request was spoken to, else everywhere.

Reachable means the hub can deliver to it: playing, paused, idle, on, or off
(a TV that is off is turned on first). Only ``unavailable`` (or an entity the
hub does not report at all) is skipped, and the reply names what was skipped
next to what was announced on. Nothing here knows any home's rooms, devices or
people: they all come from the hub's capability index and identity.
"""
from __future__ import annotations

import logging
from typing import Any

from services.execution.capability_index import MEDIA_ABILITIES, SCREEN_WORDS, SPEAKER_WORDS, norm, state_rank

log = logging.getLogger("execution.announce_targets")

#: States the hub cannot deliver to. Everything else is worth an attempt.
UNREACHABLE_STATES = frozenset({"unavailable"})
#: Players that stand for other players: announcing on them as well as their members plays twice.
VIRTUAL_INTEGRATIONS = frozenset({"group", "universal"})
#: What a device name says when it means the whole house, not one device.
EVERYWHERE_PHRASES = frozenset({
    "everywhere", "all", "all speakers", "every speaker", "all devices", "everyone", "everybody",
    "house", "the house", "whole house", "the whole house", "home", "the home", "whole home",
    "all rooms", "every room", "all of the house", "the family", "family",
})


def is_everywhere(text: str | None) -> bool:
    """The device name means the whole house ("everywhere", "the whole house", "all speakers")."""
    return norm(text) in EVERYWHERE_PHRASES


def names_a_device_kind(text: str | None) -> bool:
    """It says what kind of device ("the den TV", "office speaker"), so it is not a person's name."""
    return bool(set(norm(text).split()) & (SCREEN_WORDS | SPEAKER_WORDS | {"echo", "satellite", "display"}))


def reachable(state: dict[str, Any] | None) -> bool:
    """The hub can try it: it reports the entity and the entity is not ``unavailable``."""
    return bool(state) and str(state.get("state") or "") not in UNREACHABLE_STATES


def label(entity_id: str, by_state: dict[str, dict[str, Any]]) -> str:
    """The name a person knows the device by (its friendly name), else its entity id."""
    attrs = (by_state.get(entity_id) or {}).get("attributes") or {}
    return str(attrs.get("friendly_name") or entity_id)


def _rank(idx: Any, eid: str, by_state: dict[str, dict[str, Any]]) -> tuple:
    state = by_state.get(eid)
    announces = idx is not None and eid in idx and idx.has(eid, "announce")
    virtual = idx is not None and eid in idx and (idx.integration(eid) or "") in VIRTUAL_INTEGRATIONS
    return (0 if reachable(state) else 1, 0 if announces else 1, 1 if virtual else 0,
            state_rank((state or {}).get("state")), eid)


def best_of(idx: Any, candidates: list[str], by_state: dict[str, dict[str, Any]]) -> str | None:
    """The one entity of a device or room to announce on: reachable, able to announce, real, busiest."""
    return sorted(candidates, key=lambda e: _rank(idx, e, by_state))[0] if candidates else None


def _is_speaker(idx: Any, eid: str, by_state: dict[str, dict[str, Any]]) -> bool:
    if idx is not None and eid in idx:
        return not idx.is_screen(eid)
    attrs = (by_state.get(eid) or {}).get("attributes") or {}
    return attrs.get("device_class") != "tv"


def everywhere(idx: Any, by_state: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """One speaker in every room (and every speaker in no room), and the rooms whose speakers are all offline.

    TVs are left out: "announce to the house" should not switch on every
    screen. Groups are left out, since their members are announced on already.
    Returns ``(targets, skipped labels)``.
    """
    if idx is None:
        players = [e for e in by_state if e.startswith("media_player.") and _is_speaker(None, e, by_state)]
        groups: dict[str, list[str]] = {}
        for eid in players:
            groups.setdefault(eid, []).append(eid)
    else:
        players = [e for e in idx.media_targets()
                   if _is_speaker(idx, e, by_state) and (idx.integration(e) or "") not in VIRTUAL_INTEGRATIONS]
        groups = {}
        for eid in players:
            key = idx.area_of(eid) or idx.device_of(eid) or eid
            groups.setdefault(key, []).append(eid)
    targets: list[str] = []
    skipped: list[str] = []
    for members in groups.values():
        pick = best_of(idx, members, by_state)
        if pick is None:
            continue
        if reachable(by_state.get(pick)):
            targets.append(pick)
        else:
            skipped.append(label(pick, by_state))
    return sorted(targets), sorted(set(skipped))


def satellite_player(idx: Any, satellite: str | None, by_state: dict[str, dict[str, Any]]) -> str | None:
    """The speaker of the voice satellite a request was spoken to, else the best one in its room."""
    if idx is None or not satellite or satellite not in idx:
        return None
    device = idx.device_of(satellite)
    own = [e for e in idx.nearby(satellite, any_of=MEDIA_ABILITIES) if device and idx.device_of(e) == device]
    pick = best_of(idx, own, by_state)
    if pick and reachable(by_state.get(pick)):
        return pick
    area = idx.area_of(satellite)
    if area:
        pick = idx.pick_player(area, "speaker", "announcement", list(by_state.values()))
        if pick and reachable(by_state.get(pick)):
            return pick
    return None


def alternative_for(idx: Any, entity_id: str, by_state: dict[str, dict[str, Any]]) -> str | None:
    """A reachable player standing in for an unavailable one: on the same device, else in the same room."""
    if idx is None or entity_id not in idx:
        return None
    device = idx.device_of(entity_id)
    same_device = [e for e in idx.nearby(entity_id, any_of=MEDIA_ABILITIES)
                   if device and idx.device_of(e) == device and reachable(by_state.get(e))]
    pick = best_of(idx, same_device, by_state)
    if pick:
        return pick
    area = idx.area_of(entity_id)
    if area:
        pick = idx.pick_player(area, label(entity_id, by_state), "announcement", list(by_state.values()))
        if pick and pick != entity_id and reachable(by_state.get(pick)):
            return pick
    return None


def person_players(idx: Any, devices: list[dict[str, Any]],
                   by_state: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """The player of each device assigned to a person, and the labels of their devices that are offline.

    Satellites and speakers come first; TVs only when the person has neither.
    A satellite with no media player of its own uses the speaker on its device.
    """
    speakers = [d for d in devices if d.get("kind") != "tv"]
    chosen = speakers or [d for d in devices if d.get("kind") == "tv"]
    targets: list[str] = []
    skipped: list[str] = []
    for device in chosen:
        ids = [str(e) for e in device.get("entity_ids") or []]
        players = [e for e in ids if e.startswith("media_player.")]
        if not players and idx is not None:
            for sat in (e for e in ids if e in idx):
                own = idx.device_of(sat)
                players += [e for e in idx.nearby(sat, any_of=MEDIA_ABILITIES) if own and idx.device_of(e) == own]
        pick = best_of(idx, players, by_state)
        if pick and reachable(by_state.get(pick)):
            if pick not in targets:
                targets.append(pick)
        else:
            skipped.append(str(device.get("label") or (pick and label(pick, by_state)) or "a device"))
    return targets, skipped


def _join(names: list[str]) -> str:
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def summary(done: list[str], failed: list[tuple[str, str]], skipped: list[str], pushed: str | None = None) -> str:
    """What was actually done, in one or two sentences a person can be told as is."""
    parts = []
    if done:
        parts.append(f"Announced on {_join(done)}.")
    if pushed:
        parts.append(f"Sent it to {pushed}'s phone.")
    if failed:
        parts.append("Could not play it on " + "; ".join(f"{name} ({why})" for name, why in failed) + ".")
    if skipped:
        verb = "is" if len(skipped) == 1 else "are"
        parts.append(f"{_join(skipped)} {verb} offline, so I skipped {'it' if len(skipped) == 1 else 'them'}.")
    if not parts:
        parts.append("There is no speaker on the hub to announce on.")
    return " ".join(parts)
