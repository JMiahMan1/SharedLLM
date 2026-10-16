"""BUG-28: media transport must prefer HA's media_player.* services when the
entity advertises the matching supported_features bit, falling back to the
brand remote endpoints only when the entity cannot serve the command."""
from services.execution.handlers import android_tv, media
from services.execution.handlers import roku as roku_handler
from services.execution.schemas import MediaTransportRequest, UserContext

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_ha_registry(monkeypatch):
    """No test here may reach a real Home Assistant: the companion-remote
    lookup (device registry template, then names) answers "unknown" unless a
    test says otherwise, and its cache starts empty."""
    from services.execution import ha_client

    async def no_template(ha_url, ha_token, template):
        return None

    async def no_states(ha_url, ha_token):
        return []

    ha_client._REMOTE_CACHE.clear()
    monkeypatch.setattr(ha_client, "_render_template", no_template)
    monkeypatch.setattr(ha_client, "get_states", no_states)


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


def _patch_ha_standard(monkeypatch, calls):
    async def fake_get_state(ha_url, ha_token, entity_id):
        return _state(entity_id, {"friendly_name": "Office TV", "supported_features": 0})

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id, data=None):
        calls.append({
            "domain": domain,
            "service": service,
            "entity_id": entity_id,
            "data": data,
        })
        return {"ok": True}

    monkeypatch.setattr(media.ha_client, "get_state", fake_get_state)
    monkeypatch.setattr(media.ha_client, "call_service", fake_call_service)


async def test_shuffle_set_uses_media_player_service(monkeypatch):
    calls = []
    _patch_ha_standard(monkeypatch, calls)

    result = await media.handle_media_transport(_request("office_tv", "shuffle_set", shuffle=True))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "media_player",
        "service": "shuffle_set",
        "entity_id": "media_player.office_tv",
        "data": {"shuffle": True},
    }]


async def test_shuffle_set_requires_shuffle_value(monkeypatch):
    calls = []
    _patch_ha_standard(monkeypatch, calls)

    result = await media.handle_media_transport(_request("office_tv", "shuffle_set"))

    assert result.status == "FAILURE"
    assert "shuffle_set requires shuffle" in result.message
    assert calls == []


async def test_repeat_set_uses_media_player_service(monkeypatch):
    calls = []
    _patch_ha_standard(monkeypatch, calls)

    result = await media.handle_media_transport(_request("office_tv", "repeat_set", repeat="all"))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "media_player",
        "service": "repeat_set",
        "entity_id": "media_player.office_tv",
        "data": {"repeat": "all"},
    }]


async def test_repeat_set_requires_repeat_value(monkeypatch):
    calls = []
    _patch_ha_standard(monkeypatch, calls)

    result = await media.handle_media_transport(_request("office_tv", "repeat_set"))

    assert result.status == "FAILURE"
    assert "repeat_set requires repeat" in result.message
    assert calls == []


async def test_join_uses_media_player_service(monkeypatch):
    calls = []
    _patch_ha_standard(monkeypatch, calls)

    result = await media.handle_media_transport(
        _request("office_tv", "join", group_members=["media_player.kitchen"]),
    )

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "media_player",
        "service": "join",
        "entity_id": "media_player.office_tv",
        "data": {"group_members": ["media_player.kitchen"]},
    }]


async def test_join_requires_group_members(monkeypatch):
    calls = []
    _patch_ha_standard(monkeypatch, calls)

    result = await media.handle_media_transport(_request("office_tv", "join"))

    assert result.status == "FAILURE"
    assert "join requires group_members" in result.message
    assert calls == []


async def test_unjoin_uses_media_player_service(monkeypatch):
    calls = []
    _patch_ha_standard(monkeypatch, calls)

    result = await media.handle_media_transport(_request("office_tv", "unjoin"))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "media_player",
        "service": "unjoin",
        "entity_id": "media_player.office_tv",
        "data": None,
    }]


# Remote page: d-pad keys and power
from pydantic import ValidationError  # noqa: E402


@pytest.mark.parametrize("key, atv", [
    ("up", "DPAD_UP"), ("down", "DPAD_DOWN"), ("left", "DPAD_LEFT"),
    ("right", "DPAD_RIGHT"), ("select", "DPAD_CENTER"), ("menu", "MENU"),
])
async def test_android_dpad_keys_are_real_remote_keys(monkeypatch, key, atv):
    """The Remote page's arrows used to all send "home"; each key must reach the TV."""
    calls = []
    states = {"media_player.office_tv": _state("media_player.office_tv", dict(ANDROID_ATTRS))}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("office_tv", key))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "remote",
        "service": "send_command",
        "entity_id": "remote.office_tv",
        "data": {"command": [atv]},
    }]


async def test_roku_select_key(monkeypatch):
    calls = []
    states = {"media_player.roku_living_room": _state("media_player.roku_living_room", dict(ROKU_ATTRS), state="idle")}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("roku_living_room", "select"))

    assert result.status == "SUCCESS"
    assert calls[0]["data"] == {"command": ["select"]}


@pytest.mark.parametrize("command, service", [("power_on", "turn_on"), ("power_off", "turn_off")])
async def test_power_uses_turn_on_off_when_advertised(monkeypatch, command, service):
    """TURN_ON (128) / TURN_OFF (256) beat a brand POWER toggle, which can't tell on from off."""
    calls = []
    attrs = {"app_id": "com.google.android.youtube.tv", "supported_features": 128 | 256}
    states = {"media_player.office_tv": _state("media_player.office_tv", attrs)}
    _patch_ha(monkeypatch, states, calls)

    result = await media.handle_media_transport(_request("office_tv", command))

    assert result.status == "SUCCESS"
    assert calls == [{
        "domain": "media_player",
        "service": service,
        "entity_id": "media_player.office_tv",
        "data": None,
    }]


async def test_generic_power_off_calls_a_real_ha_service(monkeypatch):
    """There is no media_player.power_off service; a plain player gets turn_off."""
    calls = []
    states = {"media_player.kitchen": _state("media_player.kitchen", {"supported_features": 0}, state="playing")}
    _patch_ha(monkeypatch, states, calls)

    await media.handle_media_transport(_request("kitchen", "power_off"))

    assert calls[0]["domain"] == "media_player"
    assert calls[0]["service"] == "turn_off"


def test_unknown_transport_command_is_rejected():
    with pytest.raises(ValidationError):
        _request("office_tv", "unmute")


async def test_remote_keys_go_to_the_device_registry_companion(monkeypatch):
    """Live: media_player.office_tv is driven by remote.office_tv_remote. HA says
    "ok" to a call on a remote that does not exist, so a guessed name failed silently."""
    from services.execution import ha_client

    templates = []

    async def registry(ha_url, ha_token, template):
        templates.append(template)
        return "remote.office_tv_remote"

    monkeypatch.setattr(ha_client, "_render_template", registry)
    calls = []
    states = {"media_player.office_tv": _state("media_player.office_tv", dict(ANDROID_ATTRS))}
    _patch_ha(monkeypatch, states, calls)

    await media.handle_media_transport(_request("office_tv", "up"))
    await media.handle_media_transport(_request("office_tv", "down"))

    assert [c["entity_id"] for c in calls] == ["remote.office_tv_remote", "remote.office_tv_remote"]
    assert "device_id('media_player.office_tv')" in templates[0]
    assert len(templates) == 1  # cached: one lookup, not one per key press


async def test_companion_remote_falls_back_to_a_matching_name(monkeypatch):
    from services.execution import ha_client

    async def states(ha_url, ha_token):
        return [
            _state("media_player.gracies_tv", {"friendly_name": "Gracies TV"}),
            _state("remote.28_tcl_roku_tv", {"friendly_name": "Gracies TV Roku"}),
        ]

    monkeypatch.setattr(ha_client, "get_states", states)
    assert await ha_client.find_companion_remote("http://ha.test", "t", "media_player.gracies_tv") == "remote.28_tcl_roku_tv"


async def test_companion_remote_never_templates_an_unsafe_entity_id(monkeypatch):
    from services.execution import ha_client

    seen = []

    async def registry(ha_url, ha_token, template):
        seen.append(template)
        return ""

    monkeypatch.setattr(ha_client, "_render_template", registry)
    await ha_client.find_companion_remote("http://ha.test", "t", "media_player.x') }}{{ states")
    assert seen == []
