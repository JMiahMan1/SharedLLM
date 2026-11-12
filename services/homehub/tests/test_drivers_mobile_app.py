"""Phones, people and zones from Jarvis's geo service, and notify.mobile_app_* through Jarvis notices."""
from __future__ import annotations

import pytest

from services.homehub.core import ConfigEntry, EntityEntry, HubError
from services.homehub.drivers import mobile_app as ma
from services.homehub.tests.conftest import make_hub

HOME = {"latitude": 39.8725, "longitude": -105.0257, "radius": 40, "location_name": "Home"}
CHURCH = {"id": "church", "name": "Community Church", "latitude": 39.9225, "longitude": -105.0757, "radius": 37,
          "passive": False, "icon": "mdi:church"}
ACME_HARDWARE = {"id": "robin_work", "name": "Robin Work", "latitude": 39.9725,
                 "longitude": -105.1257, "radius": 46, "passive": True}
PERSONS = [
    {"id": "alex", "name": "Alex", "user_id": None, "device_trackers": ["device_tracker.alexs_s23"]},
    {"id": "sam", "name": "Sam", "user_id": None, "device_trackers": ["device_tracker.sam_s_phone"]},
    {"id": "rivera", "name": "Rivera", "user_id": "u-rivera",
     "device_trackers": ["device_tracker.truck_f250"]},
    {"id": "jordan", "name": "Jordan", "user_id": "u-jordan", "device_trackers": []},
]


def add_phone(hub, entry_id, device_name, tracker, user_id="u-x", battery_uid=None, options=None):
    entry = ConfigEntry(entry_id=entry_id, domain="mobile_app", title=device_name, options=options or {},
                        data={"device_name": device_name, "device_id": entry_id, "user_id": user_id,
                              "webhook_id": "secret"})
    hub.config_entries[entry_id] = entry
    hub.entities[tracker] = EntityEntry(entity_id=tracker, platform="mobile_app", unique_id=entry_id,
                                        config_entry_id=entry_id)
    if battery_uid:
        eid = f"sensor.{tracker.split('.')[1]}_battery_level"
        hub.entities[eid] = EntityEntry(entity_id=eid, platform="mobile_app",
                                        unique_id=f"{battery_uid}_battery_level", config_entry_id=entry_id)
    drv = ma.MobileAppDriver(hub, entry)
    hub.drivers[entry_id] = drv
    return drv


def family_hub():
    hub, _ = make_hub("zone", entities=[{"entity_id": "zone.church", "unique_id": "church", "platform": "zone"}])
    ma.setup_zones(hub, [CHURCH, ACME_HARDWARE], HOME)
    return hub


def fixes_payload(**users):
    feats = []
    for user, (lat, lon, extra) in users.items():
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"entity_id": f"person.{user}", "state": "home", "source_type": "jarvis",
                                     "gps_accuracy": 10, **extra}})
    return {"type": "FeatureCollection", "features": feats, "source": "jarvis"}


@pytest.fixture
def geo(monkeypatch):
    box = {"payload": fixes_payload(), "calls": 0}

    async def fake_fetch(base, secret):
        box["calls"] += 1
        return box["payload"]

    monkeypatch.setattr(ma, "_geo_settings", lambda: ("http://geo:8009", "s3cret"))
    monkeypatch.setattr(ma, "_fetch_geo", fake_fetch)
    return box


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def fake_send(payload, **kw):
        out.append(payload)
        return True

    async def fake_family(**kw):
        return ["alex", "sam", "jordan"]

    monkeypatch.setattr(ma.jarvis_notify, "send", fake_send)
    monkeypatch.setattr(ma.jarvis_notify, "family", fake_family)
    return out


def test_slugify_like_ha():
    assert ma.slugify("Sam's Phone") == "sam_s_phone"
    assert ma.slugify("alexs-phone") == "alexs_phone"
    assert ma.slugify("Truck-F250") == "truck_f250"
    assert ma.slugify("SM-S942U") == "sm_s942u"


def test_zones_and_zone_for():
    hub = family_hub()
    assert hub.states.get("zone.home").state == "0"
    assert hub.states.get("zone.home").attributes["radius"] == 40
    church = hub.states.get("zone.church")
    assert church.attributes["friendly_name"] == "Community Church" and church.attributes["passive"] is False
    assert ma.zone_for(hub, 39.8725, -105.0257) == "home"
    assert ma.zone_for(hub, 39.8728, -105.0257) == "home"  # 33 m out, inside 40 m
    assert ma.zone_for(hub, 39.8735, -105.0257) == "not_home"  # 111 m out
    assert ma.zone_for(hub, 39.8735, -105.0257, accuracy=80) == "home"  # the fix's error reaches in
    assert ma.zone_for(hub, 39.9225, -105.0757) == "Community Church"
    assert ma.zone_for(hub, 39.9725, -105.1257) == "not_home"  # passive zones never hold a tracker


async def test_persons_follow_trackers_and_zones_count_them():
    hub = family_hub()
    await ma.setup_persons(hub, PERSONS)
    assert hub.states.get("person.alex").state == "unknown"
    assert "person.jordan" not in hub.states.entity_ids()  # no trackers and no fix yet
    hub.states.set("device_tracker.alexs_s23", "home",
                   {"source_type": "gps", "latitude": 39.8725, "longitude": -105.0257, "gps_accuracy": 5})
    p = hub.states.get("person.alex")
    assert p.state == "home" and p.attributes["source"] == "device_tracker.alexs_s23"
    assert p.attributes["latitude"] == 39.8725 and p.attributes["device_trackers"] == ["device_tracker.alexs_s23"]
    assert hub.states.get("zone.home").state == "1"
    assert hub.states.get("zone.home").attributes["persons"] == ["person.alex"]
    hub.states.set("device_tracker.alexs_s23", "Community Church", {"source_type": "gps", "latitude": 39.9225,
                                                                   "longitude": -105.0757})
    assert hub.states.get("person.alex").state == "Community Church"
    assert hub.states.get("zone.home").state == "0" and hub.states.get("zone.church").state == "1"


async def test_person_prefers_a_router_home_over_gps():
    hub = family_hub()
    await ma.setup_persons(hub, [{"id": "a", "name": "A", "device_trackers": ["device_tracker.gps",
                                                                            "device_tracker.router"]}])
    hub.states.set("device_tracker.router", "home", {"source_type": "router"})
    hub.states.set("device_tracker.gps", "not_home", {"source_type": "gps", "latitude": 1, "longitude": 2})
    assert hub.states.get("person.a").state == "home"


async def test_jarvis_user_mapping(geo):
    hub = family_hub()
    await ma.setup_persons(hub, PERSONS)
    explicit = add_phone(hub, "e1", "SM-S942U", "device_tracker.sm_s942u", options={"jarvis_user": "Alex"})
    by_tracker = add_phone(hub, "e2", "alexs-phone", "device_tracker.alexs_s23")
    by_user_id = add_phone(hub, "e3", "FireHD", "device_tracker.alex_fire", user_id="u-rivera")
    by_name = add_phone(hub, "e4", "Robins Phone", "device_tracker.robins_phone")
    by_name2 = add_phone(hub, "e5", "Sam's Phone", "device_tracker.sam_s_phone_2")
    nobody = add_phone(hub, "e6", "Truck-F250 Head Unit", "device_tracker.unlisted")
    assert explicit.jarvis_user() == "alex"
    assert by_tracker.jarvis_user() == "alex"
    assert by_user_id.jarvis_user() == "rivera"
    assert by_name.jarvis_user() == "robin"
    assert by_name2.jarvis_user() == "sam"
    assert nobody.jarvis_user() is None
    hub.ha_users = {"u-x": "Alex Rivera"}
    assert nobody.jarvis_user() == "alex"


async def test_poll_publishes_tracker_battery_and_tracker_free_persons(geo):
    hub = family_hub()
    await ma.setup_persons(hub, PERSONS)
    geo["payload"] = fixes_payload(robin=(39.9225, -105.0757, {"battery": 64, "speed": 0.0}),
                                   jordan=(39.8725, -105.0257, {}))
    drv = add_phone(hub, "e4", "Robins Phone", "device_tracker.robins_phone", battery_uid="hook4")
    await drv.async_setup()
    st = hub.states.get("device_tracker.robins_phone")
    assert st.state == "Community Church"
    assert st.attributes["source_type"] == "gps" and st.attributes["battery_level"] == 64
    assert st.attributes["latitude"] == 39.9225 and st.attributes["gps_accuracy"] == 10.0
    bat = hub.states.get("sensor.robins_phone_battery_level")
    assert bat.state == "64" and bat.attributes["device_class"] == "battery"
    jordan = hub.states.get("person.jordan")
    assert jordan.state == "home" and jordan.attributes["source"] == "jarvis"
    # Shared cache: a second phone polling straight after does not hit geo again.
    other = add_phone(hub, "e5", "Sam's Phone", "device_tracker.sam_s_phone_2")
    await other.async_poll()
    assert geo["calls"] == 1


async def test_battery_is_not_guessed_for_a_user_with_two_phones(geo):
    hub = family_hub()
    geo["payload"] = fixes_payload(sam=(39.8725, -105.0257, {"battery": 50}))
    a = add_phone(hub, "m1", "Sam's Phone", "device_tracker.sam_s_phone", battery_uid="h1")
    b = add_phone(hub, "m2", "Sam's Phone", "device_tracker.sam_s_phone_2", battery_uid="h2")
    await a.async_poll()
    await b.async_poll()
    assert hub.states.get("device_tracker.sam_s_phone").state == "home"
    assert hub.states.get("device_tracker.sam_s_phone_2").state == "home"
    assert hub.states.get("sensor.sam_s_phone_battery_level") is None


async def test_geo_echoes_of_ha_states_are_not_fixes(geo):
    hub = family_hub()
    payload = fixes_payload()
    payload["source"] = "home_assistant"
    payload["features"] = [{"geometry": {"coordinates": [-104.4257, 39.5725]},
                            "properties": {"entity_id": "device_tracker.robins_phone", "source_type": "gps"}}]
    geo["payload"] = payload
    drv = add_phone(hub, "e4", "Robins Phone", "device_tracker.robins_phone")
    await drv.async_poll()
    assert hub.states.get("device_tracker.robins_phone") is None


async def test_notify_mobile_app_goes_to_the_jarvis_user(geo, sent):
    hub = family_hub()
    await ma.setup_persons(hub, PERSONS)
    a = add_phone(hub, "j1", "Alexs Phone", "device_tracker.alexs_phone")
    b = add_phone(hub, "j2", "alexs-phone", "device_tracker.alexs_s23")
    await a.async_setup()
    await b.async_setup()
    assert ("notify", "mobile_app_alexs_phone") in hub.services
    res = await hub.call_service("notify", "mobile_app_alexs_phone",
                                 {"title": "Garage", "message": "The garage door is open",
                                  "data": {"tag": "garage", "priority": "high"}})
    assert res["response"] == {"sent": 1}  # two phones, one person: one notice
    assert sent == [{"user": "alex", "category": "home", "kind": "home", "title": "Garage",
                     "body": "The garage door is open", "data": {}, "priority": 4, "tag": "garage"}]
    await hub.call_service("notify", "mobile_app_alexs_phone", {"message": "clear_notification"})
    assert len(sent) == 1
    await b.async_unload()
    assert ("notify", "mobile_app_alexs_phone") in hub.services  # the other phone still has it
    await a.async_unload()
    assert ("notify", "mobile_app_alexs_phone") not in hub.services


async def test_notify_notify_goes_to_the_family_and_entities_send_message(geo, sent):
    hub = family_hub()
    drv = add_phone(hub, "k1", "Robins Phone", "device_tracker.robins_phone")
    hub.entities["notify.robins_phone"] = EntityEntry(entity_id="notify.robins_phone", platform="mobile_app",
                                                      unique_id="k1", config_entry_id="k1")
    await drv.async_setup()
    assert hub.states.get("notify.robins_phone").state == "unknown"
    await hub.call_service("notify", "notify", {"message": "Dinner"})
    assert [p["user"] for p in sent] == ["alex", "sam", "jordan"]
    assert sent[0]["title"] == "Home"
    await hub.call_service("notify", "send_message", {"entity_id": "notify.robins_phone", "message": "Hi"})
    assert sent[-1]["user"] == "robin" and sent[-1]["body"] == "Hi"


async def test_notify_without_a_mapped_user_fails_loudly(geo, sent):
    hub = family_hub()
    drv = add_phone(hub, "t1", "Truck-F250", "device_tracker.truck_f250")
    await drv.async_setup()
    with pytest.raises(HubError):
        await hub.call_service("notify", "mobile_app_truck_f250", {"message": "x"})


def test_parse_people_keeps_only_jarvis_fixes():
    payload = fixes_payload(sam=(39.5725, -104.4257, {"battery": 80}))
    payload["features"].append({"geometry": {"coordinates": [0, 0]},
                                "properties": {"entity_id": "person.sam", "source_type": "gps"}})
    out = ma.parse_people(payload)
    assert out == {"sam": {"latitude": 39.5725, "longitude": -104.4257, "gps_accuracy": 10, "battery_level": 80,
                               "speed": None, "course": None, "last_updated": None, "zone": "home",
                               "activity": None, "battery_state": None, "charger_type": None,
                               "is_charging": None, "next_alarm": None, "next_alarm_ms": None,
                               "next_alarm_package": None}}
