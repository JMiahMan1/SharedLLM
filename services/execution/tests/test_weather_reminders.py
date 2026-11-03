"""Weather reminders, their actions and the heads-up (docs/WEATHER.md)."""
import json

import pytest

from services.execution import weather as w

H = 3600
NOW = 1_800_000_000 - (1_800_000_000 % H) + 600  # ten past an hour
OFFSET = -6 * H


def hour(i, **kw):
    """Forecast hour ``i`` hours from the current one."""
    base = {"ts": NOW - 600 + i * H, "temp": 50.0, "precip_prob": 5.0, "precip": 0.0, "snow": 0.0,
            "icon": "clear", "label": "Clear", "gust": 10.0, "code": 0}
    return {**base, **kw}


def forecast(hours, units="imperial"):
    u = {"imperial": {"temp": "°F", "wind": "mph", "precip": "in"},
         "metric": {"temp": "°C", "wind": "km/h", "precip": "mm"}}[units]
    return {"units": {"system": units, **u}, "utc_offset_seconds": OFFSET, "hourly": hours,
            "daily": [], "current": {"temp": hours[0]["temp"] if hours else None}}


def rule(**kw):
    return w.parse_rules(json.dumps([{"id": "hay", "kind": "rain", "title": "Cover the hay", **kw}]))[0]


# --- rules -----------------------------------------------------------------


def test_parse_rules_applies_defaults_and_drops_bad_entries():
    rules = w.parse_rules(json.dumps([
        {"id": "hay", "kind": "rain", "title": "Cover the hay", "people": [" Jeremiah ", ""], "hours": 99},
        {"kind": "freeze"},
        {"kind": "tornado"},
        "nonsense",
        {"id": "hay", "kind": "wind"},  # duplicate id
        {"id": "BAD ID!", "kind": "heat", "threshold": "abc", "location": {"latitude": 95, "longitude": 0}},
    ]))
    assert [r["kind"] for r in rules] == ["rain", "freeze", "heat"]
    hay, freeze, heat = rules
    assert hay["people"] == ["jeremiah"] and hay["hours"] == w.MAX_HOURS and hay["threshold"] == 60
    assert freeze["title"] == "Freeze coming" and freeze["threshold"] == 32 and len(freeze["id"]) == 12
    assert heat["threshold"] == 95 and heat["location"] is None and heat["id"] != "bad id!"
    assert w.parse_rules("not json") == [] and w.parse_rules(None) == []
    assert w.parse_rules(json.dumps([{"kind": "freeze"}]), "metric")[0]["threshold"] == 0


def test_rain_notifies_once_per_episode_and_again_after_a_dry_gap():
    r = rule(note="Tarp is in the barn.")
    wet = forecast([hour(i) for i in range(3)] + [hour(3, precip_prob=70, icon="rain", precip=0.1),
                                                  hour(4, precip_prob=90, icon="rain", precip=0.2)])
    note, state = w.evaluate(r, wet, NOW)
    assert note["category"] == "weather" and note["tag"] == "weather:hay"
    assert note["title"] == "Cover the hay"
    assert note["body"].startswith("Rain likely from ") and "(90% chance, about 0.30 in). Tarp is in the barn." in note["body"]
    assert note["once"] == f"weather:hay:{int(NOW - 600 + 3 * H)}"
    assert state["last_hit_ts"] == NOW - 600 + 4 * H

    # Next round, the same rain (or rain soon after it) sends nothing.
    later = NOW + 2 * H
    again, state2 = w.evaluate(r, wet, later, state)
    assert again is None and state2["last_hit_ts"] == state["last_hit_ts"]
    near = forecast([hour(8, precip_prob=80)])
    assert w.evaluate(r, near, NOW, state)[0] is None  # 4 h after the last wet hour: same episode

    # Rain a day later is a new episode.
    tomorrow = forecast([hour(i) for i in range(30)] + [hour(30, precip_prob=80)])
    fresh, _ = w.evaluate({**r, "hours": 48}, tomorrow, NOW, state)
    assert fresh is not None


def test_a_second_spell_already_in_the_window_gets_its_own_notice():
    """Tonight's freeze and tomorrow night's are two notices, not one."""
    freeze = w.parse_rules(json.dumps([{"id": "pipes", "kind": "freeze", "hours": 48}]))[0]
    hours = [hour(i, temp=20 if i < 4 or 22 <= i < 26 else 45) for i in range(30)]
    first, state = w.evaluate(freeze, forecast(hours), NOW)
    assert first is not None and state["last_hit_ts"] == NOW - 600 + 3 * H  # end of tonight only

    # Still freezing an hour later: same spell, quiet.
    assert w.evaluate(freeze, forecast(hours[1:]), NOW + H, state)[0] is None
    # Next evening, tomorrow's spell is the first one left: a new notice.
    second, _ = w.evaluate(freeze, forecast(hours[20:]), NOW + 20 * H, state)
    assert second is not None and second["once"] == f"weather:pipes:{int(NOW - 600 + 22 * H)}"


def test_thresholds_follow_the_units_they_were_set_in():
    rules = w.parse_rules(json.dumps([
        {"id": "a", "kind": "freeze", "threshold": 32, "units": "imperial"},
        {"id": "b", "kind": "wind", "threshold": 65, "units": "metric"},
        {"id": "c", "kind": "snow", "threshold": 1, "units": "imperial"},
        {"id": "d", "kind": "rain", "threshold": 70, "units": "metric"},
        {"id": "e", "kind": "heat", "threshold": 30},  # no units: taken as the household's
    ]), "metric")
    assert [r["threshold"] for r in rules] == [0, 65, 2.5, 70, 30]
    imperial = w.parse_rules(json.dumps([{"kind": "wind", "threshold": 65, "units": "metric"}]))
    assert imperial[0]["threshold"] == 40.4


def test_snow_is_in_cm_when_metric():
    snow = w.parse_rules(json.dumps([{"kind": "snow"}]), "metric")[0]
    fc = forecast([hour(1, snow=0.6, icon="snow"), hour(2, snow=0.6, icon="snow")], "metric")
    fc["units"]["snow"] = "cm"
    assert "about 1.2 cm" in w.evaluate(snow, fc, NOW)[0]["body"]


def test_dry_forecast_and_disabled_rules_send_nothing_and_keep_state():
    r = rule()
    dry = forecast([hour(i) for i in range(12)])
    assert w.evaluate(r, dry, NOW, {"last_hit_ts": 1}) == (None, {"last_hit_ts": 1})
    wet = forecast([hour(0, precip_prob=95, icon="rain", precip=0.3)])
    assert w.evaluate({**r, "enabled": False}, wet, NOW)[0] is None
    raining = w.evaluate(r, wet, NOW)[0]
    assert raining["body"].startswith("Raining now (95% chance")
    likely = w.evaluate(r, forecast([hour(0, precip_prob=70, icon="cloudy")]), NOW)[0]
    assert likely["body"].startswith("Rain likely now (70% chance")  # a chance, not rain falling


def test_rain_window_respects_hours_and_measurable_rain_counts():
    r = rule(hours=2)
    far = forecast([hour(5, precip_prob=100)])
    assert w.evaluate(r, far, NOW)[0] is None
    drizzle = forecast([hour(1, precip_prob=20, precip=0.05, icon="drizzle")])
    assert w.evaluate(r, drizzle, NOW)[0] is not None


def test_freeze_heat_wind_and_snow_bodies():
    freeze = w.parse_rules(json.dumps([{"id": "pipes", "kind": "freeze", "title": "Drip the taps",
                                        "location": {"name": "Barn", "latitude": 36, "longitude": -86.8}}]))[0]
    cold = forecast([hour(i, temp=40 - i * 3) for i in range(6)])
    note, _ = w.evaluate(freeze, cold, NOW)
    assert note["body"].startswith("Barn: Down to 25°F around ") and note["priority"] == 4

    heat = w.parse_rules(json.dumps([{"kind": "heat"}]))[0]
    assert "Up to 99°F" in w.evaluate(heat, forecast([hour(2, temp=99)]), NOW)[0]["body"]

    wind = w.parse_rules(json.dumps([{"kind": "wind"}]))[0]
    assert "Gusts to 52 mph" in w.evaluate(wind, forecast([hour(1, gust=52)]), NOW)[0]["body"]

    snow = w.parse_rules(json.dumps([{"kind": "snow"}]))[0]
    light = forecast([hour(1, snow=0.1, icon="snow"), hour(2, snow=0.1, icon="snow")])
    assert w.evaluate(snow, light, NOW)[0] is None  # 0.2 in total is under 0.5
    heavy = forecast([hour(1, snow=0.3, icon="snow"), hour(2, snow=0.4, icon="snow")])
    assert "about 0.7 in" in w.evaluate(snow, heavy, NOW)[0]["body"]


def test_heads_up_only_shows_rules_for_that_person():
    rules = w.parse_rules(json.dumps([
        {"id": "hay", "kind": "rain", "title": "Cover the hay", "people": ["jeremiah"]},
        {"id": "all", "kind": "rain", "title": "Bring the cushions in"},
        {"id": "off", "kind": "rain", "title": "Off", "enabled": False},
    ]))
    wet = forecast([hour(2, precip_prob=90)])
    titles = lambda user: [i["title"] for i in w.heads_up(rules, {"home": wet}, user, NOW)]  # noqa: E731
    assert titles("jeremiah") == ["Cover the hay", "Bring the cushions in"]
    assert titles("gracie") == ["Bring the cushions in"]
    assert w.heads_up(rules, {}, "jeremiah", NOW) == []  # no forecast for the place


def test_parse_location():
    assert w.parse_location('{"name": "Barn", "latitude": "36.1", "longitude": -86.7}') == {
        "name": "Barn", "latitude": 36.1, "longitude": -86.7}
    assert w.parse_location("") is None
    assert w.parse_location({"latitude": 0, "longitude": 200}) is None


class FakeRedis:
    def __init__(self):
        self.h: dict[str, dict] = {}
        self.stream: list[dict] = []

    async def xadd(self, name, fields, **kw):
        self.stream.append(fields)
        return f"{len(self.stream)}-0"

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value


@pytest.fixture
def round_(monkeypatch):
    """check_reminders with settings, forecasts, Redis and telemetry faked."""
    from services.common import notify

    sent, redis = [], FakeRedis()
    cfg = {w.SETTING_RULES: json.dumps([{"id": "hay", "kind": "rain", "title": "Cover the hay", "people": ["jeremiah"]},
                                        {"id": "all", "kind": "freeze"}])}
    accept = {"ok": True}

    async def settings():
        return cfg

    async def forecasts(rules, c):
        return {"home": forecast([hour(1, precip_prob=90, temp=30)])}

    async def send_many(users, make, **kw):
        sent.extend(make(u) for u in users)
        return len(users) if accept["ok"] else 0

    async def family(**kw):
        return ["jeremiah", "gracie"]

    async def r():
        return redis

    monkeypatch.setattr(w, "settings", settings)
    monkeypatch.setattr(w, "_forecasts_for", forecasts)
    monkeypatch.setattr(w, "_r", r)
    monkeypatch.setattr(notify, "send_many", send_many)
    monkeypatch.setattr(notify, "family", family)
    return sent, redis, accept


async def test_a_round_sends_to_the_people_named_or_everyone_then_stays_quiet(round_):
    sent, redis, _ = round_
    assert await w.check_reminders(NOW) == 3
    assert [(n["user"], n["title"]) for n in sent] == [
        ("jeremiah", "Cover the hay"), ("jeremiah", "Freeze coming"), ("gracie", "Freeze coming")]
    assert set(redis.h[w._RULE_STATE]) == {"hay", "all"}
    assert await w.check_reminders(NOW + 900) == 0


async def test_a_round_telemetry_refused_is_retried(round_):
    _sent, redis, accept = round_
    accept["ok"] = False
    assert await w.check_reminders(NOW) == 0
    assert w._RULE_STATE not in redis.h
    accept["ok"] = True
    assert await w.check_reminders(NOW + 900) == 3


async def test_each_new_episode_is_published_for_other_services(round_):
    _sent, redis, _ = round_
    await w.check_reminders(NOW)
    events = [(e["type"], json.loads(e["data"])) for e in redis.stream]
    assert [t for t, _ in events] == ["alert.started", "alert.started"]
    hay = events[0][1]
    assert hay["rule"] == "hay" and hay["kind"] == "rain" and hay["people"] == ["jeremiah"]
    assert hay["location"] == {"name": "Home"}
    await w.check_reminders(NOW + 900)
    assert len(redis.stream) == 2  # same episode: nothing new


async def test_a_rule_can_run_actions_without_notifying_anyone(round_, monkeypatch):
    from services.execution import weather_bus

    sent, redis, _ = round_
    ran = []

    async def run_actions(actions, event):
        ran.append((actions, event["rule"]))
        return len(actions)

    monkeypatch.setattr(weather_bus, "run_actions", run_actions)
    cfg = await w.settings()
    cfg[w.SETTING_RULES] = json.dumps([{
        "id": "barn", "kind": "rain", "title": "Close the barn", "notify": False,
        "actions": [{"type": "ha_service", "service": "cover.close_cover", "entity_id": "cover.barn_door"},
                    {"type": "ha_service", "service": "not a service"},
                    {"type": "call", "service": "geo", "path": "/api/hooks/weather"},
                    {"type": "call", "service": "geo", "path": "http://evil.example/"}],
    }])
    assert await w.check_reminders(NOW) == 0
    assert sent == []
    assert ran == [([{"type": "ha_service", "service": "cover.close_cover", "entity_id": "cover.barn_door", "data": {}},
                     {"type": "call", "service": "geo", "path": "/api/hooks/weather", "method": "POST"}], "barn")]
    assert json.loads(redis.stream[0]["data"])["people"] == []
    assert "barn" in redis.h[w._RULE_STATE]

