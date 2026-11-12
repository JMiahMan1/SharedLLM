"""``sun.sun`` and the sun sensors, computed with astral the way HA's ``sun`` integration does.

The location is the hub's (core config: latitude, longitude, elevation). Events
are found like HA's ``get_astral_event_next``: walk UTC dates forward until the
event is after now, with the observer at the configured elevation. The state
is ``above_horizon`` while the sun's elevation is above -0.833 degrees (the
refraction-corrected horizon astral itself uses), and ``rising`` is true while
the next noon comes before the next midnight.

``sun.sun`` drives the "Lights On at Night" and "String Lights" automations,
so it is recomputed every minute: a sunrise or sunset shows within a minute.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from astral import Observer
from astral import sun as astral_sun

from services.homehub.core import Driver

SUN_ENTITY = "sun.sun"
HORIZON_DEG = -0.833

#: HA's sun events, in the order its attributes list them.
EVENTS: dict[str, Callable[[Observer, date], datetime]] = {
    "next_dawn": lambda o, d: astral_sun.dawn(o, d),
    "next_dusk": lambda o, d: astral_sun.dusk(o, d),
    "next_midnight": lambda o, d: astral_sun.midnight(o, d),
    "next_noon": lambda o, d: astral_sun.noon(o, d),
    "next_rising": lambda o, d: astral_sun.sunrise(o, d),
    "next_setting": lambda o, d: astral_sun.sunset(o, d),
}


def observer_for(location: dict[str, Any]) -> Observer:
    return Observer(latitude=float(location.get("latitude") or 0.0),
                    longitude=float(location.get("longitude") or 0.0),
                    elevation=float(location.get("elevation") or 0.0))


def next_event(observer: Observer, event: str, now: datetime) -> datetime | None:
    """The first ``event`` strictly after ``now`` (None where it never happens, near the poles)."""
    fn = EVENTS[event]
    start = now.astimezone(UTC).date()
    for mod in range(-1, 367):
        try:
            when = fn(observer, start + timedelta(days=mod))
        except ValueError:  # the sun never reaches that depression on this date
            continue
        if when > now:
            return when
    return None


def sun_state(location: dict[str, Any], now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    """(state, attributes) of HA's ``sun.sun`` at ``now``."""
    now = now or datetime.now(UTC)
    obs = observer_for(location)
    attrs: dict[str, Any] = {}
    times: dict[str, datetime | None] = {}
    for key in EVENTS:
        times[key] = next_event(obs, key, now)
        attrs[key] = times[key].isoformat() if times[key] else None
    elevation = round(astral_sun.elevation(obs, now), 2)
    attrs["elevation"] = elevation
    attrs["azimuth"] = round(astral_sun.azimuth(obs, now), 2)
    noon, midnight = times["next_noon"], times["next_midnight"]
    attrs["rising"] = bool(noon and midnight and noon < midnight)
    attrs["friendly_name"] = "Sun"
    return ("above_horizon" if elevation > HORIZON_DEG else "below_horizon"), attrs


class SunDriver(Driver):
    platform = "sun"
    poll_interval = 60.0

    async def async_setup(self) -> None:
        self._update()

    async def async_poll(self) -> None:
        self._update()

    def _update(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        state, attrs = sun_state(self.hub.location or {}, now)
        self.publish(SUN_ENTITY, state, attrs)
        for ent in self.entities:
            if ent.domain != "sensor":
                continue
            key = ent.unique_id.rsplit("-", 1)[-1]
            if key in attrs and key.startswith("next_"):
                self.publish(ent, attrs[key] or "unknown", {"device_class": "timestamp"})
            elif key == "solar_elevation":
                self.publish(ent, attrs["elevation"], {"unit_of_measurement": "°", "state_class": "measurement"})
            elif key == "solar_azimuth":
                self.publish(ent, attrs["azimuth"], {"unit_of_measurement": "°", "state_class": "measurement"})
            elif key == "solar_rising":
                self.publish(ent, str(attrs["rising"]), {})  # HA's sensor shows True/False
