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
