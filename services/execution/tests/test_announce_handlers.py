import asyncio
from types import SimpleNamespace

import pytest

from services.execution.announce_handlers import (
    announce_android_tv,
    announce_samsung,
    announce_webos,
    classify_communication_devices,
    detect_tv_type,
    resolve_tv_companions,
    stop_tv_or_media_alert,
)


def test_detect_cast_by_entity_id():
    assert detect_tv_type("media_player.study_tv_cast", "idle", {}) == "cast"
    assert detect_tv_type("media_player.sunroom_cast", "idle", {}) == "cast"


def test_detect_cast_by_app_id():
    assert detect_tv_type("media_player.tv", "idle", {"app_id": "CC1AD845"}) == "cast"
    assert detect_tv_type("media_player.tv", "idle", {"app_id": "9ac10326"}) == "cast"


def test_detect_roku_by_entity_id():
    assert detect_tv_type("media_player.bedroom_roku_tv", "idle", {}) == "roku"


def test_detect_roku_by_sources():
    assert detect_tv_type("media_player.tv", "idle", {
        "source_list": ["Home", "Netflix", "The Roku Channel", "Hulu"]
    }) == "roku"


def test_detect_roku_ma_wrapped_when_off():
    """When the playroom Roku TV is off, HA sets app_id=None on the MA entity, leaving active_queue='ROKU_...'."""
    assert detect_tv_type("media_player.playroom_tv", "off", {
        "mass_player_type": "player",
        "active_queue": "ROKU_2N0062385487",
    }) == "roku"


def test_detect_webos_by_sources():
    """Sunroom LG webOS TV has entity_id media_player.sunroom_tv and LG portals in source_list."""
    assert detect_tv_type("media_player.sunroom_tv", "on", {
        "device_class": "tv",
        "supported_features": 24381,
        "source_list": ["AV", "Home Dashboard", "LG Content Store", "LG Gaming Portal"],
    }) == "webos"


def test_lg_webos_never_misclassified_as_samsung():
    """LG webOS TVs must never be misclassified as Samsung TVs, even with source_list=['TV', 'HDMI'] or port 7676."""
    from services.execution import device_discovery, device_profiler

    # LG webOS with com.webos.* app_id and TV/HDMI sources
    assert detect_tv_type("media_player.sunroom_tv", "on", {
        "device_class": "tv",
        "supported_features": 24381,
        "app_id": "com.webos.app.hdmi1",
        "source_list": ["TV", "HDMI"],
    }) == "webos"

    # LG webOS with sound_output attribute (HA webostv signature) and TV/HDMI sources
    assert detect_tv_type("media_player.sunroom_tv", "on", {
        "device_class": "tv",
        "supported_features": 24381,
        "sound_output": "tv_speaker",
        "source_list": ["TV", "HDMI"],
    }) == "webos"

    # LG webOS with friendly_name containing LG or webOS
    assert detect_tv_type("media_player.sunroom_tv", "off", {
        "device_class": "tv",
        "supported_features": 24381,
        "friendly_name": "LG OLED TV",
        "source_list": ["TV", "HDMI"],
    }) == "webos"

    # Port 7676 is Samsung UPnP/DLNA, never LG webOS
    webos_port_nums = [p for p, _ in device_discovery.DEVICE_PORTS["webos"]]
    samsung_port_nums = [p for p, _ in device_discovery.DEVICE_PORTS["samsung"]]
    assert 7676 not in webos_port_nums
    assert 7676 in samsung_port_nums
    assert 7676 not in device_profiler.CONTROL_METHODS["webostv"]["ports"]
    assert 7676 in device_profiler.CONTROL_METHODS["samsungtv"]["ports"]
    assert device_profiler._detect_device_type("media_player.some_tv", "", "tv", [7676], {}) == "samsungtv"
    assert device_profiler._detect_device_type("media_player.some_tv", "", "tv", [3000], {}) == "webostv"


def test_detect_samsung_tizen_and_legacy_by_sources():
    """Attic Samsung QLED and Guest Room legacy Orsay Samsung have source_list=['TV', 'HDMI']."""
    assert detect_tv_type("media_player.attic_tv", "off", {
        "device_class": "tv",
        "supported_features": 24381,
        "source_list": ["TV", "HDMI"],
    }) == "samsung"
    assert detect_tv_type("media_player.guest_room_samsung", "off", {
        "device_class": "tv",
        "supported_features": 24381,
        "source_list": ["TV", "HDMI"],
    }) == "samsung"


def test_detect_android_tv():
    assert detect_tv_type("media_player.study_tv", "on", {
        "app_id": "com.google.android.tvlauncher"
    }) == "android_tv"
    assert detect_tv_type("media_player.tv", "idle", {
        "app_id": "com.google.android.apps.mediashell"
    }) == "android_tv"
    assert detect_tv_type("media_player.android_tv_remote", "on", {
        "device_class": "tv",
        "app_id": "com.google.android.backdrop"
    }) == "android_tv"
    # Study TV when off (app_id is None, supported_features == 153529)
    assert detect_tv_type("media_player.study_tv", "off", {
        "device_class": "tv",
        "supported_features": 153529,
        "friendly_name": "Study TV Remote",
    }) == "android_tv"


def test_detect_with_loaded_components():
    components = {"cast.media_player", "media_player", "roku"}
    assert detect_tv_type("media_player.unknown", "idle", {"supported_features": 16384}, components) == "cast"
    assert detect_tv_type("media_player.unknown", "idle", {"supported_features": 131072}, components) == "roku"


def test_resolve_tv_companions_links_cast_ma_and_satellite():
    all_states = [
        {
            "entity_id": "media_player.study_tv",
            "state": "off",
            "attributes": {"friendly_name": "Study TV Remote", "device_class": "tv", "supported_features": 153529},
        },
        {
            "entity_id": "media_player.study_tv_cast",
            "state": "off",
            "attributes": {"friendly_name": "Study TV Cast", "supported_features": 152461},
        },
        {
            "entity_id": "media_player.study_tv_cast_2",
            "state": "off",
            "attributes": {
                "friendly_name": "Study TV Cast",
                "mass_player_type": "player",
                "active_queue": "media_player.study_tv_cast",
            },
        },
        {
            "entity_id": "remote.study_tv_remote",
            "state": "on",
            "attributes": {"friendly_name": "Study TV Remote"},
        },
        {
            "entity_id": "assist_satellite.study_voice_assist_satellite",
            "state": "idle",
            "attributes": {"friendly_name": "Study Assist satellite"},
        },
    ]
    comp = resolve_tv_companions("media_player.study_tv", all_states)
    assert comp["cast_sibling"] == "media_player.study_tv_cast"
    assert comp["ma_sibling"] == "media_player.study_tv_cast_2"
    assert comp["remote_sibling"] == "remote.study_tv_remote"
    assert comp["room_assist_satellite"] == "assist_satellite.study_voice_assist_satellite"


@pytest.mark.asyncio
async def test_announce_android_tv_routes_audio_to_cast_sibling(monkeypatch):
    calls = []

    async def fake_all_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.study_tv",
                "state": "off",
                "attributes": {"friendly_name": "Study TV Remote", "device_class": "tv", "supported_features": 153529},
            },
            {
                "entity_id": "media_player.study_tv_cast",
                "state": "idle",
                "attributes": {"friendly_name": "Study TV Cast", "supported_features": 152461},
            },
        ]

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id=None, data=None):
        calls.append((domain, service, entity_id, data))
        return {"ok": True}

    monkeypatch.setattr("services.execution.ha_client.get_all_states", fake_all_states)
    monkeypatch.setattr("services.execution.ha_client.call_service", fake_call_service)

    res = await announce_android_tv(
        "http://ha",
        "token",
        "media_player.study_tv",
        "http://192.0.2.205:8888/media/test.wav",
        0.6,
        state="on",
        attributes={"supported_features": 153529},
        message="Dinner is ready",
    )
    assert res.get("ok") is True
    assert any(
        c[0] == "media_player" and c[1] == "play_media" and c[2] == "media_player.study_tv_cast"
        for c in calls
    )


@pytest.mark.asyncio
async def test_announce_webos_sends_toast_and_plays_audio(monkeypatch):
    calls = []

    async def fake_all_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.sunroom_tv",
                "state": "on",
                "attributes": {
                    "friendly_name": "LG webOS TV 50lgtv",
                    "source_list": ["Home Dashboard", "LG Content Store"],
                },
            }
        ]

    async def fake_services(ha_url, ha_token):
        return [{"domain": "notify", "services": {"lg_webos_tv_50lgtv": {}}}]

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id=None, data=None):
        calls.append((domain, service, entity_id, data))
        return {"ok": True}

    monkeypatch.setattr("services.execution.ha_client.get_all_states", fake_all_states)
    monkeypatch.setattr("services.execution.ha_client.get_services", fake_services)
    monkeypatch.setattr("services.execution.ha_client.call_service", fake_call_service)

    res = await announce_webos(
        "http://ha",
        "token",
        "media_player.sunroom_tv",
        "http://192.0.2.205:8888/media/test.wav",
        0.5,
        state="on",
        attributes={},
        message="🥚 Soft Boiled Egg is done",
    )
    assert res.get("ok") is True
    # Toast sent with human-readable message (not raw URL)
    assert any(
        c[0] == "notify" and c[1] == "lg_webos_tv_50lgtv" and "Soft Boiled Egg" in (c[3] or {}).get("message", "")
        for c in calls
    )
    # Audio URL played via media_player.play_media
    assert any(
        c[0] == "media_player" and c[1] == "play_media" and c[2] == "media_player.sunroom_tv"
        for c in calls
    )


@pytest.mark.asyncio
async def test_announce_samsung_uses_ma_sibling_even_when_off(monkeypatch):
    calls = []

    async def fake_all_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.attic_tv",
                "state": "on",
                "attributes": {"friendly_name": "Attic TV", "source_list": ["TV", "HDMI"], "device_class": "tv"},
            },
            {
                "entity_id": "media_player.attic_tv_2",
                "state": "off",
                # Note: app_id is None when off!
                "attributes": {"friendly_name": "Attic TV", "mass_player_type": "player", "active_queue": "media_player.attic_tv"},
            },
        ]

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id=None, data=None):
        calls.append((domain, service, entity_id, data))
        return {"ok": True}

    monkeypatch.setattr("services.execution.ha_client.get_all_states", fake_all_states)
    monkeypatch.setattr("services.execution.ha_client.call_service", fake_call_service)

    res = await announce_samsung(
        "http://ha",
        "token",
        "media_player.attic_tv",
        "http://192.0.2.205:8888/media/test.wav",
        0.6,
        state="on",
        attributes={},
        message="Attic alert",
    )
    assert res.get("ok") is True
    assert any(c[2] == "media_player.attic_tv_2" for c in calls)


@pytest.mark.asyncio
async def test_stop_tv_or_media_alert_stops_primary_and_companions(monkeypatch):
    calls = []

    async def fake_all_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.study_tv",
                "state": "on",
                "attributes": {"friendly_name": "Study TV Remote", "device_class": "tv", "supported_features": 153529},
            },
            {
                "entity_id": "media_player.study_tv_cast",
                "state": "playing",
                "attributes": {"friendly_name": "Study TV Cast", "supported_features": 152461},
            },
        ]

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id=None, data=None):
        calls.append((domain, service, entity_id))
        return {"ok": True}

    monkeypatch.setattr("services.execution.ha_client.get_all_states", fake_all_states)
    monkeypatch.setattr("services.execution.ha_client.call_service", fake_call_service)

    res = await stop_tv_or_media_alert("http://ha", "token", "media_player.study_tv")
    assert res["ok"] is True
    assert "media_player.study_tv" in res["stopped"]
    assert "media_player.study_tv_cast" in res["stopped"]


@pytest.mark.asyncio
async def test_intercom_start_twoway_uses_room_assist_satellite_and_falls_back_to_oneway_on_tv(monkeypatch):
    from services.execution.handlers.intercom import handle_intercom_start
    from services.execution.schemas import UserContext

    ha_calls = []
    ann_calls = []

    async def fake_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.study_tv",
                "state": "on",
                "attributes": {"friendly_name": "Study TV Remote", "device_class": "tv", "supported_features": 153529},
            },
            {
                "entity_id": "assist_satellite.study_voice_assist_satellite",
                "state": "idle",
                "attributes": {"friendly_name": "Study Assist satellite"},
            },
            {
                "entity_id": "media_player.sunroom_tv",
                "state": "on",
                "attributes": {
                    "friendly_name": "LG webOS TV",
                    "device_class": "tv",
                    "source_list": ["Home Dashboard", "LG Content Store"],
                },
            },
        ]

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id=None, data=None):
        ha_calls.append((domain, service, entity_id, data))
        return {"ok": True}

    async def fake_identity(method, path, json_data=None):
        return {"session_id": "sess-123", **(json_data or {})}

    async def fake_run_ann(req, ctx, announce_fn=None):
        ann_calls.append((req.target_entity_ids, req.message))
        return {"status": "SUCCESS", "message": "ok"}

    monkeypatch.setattr("services.execution.ha_client.get_states", fake_states)
    monkeypatch.setattr("services.execution.ha_client.call_service", fake_call_service)
    monkeypatch.setattr("services.execution.handlers.intercom._call_identity", fake_identity)
    monkeypatch.setattr("services.execution.announce_routes.run_announcement", fake_run_ann)

    ctx = UserContext(user="alex", is_admin=True, ha_url="http://ha", ha_token="token")

    # Case 1: Study has assist_satellite.study_voice_assist_satellite -> 2-way satellite conversation started
    req_study = SimpleNamespace(
        caller_user_id="alex",
        target_user_id=None,
        target_room="study",
        target_entity_ids=["media_player.study_tv"],
        session_type="twoway",
        message="Hey Study, are you ready?",
        volume=0.6,
    )
    res_study = await handle_intercom_start(req_study, ctx)
    assert res_study.status == "SUCCESS"
    assert res_study.detail["effective_mode"] == "twoway"
    assert res_study.detail["assist_satellite"] == "assist_satellite.study_voice_assist_satellite"
    assert any(c[0] == "assist_satellite" and c[1] == "start_conversation" for c in ha_calls)

    # Case 2: Sunroom TV has no microphone satellite -> automatically falls back to 1-way announcement on TV
    req_lr = SimpleNamespace(
        caller_user_id="alex",
        target_user_id=None,
        target_room="sunroom",
        target_entity_ids=["media_player.sunroom_tv"],
        session_type="twoway",
        message="Time for dinner!",
        volume=0.7,
    )
    res_lr = await handle_intercom_start(req_lr, ctx)
    assert res_lr.status == "SUCCESS"
    assert res_lr.detail["effective_mode"] == "oneway"
    assert any("media_player.sunroom_tv" in targets for targets, _ in ann_calls)


def _media_player(entity_id, name, state, device_class, features):
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": {
            "friendly_name": name,
            "device_class": device_class,
            "supported_features": features,
        },
    }


def test_timer_targets_drop_corrupt_and_duplicate_media_players(monkeypatch):
    monkeypatch.setattr("services.execution.announce_handlers._get_device_registry", lambda: None)
    poison = (
        "media_player.id_1_username_default_display_name_shared_default_user_"
        "is_admin_true_is_system_default_true_nextcloud_url_https_cloud_example_"
        "com_nextcloud_user_rivera_ha_url_https_ha_example_com_github_url_"
        "github_user_alex_example_com_gitlab_url_git"
    )
    states = [
        _media_player(poison, '{"username":"default"}\'s Web Player', "unavailable", "speaker", 8320575),
        _media_player("media_player.study_tv", "Study TV Remote", "on", "tv", 153529),
        _media_player("media_player.den_tv", "Study TV", "idle", "speaker", 8320959),
        _media_player("media_player.study_tv_cast", "Study TV Cast", "off", None, 152461),
        _media_player("media_player.study_tv_cast_2", "Study TV", "off", "speaker", 7796671),
        _media_player("media_player.guest_room_tv", "Guest Room TV", "off", None, 152461),
        _media_player("media_player.guest_room_tv_2", "Guest Room TV", "off", "speaker", 7796671),
        _media_player("media_player.guest_room_tv_3", "Guest Room TV", "unavailable", "tv", 153529),
        _media_player("media_player.guest_room_tv_4", "Guest Room TV", "unavailable", "speaker", 8320575),
        _media_player("media_player.attic_tv", "Attic TV", "off", "tv", 24381),
        _media_player("media_player.attic_tv_2", "Attic TV", "idle", "speaker", 7796287),
        _media_player("media_player.playroom_tv", "Playroom TV", "off", "speaker", 7795643),
        _media_player("media_player.playroom_tv_2", "Playroom TV", "unavailable", "speaker", 7795643),
        _media_player("media_player.kitchen_speaker", "Kitchen Speaker", "idle", "speaker", 7796287),
    ]

    devices = asyncio.run(classify_communication_devices("", "", all_states=states, esp_devices=[]))
    by_id = {d["id"]: d for d in devices}

    assert poison not in by_id
    assert "media_player.study_tv" not in by_id
    assert "media_player.study_tv_cast" not in by_id
    assert "media_player.study_tv_cast_2" not in by_id
    assert "media_player.den_tv" in by_id
    assert "media_player.guest_room_tv_2" in by_id
    assert "media_player.guest_room_tv_3" not in by_id
    assert "media_player.guest_room_tv_4" not in by_id
    assert "media_player.attic_tv_2" in by_id
    assert "media_player.attic_tv" not in by_id
    assert "media_player.playroom_tv" in by_id
    assert "media_player.playroom_tv_2" not in by_id
    assert "media_player.kitchen_speaker" in by_id
    assert by_id["media_player.den_tv"]["announce_capable"] is True
    assert by_id["media_player.den_tv"]["supported_features"] == 8320959
    names = [d["name"] for d in devices]
    assert names.count("Study TV") == 1
    assert names.count("Guest Room TV") == 1
    assert names.count("Playroom TV") == 1
