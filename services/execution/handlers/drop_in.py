"""Native Echo-to-Echo Drop In, through the hub's ``echo-dot-assist`` nodes.

Every Echo running ``echo-dot-assist`` (hassmic) exposes the same small surface:

* ``esphome.<node>_drop_in`` with ``{"target": "<node or name>"}`` starts a call;
* ``sensor.<node>_drop_in`` is ``idle``, ``calling``, ``ringing`` or ``connected``;
* ``sensor.<node>_drop_in_with`` names the peer;
* ``button.<node>_end_drop_in`` hangs up;
* ``event.<node>_drop_in_result`` reports why a call did or did not connect;
* ``switch.<node>_do_not_disturb`` and the node's mute switch gate a call.

This handler discovers those nodes from the hub's states, resolves the source
and destination Echoes (including a person's room through Bluetooth presence),
starts or ends the call, and speaks the hub's result. It never guesses a room,
a device or a hub: anything it cannot resolve is said plainly.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from services.execution import capability_index, ha_client, hardware_router, room_presence
from services.execution.schemas import ExecutionResult, UserContext
from services.execution.schemas_drop_in import DropInRequest

log = logging.getLogger("execution.drop_in")

#: How long the hub gets to report the result of a started Drop In.
DROP_IN_WAIT_S = 4.5
#: How often the result event is polled while waiting.
DROP_IN_POLL_S = 0.15

_SENSOR = re.compile(r"^sensor\.(.+)_drop_in$")
_BUTTON = re.compile(r"^button\.(.+)_end_drop_in$")
_EVENT = re.compile(r"^event\.(.+)_drop_in_result$")
_DND = re.compile(r"^switch\.(.+)_do_not_disturb$")
_ACTIVE_STATES = ("calling", "ringing", "connected")
#: A "mute" switch says muted when it is on; a "microphone" switch says muted
#: when it is off. A microphone switch that is on means the mic is enabled.
_MUTE_ON = frozenset({"mute", "muted", "mic_mute", "microphone_mute", "microphones_off"})
_MIC_OFF = frozenset({"microphone", "microphones", "mic"})


@dataclass
class DropInNode:
    """One Echo's Drop In entities and current state."""

    node: str
    sensor: str | None = None
    button: str | None = None
    event: str | None = None
    satellite: str | None = None
    area_id: str | None = None
    label: str = ""
    state: str = "unknown"
    peer: str = ""
    dnd: bool = False
    muted: bool = False

    @property
    def active(self) -> bool:
        return self.state in _ACTIVE_STATES


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _room_slug(value: Any) -> str:
    slug = _norm(value)
    return slug[4:] if slug.startswith("the_") else slug


def _humanize(value: Any) -> str:
    return " ".join(part.capitalize() for part in _norm(value).split("_") if part)


def _state_map(states: list[dict] | None) -> dict[str, dict]:
    return {
        str(state.get("entity_id") or ""): state
        for state in states or []
        if isinstance(state, dict) and state.get("entity_id")
    }


def _state_text(state: dict | None) -> str:
    return str((state or {}).get("state") or "").strip().lower()


def _friendly_name(state: dict | None) -> str:
    return str(((state or {}).get("attributes") or {}).get("friendly_name") or "").strip()


def _node_from_entity(entity_id: str) -> str | None:
    for pattern in (_SENSOR, _BUTTON, _EVENT):
        match = pattern.match(entity_id)
        if match:
            return match.group(1)
    return None


def _satellite_score(entity_id: str, state: dict, node: DropInNode, idx: Any) -> int:
    """How strongly one ``assist_satellite.*`` entity belongs to one Drop In node."""
    obj = entity_id.split(".", 1)[-1]
    score = 0
    if obj == node.node:
        score = 100
    elif obj in node.node or node.node in obj:
        score = 80
    else:
        friendly = _norm(_friendly_name(state))
        if node.node.replace("_", " ") in friendly:
            score = 60
    if idx is not None:
        try:
            satellite_area = idx.area_of(entity_id)
            node_area = node.area_id or (idx.area_of(node.sensor) if node.sensor else None)
        except Exception:  # an index that cannot answer is no worse than no index
            satellite_area = node_area = None
        if satellite_area and node_area and satellite_area == node_area:
            score = max(score, 70)
    return score


def _discover(states: list[dict] | None, idx: Any = None) -> dict[str, DropInNode]:
    """Every Echo that exposes the Drop In surface, keyed by its ``<node>`` slug."""
    by_id = _state_map(states)
    nodes: dict[str, DropInNode] = {}
    for entity_id in by_id:
        node = _node_from_entity(entity_id)
        if node is None:
            continue
        entry = nodes.setdefault(node, DropInNode(node=node))
        if _SENSOR.match(entity_id):
            entry.sensor = entity_id
        elif _BUTTON.match(entity_id):
            entry.button = entity_id
        elif _EVENT.match(entity_id):
            entry.event = entity_id

    satellites = [
        (entity_id, state) for entity_id, state in by_id.items()
        if entity_id.startswith("assist_satellite.")
    ]
    for node in nodes.values():
        if idx is not None:
            for entity_id in (node.sensor, node.button, node.event):
                if entity_id:
                    node.area_id = node.area_id or idx.area_of(entity_id)
        node.state = _state_text(by_id.get(node.sensor or "")) or "unknown"
        node.peer = _state_text(by_id.get(f"sensor.{node.node}_drop_in_with"))
        if node.peer in ("unknown", "unavailable"):
            node.peer = ""
        dnd = by_id.get(f"switch.{node.node}_do_not_disturb")
        node.dnd = _state_text(dnd) == "on"
        for entity_id, state in by_id.items():
            obj = entity_id.split(".", 1)[-1]
            if not obj.startswith(f"{node.node}_"):
                continue
            rest = obj[len(node.node) + 1:]
            power = _state_text(state)
            if (rest in _MUTE_ON and power == "on") or (rest in _MIC_OFF and power == "off"):
                node.muted = True

    for entity_id, state in satellites:
        best: DropInNode | None = None
        best_score = 0
        for node in nodes.values():
            score = _satellite_score(entity_id, state, node, idx)
            if score > best_score:
                best, best_score = node, score
        if best is not None:
            best.satellite = entity_id

    for node in nodes.values():
        friendly = _friendly_name(by_id.get(node.satellite or ""))
        area_name = None
        if idx is not None and node.area_id:
            try:
                area_name = idx.area_name(node.area_id)
            except Exception:
                area_name = None
        node.label = str(friendly or area_name or _humanize(node.node)).strip()
    return nodes


def _source_node(nodes: dict[str, DropInNode], satellite: str | None) -> DropInNode | None:
    """The Drop In node behind an ``assist_satellite.*`` entity (or a node slug)."""
    wanted = str(satellite or "").strip().lower()
    if not wanted:
        return None
    obj = wanted.split(".", 1)[-1]
    for node in nodes.values():
        if node.satellite and node.satellite.lower() == wanted:
            return node
    for node in nodes.values():
        if node.node == obj:
            return node
    matches = [node for node in nodes.values() if node.node in obj or obj in node.node]
    return sorted(matches, key=lambda node: node.node)[0] if matches else None


def _find_device(nodes: dict[str, DropInNode], device: str) -> DropInNode | None:
    """The node a person named, by node slug, entity id or friendly name."""
    raw = str(device or "").strip().lower()
    if "." in raw:
        raw = raw.split(".", 1)[-1]
    wanted = re.sub(r"_(?:end_)?drop_in(?:_result)?$", "", _norm(raw))
    if not wanted:
        return None
    for node in nodes.values():
        candidates = {_norm(node.node), _norm(node.label)}
        if node.satellite:
            candidates.add(_norm(node.satellite.split(".", 1)[-1]))
        if wanted in candidates:
            return node
    partial = [node for node in nodes.values() if wanted in _norm(node.node) or _norm(node.node) in wanted]
    return sorted(partial, key=lambda node: node.node)[0] if partial else None


def _nodes_in_room(nodes: dict[str, DropInNode], room: str, idx: Any) -> list[DropInNode]:
    """The Echoes in one room. An explicitly named room never falls back to another."""
    if idx is not None:
        area_id = idx.require_area(room)
        return sorted(
            (node for node in nodes.values() if node.area_id == area_id),
            key=lambda node: node.node,
        )
    wanted = _room_slug(room)
    if not wanted:
        return []
    found = []
    for node in nodes.values():
        haystacks = [_norm(node.node), _room_slug(node.label)]
        if node.satellite:
            haystacks.append(_norm(node.satellite.split(".", 1)[-1]))
        if node.area_id:
            haystacks.append(_norm(node.area_id))
        if any(wanted in haystack for haystack in haystacks if haystack):
            found.append(node)
    return sorted(found, key=lambda node: node.node)


def _room_label(room: str, idx: Any) -> str:
    if idx is not None:
        try:
            name = idx.area_name(idx.require_area(room))
        except Exception:
            name = None
        if name:
            return str(name)
    return _humanize(room)


def _pick_destination(candidates: list[DropInNode], source: DropInNode) -> DropInNode:
    """A free Echo first: not the source, not in Do Not Disturb, not busy, not muted."""
    others = [node for node in candidates if node.node != source.node]
    return sorted(others, key=lambda node: (node.dnd, node.active, node.muted, node.node))[0]


def _utc(value: datetime) -> datetime:
    """Make a datetime comparable with an aware one; a naive stamp is read as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _timestamp(state: dict | None) -> datetime | None:
    """The newest ISO timestamp for an entity, for spotting a fresh result event.

    Home Assistant's ``event.*`` entities keep the monotonic firing time in
    ``state["state"]`` (and may repeat it in ``last_changed``/``last_updated``).
    """
    if not state:
        return None
    stamps: list[datetime] = []
    for key in ("state", "last_changed", "last_updated"):
        raw = state.get(key)
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        stamps.append(_utc(stamp))
    return max(stamps) if stamps else None


def _advanced(state: dict | None, before: datetime | None) -> bool:
    """Whether ``state`` is a new event, not the stale event already on the hub."""
    after = _timestamp(state)
    if after is None:
        return False  # no timestamp means no evidence of a new event
    if before is None:
        return True
    return after > _utc(before)


def _event_type(state: dict | None) -> str:
    if not state:
        return ""
    attrs = state.get("attributes") or {}
    event = str(attrs.get("event_type") or state.get("state") or "").strip().lower()
    return "" if event in ("", "unknown", "unavailable") else event


def _reply(event: str, label: str) -> ExecutionResult:
    """The spoken answer for one ``drop_in_result`` event type."""
    service = "drop_in"
    detail = {"event": event}
    if event == "connected":
        return ExecutionResult.ok(f"Dropping in on {label}.", service=service, detail=detail)
    if event == "ringing":
        return ExecutionResult.ok(f"Ringing {label}.", service=service, detail=detail)
    if event == "do_not_disturb":
        return ExecutionResult.fail(f"{label} is in Do Not Disturb.", service=service, detail=detail)
    if event == "microphones_off":
        return ExecutionResult.fail(f"{label} has its microphones muted.", service=service, detail=detail)
    if event == "drop_in_off":
        return ExecutionResult.fail(f"Drop In is turned off on {label}.", service=service, detail=detail)
    if event == "busy":
        return ExecutionResult.fail(f"{label} is already on another call.", service=service, detail=detail)
    if event in ("declined", "not_answered", "no_answer"):
        return ExecutionResult.fail(f"{label} did not answer.", service=service, detail=detail)
    if event == "not_found":
        return ExecutionResult.fail(
            f"{label} is not in this Echo's Drop In network.", service=service, detail=detail
        )
    if event == "busy_here":
        return ExecutionResult.fail("This Echo is already on another call.", service=service, detail=detail)
    if event == "microphones_off_here":
        return ExecutionResult.fail("This Echo's microphones are muted.", service=service, detail=detail)
    if event == "cancelled":
        return ExecutionResult.fail("The Drop In was cancelled.", service=service, detail=detail)
    if event == "failed":
        return ExecutionResult.fail("The Drop In failed.", service=service, detail=detail)
    return ExecutionResult.fail("The Drop In failed.", service=service, detail=detail)


async def _wait_for_result(hub_url: str, hub_token: str, source: DropInNode,
                           before: datetime | None) -> str:
    """Poll the source's result event until it reports a new outcome or time runs out."""
    if not source.event:
        return ""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DROP_IN_WAIT_S
    while True:
        state = await ha_client.get_state(hub_url, hub_token, source.event)
        event = _event_type(state)
        if event and _advanced(state, before):
            return event
        if loop.time() >= deadline:
            return ""
        await asyncio.sleep(DROP_IN_POLL_S)


def _precheck(source: DropInNode, destination: DropInNode) -> str | None:
    """Why a call cannot start, learned from states before asking the hub."""
    if source.active:
        return "This Echo is already on another call."
    if destination.dnd:
        return f"{destination.label} is in Do Not Disturb."
    if destination.state in ("off", "disabled"):
        return f"Drop In is turned off on {destination.label}."
    if destination.active:
        return f"{destination.label} is already on another call."
    if destination.muted:
        return f"{destination.label} has its microphones muted."
    return None


async def _person_room(req: DropInRequest, hub_url: str, hub_token: str) -> tuple[str | None, str | None]:
    """``(room, person)`` for a person's current room, or the room as spoken."""
    person = str(req.target_user_id or "").strip().lower()
    named = room_presence.room_of_target(req.target_room or "")
    if not person and named:
        person = named
    if not person:
        return (str(req.target_room).strip() or None), None
    presence = await room_presence.resolve_room_target(person, hub_url, hub_token)
    return (presence.area_id or presence.room), person


async def _start(req: DropInRequest, user_context: UserContext, nodes: dict[str, DropInNode],
                 states: list[dict], hub_url: str, hub_token: str, idx: Any) -> ExecutionResult:
    source_satellite = str(req.source_satellite or user_context.voice_satellite or "").strip()
    if not source_satellite:
        return ExecutionResult.fail(
            "I need to be asked from an Echo to start a Drop In.", service="drop_in"
        )
    source = _source_node(nodes, source_satellite)
    if source is None:
        return ExecutionResult.fail("That speaker cannot start an Echo Drop In.", service="drop_in")

    # Check permission before asking the hub for the room: a failed presence
    # lookup ("no beacon", "not home") would otherwise tell the caller where
    # the family member is, or at least that their beacon is not set up.
    person = str(req.target_user_id or "").strip().lower() or room_presence.room_of_target(req.target_room or "")
    if person and not await room_presence.may_see_room(str(user_context.user or ""), person):
        # Never name the room of someone the caller may not see.
        return ExecutionResult.fail(f"I can't share where {person} is.", service="drop_in")
    try:
        room, person = await _person_room(req, hub_url, hub_token)
    except room_presence.RoomTargetError as exc:
        return ExecutionResult.fail(f"Drop In not started: {exc}.", service="drop_in")

    if req.target_device:
        destination = _find_device(nodes, req.target_device)
        if destination is None:
            return ExecutionResult.fail(
                f"I couldn't find an Echo called {req.target_device}.", service="drop_in"
            )
    elif room:
        try:
            candidates = _nodes_in_room(nodes, room, idx)
        except Exception as exc:
            return ExecutionResult.fail(f"Drop In not started: {exc}.", service="drop_in")
        if not candidates:
            return ExecutionResult.fail(f"I couldn't find an Echo in the {_room_label(room, idx)}.", service="drop_in")
        others = [node for node in candidates if node.node != source.node]
        if not others:
            return ExecutionResult.fail(
                f"You are already in the {_room_label(room, idx)}.", service="drop_in"
            )
        destination = _pick_destination(candidates, source)
    else:
        return ExecutionResult.fail("Tell me which room or Echo to drop in on.", service="drop_in")

    if destination.node == source.node:
        return ExecutionResult.fail(
            f"You are already in the {_room_label(room or destination.label, idx)}.", service="drop_in"
        )

    problem = _precheck(source, destination)
    if problem:
        return ExecutionResult.fail(problem, service="drop_in", detail={"target": destination.node})

    by_id = _state_map(states)
    before = _timestamp(by_id.get(source.event or ""))
    try:
        called = await ha_client.call_service(
            hub_url, hub_token, "esphome", f"{source.node}_drop_in",
            service_data={"target": destination.node},
        )
    except Exception as exc:
        log.warning("[drop_in] %s -> %s could not be started: %s", source.node, destination.node, exc)
        return ExecutionResult.fail(f"Drop In could not start: {exc}.", service="drop_in")
    if not called.get("ok"):
        error = str(called.get("error") or "the hub refused it")
        log.warning("[drop_in] %s -> %s failed: %s", source.node, destination.node, error)
        return ExecutionResult.fail(f"Drop In could not start: {error}.", service="drop_in")

    event = await _wait_for_result(hub_url, hub_token, source, before)
    if not event:
        return ExecutionResult.fail(
            f"{destination.label} did not answer.", service="drop_in",
            detail={"target": destination.node, "timed_out": True},
        )
    result = _reply(event, destination.label)
    if result.detail is None:
        result.detail = {}
    result.detail.update({"target": destination.node, "source": source.node})
    return result


async def _end(req: DropInRequest, user_context: UserContext, nodes: dict[str, DropInNode],
               hub_url: str, hub_token: str) -> ExecutionResult:
    source_satellite = str(req.source_satellite or user_context.voice_satellite or "").strip()
    if source_satellite:
        source = _source_node(nodes, source_satellite)
        if source is None:
            return ExecutionResult.fail("That speaker cannot end an Echo Drop In.", service="drop_in")
        target = source if source.active else None
    else:
        active = sorted((node for node in nodes.values() if node.active), key=lambda node: node.node)
        target = active[0] if active else None
    if target is None:
        return ExecutionResult.fail("No Drop-In is active.", service="drop_in")
    if not target.button:
        return ExecutionResult.fail("This Echo cannot hang up a Drop In.", service="drop_in")
    try:
        called = await ha_client.call_service(hub_url, hub_token, "button", "press", entity_id=target.button)
    except Exception as exc:
        log.warning("[drop_in] %s could not hang up: %s", target.node, exc)
        return ExecutionResult.fail(f"Drop In could not end: {exc}.", service="drop_in")
    if not called.get("ok"):
        error = str(called.get("error") or "the hub refused it")
        log.warning("[drop_in] %s could not hang up: %s", target.node, error)
        return ExecutionResult.fail(f"Drop In could not end: {error}.", service="drop_in")
    return ExecutionResult.ok("Drop In ended.", service="drop_in", detail={"node": target.node})


def _status(nodes: dict[str, DropInNode]) -> ExecutionResult:
    active = sorted((node for node in nodes.values() if node.active), key=lambda node: node.node)
    calls = [
        {"node": node.node, "label": node.label, "state": node.state, "with": node.peer or None}
        for node in active
    ]
    if not active:
        return ExecutionResult.ok("No Drop-In calls are active.", service="drop_in", detail={"calls": []})
    parts = []
    for node in active:
        if node.state == "connected" and node.peer:
            parts.append(f"{node.label} is connected with {_humanize(node.peer)}")
        elif node.state == "connected":
            parts.append(f"{node.label} is connected")
        elif node.state == "ringing":
            parts.append(f"{node.label} is ringing")
        elif node.state == "calling":
            parts.append(f"{node.label} is calling")
        else:
            parts.append(f"{node.label} is {node.state}")
    return ExecutionResult.ok(
        "Drop In active: " + "; ".join(parts) + ".", service="drop_in", detail={"calls": calls}
    )


async def handle_drop_in(req: DropInRequest, user_context: UserContext) -> ExecutionResult:
    """Start, end, or report native Echo Drop In calls."""
    hub_url, hub_token = hardware_router.hub_pair(user_context)
    if not hub_url:
        return ExecutionResult.fail(hardware_router.NO_HUB_MESSAGE, service="drop_in")
    if not hub_token:
        return ExecutionResult.fail("The home hub token is not configured for this user.", service="drop_in")

    try:
        states = await ha_client.get_states(hub_url, hub_token)
    except Exception as exc:
        log.warning("[drop_in] could not read the home hub: %s", exc)
        return ExecutionResult.fail(f"Could not read the home hub: {exc}", service="drop_in")

    idx = None
    try:
        idx = await capability_index.routing_index(hub_url, hub_token, "Echo Drop In")
    except capability_index.CapabilityIndexError as exc:
        # Plain HA returns None (logged by routing_index). A hub that cannot build
        # its index is still usable through entity and friendly-name matching.
        log.warning("[drop_in] capability index unavailable: %s", exc)

    nodes = _discover(states, idx)
    action = str(req.action or "start").strip().lower()
    if action == "status":
        return _status(nodes)
    if action == "end":
        return await _end(req, user_context, nodes, hub_url, hub_token)
    if action == "start":
        return await _start(req, user_context, nodes, states or [], hub_url, hub_token, idx)
    return ExecutionResult.fail(f"Unknown Drop In action {req.action!r}.", service="drop_in")
