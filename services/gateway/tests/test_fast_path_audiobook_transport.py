"""Spoken audiobook controls must reach FastPath as an action plus its numbers.

The old extractor only knew pause/resume/stop/back/next, so phrases such as
"go to chapter 5" fell through to the model (which answered without calling
anything). These checks pin the phrase-to-action map and the payload numbers
that ``extract_media_transport_payload`` hands to the execution service.
"""
import pytest

from services.execution.schemas import MediaTransportRequest, UserContext
from services.gateway.main import (
    extract_media_transport_command,
    extract_media_transport_payload,
    is_plain_media_command,
)

CTX = UserContext(user="tester", ha_url="http://ha.test", ha_token="token")


@pytest.mark.parametrize("query, command, payload", [
    ("skip back 30 seconds", "seek_relative", {"seconds": -30.0}),
    ("go back 30 seconds", "seek_relative", {"seconds": -30.0}),
    ("skip ahead 30 seconds", "seek_relative", {"seconds": 30.0}),
    ("skip forward 30 seconds", "seek_relative", {"seconds": 30.0}),
    ("go forward 45 seconds", "seek_relative", {"seconds": 45.0}),
    ("next chapter", "chapter_next", {}),
    ("previous chapter", "chapter_previous", {}),
    ("go to chapter 5", "chapter_goto", {"chapter": 5}),
    ("play faster", "speed", {"rate": 1.5}),
    ("play slower", "speed", {"rate": 0.5}),
    ("speed 1.5", "speed", {"rate": 1.5}),
    ("set a sleep timer for 30 minutes", "sleep", {"minutes": 30.0}),
    ("sleep timer 20 minutes", "sleep", {"minutes": 20.0}),
    ("stop at the end of the chapter", "sleep", {"end_of_chapter": True}),
])
def test_audiobook_phrases_map_to_actions_and_payload(query, command, payload):
    assert extract_media_transport_command(query) == command
    assert extract_media_transport_payload(query) == payload


@pytest.mark.parametrize("query", [
    "skip back 30 seconds", "next chapter", "go to chapter 5", "speed 1.5",
    "set a sleep timer for 30 minutes", "stop at the end of the chapter",
])
def test_audiobook_phrases_are_plain_media_commands(query):
    assert is_plain_media_command(query)


@pytest.mark.parametrize("query, command", [
    ("pause", "pause"), ("stop", "stop"), ("resume", "resume"),
    ("skip this track", "next"), ("go back", "previous"),
])
def test_existing_transport_phrases_still_work(query, command):
    assert extract_media_transport_command(query) == command
    assert extract_media_transport_payload(query) == {}


@pytest.mark.parametrize("query", [
    "skip back 30 seconds", "go to chapter 5", "speed 1.5",
    "set a sleep timer for 30 minutes", "stop at the end of the chapter",
])
def test_fastpath_payload_is_accepted_by_the_transport_schema(query):
    command = extract_media_transport_command(query)
    payload = extract_media_transport_payload(query)
    request = MediaTransportRequest(
        user_context=CTX, entity_id="media_player.living_room",
        command=command, **payload,
    )
    assert request.command == command


def test_a_kitchen_timer_is_not_an_audiobook_sleep_timer():
    from services.gateway.main import extract_media_transport_command, is_plain_media_command

    assert extract_media_transport_command("set a timer for 10 minutes") != "sleep"
    assert not is_plain_media_command("set a timer for 10 minutes")
    assert extract_media_transport_command("set a sleep timer for 30 minutes") == "sleep"
    assert extract_media_transport_command("stop playing in 20 minutes") == "sleep"


def test_volume_phrases_still_work_with_the_audiobook_commands():
    from services.gateway.main import extract_media_transport_command

    assert extract_media_transport_command("louder") == "volume_up"
    assert extract_media_transport_command("turn it down") == "volume_down"
    assert extract_media_transport_command("next chapter") == "chapter_next"
    assert extract_media_transport_command("pause") == "pause"
