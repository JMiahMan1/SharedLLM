"""Media endpoints for a caller with no hub login of their own (2026-11-17).

Home Assistant is retired and only people imported from it have a stored HA
token, so every other family member got HTTP 400 "Missing Home Assistant
credentials" from the media endpoints, the Android media widget included. They
now go to the hub with its own login (``hardware_router.hub_login``), which can
see every player, so listings are filtered to what the caller may view and
commands are checked against what the caller may control.
"""
import os

import pytest
from fastapi.testclient import TestClient

import services.config as config
import services.execution.entity_access as entity_access
import services.execution.main as exec_main
from services.execution import media_playback_service
from services.execution.entity_access import EntityPermissions
from services.execution.handlers import audiobookshelf, media_status
from services.execution.handlers import media as media_handler
from services.execution.main import app
from services.execution.schemas import ExecutionResult

client = TestClient(app)

HUB_URL = "http://hub.test:8123"
HUB_SECRET = "hub-internal-secret"
NO_LOGIN = {"user": "kid1"}
OWN_LOGIN = {"user": "parent1", "ha_url": "http://own.test:8123", "ha_token": "own-token"}

STATES = [
    {"entity_id": "media_player.kids_room", "state": "idle", "attributes": {"friendly_name": "Kids Room"}},
    {"entity_id": "media_player.office_tv", "state": "playing",
     "attributes": {"friendly_name": "Office TV", "media_title": "News"}},
]


def _headers() -> dict:
    return {"X-Internal-Secret": os.environ["INTERNAL_SECRET"]}


@pytest.fixture
def hub(monkeypatch):
    """A configured hub, kid1 allowed only the kids' room player, and a record of hub reads."""
    monkeypatch.setattr(config, "HA_URL", HUB_URL)
    monkeypatch.setattr(config, "INTERNAL_SECRET", HUB_SECRET)

    async def permissions(username, is_admin=False):
        if is_admin:
            return EntityPermissions.everything()
        return EntityPermissions(assigned=frozenset({"media_player.kids_room"}))

    monkeypatch.setattr(entity_access, "load_entity_permissions", permissions)
    monkeypatch.setattr(exec_main, "load_entity_permissions", permissions)

    reads: list[tuple[str, str]] = []

    async def get_states(ha_url, ha_token, *args, **kwargs):
        reads.append((ha_url, ha_token))
        return STATES

    async def no_playback_state(username):
        return None

    monkeypatch.setattr(media_status.ha_client, "get_states", get_states)
    monkeypatch.setattr(media_playback_service.registry, "get_playback_state", no_playback_state)
    return reads


@pytest.fixture
def sent(monkeypatch):
    """Commands that reached the playback layer, with the login they carried."""
    calls: list[tuple[str, str | None, str | None, str | None]] = []

    def record(kind):
        async def fake(req):
            ctx = req.user_context
            calls.append((kind, req.entity_id, ctx.ha_url, ctx.ha_token))
            return ExecutionResult(status="SUCCESS", message="sent", service=kind)
        return fake

    monkeypatch.setattr(exec_main.MediaPlaybackService, "play", record("media_play"))
    monkeypatch.setattr(exec_main.MediaPlaybackService, "transport", record("media_transport"))
    monkeypatch.setattr(audiobookshelf, "handle_audiobookshelf", record("audiobookshelf"))
    return calls


def _status(user_context: dict):
    return client.post("/execute/media/status", headers=_headers(), json={"user_context": user_context})


def test_a_caller_without_a_login_sees_only_the_players_they_may_view(hub):
    resp = _status(NO_LOGIN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert [p["entity_id"] for p in body["detail"]["all_players"]] == ["media_player.kids_room"]
    assert body["detail"]["active"] is None
    assert hub == [(HUB_URL, HUB_SECRET)]


def test_an_admin_without_a_login_sees_every_player(hub):
    resp = _status({"user": "admin1", "is_admin": True})
    assert resp.status_code == 200
    players = {p["entity_id"] for p in resp.json()["detail"]["all_players"]}
    assert players == {"media_player.kids_room", "media_player.office_tv"}


def test_a_caller_with_their_own_login_reads_the_hub_with_it(hub):
    resp = _status(OWN_LOGIN)
    assert resp.status_code == 200
    assert hub == [(OWN_LOGIN["ha_url"], OWN_LOGIN["ha_token"])]


def test_a_caller_without_a_login_cannot_command_a_player_they_may_not_use(hub, sent):
    transport = client.post("/execute/media/transport", headers=_headers(), json={
        "user_context": NO_LOGIN, "entity_id": "media_player.office_tv", "command": "pause"})
    play = client.post("/execute/media/play", headers=_headers(), json={
        "user_context": NO_LOGIN, "entity_id": "media_player.office_tv", "query": "a song"})
    book = client.post("/execute/audiobookshelf", headers=_headers(), json={
        "user_context": NO_LOGIN, "action": "play", "book_id": "b1", "entity_id": "media_player.office_tv"})
    assert (transport.status_code, play.status_code, book.status_code) == (403, 403, 403)
    assert sent == []


def test_a_caller_without_a_login_commands_their_own_player_with_the_hub_login(hub, sent):
    resp = client.post("/execute/media/transport", headers=_headers(), json={
        "user_context": NO_LOGIN, "entity_id": "media_player.kids_room", "command": "pause"})
    assert resp.status_code == 200
    assert sent == [("media_transport", "media_player.kids_room", HUB_URL, HUB_SECRET)]


def test_a_play_that_names_no_player_is_given_one_the_caller_may_use(hub, sent):
    resp = client.post("/execute/media/play", headers=_headers(), json={
        "user_context": NO_LOGIN, "query": "a song"})
    assert resp.status_code == 200
    # The playing office TV would be the first pick for the whole house.
    assert sent == [("media_play", "media_player.kids_room", HUB_URL, HUB_SECRET)]


def test_a_caller_with_their_own_login_plays_as_before(hub, sent):
    resp = client.post("/execute/media/play", headers=_headers(), json={
        "user_context": OWN_LOGIN, "query": "a song"})
    assert resp.status_code == 200
    assert sent == [("media_play", None, OWN_LOGIN["ha_url"], OWN_LOGIN["ha_token"])]


@pytest.mark.parametrize("path, body", [
    ("/execute/media/status", {}),
    ("/execute/media/play", {"entity_id": "media_player.kids_room", "query": "a song"}),
    ("/execute/media/transport", {"entity_id": "media_player.kids_room", "command": "pause"}),
    ("/execute/audiobookshelf", {"action": "search", "query": "a book"}),
])
def test_no_hub_configured_is_a_clear_503(hub, sent, monkeypatch, path, body):
    monkeypatch.setattr(config, "HA_URL", None)
    resp = client.post(path, headers=_headers(), json={"user_context": NO_LOGIN, **body})
    assert resp.status_code == 503
    assert "No home hub" in resp.json()["detail"]
    assert sent == []


async def test_resolve_entity_chooses_only_among_allowed_players(monkeypatch):
    from services.execution.schemas import MediaPlayRequest, UserContext

    async def get_states(ha_url, ha_token, *args, **kwargs):
        return STATES

    monkeypatch.setattr(media_handler.ha_client, "get_states", get_states)
    req = MediaPlayRequest(user_context=UserContext(user="kid1"), query="a song")
    assert await media_handler.resolve_entity(req, HUB_URL, HUB_SECRET) == "media_player.office_tv"
    picked = await media_handler.resolve_entity(
        req, HUB_URL, HUB_SECRET, may_use=lambda eid: eid == "media_player.kids_room")
    assert picked == "media_player.kids_room"
