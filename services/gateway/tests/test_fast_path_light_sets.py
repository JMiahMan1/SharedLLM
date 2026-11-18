"""Several lights from one spoken phrase: "turn off the kitchen lights" without the model.

Seven bulbs sharing a name used to tie in the fast path and go to the model,
which switched one bulb or none and said the rest "should be off now". Device
names here are made up (a pantry, a den, a porch), shaped like a real hub's.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.gateway import fast_lights
from services.gateway import main as gateway_main
from services.gateway.intent_engine import IntentEngine

PANTRY = [f"light.wiz_rgbw_{n}" for n in range(1, 8)]


def _entity(eid, name, state="on", area="", **attrs):
    a = {"friendly_name": name, **attrs}
    if area:
        a["area_id"] = area
    return {"entity_id": eid, "state": state, "attributes": a}


DEVICES = [
    *[_entity(eid, f"Pantry Light {n}", area="Pantry") for n, eid in enumerate(PANTRY, 1)],
    _entity("light.den_lamp", "Den Lamp", area="Den"),
    _entity("light.den_reading", "Reading Lamp", area="Den"),
    _entity("light.den_status_led", "Den Speaker Status LED", area="Den"),
    _entity("switch.den_plug", "Den Plug", area="Den"),
    _entity("switch.den_lamp_plug", "Den Floor Lamp Plug", area="Den"),
    _entity("light.porch_left", "Porch Light Left", area="Outside"),
    _entity("light.porch_right", "Porch Light Right", area="Outside"),
    _entity("automation.pantry_lights_timer", "Pantry Lights Timer"),
    _entity("light.hall_1", "Hall Light 1"),
    _entity("light.hall_2", "Hall Light 2"),
    _entity("light.hall_group", "Hallway", entity_id=["light.hall_1", "light.hall_2"]),
]


@pytest.fixture
def engine():
    eng = IntentEngine()  # not loaded: the keyword rules classify, as with no embedding model
    eng.update_entity_cache(DEVICES)
    return eng


@pytest.mark.parametrize("query, intent, expected, label", [
    ("turn off the pantry lights", "turn_off", PANTRY, "pantry lights"),
    ("Turn off the pantry lights.", "turn_off", PANTRY, "pantry lights"),
    ("turn off all the lights in the pantry", "turn_off", PANTRY, "pantry lights"),
    ("turn the pantry lights on", "turn_on", PANTRY, "pantry lights"),
    ("turn on the pantry", "turn_on", PANTRY, "pantry lights"),
    # Not numbered, but one word apart: both porch lights.
    ("turn off the porch lights", "turn_off", ["light.porch_left", "light.porch_right"], "porch lights"),
    # A room: its lights, and a switch only when its name says lamp or light; never a status LED.
    ("turn off the lights in the den", "turn_off",
     ["light.den_lamp", "light.den_reading", "switch.den_lamp_plug"], "den lights"),
])
def test_a_plural_or_room_phrase_names_the_lights_together(engine, query, intent, expected, label):
    found = engine.extract_device_set(query, intent)
    assert found is not None and found.entity_ids == sorted(expected) and found.label == label
    assert found.group is None


def test_a_group_holding_them_is_used(engine):
    found = engine.extract_device_set("turn off the hall lights", "turn_off")
    assert found.group == "light.hall_group" and found.entity_ids == ["light.hall_group"]
    assert found.label == "Hallway"
    # Named itself, a group resolves like any device.
    assert engine.extract_entity("turn on the hallway", "turn_on") == "light.hall_group"


@pytest.mark.parametrize("query", [
    "turn off the pantry light",     # one light said, seven of that name: the model asks which
    "turn off the lights",           # which lights? the model asks
    "turn off the attic lights",     # nothing of that name or room
    "turn off the den lamp switch",  # a device's own name, not a room
    "turn off the music",            # not a room
])
def test_one_device_or_none_is_never_a_set(engine, query):
    assert engine.extract_device_set(query, "turn_off") is None


def test_the_bulbs_tie_as_one_device(engine):
    """What used to send "turn off the pantry lights" to the model."""
    assert engine.extract_entity("turn off the pantry lights", "turn_off") is None


def test_a_plural_phrase_wins_over_one_close_device(engine):
    """"the porch lights" scored Porch Light Right a hair ahead of Left; it means both."""
    single = engine.extract_entity("turn off the porch lights", "turn_off")
    chosen = fast_lights.choose(engine, "turn off the porch lights", "turn_off", single, DEVICES)
    assert chosen.entity_ids == ["light.porch_left", "light.porch_right"]
    # One device of that name: itself, not a set.
    assert fast_lights.choose(engine, "turn on the den lamp", "turn_on", "light.den_lamp", DEVICES) is None


def test_only_lights_this_person_sees_are_switched(engine):
    seen = [d for d in DEVICES if d["entity_id"] not in PANTRY[5:]]
    found = fast_lights.light_set_for(engine, "turn off the pantry lights", "turn_off", seen)
    assert found.entity_ids == PANTRY[:5] and found.label == "pantry lights"
    assert fast_lights.light_set_for(engine, "turn off the pantry lights", "turn_off", []) is None


def test_a_group_switches_as_its_members_only_when_all_are_seen(engine):
    found = fast_lights.group_members("light.hall_group", DEVICES)
    assert found.entity_ids == ["light.hall_1", "light.hall_2"] and found.label == "Hallway"
    without = [d for d in DEVICES if d["entity_id"] != "light.hall_2"]
    assert fast_lights.group_members("light.hall_group", without) is None
    assert fast_lights.group_members("light.den_lamp", DEVICES) is None


async def test_switch_says_what_execution_saw():
    sent = []

    async def post(path, payload):
        sent.append((path, payload))
        return {"status": "PARTIAL", "message": "6 of 7 pantry lights are off; Pantry Light 4 isn't responding."}
    out = await fast_lights.switch(fast_lights.LightSet(PANTRY, "pantry lights"), "turn_off", {"user": "sam"}, post)
    assert out == "6 of 7 pantry lights are off; Pantry Light 4 isn't responding."
    path, payload = sent[0]
    assert path == "/execute/lights" and payload["entity_ids"] == PANTRY and payload["action"] == "turn_off"
    assert payload["label"] == "pantry lights" and payload["user_context"] == {"user": "sam"}


async def test_switch_claims_nothing_when_execution_does_not_answer():
    async def down(path, payload):
        raise TimeoutError()
    out = await fast_lights.switch(fast_lights.LightSet(PANTRY, "pantry lights"), "turn_off", {}, down)
    assert out == "I couldn't reach the pantry lights just now, so I don't know if they changed."


# -- the chat route: the whole turn, no model ------------------------------------------------

class _Ctx:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *a):
        return False


class _Either(_Ctx):
    """A response used either way the gateway uses its client: awaited, or ``async with``."""

    def __await__(self):
        async def value():
            return self.resp
        return value().__await__()


def _resp(status, body):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=body)
    m.text = AsyncMock(return_value=json.dumps(body))
    m.release = AsyncMock()
    m.raise_for_status = MagicMock()
    return m


def test_turn_off_the_pantry_lights_by_voice_switches_all_seven_without_the_model(monkeypatch, engine):
    executed = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, json=None, **kw):
            if "/execute/" not in url:
                return _Either(_resp(200, {}))  # the request log, and other housekeeping
            executed.append((url, json))
            return _Either(_resp(200, {"status": "SUCCESS", "message": "The pantry lights are off."}))

        get = post

    def http_post(url, **kw):
        if url.endswith("/hub/conversation/triggers"):
            return _Ctx(_resp(200, {"matched": False}))
        raise AssertionError(f"the model or anything else must not be asked: {url}")

    sess = MagicMock()
    sess.post = MagicMock(side_effect=http_post)
    sess.get = MagicMock(side_effect=lambda url, **kw: _Ctx(_resp(200, {})))
    history: list = []

    async def update_history(user, role, text):
        history.append((role, text))

    monkeypatch.setattr(gateway_main, "engine", engine)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())
    monkeypatch.setattr(gateway_main, "fetch_ha_entities", AsyncMock(return_value=DEVICES))
    monkeypatch.setattr(gateway_main, "fetch_global_setting", AsyncMock(return_value="0.85"))
    monkeypatch.setattr(gateway_main, "get_last_used_device", lambda user: None)
    monkeypatch.setattr(gateway_main, "update_history", update_history)
    monkeypatch.setattr(gateway_main, "_recognised_speaker", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value={"user": "sam"}))
    r = TestClient(gateway_main.app).post("/api/chat", json={"query": "Turn off the pantry lights.",
                                                              "model": "any-model", "stream": False},
                                          headers={"Authorization": "Bearer made-up-key"})
    assert r.status_code == 200, r.text
    assert r.json()["message"]["content"] == "The pantry lights are off."
    assert [u.rsplit("/", 2)[-2:] for u, _ in executed] == [["execute", "lights"]]
    assert executed[0][1]["entity_ids"] == PANTRY
    assert history[-1] == ("assistant", "The pantry lights are off.")
