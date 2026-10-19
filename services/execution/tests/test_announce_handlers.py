from types import SimpleNamespace
import pytest

from services.execution.announce_handlers import (
    announce_android_tv,
    announce_roku,
    announce_samsung,
    announce_webos,
    classify_communication_devices,
    detect_tv_type,
    resolve_tv_companions,
    stop_tv_or_media_alert,
)


def test_detect_cast_by_entity_id():
    assert detect_tv_type("media_player.office_tv_chrome", "idle", {}) == "cast"
    assert detect_tv_type("media_player.living_room_cast", "idle", {}) == "cast"


def test_detect_cast_by_app_id():
    assert detect_tv_type("media_player.tv", "idle", {"app_id": "CC1AD845"}) == "cast"
    assert detect_tv_type("media_player.tv", "idle", {"app_id": "9ac10326"}) == "cast"


def test_detect_roku_by_entity_id():
    assert detect_tv_type("media_player.28_tcl_roku_tv", "idle", {}) == "roku"


def test_detect_roku_by_sources():
    assert detect_tv_type("media_player.tv", "idle", {
        "source_list": ["Home", "Netflix", "The Roku Channel", "Hulu"]
    }) == "roku"


def test_detect_roku_ma_wrapped_when_off():
    """When Gracie's Roku TV is off, HA sets app_id=None on the MA entity, leaving active_queue='ROKU_...'."""
    assert detect_tv_type("media_player.gracies_tv", "off", {
        "mass_player_type": "player",
        "active_queue": "ROKU_2N0062385487",
    }) == "roku"


def test_detect_webos_by_sources():
    """Living Room LG webOS TV has entity_id media_player.living_room_tv and LG portals in source_list."""
    assert detect_tv_type("media_player.living_room_tv", "on", {
        "device_class": "tv",
        "supported_features": 24381,
        "source_list": ["AV", "Home Dashboard", "LG Content Store", "LG Gaming Portal"],
    }) == "webos"


def test_lg_webos_never_misclassified_as_samsung():
    """LG webOS TVs must never be misclassified as Samsung TVs, even with source_list=['TV', 'HDMI'] or port 7676."""
    from services.execution import device_discovery, device_profiler

    # LG webOS with com.webos.* app_id and TV/HDMI sources
    assert detect_tv_type("media_player.living_room_tv", "on", {
        "device_class": "tv",
        "supported_features": 24381,
        "app_id": "com.webos.app.hdmi1",
        "source_list": ["TV", "HDMI"],
    }) == "webos"

    # LG webOS with sound_output attribute (HA webostv signature) and TV/HDMI sources
    assert detect_tv_type("media_player.living_room_tv", "on", {
        "device_class": "tv",
        "supported_features": 24381,
        "sound_output": "tv_speaker",
        "source_list": ["TV", "HDMI"],
    }) == "webos"

    # LG webOS with friendly_name containing LG or webOS
    assert detect_tv_type("media_player.living_room_tv", "off", {
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
    """Loft Samsung QLED and Master Bedroom legacy Orsay Samsung have source_list=['TV', 'HDMI']."""
    assert detect_tv_type("media_player.loft_tv", "off", {
        "device_class": "tv",
        "supported_features": 24381,
        "source_list": ["TV", "HDMI"],
    }) == "samsung"
    assert detect_tv_type("media_player.master_bedroom_samsung", "off", {
        "device_class": "tv",
        "supported_features": 24381,
        "source_list": ["TV", "HDMI"],
    }) == "samsung"


def test_detect_android_tv():
    assert detect_tv_type("media_player.office_tv", "on", {
        "app_id": "com.google.android.tvlauncher"
    }) == "android_tv"
    assert detect_tv_type("media_player.tv", "idle", {
        "app_id": "com.google.android.apps.mediashell"
    }) == "android_tv"
    assert detect_tv_type("media_player.android_tv_remote", "on", {
        "device_class": "tv",
        "app_id": "com.google.android.backdrop"
    }) == "android_tv"
    # Office TV when off (app_id is None, supported_features == 153529)
    assert detect_tv_type("media_player.office_tv", "off", {
        "device_class": "tv",
        "supported_features": 153529,
        "friendly_name": "Office TV Remote",
    }) == "android_tv"


def test_detect_with_loaded_components():
    components = {"cast.media_player", "media_player", "roku"}
    assert detect_tv_type("media_player.unknown", "idle", {"supported_features": 16384}, components) == "cast"
    assert detect_tv_type("media_player.unknown", "idle", {"supported_features": 131072}, components) == "roku"


def test_resolve_tv_companions_links_cast_ma_and_satellite():
    all_states = [
        {
            "entity_id": "media_player.office_tv",
            "state": "off",
            "attributes": {"friendly_name": "Office TV Remote", "device_class": "tv", "supported_features": 153529},
        },
        {
            "entity_id": "media_player.office_tv_chrome",
            "state": "off",
            "attributes": {"friendly_name": "Office TV Cast", "supported_features": 152461},
        },
        {
            "entity_id": "media_player.office_tv_chrome_2",
            "state": "off",
            "attributes": {
                "friendly_name": "Office TV Cast",
                "mass_player_type": "player",
                "active_queue": "media_player.office_tv_chrome",
            },
        },
        {
            "entity_id": "remote.office_tv_remote",
            "state": "on",
            "attributes": {"friendly_name": "Office TV Remote"},
        },
        {
            "entity_id": "assist_satellite.office_jarvis_assist_satellite",
            "state": "idle",
            "attributes": {"friendly_name": "Office Jarvis Assist satellite"},
        },
    ]
    comp = resolve_tv_companions("media_player.office_tv", all_states)
    assert comp["cast_sibling"] == "media_player.office_tv_chrome"
    assert comp["ma_sibling"] == "media_player.office_tv_chrome_2"
    assert comp["remote_sibling"] == "remote.office_tv_remote"
    assert comp["room_assist_satellite"] == "assist_satellite.office_jarvis_assist_satellite"


@pytest.mark.asyncio
async def test_announce_android_tv_routes_audio_to_cast_sibling(monkeypatch):
    calls = []

    async def fake_all_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.office_tv",
                "state": "off",
                "attributes": {"friendly_name": "Office TV Remote", "device_class": "tv", "supported_features": 153529},
            },
            {
                "entity_id": "media_player.office_tv_chrome",
                "state": "idle",
                "attributes": {"friendly_name": "Office TV Cast", "supported_features": 152461},
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
        "media_player.office_tv",
        "http://192.168.2.205:8888/media/test.wav",
        0.6,
        state="on",
        attributes={"supported_features": 153529},
        message="Dinner is ready",
    )
    assert res.get("ok") is True
    assert any(
        c[0] == "media_player" and c[1] == "play_media" and c[2] == "media_player.office_tv_chrome"
        for c in calls
    )


@pytest.mark.asyncio
async def test_announce_webos_sends_toast_and_plays_audio(monkeypatch):
    calls = []

    async def fake_all_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.living_room_tv",
                "state": "on",
                "attributes": {
                    "friendly_name": "LG webOS TV 50UN6955ZUF",
                    "source_list": ["Home Dashboard", "LG Content Store"],
                },
            }
        ]

    async def fake_services(ha_url, ha_token):
        return [{"domain": "notify", "services": {"lg_webos_tv_50un6955zuf": {}}}]

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id=None, data=None):
        calls.append((domain, service, entity_id, data))
        return {"ok": True}

    monkeypatch.setattr("services.execution.ha_client.get_all_states", fake_all_states)
    monkeypatch.setattr("services.execution.ha_client.get_services", fake_services)
    monkeypatch.setattr("services.execution.ha_client.call_service", fake_call_service)

    res = await announce_webos(
        "http://ha",
        "token",
        "media_player.living_room_tv",
        "http://192.168.2.205:8888/media/test.wav",
        0.5,
        state="on",
        attributes={},
        message="🥚 Soft Boiled Egg is done",
    )
    assert res.get("ok") is True
    # Toast sent with human-readable message (not raw URL)
    assert any(
        c[0] == "notify" and c[1] == "lg_webos_tv_50un6955zuf" and "Soft Boiled Egg" in (c[3] or {}).get("message", "")
        for c in calls
    )
    # Audio URL played via media_player.play_media
    assert any(
        c[0] == "media_player" and c[1] == "play_media" and c[2] == "media_player.living_room_tv"
        for c in calls
    )


@pytest.mark.asyncio
async def test_announce_samsung_uses_ma_sibling_even_when_off(monkeypatch):
    calls = []

    async def fake_all_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.loft_tv",
                "state": "on",
                "attributes": {"friendly_name": "Loft TV", "source_list": ["TV", "HDMI"], "device_class": "tv"},
            },
            {
                "entity_id": "media_player.loft_tv_2",
                "state": "off",
                # Note: app_id is None when off!
                "attributes": {"friendly_name": "Loft TV", "mass_player_type": "player", "active_queue": "media_player.loft_tv"},
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
        "media_player.loft_tv",
        "http://192.168.2.205:8888/media/test.wav",
        0.6,
        state="on",
        attributes={},
        message="Loft alert",
    )
    assert res.get("ok") is True
    assert any(c[2] == "media_player.loft_tv_2" for c in calls)


@pytest.mark.asyncio
async def test_stop_tv_or_media_alert_stops_primary_and_companions(monkeypatch):
    calls = []

    async def fake_all_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.office_tv",
                "state": "on",
                "attributes": {"friendly_name": "Office TV Remote", "device_class": "tv", "supported_features": 153529},
            },
            {
                "entity_id": "media_player.office_tv_chrome",
                "state": "playing",
                "attributes": {"friendly_name": "Office TV Cast", "supported_features": 152461},
            },
        ]

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id=None, data=None):
        calls.append((domain, service, entity_id))
        return {"ok": True}

    monkeypatch.setattr("services.execution.ha_client.get_all_states", fake_all_states)
    monkeypatch.setattr("services.execution.ha_client.call_service", fake_call_service)

    res = await stop_tv_or_media_alert("http://ha", "token", "media_player.office_tv")
    assert res["ok"] is True
    assert "media_player.office_tv" in res["stopped"]
    assert "media_player.office_tv_chrome" in res["stopped"]


@pytest.mark.asyncio
async def test_intercom_start_twoway_uses_room_assist_satellite_and_falls_back_to_oneway_on_tv(monkeypatch):
    from services.execution.handlers.intercom import handle_intercom_start
    from services.execution.schemas import UserContext

    ha_calls = []
    ann_calls = []

    async def fake_states(ha_url, ha_token):
        return [
            {
                "entity_id": "media_player.office_tv",
                "state": "on",
                "attributes": {"friendly_name": "Office TV Remote", "device_class": "tv", "supported_features": 153529},
            },
            {
                "entity_id": "assist_satellite.office_jarvis_assist_satellite",
                "state": "idle",
                "attributes": {"friendly_name": "Office Jarvis Assist satellite"},
            },
            {
                "entity_id": "media_player.living_room_tv",
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

    ctx = UserContext(user="jeremiah", is_admin=True, ha_url="http://ha", ha_token="token")

    # Case 1: Office has assist_satellite.office_jarvis_assist_satellite -> 2-way satellite conversation started
    req_office = SimpleNamespace(
        caller_user_id="jeremiah",
        target_user_id=None,
        target_room="office",
        target_entity_ids=["media_player.office_tv"],
        session_type="twoway",
        message="Hey Office, are you ready?",
        volume=0.6,
    )
    res_office = await handle_intercom_start(req_office, ctx)
    assert res_office.status == "SUCCESS"
    assert res_office.detail["effective_mode"] == "twoway"
    assert res_office.detail["assist_satellite"] == "assist_satellite.office_jarvis_assist_satellite"
    assert any(c[0] == "assist_satellite" and c[1] == "start_conversation" for c in ha_calls)

    # Case 2: Living Room TV has no microphone satellite -> automatically falls back to 1-way announcement on TV
    req_lr = SimpleNamespace(
        caller_user_id="jeremiah",
        target_user_id=None,
        target_room="living_room",
        target_entity_ids=["media_player.living_room_tv"],
        session_type="twoway",
        message="Time for dinner!",
        volume=0.7,
    )
    res_lr = await handle_intercom_start(req_lr, ctx)
    assert res_lr.status == "SUCCESS"
    assert res_lr.detail["effective_mode"] == "oneway"
    assert any("media_player.living_room_tv" in targets for targets, _ in ann_calls)
