""""Play X on YouTube" from a voice satellite, and an execution answer that is not a result.

On 2026-11-24 "Play <song> on YouTube" at a satellite took "YouTube" for the device, execution
answered 403 with a string ``detail``, and the gateway crashed reading it (a 500, no answer).
Every player, area and title here is made up.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest

from services.gateway import fast_media
from services.gateway import main as gateway_main
from services.gateway.tests import test_fast_media as tfm
from services.gateway.tests.test_fast_media import SAT_SPEAKER, TV, VOICE, _ask


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    from services.gateway import cache

    monkeypatch.setattr(cache, "_redis", fakeredis.FakeRedis(decode_responses=True))


# --------------------------------------------------------------------------- the words


@pytest.mark.parametrize("query, expected", [
    ("Play Moon Garden on YouTube.", ("Moon Garden on YouTube", None)),
    ("play moon garden on youtube on the Den TV", ("moon garden on YouTube", "Den TV")),
    ("Play Moon Garden on the Den TV", ("Moon Garden", "Den TV")),
    ("Play Moon Garden", ("Moon Garden", None)),
])
def test_youtube_is_where_to_look_not_a_device(query, expected):
    assert gateway_main.extract_media_request(query) == expected


@pytest.mark.parametrize("query, last", [
    ("Play moon garden.  Play moon garden on YouTube.", "Play moon garden on YouTube."),
    ("Play moon garden. Play it on YouTube!", "Play it on YouTube!"),
    ("Play moon garden on YouTube.", "Play moon garden on YouTube."),
    ("Mr. Fake. Play his song", "Mr. Fake. Play his song"),  # not every sentence is a play
])
def test_a_repeated_request_is_read_as_its_last_sentence(query, last):
    assert fast_media.last_play_request(query) == last


# --------------------------------------------------------------------------- where it plays


def test_youtube_at_a_satellite_goes_to_the_satellites_own_speaker(monkeypatch):
    _said, executed = _ask(monkeypatch, "Play Moon Garden on YouTube.", "play_media", voice=VOICE, cache=TV)
    [(path, payload)] = executed
    assert path == "/media/play"
    assert payload["entity_id"] == SAT_SPEAKER and payload["device_name"] != "YouTube"
    assert payload["query"] == "Moon Garden on YouTube" and payload["media_type"] == "video"


def test_a_repeated_youtube_request_searches_one_title(monkeypatch):
    _said, executed = _ask(monkeypatch, "Play moon garden.  Play moon garden on YouTube.", "play_media",
                           voice=VOICE, cache=TV)
    [(_path, payload)] = executed
    assert payload["query"] == "moon garden on YouTube"


def test_youtube_on_a_named_tv_plays_on_that_tv(monkeypatch):
    _said, executed = _ask(monkeypatch, "Play Moon Garden on YouTube on the Den TV.", "play_media", voice=VOICE,
                           cache=SAT_SPEAKER)
    [(_path, payload)] = executed
    assert payload["entity_id"] == TV and payload["query"] == "Moon Garden on YouTube"


def test_the_tv_named_in_answer_to_which_tv_is_where_it_plays(monkeypatch):
    fast_media.remember_choice("sam", {
        "query": "the video Moon Garden on YouTube", "entity_id": SAT_SPEAKER,
        "options": [{"label": "Den TV Chrome", "words": ["den tv chrome", "den"], "entity_id": TV},
                    {"label": "Kitchen Display", "words": ["kitchen display", "kitchen"],
                     "entity_id": "media_player.kitchen_display"}]})
    said, executed = _ask(monkeypatch, "The den one.", "unknown", voice=VOICE, cache=TV, reply="Playing it.")
    assert said == "Playing it."
    [(path, payload)] = executed
    assert path == "/media/play" and payload["entity_id"] == TV
    assert payload["query"] == "the video Moon Garden on YouTube" and payload.get("pick") is None
    assert fast_media.pending_choice("sam") is None


# --------------------------------------------------------------------------- answers that are not results


def _answering(monkeypatch, status, body):
    """Make execution answer ``status`` with ``body`` (any JSON, or text) to every /execute/ call."""
    plain = tfm._resp

    def resp(st, b):
        if isinstance(b, dict) and b.get("status") == "SUCCESS":  # what _ask sends back for /execute/
            m = MagicMock()
            m.status = status
            if isinstance(body, Exception):
                m.json = AsyncMock(side_effect=body)
                m.text = AsyncMock(return_value="<html>Internal Server Error</html>")
            else:
                m.json = AsyncMock(return_value=body)
                m.text = AsyncMock(return_value=json.dumps(body))
            return m
        return plain(st, b)

    monkeypatch.setattr(tfm, "_resp", resp)


@pytest.mark.parametrize("status, body, said", [
    (403, {"detail": "Access denied to this device"}, gateway_main.EXECUTION_DENIED_REPLY),
    (422, {"detail": [{"loc": ["body", "user_context"], "msg": "field required"}]}, gateway_main.EXECUTION_TROUBLE_REPLY),
    (200, "a bare string", gateway_main.EXECUTION_TROUBLE_REPLY),
    (200, ["a", "list"], gateway_main.EXECUTION_TROUBLE_REPLY),
    (500, ValueError("not JSON"), gateway_main.EXECUTION_TROUBLE_REPLY),
])
def test_an_execution_answer_that_is_not_a_result_is_said_plainly(monkeypatch, caplog, status, body, said):
    _answering(monkeypatch, status, body)
    caplog.set_level("WARNING", logger="gateway")
    answer, executed = _ask(monkeypatch, "Play Moon Garden on YouTube.", "play_media", voice=VOICE, cache=TV)
    assert executed and answer == said  # _ask asserts the HTTP status is 200
    assert any("is not a result" in r.getMessage() for r in caplog.records)


def test_a_result_whose_detail_is_a_string_does_not_crash(monkeypatch):
    _answering(monkeypatch, 200, {"status": "FAILURE", "message": "Nothing to play.", "detail": "odd"})
    answer, _executed = _ask(monkeypatch, "Play Moon Garden on YouTube.", "play_media", voice=VOICE, cache=TV)
    assert answer == "Nothing to play."


@pytest.mark.asyncio
async def test_read_execution_reply_passes_a_result_through():
    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"status": "SUCCESS", "message": "Playing.", "detail": {"route": "x"}})
    assert await gateway_main.read_execution_reply(resp, "/execute/media/play") == {
        "status": "SUCCESS", "message": "Playing.", "detail": {"route": "x"}}
