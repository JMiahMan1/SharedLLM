"""Corroborated sound detection (Alexa Guard+) for Echo satellites.

Stateful processor for ``event.*_sound`` states reported by Echo satellites.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("homehub.sound_guard")

ALL_SOUND_EVENTS = frozenset({
    "smoke_or_co_alarm",
    "glass_break",
    "dog_bark",
    "baby_cry",
    "snoring",
    "cough",
    "water",
    "beeping_appliance",
})


@dataclass
class SoundGuardConfig:
    corroboration_window_s: float = 15.0
    emergency_events: frozenset[str] = frozenset({"smoke_or_co_alarm", "glass_break"})
    monitor_events: frozenset[str] = frozenset({"baby_cry", "dog_bark"})
    require_corroboration_when_home: bool = True
    cooldown_s: float = 60.0


@dataclass
class SoundAlert:
    event_type: str
    priority: int
    corroborated: bool
    devices: list[str]
    rooms: list[str]
    title: str
    body: str
    flash_rings: bool
    drop_in_room: str | None


def _state_value(state: Any) -> Any:
    # Home Assistant event.* entities store the sound type in attributes['event_type'],
    # while state.state is just an ISO-8601 timestamp. Check attributes first.
    attrs = None
    if hasattr(state, "attributes"):
        attrs = getattr(state, "attributes", None)
    elif isinstance(state, dict):
        attrs = state.get("attributes")
    if isinstance(attrs, dict) and "event_type" in attrs:
        return attrs["event_type"]
    if isinstance(state, dict) and "event_type" in state:
        return state["event_type"]
    if hasattr(state, "state"):
        return state.state
    if isinstance(state, dict) and "state" in state:
        return state["state"]
    return state


def extract_sound_event(entity_id: str, state: Any) -> str | None:
    if not isinstance(entity_id, str):
        return None
    if not entity_id.startswith("event."):
        return None
    if "_sound" not in entity_id:
        return None
    value = _state_value(state)
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    low = value.lower()
    if low in ("unavailable", "unknown", "none", "off", "on", "idle"):
        return None
    if value not in ALL_SOUND_EVENTS:
        # allow any recognized by config? keep strict known set
        return None
    return value


class SoundGuard:
    def __init__(self, config: SoundGuardConfig | None = None) -> None:
        self.config = config or SoundGuardConfig()
        self._observations: list[dict[str, Any]] = []
        self._last_alert: dict[tuple[str, str], float] = {}

    def _scope(self, room: str | None, device_id: str) -> str:
        return room if room else device_id

    def _prune(self, event_type: str, now: float) -> None:
        cutoff = now - self.config.corroboration_window_s
        self._observations = [o for o in self._observations
                              if not (o["event_type"] == event_type and o["now"] < cutoff)]

    def evaluate(self, *, device_id: str, entity_id: str, room: str | None,
                event_type: str, now: float, nobody_home: bool = False) -> SoundAlert | None:
        if (event_type not in ALL_SOUND_EVENTS and event_type not in self.config.emergency_events
                and event_type not in self.config.monitor_events):
            return None
        cfg = self.config
        scope = self._scope(room, device_id)
        is_emergency = event_type in cfg.emergency_events
        is_monitor = event_type in cfg.monitor_events
        cooldown_key = (event_type, "__emergency__") if is_emergency else (event_type, scope)

        # cooldown check
        last = self._last_alert.get(cooldown_key)
        if last is not None and (now - last) < cfg.cooldown_s:
            return None

        if is_monitor and not is_emergency:
            # monitor event: emit priority 3
            self._last_alert[cooldown_key] = now
            rooms = [room] if room else []
            return SoundAlert(
                event_type=event_type,
                priority=3,
                corroborated=False,
                devices=[device_id],
                rooms=rooms,
                title=f"{event_type.replace('_', ' ').title()} detected",
                body=f"{event_type.replace('_', ' ')} detected in {room or 'unknown room'}",
                flash_rings=False,
                drop_in_room=room,
            )

        if not is_emergency:
            return None

        # record observation
        self._prune(event_type, now)
        self._observations.append({
            "device_id": device_id,
            "room": room,
            "now": now,
            "event_type": event_type,
        })
        self._prune(event_type, now)

        # distinct devices within window
        obs = [o for o in self._observations
               if o["event_type"] == event_type and now - o["now"] <= cfg.corroboration_window_s]
        devices = []
        rooms = []
        for o in obs:
            if o["device_id"] not in devices:
                devices.append(o["device_id"])
            if o["room"] is not None and o["room"] not in rooms:
                rooms.append(o["room"])

        # determine escalation
        escalate = False
        corroborated = len(devices) >= 2
        if nobody_home or not cfg.require_corroboration_when_home or len(devices) >= 2:
            escalate = True
        # if require_corroboration_when_home and not nobody_home and len<2: not escalate

        if not escalate:
            return None

        # cooldown global? apply per scope of triggering device; also suppress other scopes?
        self._last_alert[cooldown_key] = now
        title = f"{event_type.replace('_', ' ').title()} detected"
        body = f"{event_type.replace('_', ' ')} detected in {', '.join(rooms) if rooms else 'unknown room'}"
        return SoundAlert(
            event_type=event_type,
            priority=5,
            corroborated=corroborated,
            devices=devices,
            rooms=rooms,
            title=title,
            body=body,
            flash_rings=True,
            drop_in_room=None,
        )


def attach_to_hub(
    hub: Any,
    guard: SoundGuard | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], None]:
    """Subscribe a ``SoundGuard`` to ``hub.bus`` ``state_changed`` events.

    Returns a cleanup callable that unsubscribes from the bus and cancels any
    in-flight alert action tasks.
    """
    from services.homehub import echo_ring

    sg = guard or SoundGuard()
    tasks: set[asyncio.Task[Any]] = set()

    async def _handle_alert(alert: SoundAlert) -> None:
        if alert.flash_rings:
            try:
                await echo_ring.flash_all(hub, color="red", repeat=8)
            except Exception as exc:
                log.warning("sound guard could not flash Echo rings: %s", exc)
        if ("persistent_notification", "create") in getattr(hub, "services", {}):
            try:
                await hub.call_service("persistent_notification", "create", {
                    "notification_id": f"sound_guard_{alert.event_type}",
                    "title": alert.title,
                    "message": alert.body,
                })
            except Exception as exc:
                log.warning("sound guard could not create notification: %s", exc)

    def _spawn(coro: Any) -> None:
        task = asyncio.create_task(coro, name="sound-guard-alert")
        tasks.add(task)

        def _done(done_task: asyncio.Task[Any]) -> None:
            tasks.discard(done_task)
            if not done_task.cancelled():
                exc = done_task.exception()
                if exc is not None:
                    log.warning("sound guard alert action failed: %r", exc)

        task.add_done_callback(_done)

    def _on_state_changed(_event_type: str, data: dict[str, Any]) -> None:
        entity_id = str((data or {}).get("entity_id") or "")
        new_state = (data or {}).get("new_state")
        sound_type = extract_sound_event(entity_id, new_state)
        if sound_type is None:
            return
        entry = hub.entities.get(entity_id)
        device_id = (entry.device_id if entry is not None and entry.device_id else None) or entity_id
        room = hub.area_name(entity_id)
        persons = hub.states.all("person")
        nobody_home = bool(persons) and not any(
            str(p.state or "").strip().lower() == "home" for p in persons
        )
        alert = sg.evaluate(
            device_id=device_id,
            entity_id=entity_id,
            room=room,
            event_type=sound_type,
            now=clock(),
            nobody_home=nobody_home,
        )
        if alert is None:
            return
        hub.bus.fire("echo_sound_alert", {
            "event_type": alert.event_type,
            "priority": alert.priority,
            "corroborated": alert.corroborated,
            "devices": list(alert.devices),
            "rooms": list(alert.rooms),
            "title": alert.title,
            "body": alert.body,
            "flash_rings": alert.flash_rings,
            "drop_in_room": alert.drop_in_room,
        })
        _spawn(_handle_alert(alert))

    unsub = hub.bus.subscribe(_on_state_changed, "state_changed")

    def detach() -> None:
        unsub()
        for t in list(tasks):
            t.cancel()
        tasks.clear()

    detach.tasks = tasks  # type: ignore[attr-defined]
    return detach
