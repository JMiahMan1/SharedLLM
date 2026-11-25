"""A volume change is read back from the player and reported as the player reports it.

2026-11-24: "Set volume to 100%" was answered with an apology while the Echo stayed at 90%.
The reply now says the level the player reports after the change, says so when the player
contradicts the request, and keeps an Echo's Music Assistant twin at the same level.
"""
import pytest

from services.execution.handlers import media
from services.execution.schemas import MediaTransportRequest, UserContext

ECHO = "media_player.office_echo"
TWIN = "media_player.office_echo_ma"


def _request(command, **kwargs):
    return MediaTransportRequest(
        user_context=UserContext(user="tester", ha_url="http://ha.test", ha_token="token"),
        entity_id=ECHO, command=command, **kwargs,
    )


@pytest.fixture
def hub(monkeypatch):
    """A fake hub whose players change state the way a real one does when a service is called."""
    states = {
        ECHO: {"entity_id": ECHO, "state": "idle", "attributes": {
            "friendly_name": "Office Echo", "supported_features": 4 | 8 | 1024,
            "volume_level": 0.9, "is_volume_muted": False}},
    }
    calls = []
    apply = {"on": True}

    async def get_state(ha_url, ha_token, entity_id):
        return states.get(entity_id)

    async def call_service(ha_url, ha_token, domain, service, entity_id, data=None):
        calls.append((service, entity_id, data))
        attrs = states.get(entity_id, {}).get("attributes")
        if apply["on"] and attrs is not None:
            level = attrs.get("volume_level")
            if service == "volume_set" and "volume_level" in attrs:
                attrs["volume_level"] = data["volume_level"]
            elif service == "volume_up" and level is not None:
                attrs["volume_level"] = min(1.0, level + 0.1)
            elif service == "volume_down" and level is not None:
                attrs["volume_level"] = max(0.0, level - 0.1)
            elif service == "volume_mute":
                attrs["is_volume_muted"] = data["is_volume_muted"]
        return {"ok": True}

    async def no_twin(ctx, entity_id):
        return entity_id

    monkeypatch.setattr(media.ha_client, "get_state", get_state)
    monkeypatch.setattr(media.ha_client, "call_service", call_service)
    monkeypatch.setattr(media, "resolve_mass_entity", no_twin)
    return {"states": states, "calls": calls, "apply": apply}


async def test_set_to_full_is_confirmed_with_the_level_the_player_reports(hub):
    result = await media.handle_media_transport(_request("volume_set", volume_level=1.0))
    assert result.status == "SUCCESS"
    assert result.message == "Volume is at 100% on Office Echo."
    assert result.detail["volume_level"] == 1.0
    assert result.detail["confirmed"] is True
    assert hub["calls"] == [("volume_set", ECHO, {"volume_level": 1.0})]


async def test_a_player_that_does_not_change_is_reported_truthfully(hub):
    hub["apply"]["on"] = False
    result = await media.handle_media_transport(_request("volume_set", volume_level=1.0))
    assert result.status == "FAILURE"
    assert result.message == "I asked Office Echo for 100%, but it still reports 90%."


async def test_volume_up_reports_the_new_level(hub):
    hub["states"][ECHO]["attributes"]["volume_level"] = 0.5
    result = await media.handle_media_transport(_request("volume_up"))
    assert result.status == "SUCCESS"
    assert result.message == "Volume is at 60% on Office Echo."


async def test_mute_and_unmute_are_read_back(hub):
    muted = await media.handle_media_transport(_request("volume_mute", muted=True))
    assert muted.status == "SUCCESS" and muted.message == "Muted Office Echo."
    unmuted = await media.handle_media_transport(_request("volume_unmute"))
    assert unmuted.status == "SUCCESS"
    assert unmuted.message == "Unmuted Office Echo, volume is at 90%."


async def test_a_player_without_a_volume_is_not_claimed_as_confirmed(hub):
    attrs = hub["states"][ECHO]["attributes"]
    attrs.pop("volume_level")
    attrs.pop("is_volume_muted")
    result = await media.handle_media_transport(_request("volume_set", volume_level=0.4))
    assert result.status == "SUCCESS"
    assert result.detail["confirmed"] is False
    assert "can't confirm" in result.message


async def test_the_music_assistant_twin_is_kept_at_the_same_level(hub, monkeypatch):
    hub["states"][TWIN] = {"entity_id": TWIN, "state": "idle", "attributes": {
        "friendly_name": "Office Echo", "volume_level": 0.3, "mass_player_type": "player"}}

    async def twin(ctx, entity_id):
        return TWIN

    monkeypatch.setattr(media, "resolve_mass_entity", twin)
    result = await media.handle_media_transport(_request("volume_set", volume_level=1.0))
    assert result.status == "SUCCESS"
    assert result.detail["mirrored_to"] == TWIN
    assert hub["calls"] == [
        ("volume_set", ECHO, {"volume_level": 1.0}),
        ("volume_set", TWIN, {"volume_level": 1.0}),
    ]


async def test_a_twin_already_at_the_level_is_left_alone(hub, monkeypatch):
    hub["states"][TWIN] = {"entity_id": TWIN, "state": "idle", "attributes": {"volume_level": 1.0}}

    async def twin(ctx, entity_id):
        return TWIN

    monkeypatch.setattr(media, "resolve_mass_entity", twin)
    result = await media.handle_media_transport(_request("volume_set", volume_level=1.0))
    assert result.detail["mirrored_to"] is None
    assert len(hub["calls"]) == 1
