"""Home notices from Home Assistant state changes, for admin-mapped entities only."""
from datetime import UTC, datetime

import pytz

from services.execution import home_alerts as ha

TZ = pytz.timezone("America/Phoenix")
T0 = TZ.localize(datetime(2026, 10, 7, 13, 0)).timestamp()


def iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat()


def st(eid, state, changed, name=None):
    return {eid: {"entity_id": eid, "state": state, "last_changed": iso(changed),
                  "attributes": {"friendly_name": name or eid}}}


def entry(eid, event, **kw):
    return ha.parse_mapping(f'[{{"entity_id": "{eid}", "event": "{event}"}}]')[0] | kw


def run(w, entries, states, now, away=None):
    return w.check(entries, states, now, away, TZ)


def test_the_mapping_keeps_only_known_events_on_real_entities():
    raw = ('[{"entity_id": "binary_sensor.door", "event": "doorbell", "people": ["Michele", " "]},'
           ' {"entity_id": "nodot", "event": "doorbell"}, {"entity_id": "lock.x", "event": "bogus"}, "junk"]')
    (only,) = ha.parse_mapping(raw)
    assert only["entity_id"] == "binary_sensor.door" and only["people"] == ["michele"]
    assert ha.parse_mapping("not json") == []
    assert ha.parse_mapping(None) == []


def test_doorbell_rings_on_a_press_even_one_shorter_than_a_round():
    w, e = ha.Watcher(), [entry("binary_sensor.door", "doorbell")]
    assert run(w, e, st("binary_sensor.door", "off", T0 - 600, "Front door"), T0) == []  # first sight
    ((_, note),) = run(w, e, st("binary_sensor.door", "on", T0 + 5, "Front door"), T0 + 15)
    assert note["title"] == "Someone is at the Front door" and note["category"] == "home"
    assert note["tag"] == "home:binary_sensor.door" and note["kind"] == "doorbell"
    assert run(w, e, st("binary_sensor.door", "off", T0 + 8), T0 + 30) == []  # the release
    # pressed and released between two rounds
    assert len(run(w, e, st("binary_sensor.door", "off", T0 + 40), T0 + 45)) == 1


def test_an_event_entity_doorbell_fires_on_each_new_timestamp():
    w, e = ha.Watcher(), [entry("event.ding", "doorbell")]
    run(w, e, st("event.ding", iso(T0 - 600), T0 - 600), T0)
    assert len(run(w, e, st("event.ding", iso(T0 + 3), T0 + 3), T0 + 15)) == 1


def test_motion_only_counts_when_nobody_is_home_and_is_rate_limited():
    w, e = ha.Watcher(), [entry("binary_sensor.hall", "motion_away")]
    run(w, e, st("binary_sensor.hall", "off", T0 - 60), T0)
    assert run(w, e, st("binary_sensor.hall", "on", T0 + 1), T0 + 15, away=False) == []
    run(w, e, st("binary_sensor.hall", "off", T0 + 20), T0 + 30, away=True)
    ((_, a),) = run(w, e, st("binary_sensor.hall", "on", T0 + 40), T0 + 45, away=True)
    run(w, e, st("binary_sensor.hall", "off", T0 + 50), T0 + 60, away=True)
    ((_, b),) = run(w, e, st("binary_sensor.hall", "on", T0 + 70), T0 + 75, away=True)
    assert a["once"] == b["once"]  # one notice per sensor per quiet window
    assert run(w, e, st("binary_sensor.hall", "on", T0 + 90), T0 + 95, away=None) == []  # presence unknown


def test_garage_open_longer_than_its_minutes():
    w = ha.Watcher()
    e = [entry("cover.garage", "garage_open", minutes=10)]
    assert run(w, e, st("cover.garage", "open", T0), T0 + 9 * 60) == []
    ((_, note),) = run(w, e, st("cover.garage", "open", T0, "Garage door"), T0 + 11 * 60)
    assert note["title"] == "Garage door is still open" and note["body"].startswith("Open for 11 min")
    assert note["once"] == f"home:garage_open:cover.garage:{iso(T0)}"  # one per opening
    assert run(w, [entry("cover.garage", "garage_open")], st("cover.garage", "closed", T0), T0 + 3600) == []
    assert ha.DEFAULT_MINUTES["garage_open"] == 15


def test_a_door_unlocked_at_night_once_per_night():
    w, e = ha.Watcher(), [entry("lock.back", "door_unlocked_night")]
    evening = TZ.localize(datetime(2026, 10, 7, 21, 0)).timestamp()
    assert run(w, e, st("lock.back", "unlocked", evening), evening + 30 * 60) == []  # 9:30 pm is not night
    late = TZ.localize(datetime(2026, 10, 7, 23, 0)).timestamp()
    ((_, a),) = run(w, e, st("lock.back", "unlocked", evening), late)
    small_hours = TZ.localize(datetime(2026, 10, 8, 2, 0)).timestamp()
    ((_, b),) = run(w, e, st("lock.back", "unlocked", evening), small_hours)
    assert a["once"] == b["once"] and "2026-10-07" in a["once"]
    assert run(w, e, st("lock.back", "unlocked", late - 60), late) == []  # just unlocked: give it a few minutes
    assert run(w, e, st("lock.back", "locked", evening), late) == []


def test_a_water_leak_is_urgent_and_sent_on_first_sight():
    w, e = ha.Watcher(), [entry("binary_sensor.sink_leak", "water_leak")]
    ((_, note),) = run(w, e, st("binary_sensor.sink_leak", "on", T0, "Sink"), T0 + 5)
    assert note["priority"] == 5 and note["title"] == "Water leak: Sink"
    assert run(w, e, st("binary_sensor.sink_leak", "on", T0, "Sink"), T0 + 20) == []  # same leak
    assert run(w, e, st("binary_sensor.sink_leak", "unavailable", T0 + 25), T0 + 30) == []


def test_washer_done_after_power_stays_low_and_pauses_do_not_count():
    w = ha.Watcher()
    e = [entry("sensor.washer_power", "appliance_done")]

    def at(minute, watts):
        return run(w, e, st("sensor.washer_power", str(watts), T0 + minute * 60, "Washer"), T0 + minute * 60)

    assert at(0, 0.5) == []  # idle at first sight
    assert at(1, 350) == []  # starts
    assert at(20, 2) == []  # soak pause...
    assert at(23, 400) == []  # ...and back on
    assert at(50, 1) == []  # power drops
    assert at(53, 1) == []  # not low long enough yet
    ((_, note),) = at(56, 1)
    assert note["title"] == "Washer is done" and note["body"] == "Ran for 49 min."
    assert at(60, 1) == []


def test_a_short_blip_is_not_a_cycle_and_named_states_work():
    w = ha.Watcher()
    e = [entry("sensor.dryer", "appliance_done", watts=50)]
    run(w, e, st("sensor.dryer", "60", T0), T0)
    run(w, e, st("sensor.dryer", "5", T0 + 60), T0 + 60)
    assert run(w, e, st("sensor.dryer", "5", T0 + 60), T0 + 7 * 60) == []
    assert ha._running("drying", 10) and not ha._running("idle", 10)


async def test_a_round_sends_to_the_listed_people_or_everyone(monkeypatch):
    import services.execution.handlers.calendar as cal
    import services.execution.main as _main
    from services.common import notify
    from services.execution import ha_client

    sent = []
    mapping = [{"entity_id": "binary_sensor.sink_leak", "event": "water_leak", "people": []},
               {"entity_id": "binary_sensor.door", "event": "doorbell", "people": ["michele"]}]

    async def fake_entries():
        return ha.parse_mapping(__import__("json").dumps(mapping))

    async def fake_creds():
        return {"ha_url": "http://ha", "ha_token": "t"}

    async def fake_states(url, token, account=""):
        assert account == "the system account"
        return list(st("binary_sensor.sink_leak", "on", T0).values()) + list(st("binary_sensor.door", "off", T0).values())

    async def fake_family(**_):
        return ["jeremiah", "michele"]

    async def fake_send(payload, **_):
        sent.append(payload)
        return True

    monkeypatch.setattr(ha, "_entries", fake_entries)
    monkeypatch.setattr(_main, "resolve_first_user", fake_creds)
    monkeypatch.setattr(ha_client, "get_all_states", fake_states)
    monkeypatch.setattr(notify, "family", fake_family)
    monkeypatch.setattr(notify, "send", fake_send)
    monkeypatch.setattr(cal, "_get_local_tz", lambda: TZ)

    assert await ha.check_once(ha.Watcher()) == 2
    assert [(p["user"], p["kind"]) for p in sent] == [("jeremiah", "water_leak"), ("michele", "water_leak")]


async def test_no_mapping_means_no_home_assistant_call(monkeypatch):
    from services.execution import ha_client

    async def empty():
        return []

    async def boom(*_):
        raise AssertionError("polled Home Assistant with nothing mapped")

    monkeypatch.setattr(ha, "_entries", empty)
    monkeypatch.setattr(ha_client, "get_all_states", boom)
    assert await ha.check_once(ha.Watcher()) == 0


async def test_nobody_home_reads_the_presence_tracker(monkeypatch):
    from services.execution import presence

    class Tracker:
        def __init__(self, data):
            self.data = data

        async def get_all_presence(self):
            return self.data

    for data, want in [({}, None), ({"a": {"room": "not_home"}}, True),
                       ({"a": {"room": "not_home"}, "b": {"room": "kitchen"}}, False)]:
        monkeypatch.setattr(presence, "get_presence_tracker", lambda d=data: Tracker(d))
        assert await ha.nobody_home() is want
