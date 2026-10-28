# services/execution/tests/test_esphome.py
"""Unit tests for direct ESPHome native-API control.

All device connections and the Identity settings fetch are mocked;
no live devices or services needed.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import aioesphomeapi
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def one_device(monkeypatch):
    """Seed the module-level device cache with a single test device."""
    import services.execution.esphome_client as ec

    monkeypatch.setattr(
        ec,
        "_device_cache",
        {
            "office-light": {
                "host": "192.168.2.87",
                "port": 6053,
                "noise_psk": "psk-key-abc",
            }
        },
    )
    monkeypatch.setattr(ec, "_cache_loaded_at", time.monotonic())
    return ec


def _mock_api_class(entities, device_info=None):
    """Build an APIClient stand-in exposing the pieces esphome_client uses."""
    info = device_info or MagicMock(friendly_name="Office Light", name="office-light",
                                    esphome_version="2026.8.0", model="esp32")
    instance = MagicMock()
    instance.connect = AsyncMock()
    instance.disconnect = AsyncMock()
    instance.device_info_and_list_entities = AsyncMock(return_value=(info, entities, []))
    return MagicMock(return_value=instance), instance


@pytest.mark.asyncio
async def test_list_entities_success(one_device):
    from services.execution.esphome_client import list_entities

    entities = [
        aioesphomeapi.LightInfo(key=1, name="Office Light", object_id="office_light"),
        aioesphomeapi.ButtonInfo(key=2, name="Restart", object_id="restart"),
    ]
    api_cls, _instance = _mock_api_class(entities)
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        data = await list_entities("office-light")

    assert data["device"]["name"] == "Office Light"
    domains = {e["domain"] for e in data["entities"]}
    assert domains == {"light", "button"}
    # noise PSK from Identity config must reach the client constructor
    kwargs = api_cls.call_args.kwargs
    assert kwargs.get("noise_psk") == "psk-key-abc"


@pytest.mark.asyncio
async def test_call_light_brightness_and_state(one_device):
    from services.execution.esphome_client import call_entity

    entities = [aioesphomeapi.LightInfo(key=7, name="Office Light", object_id="office_light")]
    api_cls, instance = _mock_api_class(entities)
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        result = await call_entity(
            "office-light", "office_light",
            {"state": True, "brightness_pct": 55},
        )

    assert result["domain"] == "light"
    instance.light_command.assert_called_once_with(7, state=True, brightness=0.55, rgb=None)


@pytest.mark.asyncio
async def test_call_button_command(one_device):
    from services.execution.esphome_client import call_entity

    entities = [aioesphomeapi.ButtonInfo(key=9, name="Restart", object_id="restart")]
    api_cls, instance = _mock_api_class(entities)
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        result = await call_entity("office-light", "restart")

    assert result["domain"] == "button"
    instance.button_command.assert_called_once_with(9)


@pytest.mark.asyncio
async def test_unknown_entity_lists_available(one_device):
    from services.execution.esphome_client import call_entity

    entities = [aioesphomeapi.LightInfo(key=1, name="Office Light", object_id="office_light")]
    api_cls, _ = _mock_api_class(entities)
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls), pytest.raises(ValueError) as exc:
        await call_entity("office-light", "does_not_exist")
    assert "Available:" in str(exc.value)


@pytest.mark.asyncio
async def test_missing_setting_fails_fast():
    """Empty/unset esphome_devices raises a clear error naming the setting."""
    import services.execution.esphome_client as ec

    async def fake_load():
        raise ec.EsphomeConfigError("Setting 'esphome_devices' is not configured.")

    with patch.object(ec, "_load_devices_from_identity", fake_load), pytest.raises(ec.EsphomeConfigError) as exc:
        await ec.get_devices(force_refresh=True)
    assert "esphome_devices" in str(exc.value)


@pytest.mark.asyncio
async def test_settings_ttl_cache_avoids_refetch(one_device):
    """Second resolution within TTL must not hit Identity again."""
    calls = {"n": 0}

    async def counting_load():
        calls["n"] += 1
        return {}

    with patch.object(one_device, "_load_devices_from_identity", counting_load):
        await one_device.get_devices()  # cache seeded by fixture -> no fetch
        assert calls["n"] == 0
        await one_device.get_devices(force_refresh=True)  # explicit refresh -> 1 fetch
        assert calls["n"] == 1


@pytest.mark.asyncio
async def test_handler_list_success(one_device):
    from services.execution.handlers.esphome import handle_esphome
    from services.execution.schemas import EsphomeRequest, UserContext

    req = EsphomeRequest(
        user_context=UserContext(user="jeremiah"),
        action="list",
        device="office-light",
    )
    entities = [aioesphomeapi.LightInfo(key=1, name="Office Light", object_id="office_light")]
    api_cls, _ = _mock_api_class(entities)
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        result = await handle_esphome(req)
    assert result.status == "SUCCESS"
    assert "office_light" in result.message


@pytest.mark.asyncio
async def test_handler_config_error_is_failure_not_exception(one_device):
    from services.execution.handlers.esphome import handle_esphome
    from services.execution.schemas import EsphomeRequest, UserContext

    req = EsphomeRequest(
        user_context=UserContext(user="jeremiah"),
        action="call",
        device="nope",
        entity="whatever",
    )
    result = await handle_esphome(req)
    assert result.status == "FAILURE"
    assert "nope" in result.message
    assert "esphome_devices" not in result.message  # it's a device-name error, not settings


def test_route_wired_via_testclient(one_device):
    """POST /execute/esphome reaches the handler end-to-end."""
    from services.config import INTERNAL_SECRET
    from services.execution.main import app

    client = TestClient(app)
    payload = {
        "user_context": {"user": "jeremiah"},
        "action": "call",
        "device": "office-light",
        "entity": "office_light",
        "params": {"state": True},
    }
    entities = [aioesphomeapi.LightInfo(key=3, name="Office Light", object_id="office_light")]
    api_cls, instance = _mock_api_class(entities)
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        resp = client.post(
            "/execute/esphome", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["service"] == "esphome"
    instance.light_command.assert_called_once_with(3, state=True, brightness=None, rgb=None)


# configure_jarvis: Jarvis hands a device its own endpoint
def _configure_service(args=("url", "api_key")):
    return aioesphomeapi.UserService(
        name="configure_jarvis",
        key=42,
        args=[aioesphomeapi.UserServiceArg(name=a, type=aioesphomeapi.UserServiceArgType.STRING) for a in args],
    )


def _mock_api_with_services(services):
    instance = MagicMock()
    instance.connect = AsyncMock()
    instance.disconnect = AsyncMock()
    instance.list_entities_services = AsyncMock(return_value=([], services))
    instance.execute_service = AsyncMock()
    return MagicMock(return_value=instance), instance


def _configure_req(api_key="sk-user"):
    from services.execution.schemas import EsphomeRequest, UserContext

    return EsphomeRequest(
        user_context=UserContext(user="jeremiah", api_key=api_key),
        action="configure_jarvis",
        device="office-light",
    )


@pytest.mark.asyncio
async def test_configure_jarvis_unset_url_fails_fast(one_device):
    """No JARVIS_HOST and no address seen -> FAILURE naming it; nothing minted or sent."""
    from services.execution.handlers.esphome import handle_esphome

    mint = AsyncMock(return_value="sk-device")
    api_cls, _ = _mock_api_with_services([_configure_service()])
    with patch.object(one_device, "JARVIS_HOST", None), \
         patch.object(one_device, "mint_device_api_key", mint), \
         patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        result = await handle_esphome(_configure_req())
    assert result.status == "FAILURE"
    assert "JARVIS_HOST" in result.message
    mint.assert_not_called()
    api_cls.assert_not_called()


@pytest.mark.asyncio
async def test_configure_jarvis_requires_user_api_key(one_device):
    from services.execution.handlers.esphome import handle_esphome

    api_cls, _ = _mock_api_with_services([_configure_service()])
    with patch.object(one_device, "JARVIS_HOST", "http://10.0.0.9:11435"), \
         patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        result = await handle_esphome(_configure_req(api_key=None))
    assert result.status == "FAILURE"
    assert "API key" in result.message
    api_cls.assert_not_called()


@pytest.mark.asyncio
async def test_configure_jarvis_device_without_action_mints_nothing(one_device):
    """A device lacking the action fails clearly and no key is left behind."""
    from services.execution.handlers.esphome import handle_esphome

    mint = AsyncMock(return_value="sk-device")
    api_cls, instance = _mock_api_with_services([])
    with patch.object(one_device, "JARVIS_HOST", "http://10.0.0.9:11435"), \
         patch.object(one_device, "mint_device_api_key", mint), \
         patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        result = await handle_esphome(_configure_req())
    assert result.status == "FAILURE"
    assert "configure_jarvis" in result.message
    mint.assert_not_called()
    instance.execute_service.assert_not_called()


@pytest.mark.asyncio
async def test_configure_jarvis_pushes_url_and_minted_key(one_device):
    from services.execution.handlers.esphome import handle_esphome

    mint = AsyncMock(return_value="sk-device")
    service = _configure_service()
    api_cls, instance = _mock_api_with_services([service])
    with patch.object(one_device, "JARVIS_HOST", "http://10.0.0.9:11435/"), \
         patch.object(one_device, "mint_device_api_key", mint), \
         patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        result = await handle_esphome(_configure_req())
    assert result.status == "SUCCESS", result.message
    mint.assert_awaited_once_with("sk-user", "office-light")
    # return_response: the device's own refusal (e.g. "pair first") is surfaced
    instance.execute_service.assert_awaited_once_with(
        service, {"url": "http://10.0.0.9:11435", "api_key": "sk-device"}, return_response=True
    )
    assert "sk-device" not in result.message  # the key is never echoed back


@pytest.mark.asyncio
async def test_jarvis_host_must_be_http(one_device):
    with patch.object(one_device, "JARVIS_HOST", "10.0.0.9:11435"), \
         pytest.raises(one_device.EsphomeConfigError) as exc:
        await one_device.get_jarvis_device_url()
    assert "http://" in str(exc.value)


# Companion pairing
def _svc(name, *args):
    return aioesphomeapi.UserService(
        name=name, key=hash(name) & 0xFFFF,
        args=[aioesphomeapi.UserServiceArg(name=a, type=aioesphomeapi.UserServiceArgType.STRING) for a in args],
    )


WATCH_SERVICES = [_svc("pair_begin"), _svc("pair_confirm", "code", "owner"), _svc("configure_jarvis", "url", "api_key")]


def _device_api(services, confirm_ok=True, requires_encryption=False):
    """APIClient stand-in recording connects (with their psk) and actions."""
    calls = {"connects": [], "actions": [], "keys": []}
    info = MagicMock(friendly_name="Jarvis Watch", mac_address="74:4D:BD:2C:97:28",
                     model="esp32-s3-devkitc-1", esphome_version="2026.9.0", project_version="1.2.0")
    info.name = "jarvis-watch"

    def make(host, port, password, noise_psk=None):
        inst = MagicMock()

        async def connect(login=True):
            if requires_encryption:
                raise aioesphomeapi.RequiresEncryptionAPIError("needs key")
            calls["connects"].append(noise_psk)
        inst.connect = connect
        inst.disconnect = AsyncMock()
        inst.device_info = AsyncMock(return_value=info)
        inst.list_entities_services = AsyncMock(return_value=([], services))

        async def set_key(k):
            calls["keys"].append(k)
            return True
        inst.noise_encryption_set_key = set_key

        async def execute(service, data, return_response=None):
            calls["actions"].append((service.name, data))
            ok = confirm_ok if service.name == "pair_confirm" else True
            return MagicMock(success=ok, error_message="" if ok else "Wrong code.")
        inst.execute_service = execute
        return inst
    return make, calls


def _pair_req(step, **kw):
    from services.execution.schemas import EsphomePairRequest, UserContext
    return EsphomePairRequest(user_context=UserContext(user="jeremiah", api_key="sk-user"), step=step, **kw)


@pytest.fixture()
def pairing_env(one_device, monkeypatch):
    ec = one_device
    saved, removed, claims = [], [], []
    monkeypatch.setattr(ec, "JARVIS_HOST", "http://10.0.0.9:11435")

    async def save(name, entry):
        saved.append((name, entry))

    async def remove(name):
        removed.append(name)

    async def claim(body):
        claims.append(body)
        return {"device_key": body["device_key"], "owner_username": body["owner_username"]}
    monkeypatch.setattr(ec, "_save_device", save)
    monkeypatch.setattr(ec, "_remove_device", remove)
    monkeypatch.setattr(ec, "_claim", claim)
    monkeypatch.setattr(ec, "mint_device_api_key", AsyncMock(return_value="sk-device"))
    return ec, saved, removed, claims


@pytest.mark.asyncio
async def test_pair_start_asks_a_screen_device_for_its_code(pairing_env):
    from services.execution.handlers.esphome import handle_esphome_pair
    ec = pairing_env[0]
    make, calls = _device_api(WATCH_SERVICES)
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("start", host="192.168.2.105"))
    assert res.status == "SUCCESS", res.message
    assert res.detail["method"] == "code"
    assert calls["actions"] == [("pair_begin", {})]
    assert "code shown on Jarvis Watch" in res.message


@pytest.mark.asyncio
async def test_pair_start_on_a_screenless_device_adopts(pairing_env):
    from services.execution.handlers.esphome import handle_esphome_pair
    ec = pairing_env[0]
    make, calls = _device_api([_svc("announce", "text")])
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("start", host="10.0.0.40"))
    assert res.detail["method"] == "adopt"
    assert calls["actions"] == []  # nothing to show a code on


@pytest.mark.asyncio
async def test_pair_finish_with_the_right_code_links_the_device(pairing_env):
    from services.execution.handlers.esphome import handle_esphome_pair
    ec, saved, removed, claims = pairing_env
    make, calls = _device_api(WATCH_SERVICES)
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("finish", host="192.168.2.105", code="123456"))
    assert res.status == "SUCCESS", res.message
    psk = calls["keys"][0].decode()
    assert calls["connects"] == [None, psk]  # plain, then encrypted with the new key
    assert ("pair_confirm", {"code": "123456", "owner": "jeremiah"}) in calls["actions"]
    assert ("configure_jarvis", {"url": "http://10.0.0.9:11435", "api_key": "sk-device"}) in calls["actions"]
    assert saved == [("jarvis-watch", {"host": "192.168.2.105", "port": 6053, "noise_psk": psk, "owner": "jeremiah",
                                       "jarvis_url": "http://10.0.0.9:11435"})]
    assert claims[0]["verified"] is True and claims[0]["kind"] == "watch"
    assert claims[0]["device_key"] == "esphome:744dbd2c9728"
    assert claims[0]["app_version"] == "1.2.0"  # the watch's own firmware version
    assert removed == []


@pytest.mark.asyncio
async def test_pair_finish_with_a_wrong_code_undoes_everything(pairing_env):
    from services.execution.handlers.esphome import handle_esphome_pair
    ec, saved, removed, claims = pairing_env
    make, calls = _device_api(WATCH_SERVICES, confirm_ok=False)
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("finish", host="192.168.2.105", code="000000"))
    assert res.status == "FAILURE"
    assert res.message == "Wrong code."
    assert calls["keys"][-1] == b""  # key cleared on the device
    assert [name for name, _ in saved] == ["jarvis-watch"]  # recorded as soon as it was set...
    assert removed == ["jarvis-watch"]  # ...and dropped again
    assert claims == []
    ec.mint_device_api_key.assert_not_called()


@pytest.mark.asyncio
async def test_pair_finish_adopts_a_screenless_device_unverified(pairing_env):
    from services.execution.handlers.esphome import handle_esphome_pair
    ec, _saved, _removed, claims = pairing_env
    make, _calls = _device_api([_svc("announce", "text")])
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("finish", host="10.0.0.40"))
    assert res.status == "SUCCESS", res.message
    assert claims[0]["verified"] is False and claims[0]["kind"] == "assistant"
    assert "no key or address was sent" in res.message  # it never calls Jarvis
    ec.mint_device_api_key.assert_not_called()


@pytest.mark.asyncio
async def test_code_device_without_a_code_is_asked_for_one(pairing_env):
    from services.execution.handlers.esphome import handle_esphome_pair
    ec, saved = pairing_env[0], pairing_env[1]
    make, calls = _device_api(WATCH_SERVICES)
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("finish", host="192.168.2.105"))
    assert res.status == "FAILURE" and "Enter the code" in res.message
    assert calls["keys"] == [] and saved == []  # nothing changed on the device


@pytest.mark.asyncio
async def test_an_already_keyed_device_says_how_to_proceed(pairing_env):
    from services.execution.handlers.esphome import handle_esphome_pair
    ec = pairing_env[0]
    make, _ = _device_api(WATCH_SERVICES, requires_encryption=True)
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("start", host="192.168.2.105"))
    assert res.status == "FAILURE" and "already has an encryption key" in res.message


@pytest.mark.asyncio
async def test_pairing_without_any_address_fails_before_touching_the_device(pairing_env, monkeypatch):
    from services.execution.handlers.esphome import handle_esphome_pair
    ec = pairing_env[0]
    monkeypatch.setattr(ec, "JARVIS_HOST", None)
    make, calls = _device_api(WATCH_SERVICES)
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("finish", host="192.168.2.105", code="123456"))
    assert res.status == "FAILURE" and "JARVIS_HOST" in res.message
    assert calls["connects"] == []


@pytest.mark.asyncio
async def test_a_paired_device_obeys_only_its_owner(one_device, monkeypatch):
    from services.execution.handlers.esphome import handle_esphome
    from services.execution.schemas import EsphomeRequest, UserContext
    one_device._device_cache["office-light"]["owner"] = "kate"
    api_cls, _ = _mock_api_class([aioesphomeapi.LightInfo(key=1, name="Office Light", object_id="office_light")])
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        mine = await handle_esphome(EsphomeRequest(user_context=UserContext(user="jeremiah"), action="list", device="office-light"))
        admin = await handle_esphome(EsphomeRequest(user_context=UserContext(user="jeremiah", is_admin=True), action="list", device="office-light"))
        owner = await handle_esphome(EsphomeRequest(user_context=UserContext(user="Kate"), action="list", device="office-light"))
    assert mine.status == "FAILURE" and "another user" in mine.message
    assert admin.status == "SUCCESS" and owner.status == "SUCCESS"


@pytest.mark.asyncio
async def test_pairing_gives_the_device_the_address_jarvis_was_reached_at(pairing_env, monkeypatch):
    """JARVIS_HOST unset: the gateway passes the address the user is on."""
    from services.execution.handlers.esphome import handle_esphome_pair
    ec = pairing_env[0]
    monkeypatch.setattr(ec, "JARVIS_HOST", None)
    make, calls = _device_api(WATCH_SERVICES)
    with patch.object(ec.aioesphomeapi, "APIClient", side_effect=make):
        res = await handle_esphome_pair(_pair_req("finish", host="192.168.2.105", code="123456",
                                                  jarvis_url="https://jarvis.sumemail.com/"))
    assert res.status == "SUCCESS", res.message
    assert ("configure_jarvis", {"url": "https://jarvis.sumemail.com", "api_key": "sk-device"}) in calls["actions"]


@pytest.mark.asyncio
async def test_get_device_resolves_friendly_label_and_slug(one_device):
    """'Office Light' (the friendly label stored in Identity's Device table)
    resolves the 'office-light' entry in esphome_devices."""
    cfg = await one_device.get_device("Office Light")
    assert cfg["host"] == "192.168.2.87"
    cfg_prefix = await one_device.get_device("esphome:Office Light")
    assert cfg_prefix["host"] == "192.168.2.87"


@pytest.mark.asyncio
async def test_list_entities_includes_live_states_and_metadata(one_device):
    """list_entities subscribes to live states and returns sensor/select/number/media_player/light values."""
    from services.execution.esphome_client import list_entities

    entities = [
        aioesphomeapi.SensorInfo(
            key=10, name="Battery", object_id="battery", unit_of_measurement="%", accuracy_decimals=0
        ),
        aioesphomeapi.BinarySensorInfo(key=11, name="Charging", object_id="charging"),
        aioesphomeapi.TextSensorInfo(key=12, name="Wi-Fi Network", object_id="wi_fi_network"),
        aioesphomeapi.SelectInfo(
            key=13, name="Power Mode", object_id="power_mode", options=["deep", "ble", "live"]
        ),
        aioesphomeapi.NumberInfo(
            key=14, name="Screen Brightness", object_id="screen_brightness", min_value=0.0, max_value=100.0, step=5.0
        ),
        aioesphomeapi.LightInfo(key=15, name="Display Backlight", object_id="display_backlight"),
        aioesphomeapi.MediaPlayerInfo(key=16, name="Smart Speaker", object_id="smart_speaker", supports_pause=True),
        aioesphomeapi.TextInfo(key=17, name="Now Playing Title", object_id="now_playing_title", min_length=0, max_length=120),
    ]
    services = [
        _svc("show_page", "page"),
        _svc("set_rotation", "angle"),
    ]
    info = MagicMock(
        friendly_name="Jarvis Screen",
        name="fnk0104b",
        esphome_version="2026.9.0",
        model="esp32-s3-devkitc-1",
        mac_address="AA:BB:CC:DD:EE:FF",
        project_name="jarvis.fnk0104b",
        project_version="2.1.0",
        has_deep_sleep=False,
        webserver_port=80,
    )
    info.name = "fnk0104b"
    api_cls, instance = _mock_api_class(entities, device_info=info)
    instance.device_info_and_list_entities = AsyncMock(return_value=(info, entities, services))

    def fake_subscribe(on_state):
        on_state(aioesphomeapi.SensorState(key=10, state=84.4, missing_state=False))
        on_state(aioesphomeapi.BinarySensorState(key=11, state=True, missing_state=False))
        on_state(aioesphomeapi.TextSensorState(key=12, state="SummersLAN", missing_state=False))
        on_state(aioesphomeapi.SelectState(key=13, state="live", missing_state=False))
        on_state(aioesphomeapi.NumberState(key=14, state=75.0, missing_state=False))
        on_state(aioesphomeapi.LightState(key=15, state=True, brightness=0.8))
        on_state(
            aioesphomeapi.MediaPlayerEntityState(
                key=16, state=aioesphomeapi.MediaPlayerState.PLAYING, volume=0.65, muted=False
            )
        )
        on_state(aioesphomeapi.TextState(key=17, state="psalm 23", missing_state=False))

    instance.subscribe_states.side_effect = fake_subscribe

    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        data = await list_entities("office-light")

    assert data["device"]["inferred_kind"] == "screen"
    assert data["device"]["webserver_port"] == 80
    assert data["has_speaker"] is True
    by_oid = {e["object_id"]: e for e in data["entities"]}
    assert by_oid["battery"]["state"] == 84
    assert by_oid["battery"]["unit"] == "%"
    assert by_oid["charging"]["state"] is True
    assert by_oid["wi_fi_network"]["state"] == "SummersLAN"
    assert by_oid["power_mode"]["state"] == "live"
    assert by_oid["power_mode"]["options"] == ["deep", "ble", "live"]
    assert by_oid["screen_brightness"]["state"] == 75.0
    assert by_oid["screen_brightness"]["max_value"] == 100.0
    assert by_oid["display_backlight"]["state"] is True
    assert by_oid["display_backlight"]["brightness_pct"] == 80
    assert by_oid["smart_speaker"]["state"] == "playing"
    assert by_oid["smart_speaker"]["volume_pct"] == 65
    assert by_oid["now_playing_title"]["state"] == "psalm 23"


def test_infer_esphome_kind_recognizes_sibling_profiles():
    from services.execution.esphome_client import infer_esphome_kind

    watch_info = MagicMock(friendly_name="Jarvis Watch")
    watch_info.name = "jarvis-watch"
    assert infer_esphome_kind(watch_info, [], [_svc("find_watch")], "code") == "watch"

    speaker_info = MagicMock(friendly_name="Smart Speaker")
    speaker_info.name = "smart-speaker"
    speaker_entities = [aioesphomeapi.MediaPlayerInfo(key=1, name="Speaker", object_id="smart_speaker")]
    assert infer_esphome_kind(speaker_info, speaker_entities, [], "adopt") == "speaker"

    screen_info = MagicMock(friendly_name="Waveshare 7 Screen")
    screen_info.name = "waveshare-7-screen"
    assert infer_esphome_kind(screen_info, [], [], "adopt") == "screen"

    cyd_info = MagicMock(friendly_name="CYD Dashboard")
    cyd_info.name = "jarvis-cyd2"
    assert infer_esphome_kind(cyd_info, [], [_svc("show_page", "page")], "adopt") == "screen"

    light_info = MagicMock(friendly_name="Office Light")
    light_info.name = "office-light"
    light_entities = [aioesphomeapi.LightInfo(key=1, name="Office Light", object_id="office_light")]
    assert infer_esphome_kind(light_info, light_entities, [], "adopt") == "light"


@pytest.mark.asyncio
async def test_call_entity_supports_text_select_number_media_and_services(one_device):
    """call_entity controls text, select, number, media_player, and user services (watch/screen actions)."""
    from services.execution.esphome_client import call_entity

    entities = [
        aioesphomeapi.TextInfo(key=21, name="Now Playing Title", object_id="now_playing_title"),
        aioesphomeapi.SelectInfo(key=22, name="Power Mode", object_id="power_mode", options=["deep", "ble", "live"]),
        aioesphomeapi.NumberInfo(key=23, name="Step Goal", object_id="step_goal", min_value=1000, max_value=50000, step=500),
        aioesphomeapi.MediaPlayerInfo(key=24, name="Smart Speaker", object_id="smart_speaker"),
    ]
    show_page_svc = aioesphomeapi.UserService(
        name="show_page",
        key=99,
        args=[aioesphomeapi.UserServiceArg(name="page", type=aioesphomeapi.UserServiceArgType.INT)],
    )
    api_cls, instance = _mock_api_class(entities)
    instance.device_info_and_list_entities = AsyncMock(
        return_value=(MagicMock(friendly_name="Smart Hub", name="smart-hub", esphome_version="2026.9.0", model="esp32-s3"), entities, [show_page_svc])
    )
    instance.execute_service = AsyncMock()

    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        await call_entity("office-light", "now_playing_title", {"value": "Morning Hymn"})
        instance.text_command.assert_called_once_with(21, "Morning Hymn")

        await call_entity("office-light", "power_mode", {"option": "ble"})
        instance.select_command.assert_called_once_with(22, "ble")

        await call_entity("office-light", "step_goal", {"value": 12000})
        instance.number_command.assert_called_once_with(23, 12000.0)

        await call_entity("office-light", "smart_speaker", {"command": "play_pause", "volume_pct": 60})
        instance.media_player_command.assert_called_once_with(
            24,
            command=aioesphomeapi.MediaPlayerCommand.PLAY,
            volume=0.6,
            media_url=None,
            announcement=None,
        )

        svc_res = await call_entity("office-light", "show_page", {"page": "3"})
        assert svc_res["domain"] == "service"
        instance.execute_service.assert_awaited_once_with(show_page_svc, {"page": 3}, return_response=None)


@pytest.mark.asyncio
async def test_esphome_ota_update_via_native_protocol_and_handler(one_device):
    """handle_esphome(action='ota') pushes a firmware binary via ESPHome's OTA protocol."""
    import base64
    from services.execution.handlers.esphome import handle_esphome
    from services.execution.schemas import EsphomeRequest, UserContext

    firmware = b"\xe9\x04\x02\x20" + b"jarvis-watch-firmware-payload" * 32
    req = EsphomeRequest(
        user_context=UserContext(user="jeremiah", is_admin=True),
        action="ota",
        device="office-light",
        params={
            "firmware_b64": base64.b64encode(firmware).decode("ascii"),
            "filename": "watch-v1.3.0.bin",
        },
    )
    fake_sync = MagicMock(
        return_value={
            "method": "esphome_ota",
            "ota_version": 2,
            "compressed": True,
            "uploaded_bytes": 120,
        }
    )
    with patch.object(one_device, "_esphome_native_ota_sync", fake_sync):
        res = await handle_esphome(req)

    assert res.status == "SUCCESS", res.message
    assert res.detail["method"] == "esphome_ota"
    assert res.detail["filename"] == "watch-v1.3.0.bin"
    assert res.detail["bytes"] == len(firmware)
    fake_sync.assert_called_once_with("192.168.2.87", 3232, firmware, None)


@pytest.mark.asyncio
async def test_esphome_ota_falls_back_to_web_server_when_tcp_3232_refused(one_device):
    """When TCP port 3232 is refused, ota_update falls back to HTTP POST /update on port 80."""
    from services.execution.esphome_client import ota_update

    firmware = b"\xe9\x04\x02\x20" + b"speaker-firmware"
    fake_http = AsyncMock(return_value={"method": "web_server", "uploaded_bytes": len(firmware)})
    with patch.object(one_device, "_esphome_native_ota_sync", side_effect=ConnectionRefusedError("port 3232 closed")), \
         patch.object(one_device, "_esphome_http_ota_push", fake_http):
        res = await ota_update("office-light", firmware, filename="s3_speaker.bin")

    assert res["method"] == "web_server"
    assert res["bytes"] == len(firmware)
    fake_http.assert_awaited_once_with("192.168.2.87", 80, firmware, filename="s3_speaker.bin", password=None)


@pytest.mark.asyncio
async def test_ota_refuses_non_admin_member(one_device):
    """Non-admin callers cannot trigger OTA updates."""
    import base64
    from services.execution.handlers.esphome import handle_esphome
    from services.execution.schemas import EsphomeRequest, UserContext

    req = EsphomeRequest(
        user_context=UserContext(user="alice", is_admin=False),
        action="ota",
        device="office-light",
        params={"firmware_b64": base64.b64encode(b"firmware").decode("ascii")},
    )
    res = await handle_esphome(req)
    assert res.status == "FAILURE"
    assert "administrator" in res.message.lower()


@pytest.mark.asyncio
async def test_member_guard_allows_safe_controls_and_blocks_restarts(one_device):
    """Members may control lights and media, but maintenance commands (restart/reboot) are denied."""
    from services.execution.handlers.esphome import handle_esphome
    from services.execution.schemas import EsphomeRequest, UserContext

    # Blocked: Restart button
    restart_btn = aioesphomeapi.ButtonInfo(key=10, name="Restart Device", object_id="restart_device")
    api_cls, _ = _mock_api_class([restart_btn])
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls):
        res = await handle_esphome(
            EsphomeRequest(
                user_context=UserContext(user="alice", is_admin=False),
                action="call",
                device="office-light",
                entity="restart_device",
            )
        )
    assert res.status == "FAILURE"
    assert "admin" in res.message.lower()

    # Allowed: Safe light control
    light_ent = aioesphomeapi.LightInfo(key=1, name="Office Light", object_id="office_light")
    api_cls2, inst2 = _mock_api_class([light_ent])
    with patch.object(one_device.aioesphomeapi, "APIClient", api_cls2):
        res2 = await handle_esphome(
            EsphomeRequest(
                user_context=UserContext(user="alice", is_admin=False),
                action="call",
                device="office-light",
                entity="office_light",
                params={"state": True},
            )
        )
    assert res2.status == "SUCCESS"



