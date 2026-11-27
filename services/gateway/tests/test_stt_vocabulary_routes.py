"""The household's names for the Whisper servers, in the shape their Home Assistant names client reads.

The Wyoming Whisper server (rhasspy/wyoming-whisper 3.8.1, ``hass_api.HomeAssistant``) opens
``<api>/websocket``, waits for ``auth_required``, sends ``auth``, expects ``auth_ok``, then sends
one command at a time and reads one ``result``. It keeps the names in tiers: areas holding an
exposed entity, then exposed entities of the domains people name (media_player is one), sorted
by entity id, then the other areas, then everything else. These tests drive the route the same
way. Every name is made up.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.gateway import stt_vocabulary_routes as routes

VOCAB = {"names": ["Robin", "Example Band", "Moon Garden"], "rooms": ["Den", "Kitchen"], "devices": ["Den Echo"]}
COMMANDS = ["homeassistant/expose_entity/list", "get_states", "config/entity_registry/get_entries",
            "config/area_registry/list", "config/floor_registry/list"]


@pytest.fixture
def client(monkeypatch):
    routes.reset()

    async def fetch():
        return VOCAB

    async def token_ok(token):
        return token == "a-made-up-key"

    monkeypatch.setattr(routes, "_fetch_vocabulary", fetch)
    monkeypatch.setattr(routes, "_token_ok", token_ok)
    monkeypatch.setattr(routes.client_origin, "is_inside", lambda peer, fwd: True)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _names_as_whisper_reads_them(answers: dict) -> list[str]:
    """The prompt order of wyoming_faster_whisper.hass_api: priority entities by id, then empty areas, then others."""
    exposed = sorted(e for e, v in answers["homeassistant/expose_entity/list"]["exposed_entities"].items()
                     if v.get("conversation"))
    friendly = {s["entity_id"]: s["attributes"]["friendly_name"] for s in answers["get_states"]}
    priority = [friendly[e] for e in exposed if e.split(".")[0] in ("light", "switch", "fan", "media_player",
                                                                    "climate", "scene", "todo")]
    other = [friendly[e] for e in exposed if e not in priority and friendly[e] not in priority]
    used_areas = {answers["config/entity_registry/get_entries"][e]["area_id"] for e in exposed} - {None}
    areas = [a["name"] for a in answers["config/area_registry/list"] if a["area_id"] not in used_areas]
    return priority + areas + other


def test_the_names_reach_whisper_in_the_vocabularys_order():
    answers = routes.ha_answers(VOCAB)
    assert _names_as_whisper_reads_them(answers) == ["Robin", "Example Band", "Moon Garden", "Den", "Kitchen",
                                                     "Den Echo"]


def test_a_long_list_keeps_its_order_past_ten_names():
    names = [f"Made Up Name {n}" for n in range(150)]
    answers = routes.ha_answers({"names": names})
    assert _names_as_whisper_reads_them(answers) == names


def test_the_whisper_server_logs_in_and_reads_every_command(client):
    with client.websocket_connect(routes.PATH) as ws:
        assert ws.receive_json()["type"] == "auth_required"
        ws.send_json({"type": "auth", "access_token": "a-made-up-key"})
        assert ws.receive_json()["type"] == "auth_ok"
        for n, command in enumerate(COMMANDS, start=1):
            ws.send_json({"id": n, "type": command})
            reply = ws.receive_json()
            assert reply["id"] == n and reply["success"] is True
        ws.send_json({"id": 99, "type": "call_service", "domain": "light", "service": "turn_on"})
        refused = ws.receive_json()
        assert refused["success"] is False  # names only: nothing can be changed through here


def test_a_server_without_a_jarvis_key_is_refused(client):
    with client.websocket_connect(routes.PATH) as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": "not-a-key"})
        assert ws.receive_json()["type"] == "auth_invalid"


def test_a_caller_outside_the_home_is_refused(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(routes.client_origin, "is_inside", lambda peer, fwd: False)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(routes.PATH) as ws:
        ws.receive_json()


async def test_the_vocabulary_is_read_from_execution_and_kept_briefly(monkeypatch):
    routes.reset()
    calls = []

    class _Resp:
        def raise_for_status(self):
            return None

        async def json(self):
            return VOCAB

    class _Get:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, **k):
            calls.append(url)
            return _Get()

    monkeypatch.setattr(routes.aiohttp, "ClientSession", _Session)
    assert await routes._fetch_vocabulary() == VOCAB
    assert await routes._fetch_vocabulary() == VOCAB
    assert len(calls) == 1 and calls[0].endswith("/execute/voice/vocabulary")
