# services/execution/tests/test_esphome.py
"""Unit tests for direct ESPHome native-API control.

All device connections and the Identity settings fetch are mocked —
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


# ─── configure_jarvis: Jarvis hands a device its own endpoint ──────────────────

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


# ─── Companion pairing ─────────────────────────────────────────────────────────

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
                     model="esp32-s3-devkitc-1", esphome_version="2026.9.0")
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
