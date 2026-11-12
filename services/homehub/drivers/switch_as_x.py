"""switch_as_x: show a switch as another kind of entity (HA's helper of that name).

The entry's options name the wrapped switch (``entity_id``), the domain it is
shown as (``target_domain``) and whether its sense is inverted (``invert``).
Nothing here talks to a device: the state mirrors the wrapped switch through
the hub's ``state_changed`` events, and commands become ``switch.turn_on`` /
``turn_off`` / ``toggle`` calls on it, so whichever driver owns the switch does
the work. A typical one: ``light.smart_plug_kauf_plug`` wrapping
``switch.string_lights_front`` (an ESPHome Kauf plug).
"""
from __future__ import annotations

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


class SwitchAsXDriver(Driver):
    platform = "switch_as_x"
    poll_interval = None  # push: follows the wrapped switch's events

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        opts = {**entry.data, **entry.options}
        self.source: str = str(opts.get("entity_id") or "")
        self.invert: bool = bool(opts.get("invert"))
        self.target_domain: str = str(opts.get("target_domain") or "light")
        self._unsub = None

    @property
    def wrapper(self) -> EntityEntry | None:
        ents = [e for e in self.entities if e.domain == self.target_domain] or self.entities
        return ents[0] if ents else None

    async def async_setup(self) -> None:
        if not self.source:
            raise HubError("switch_as_x entry has no wrapped entity_id")
        if self.target_domain not in STATES:
            raise HubError(f"switch_as_x cannot show a switch as {self.target_domain}")
        self._unsub = self.hub.bus.subscribe(self._on_state_changed, "state_changed")
        # The wrapped switch's driver may not be up yet; mirror whatever is there
        # now (possibly nothing) and follow its events from here on.
        self._mirror(self.hub.states.get(self.source))

    async def async_unload(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _on_state_changed(self, _event_type: str, ev: dict[str, Any]) -> None:
        if ev.get("entity_id") == self.source:
            self._mirror(ev.get("new_state"))

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

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
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
