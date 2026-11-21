"""switch_as_x: show a switch as another kind of entity (HA's helper of that name).

The entry's options name the wrapped switch (``entity_id``), the domain it is
shown as (``target_domain``) and whether its sense is inverted (``invert``).
Nothing here talks to a device: the state mirrors the wrapped switch through
the hub's ``state_changed`` events, and commands become ``switch.turn_on`` /
``turn_off`` / ``toggle`` calls on it, so whichever driver owns the switch does
the work. A typical one: ``light.example_plug`` wrapping
``switch.example_plug`` (an ESPHome smart plug). As in HA, adding a
wrapper hides the switch (``hidden_by: integration``), so the device shows once,
and removing the wrapper shows it again.

**A door opener as a cover.** A garage door opener wired to a relay is a pulse
button plus, usually, a contact sensor: pressing it starts, stops or reverses the
door, and the switch says nothing about where the door is. HA builds that with a
template cover; here a ``cover`` wrapper takes three more options:

- ``state_entity_id``: a ``binary_sensor`` whose ``on`` means open (``invert``
  flips it for a sensor that reads ``on`` when closed). The cover's state follows
  it. Right after a press the cover reads ``opening`` or ``closing`` until the
  sensor changes or ``travel_s`` (default 30 s) passes, as a template cover does.
- ``momentary``: the switch is a pulse. ``open_cover`` presses only while the door
  is closed, ``close_cover`` only while it is open (or opening), ``stop_cover``
  only while it is moving, ``toggle`` always. A press is ``switch.turn_on`` and,
  ``PULSE_S`` later, ``switch.turn_off`` unless the switch already turned itself
  off (ESPHome relays with ``on_turn_on: delay, turn_off`` do).
- ``device_class``: ``garage``, ``door``, ``gate``... (HA's cover classes).

Without a sensor a momentary cover cannot know where the door is: its state is
``unknown`` (``assumed_state``) and every open or close presses. Without any of
these options a cover is the plain switch mirror it always was.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from services.homehub.core import UNAVAILABLE, UNKNOWN, Driver, EntityEntry, HubError, ServiceNotFound, State

# (state when the switch reads "on", state when "off") per target domain; invert swaps them.
STATES: dict[str, tuple[str, str]] = {
    "light": ("on", "off"),
    "fan": ("on", "off"),
    "siren": ("on", "off"),
    "cover": ("open", "closed"),
    "valve": ("open", "closed"),
    "lock": ("locked", "unlocked"),
}

# Target-domain service -> does it want the switch on (True), off (False), or toggled (None).
SERVICES: dict[str, dict[str, bool | None]] = {
    "light": {"turn_on": True, "turn_off": False, "toggle": None},
    "fan": {"turn_on": True, "turn_off": False, "toggle": None},
    "siren": {"turn_on": True, "turn_off": False, "toggle": None},
    "cover": {"open_cover": True, "close_cover": False, "toggle": None},
    "valve": {"open_valve": True, "close_valve": False, "toggle": None},
    "lock": {"lock": True, "unlock": False},
}

# HA supported_features for what a plain on/off switch can do in each domain.
FEATURES: dict[str, int] = {
    "light": 0,
    "fan": 16 | 32,          # TURN_OFF | TURN_ON
    "siren": 1 | 2,          # TURN_ON | TURN_OFF
    "cover": 1 | 2,          # OPEN | CLOSE
    "valve": 1 | 2,          # OPEN | CLOSE
    "lock": 0,
}
COVER_STOP = 8

#: HA's CoverDeviceClass values, offered when a switch is shown as a cover.
COVER_CLASSES = ("garage", "door", "gate", "awning", "blind", "curtain", "damper", "shade", "shutter", "window")
#: How long a press holds the relay before the hub lets go (the opener sees a button press).
PULSE_S = 0.5
#: How long "opening"/"closing" is shown after a press when the sensor does not change.
DEFAULT_TRAVEL_S = 30.0


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


class SwitchAsXDriver(Driver):
    platform = "switch_as_x"
    poll_interval = None  # push: follows the wrapped switch's events

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        opts = {**entry.data, **entry.options}
        self.source: str = str(opts.get("entity_id") or "")
        self.invert: bool = _flag(opts.get("invert"))
        self.target_domain: str = str(opts.get("target_domain") or "light")
        # The door-opener options (cover only).
        self.sensor: str = str(opts.get("state_entity_id") or "") if self.target_domain == "cover" else ""
        self.momentary: bool = self.target_domain == "cover" and _flag(opts.get("momentary"))
        self.device_class: str | None = (str(opts.get("device_class") or "") or None
                                         if self.target_domain == "cover" else None)
        try:
            self.travel_s = float(opts.get("travel_s") or DEFAULT_TRAVEL_S)
        except (TypeError, ValueError):
            self.travel_s = DEFAULT_TRAVEL_S
        #: ("opening" | "closing", the sensor state when it started, monotonic deadline) after a press.
        self._moving: tuple[str, str | None, float] | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._unsub = None

    @property
    def opener(self) -> bool:
        """A cover driven as a door opener (sensor and/or pulse), not a plain switch mirror."""
        return bool(self.sensor or self.momentary)

    @property
    def wrapper(self) -> EntityEntry | None:
        ents = [e for e in self.entities if e.domain == self.target_domain] or self.entities
        return ents[0] if ents else None

    async def async_setup(self) -> None:
        if not self.source:
            raise HubError("switch_as_x entry has no wrapped entity_id")
        if self.target_domain not in STATES:
            raise HubError(f"switch_as_x cannot show a switch as {self.target_domain}")
        if self.sensor and not self.sensor.startswith("binary_sensor."):
            raise HubError(f"switch_as_x: the door sensor must be a binary_sensor, not {self.sensor}")
        self._unsub = self.hub.bus.subscribe(self._on_state_changed, "state_changed")
        # The wrapped switch's driver may not be up yet; mirror whatever is there
        # now (possibly nothing) and follow its events from here on.
        self._refresh()

    async def async_unload(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._cancel_timer()

    def _on_state_changed(self, _event_type: str, ev: dict[str, Any]) -> None:
        eid = ev.get("entity_id")
        if eid == self.source or (self.sensor and eid == self.sensor):
            if self.sensor and eid == self.sensor and self._moving is not None:
                new = ev.get("new_state")
                if new is None or new.state != self._moving[1]:
                    self._stop_moving()  # the door got somewhere: the sensor speaks again
            self._refresh()

    def _refresh(self) -> None:
        if self.opener:
            self._publish_opener()
        else:
            self._mirror(self.hub.states.get(self.source))

    def _mirror(self, src: State | None) -> None:
        ent = self.wrapper
        if ent is None:
            return
        on_state, off_state = STATES[self.target_domain]
        if self.invert:
            on_state, off_state = off_state, on_state
        if src is None:
            state = UNAVAILABLE
        elif src.state == "on":
            state = on_state
        elif src.state == "off":
            state = off_state
        else:
            state = src.state if src.state in (UNAVAILABLE, UNKNOWN) else UNKNOWN
        attrs: dict[str, Any] = {}
        if self.target_domain == "light":
            attrs.update({"supported_color_modes": ["onoff"],
                          "color_mode": "onoff" if state == "on" else None})
        attrs["supported_features"] = FEATURES.get(self.target_domain, 0)
        self.publish(ent, state, attrs)

    # -- door opener -----------------------------------------------------------------
    def _position(self) -> str:
        """``open`` / ``closed`` from the sensor, ``unknown`` without one, ``unavailable`` when it is."""
        if not self.sensor:
            return UNKNOWN
        st = self.hub.states.get(self.sensor)
        if st is None or st.state == UNAVAILABLE:
            return UNAVAILABLE
        if st.state not in ("on", "off"):
            return UNKNOWN
        return "open" if (st.state == "on") != self.invert else "closed"

    def _state(self) -> str:
        switch = self.hub.states.get(self.source)
        if switch is None or switch.state == UNAVAILABLE:
            return UNAVAILABLE
        if self._moving is not None and time.monotonic() < self._moving[2]:
            return self._moving[0]
        self._moving = None
        return self._position()

    def _publish_opener(self) -> None:
        ent = self.wrapper
        if ent is None:
            return
        features = FEATURES["cover"] | (COVER_STOP if self.momentary else 0)
        attrs: dict[str, Any] = {"supported_features": features}
        if self.device_class:
            attrs["device_class"] = self.device_class
        if not self.sensor:
            attrs["assumed_state"] = True
        self.publish(ent, self._state(), attrs)

    def _start_moving(self, direction: str) -> None:
        sensor = self.hub.states.get(self.sensor) if self.sensor else None
        self._moving = (direction, sensor.state if sensor else None, time.monotonic() + self.travel_s)
        self._cancel_timer()
        with contextlib.suppress(RuntimeError):  # no running loop (sync tests): the state ages on the next read
            self._timer = asyncio.get_running_loop().call_later(self.travel_s, self._travel_over)

    def _travel_over(self) -> None:
        self._timer = None
        self._moving = None
        self._publish_opener()

    def _stop_moving(self) -> None:
        self._moving = None
        self._cancel_timer()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    async def _press(self) -> None:
        """One button press: the relay on, then off again unless it let go by itself."""
        await self.hub.call_service("switch", "turn_on", {"entity_id": self.source})
        await asyncio.sleep(PULSE_S)
        cur = self.hub.states.get(self.source)
        if cur is not None and cur.state == "on":
            await self.hub.call_service("switch", "turn_off", {"entity_id": self.source})

    async def _opener_call(self, service: str) -> None:
        current = self._state()
        if current == UNAVAILABLE:
            raise HubError(f"{self.source} is unavailable")
        if service == "toggle":
            service = "close_cover" if current in ("open", "opening") else "open_cover"
            if current == UNKNOWN and self.momentary:  # no idea where it is: just press, as the button would
                await self._press()
                self._publish_opener()
                return
        if service == "stop_cover":
            if not self.momentary:
                raise ServiceNotFound("this cover has no stop (its switch is not a push button)")
            if current in ("opening", "closing"):
                await self._press()
                self._stop_moving()
            self._publish_opener()
            return
        want_open = service == "open_cover"
        if self.momentary:
            already = current in (("open", "opening") if want_open else ("closed", "closing"))
            if already:
                self._publish_opener()
                return
            await self._press()
        else:
            on = want_open  # with a sensor, invert describes the sensor, not the switch
            await self.hub.call_service("switch", "turn_on" if on else "turn_off", {"entity_id": self.source})
        if self.sensor and current != ("open" if want_open else "closed"):
            self._start_moving("opening" if want_open else "closing")
        self._publish_opener()

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if self.opener:
            if service not in ("open_cover", "close_cover", "stop_cover", "toggle"):
                raise ServiceNotFound(f"switch_as_x cannot run {domain}.{service} on {entity.entity_id}")
            await self._opener_call(service)
            return None
        wanted = SERVICES.get(self.target_domain, {})
        if service not in wanted:
            raise ServiceNotFound(f"switch_as_x cannot run {domain}.{service} on {entity.entity_id}")
        want_on = wanted[service]
        # An inverted wrapper turns the switch off to "turn on".
        switch_service = "toggle" if want_on is None else ("turn_on" if want_on != self.invert else "turn_off")
        await self.hub.call_service("switch", switch_service, {"entity_id": self.source})
        # The wrapped driver publishes the switch's new state, which reaches
        # _mirror through the bus; mirror once more in case it was unchanged.
        self._mirror(self.hub.states.get(self.source))
        return None
