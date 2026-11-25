""""Hit play on the study TV" is a resume on that TV, not a library search for a song named after it.

A real turn reached execution as ``MediaPlayRequest(query="office tv", media_type="music",
device_name="Office TV")``: the model searched the music library for the device's name. A play
whose query is empty or only names the target device now becomes a transport ``resume`` there.
"""
import os

import pytest
from fastapi.testclient import TestClient

import services.config as config
import services.execution.entity_access as entity_access
import services.execution.main as exec_main
from services.execution import ha_client, ha_health
from services.execution.entity_access import EntityPermissions
from services.execution.handlers import media as media_handler
from services.execution.main import app
from services.execution.schemas import ExecutionResult, MediaPlayRequest, UserContext

client = TestClient(app)
HUB_URL = "http://hub.test:8123"
OWN_LOGIN = {"user": "parent1", "ha_url": "http://own.test:8123", "ha_token": "own-token"}
STATES = [
    {"entity_id": "media_player.study_tv", "state": "paused", "attributes": {"friendly_name": "Study TV"}},
    {"entity_id": "media_player.den_speaker", "state": "idle", "attributes": {"friendly_name": "Den speaker"}},
]


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", HUB_URL)

    async def permissions(username, is_admin=False):
        return EntityPermissions.everything()

    monkeypatch.setattr(entity_access, "load_entity_permissions", permissions)
    monkeypatch.setattr(exec_main, "load_entity_permissions", permissions)
    monkeypatch.setattr(ha_health, "is_down", lambda url: False)

    async def get_states(ha_url, ha_token, *args, **kwargs):
        return STATES

    monkeypatch.setattr(ha_client, "get_states", get_states)
    calls: list[tuple] = []

    def record(kind):
        async def fake(req):
            calls.append((kind, req.entity_id, req.device_name, getattr(req, "command", None),
                          getattr(req, "query", None)))
            return ExecutionResult(status="SUCCESS", message="sent", service=kind)
        return fake

    monkeypatch.setattr(exec_main.MediaPlaybackService, "play", record("media_play"))
    monkeypatch.setattr(exec_main.MediaPlaybackService, "transport", record("media_transport"))
    return calls


def _play(**body):
    return client.post("/execute/media/play", headers={"X-Internal-Secret": os.environ["INTERNAL_SECRET"]},
                       json={"user_context": OWN_LOGIN, **body})


def test_a_play_whose_query_is_the_device_name_resumes_that_device(sent):
    resp = _play(query="study tv", media_type="music", device_name="Study TV")
    assert resp.status_code == 200
    assert sent == [("media_transport", None, "Study TV", "resume", None)]


def test_a_play_with_no_query_on_a_device_resumes_it(sent):
    resp = _play(device_name="Study TV")
    assert resp.status_code == 200
    assert sent == [("media_transport", None, "Study TV", "resume", None)]


def test_a_play_whose_query_names_a_known_player_resumes_it(sent):
    resp = _play(query="the Study TV", media_type="music")
    assert resp.status_code == 200
    assert sent == [("media_transport", None, "the Study TV", "resume", None)]


def test_a_real_search_still_plays(sent):
    resp = _play(query="Miles Davis", media_type="music", device_name="Study TV")
    assert resp.status_code == 200
    assert sent == [("media_play", None, "Study TV", None, "Miles Davis")]


def test_a_song_that_contains_the_device_name_is_still_a_search(sent):
    resp = _play(query="study tv blues", device_name="Study TV")
    assert resp.status_code == 200
    assert sent[0][0] == "media_play"


@pytest.mark.asyncio
async def test_play_means_resume_needs_a_target(monkeypatch):
    monkeypatch.setattr(ha_health, "is_down", lambda url: False)

    async def get_states(ha_url, ha_token, *args, **kwargs):
        return STATES

    monkeypatch.setattr(ha_client, "get_states", get_states)
    ctx = UserContext(user="tester", ha_url=HUB_URL, ha_token="tok")
    # No query and no device: nothing to resume on, left to the play path.
    assert await media_handler.play_means_resume(MediaPlayRequest(user_context=ctx)) is None
    assert await media_handler.play_means_resume(MediaPlayRequest(user_context=ctx, query="den speaker")) == "den speaker"
    # A direct media id is never a resume.
    assert await media_handler.play_means_resume(MediaPlayRequest(
        user_context=ctx, device_name="Study TV", media_content_id="http://example.test/a.mp3")) is None
    # A pinned entity whose own name is the query resumes on that entity.
    assert await media_handler.play_means_resume(MediaPlayRequest(
        user_context=ctx, entity_id="media_player.study_tv", query="study tv")) == ""
