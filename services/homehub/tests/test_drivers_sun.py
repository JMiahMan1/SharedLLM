"""sun.sun and the sun sensors, computed with astral for the hub's location."""
from __future__ import annotations

from datetime import UTC, datetime

from services.homehub.drivers import sun
from services.homehub.tests.conftest import make_hub

PHOENIX = {"latitude": 33.4, "longitude": -111.9, "elevation": 360}


def at(h, m=0):
    # 2026-11-11 in UTC; Phoenix is UTC-7 all year.
    return datetime(2026, 11, 11, h, m, tzinfo=UTC)


def test_midday_is_above_the_horizon_and_falling():
    state, a = sun.sun_state(PHOENIX, at(20))  # 13:00 local
    assert state == "above_horizon"
    assert a["elevation"] > 30 and a["rising"] is False
    assert 150 < a["azimuth"] < 240
    nxt = datetime.fromisoformat(a["next_setting"])
    assert nxt > at(20)
    assert nxt.astimezone(UTC).strftime("%m-%d %H") == "11-12 00"  # this evening, about 17:30 local
    assert datetime.fromisoformat(a["next_rising"]).date().isoformat() == "2026-11-12"
    assert a["friendly_name"] == "Sun"


def test_night_is_below_the_horizon_and_rising_after_midnight():
    state, a = sun.sun_state(PHOENIX, at(10))  # 03:00 local, after solar midnight
    assert state == "below_horizon"
    assert a["elevation"] < -20 and a["rising"] is True
    for key in ("next_dawn", "next_dusk", "next_midnight", "next_noon", "next_rising", "next_setting"):
        assert datetime.fromisoformat(a[key]) > at(10)
    dawn, rising = datetime.fromisoformat(a["next_dawn"]), datetime.fromisoformat(a["next_rising"])
    assert dawn < rising < datetime.fromisoformat(a["next_noon"])
    # Phoenix in November: sunrise is a little after 07:00 local (14:00 UTC).
    assert 13 <= rising.astimezone(UTC).hour <= 14


def test_events_are_strictly_after_now():
    obs = sun.observer_for(PHOENIX)
    rising = sun.next_event(obs, "next_rising", at(10))
    assert sun.next_event(obs, "next_rising", rising) > rising


async def test_driver_publishes_sun_and_sensors():
    eid = "01J5KJ2M16DY694JTWCMPQXM8N"
    hub, entry = make_hub("sun", entities=[
        {"entity_id": "sensor.sun_next_dawn", "unique_id": f"{eid}-next_dawn"},
        {"entity_id": "sensor.sun_next_setting", "unique_id": f"{eid}-next_setting"},
    ], entry_id=eid)
    drv = sun.SunDriver(hub, entry)
    drv._update(at(20))
    st = hub.states.get("sun.sun")
    assert st.state == "above_horizon" and st.attributes["friendly_name"] == "Sun"
    assert hub.states.get("sensor.sun_next_setting").state == st.attributes["next_setting"]
    assert hub.states.get("sensor.sun_next_dawn").attributes["device_class"] == "timestamp"
    drv._update(at(10))
    assert hub.states.get("sun.sun").state == "below_horizon"
    await drv.async_setup()  # now: whatever the real time is, it must not fail
    assert hub.states.get("sun.sun").state in ("above_horizon", "below_horizon")
