"""group: a light group, Home Assistant's "Group" helper of type light.

The entry keeps HA's options shape (``group_type: light``, ``entities``,
``hide_members``, ``all``), so an imported HA light group and one made on the
Devices page (``light_groups.py``) are the same thing. The group is one
``light`` entity (unique id = the entry id) whose members are other lights:

* Commands fan out. ``light.turn_on`` / ``turn_off`` with everything they carry
  (brightness, colour, transition) become one ``light.*`` call on the members,
  so whichever driver owns each bulb does the work. ``toggle`` turns every
  member off while any is on (``all``: while all are on), else on, as HA does.
* The state follows the members through the hub's ``state_changed`` events:
  ``on`` when any member is on (``all``: when every available member is), ``off``
  otherwise, ``unavailable`` only when no member can be reached. Brightness and
  colour temperature are the average of the members that are on; colour modes,
  features and effects are what the members offer together.
* A member that was deleted (or never existed) is skipped, never an error: the
  group goes on with the rest, and ``light_groups.list_groups`` says which ones
  are missing. A member hidden as a duplicate copy still belongs to the group
  and is still switched (hidden only keeps it out of what people browse).
* A group never contains another group (``light_groups`` refuses it), so a
  command cannot loop.
"""
from __future__ import annotations

import contextlib
from collections import Counter
from typing import Any

from services.homehub.core import UNAVAILABLE, UNKNOWN, Driver, EntityEntry, HubError, ServiceNotFound, State

#: The light services a group answers.
SERVICES = ("turn_on", "turn_off", "toggle")
#: HA's colour modes that carry a colour (the rest are on/off, brightness and white).
COLOR_MODES = ("hs", "rgb", "rgbw", "rgbww", "xy")


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def members_of(opts: dict[str, Any]) -> list[str]:
    """The member entity ids an entry's options name, in order, without repeats."""
    raw = opts.get("entities") or []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.split(",")]
    out: list[str] = []
    for eid in raw:
        eid = str(eid or "").strip()
        if eid.startswith("light.") and eid not in out:
            out.append(eid)
    return out


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _color_modes(modes: set[str]) -> list[str]:
    """HA's rule: a light that can do more than on/off does not also list on/off (or plain brightness)."""
    if modes - {"onoff"}:
        modes = modes - {"onoff"}
    if modes - {"onoff", "brightness"}:
        modes = modes - {"brightness"}
    return sorted(modes) or ["onoff"]


def aggregate(members: list[State], *, all_on: bool = False) -> tuple[str, dict[str, Any]]:
    """The group's state and attributes from its members' current states (missing members left out)."""
    live = [s for s in members if s.state not in (UNAVAILABLE, UNKNOWN)]
    if not members or not live:
        return UNAVAILABLE, {}
    on = [s for s in live if s.state == "on"]
    state = "on" if (len(on) == len(live) if all_on else on) else "off"
    attrs: dict[str, Any] = {}
    modes: set[str] = set()
    features = 0
    effects: set[str] = set()
    mins: list[float] = []
    maxs: list[float] = []
    for s in live:
        a = s.attributes
        modes.update(str(m) for m in (a.get("supported_color_modes") or []))
        with contextlib.suppress(TypeError, ValueError):
            features |= int(a.get("supported_features") or 0)
        effects.update(str(e) for e in (a.get("effect_list") or []))
        if isinstance(a.get("min_color_temp_kelvin"), (int, float)):
            mins.append(a["min_color_temp_kelvin"])
        if isinstance(a.get("max_color_temp_kelvin"), (int, float)):
            maxs.append(a["max_color_temp_kelvin"])
    attrs["supported_color_modes"] = _color_modes(modes)
    attrs["supported_features"] = features
    if mins:
        attrs["min_color_temp_kelvin"] = min(mins)
    if maxs:
        attrs["max_color_temp_kelvin"] = max(maxs)
    if effects:
        attrs["effect_list"] = sorted(effects)
    if state == "on":
        bright = _mean([float(s.attributes["brightness"]) for s in on
                        if isinstance(s.attributes.get("brightness"), (int, float))])
        if bright is not None:
            attrs["brightness"] = round(bright)
        mode_counts = Counter(str(s.attributes.get("color_mode")) for s in on if s.attributes.get("color_mode"))
        mode = mode_counts.most_common(1)[0][0] if mode_counts else None
        attrs["color_mode"] = mode or attrs["supported_color_modes"][0]
        kelvin = _mean([float(s.attributes["color_temp_kelvin"]) for s in on
                        if s.attributes.get("color_mode") == "color_temp"
                        and isinstance(s.attributes.get("color_temp_kelvin"), (int, float))])
        if kelvin is not None:
            attrs["color_temp_kelvin"] = round(kelvin)
        colored = [s for s in on if s.attributes.get("color_mode") in COLOR_MODES and s.attributes.get("hs_color")]
        if colored:
            hue = _mean([float(s.attributes["hs_color"][0]) for s in colored])
            sat = _mean([float(s.attributes["hs_color"][1]) for s in colored])
            attrs["hs_color"] = [round(hue or 0, 1), round(sat or 0, 1)]
        shared = {s.attributes.get("effect") for s in on}
        if len(shared) == 1 and next(iter(shared)):
            attrs["effect"] = next(iter(shared))
    else:
        attrs["color_mode"] = None
    return state, attrs


class LightGroupDriver(Driver):
    platform = "group"
    poll_interval = None  # push: follows its members' events

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        opts = {**entry.data, **entry.options}
        self.group_type = str(opts.get("group_type") or "light")
        self.members: list[str] = members_of(opts)
        self.all_on = _flag(opts.get("all"))
        self._unsub = None

    @property
    def group(self) -> EntityEntry | None:
        ents = [e for e in self.entities if e.domain == "light"]
        return ents[0] if ents else None

    async def async_setup(self) -> None:
        if self.group_type != "light":
            raise HubError(f"Only light groups are supported here, not {self.group_type} groups")
        self._unsub = self.hub.bus.subscribe(self._on_event, "state_changed")
        # Members' drivers may not be up yet: publish what is there now and follow from here on.
        self.refresh()

    async def async_unload(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _on_event(self, _event_type: str, ev: dict[str, Any]) -> None:
        if ev.get("entity_id") in self.members:
            self.refresh()

    def present(self) -> list[str]:
        """Members that exist in the hub (deleted ones are skipped)."""
        return [eid for eid in self.members if eid in self.hub.entities and not self.hub.entities[eid].disabled_by]

    def refresh(self) -> None:
        ent = self.group
        if ent is None:
            return
        states = [st for eid in self.present() if (st := self.hub.states.get(eid)) is not None]
        state, attrs = aggregate(states, all_on=self.all_on)
        attrs["entity_id"] = list(self.members)
        self.publish(ent, state, attrs)

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain != "light" or service not in SERVICES:
            raise ServiceNotFound(f"a light group cannot run {domain}.{service} on {entity.entity_id}")
        reachable = [eid for eid in self.present()
                     if (st := self.hub.states.get(eid)) is not None and st.state != UNAVAILABLE]
        if not reachable:
            raise HubError(f"none of the lights in {self.hub.friendly_name(entity)} can be reached")
        if service == "toggle":
            current = self.hub.states.get(entity.entity_id)
            service = "turn_off" if current is not None and current.state == "on" else "turn_on"
        payload = dict(data)
        if service == "turn_off":
            payload = {k: v for k, v in payload.items() if k in ("transition", "flash")}
        await self.hub.call_service("light", service, {**payload, "entity_id": reachable})
        self.refresh()
        return None
