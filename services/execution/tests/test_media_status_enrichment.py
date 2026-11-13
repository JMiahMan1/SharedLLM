"""P2-T24 section 7.1: /execute/media/status must enrich every player with
position timestamp, content id, shuffle/repeat, group members, app name, an
icon kind ("tv" vs "speaker") and the Music Assistant player id."""
from services.execution.handlers import media_status
from services.execution.schemas import MediaStatusRequest, UserContext

TV_STATE = {
    "entity_id": "media_player.office_tv",
    "state": "playing",
    "attributes": {
        "friendly_name": "Office TV",
        "app_id": "com.google.android.youtube.tv",
        "device_class": "tv",
        "media_title": "A Video",
        "media_position_updated_at": "2026-10-05T12:00:00+00:00",
        "media_content_id": "abc123",
        "shuffle": True,
        "repeat": "all",
        "group_members": ["media_player.kitchen"],
        "app_name": "YouTube",
        "supported_features": 0,
    },
}

MA_STATE = {
    "entity_id": "media_player.mass_kitchen",
    "state": "idle",
    "attributes": {
        "friendly_name": "Kitchen",
        "integration": "music_assistant",
        "active_queue": "kitchen",
        "mass_player_type": "player",
        "supported_features": 0,
    },
}


def _request():
    return MediaStatusRequest(
        user_context=UserContext(user="tester", ha_url="http://ha.test", ha_token="token"),
    )


async def test_status_enriches_tv_and_ma_players(monkeypatch):
    async def fake_get_states(ha_url, ha_token):
        return [TV_STATE, MA_STATE]

    monkeypatch.setattr(media_status.ha_client, "get_states", fake_get_states)

    result = await media_status.handle_media_status(_request())

    assert result.status == "SUCCESS"
    players = {p["entity_id"]: p for p in result.detail["all_players"]}
    assert set(players) == {"media_player.office_tv", "media_player.mass_kitchen"}

    tv = players["media_player.office_tv"]
    assert tv["media_position_updated_at"] == "2026-10-05T12:00:00+00:00"
    assert tv["media_content_id"] == "abc123"
    assert tv["shuffle"] is True
    assert tv["repeat"] == "all"
    assert tv["group_members"] == ["media_player.kitchen"]
    assert tv["app_name"] == "YouTube"
    assert tv["icon_kind"] == "tv"
    assert tv["ma_player_id"] is None

    speaker = players["media_player.mass_kitchen"]
    assert speaker["icon_kind"] == "speaker"
    assert speaker["ma_player_id"] == "kitchen"
    assert speaker["group_members"] == []


async def test_status_ma_player_id_prefers_explicit_mass_player_id(monkeypatch):
    state = {
        "entity_id": "media_player.mass_office",
        "state": "idle",
        "attributes": {
            "friendly_name": "Office",
            "integration": "music_assistant",
            "mass_player_id": "office_player",
            "active_queue": "office_queue",
        },
    }

    async def fake_get_states(ha_url, ha_token):
        return [state]

    monkeypatch.setattr(media_status.ha_client, "get_states", fake_get_states)

    result = await media_status.handle_media_status(_request())

    player = result.detail["all_players"][0]
    assert player["ma_player_id"] == "office_player"


async def test_status_non_ma_player_has_no_ma_player_id(monkeypatch):
    state = {
        "entity_id": "media_player.office_speaker",
        "state": "idle",
        "attributes": {"friendly_name": "Office Speaker"},
    }

    async def fake_get_states(ha_url, ha_token):
        return [state]

    monkeypatch.setattr(media_status.ha_client, "get_states", fake_get_states)

    result = await media_status.handle_media_status(_request())

    player = result.detail["all_players"][0]
    assert player["ma_player_id"] is None
    assert player["icon_kind"] == "speaker"


async def test_a_music_assistant_wrapper_says_whether_it_plays_its_own_queue(monkeypatch):
    """Only then may the screens show the Cast device's song as the wrapper's: the Office TV
    box's Spotify app reported sessions that never played there."""
    def wrapper(eid, source, app_id=None):
        attrs = {"friendly_name": "TV", "mass_player_id": "media_player.tv_cast", "mass_player_type": "player",
                 "source": source}
        if app_id:
            attrs["app_id"] = app_id
        return {"entity_id": eid, "state": "playing", "attributes": attrs}

    async def fake_get_states(ha_url, ha_token):
        return [wrapper("media_player.tv_a", "Music Assistant Queue"),
                wrapper("media_player.tv_b", "Spotify", app_id="music_assistant"),
                wrapper("media_player.tv_c", "External Source"),
                wrapper("media_player.tv_d", "Spotify", app_id="spotify"),
                TV_STATE]

    monkeypatch.setattr(media_status.ha_client, "get_states", fake_get_states)
    players = {p["entity_id"]: p for p in (await media_status.handle_media_status(_request())).detail["all_players"]}
    assert players["media_player.tv_a"]["ma_own_queue"] is True
    assert players["media_player.tv_b"]["ma_own_queue"] is True
    assert players["media_player.tv_c"]["ma_own_queue"] is False
    assert players["media_player.tv_d"]["ma_own_queue"] is False
    assert players["media_player.office_tv"]["ma_own_queue"] is False
