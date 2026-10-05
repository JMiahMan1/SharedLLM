"""BUG-28: media transport must prefer HA's media_player.* services when the
entity advertises the matching supported_features bit, falling back to the
brand remote endpoints only when the entity cannot serve the command."""
from services.execution.handlers import android_tv, media
from services.execution.handlers import roku as roku_handler
from services.execution.schemas import MediaTransportRequest, UserContext

ANDROID_ATTRS = {"app_id": "com.google.android.youtube.tv", "supported_features": 0}
ROKU_ATTRS = {"friendly_name": "Roku Living Room", "supported_features": 0}


def _request(entity_id, command, **kwargs):
    return MediaTransportRequest(
        user_context=UserContext(user="tester", ha_url="http://ha.test", ha_token="token"),
        entity_id=entity_id,
        command=command,
        **kwargs,
    )


def _state(entity_id, attrs, state="on"):
    return {"entity_id": entity_id, "state": state, "attributes": attrs}


def _patch_ha(monkeypatch, states, calls):
    async def fake_get_state(ha_url, ha_token, entity_id):
        return states.get(entity_id)

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id, data=None):
        calls.append({
            "domain": domain,
            "service": service,
            "entity_id": entity_id,
            "data": data,
        })
        return {"ok": True}

    for handler in (media, android_tv, roku_handler):
        monkeypatch.setattr(handler.ha_client, "get_state", fake_get_state)
        monkeypatch.setattr(handler.ha_client, "call_service", fake_call_service)


async def test_feature_supported_play_uses_media_player_service(monkeypatch):
    calls = []
    states = {
        "media_player.office_tv": _state(
            "media_player.office_tv",
            {"app_id": "com.google.android.youtube.tv", "supported_features": 16384},
        )
    }
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("office_tv", "play"))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "media_player",
        "service": "media_play",
        "entity_id": "media_player.office_tv",
        "data": None,
    }]


async def test_android_play_without_feature_bit_falls_back_to_remote(monkeypatch):
    calls = []
    states = {"media_player.office_tv": _state("media_player.office_tv", dict(ANDROID_ATTRS))}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("office_tv", "play"))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "remote",
        "service": "send_command",
        "entity_id": "remote.office_tv",
        "data": {"command": ["MEDIA_PLAY"]},
    }]


async def test_android_home_uses_remote_command(monkeypatch):
    calls = []
    states = {"media_player.office_tv": _state("media_player.office_tv", dict(ANDROID_ATTRS))}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("office_tv", "home"))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "remote",
        "service": "send_command",
        "entity_id": "remote.office_tv",
        "data": {"command": ["HOME"]},
    }]


async def test_android_unknown_command_fails_without_calls(monkeypatch):
    calls = []
    states = {"media_player.office_tv": _state("media_player.office_tv", dict(ANDROID_ATTRS))}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("office_tv", "seek"))

    assert result.status == "FAILURE"
    assert "Unsupported Android TV command" in result.message
    assert calls == []


async def test_roku_play_without_feature_bit_falls_back_to_remote(monkeypatch):
    calls = []
    states = {"media_player.roku_living_room": _state("media_player.roku_living_room", dict(ROKU_ATTRS), state="idle")}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("roku_living_room", "pause"))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "remote",
        "service": "send_command",
        "entity_id": "remote.roku_living_room",
        "data": {"command": ["play"]},
    }]


async def test_roku_volume_mute_uses_remote_command(monkeypatch):
    calls = []
    states = {"media_player.roku_living_room": _state("media_player.roku_living_room", dict(ROKU_ATTRS), state="idle")}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("roku_living_room", "volume_mute"))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "remote",
        "service": "send_command",
        "entity_id": "remote.roku_living_room",
        "data": {"command": ["volume_mute"]},
    }]


async def test_roku_stop_without_feature_bit_fails(monkeypatch):
    calls = []
    states = {"media_player.roku_living_room": _state("media_player.roku_living_room", dict(ROKU_ATTRS), state="idle")}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("roku_living_room", "stop"))

    assert result.status == "FAILURE"
    assert "Unsupported Roku command" in result.message
    assert calls == []


async def test_find_cast_sibling_excludes_wrong_bitmask(monkeypatch):
    async def fake_get_states(ha_url, ha_token):
        return [
            _state("media_player.office_tv", {"friendly_name": "Office TV"}),
            _state(
                "media_player.office_tv_chrome",
                {"friendly_name": "Office TV Chrome", "supported_features": 8424, "cast_type": "cast"},
            ),
        ]

    monkeypatch.setattr(android_tv.ha_client, "get_states", fake_get_states)

    assert await android_tv._find_cast_sibling("http://ha.test", "token", "media_player.office_tv") is None


async def test_find_cast_sibling_returns_play_media_candidate(monkeypatch):
    async def fake_get_states(ha_url, ha_token):
        return [
            _state("media_player.office_tv", {"friendly_name": "Office TV"}),
            _state(
                "media_player.office_tv_chrome",
                {"friendly_name": "Office TV Chrome", "supported_features": 512, "cast_type": "cast"},
            ),
        ]

    monkeypatch.setattr(android_tv.ha_client, "get_states", fake_get_states)

    assert await android_tv._find_cast_sibling("http://ha.test", "token", "media_player.office_tv") == "media_player.office_tv_chrome"
