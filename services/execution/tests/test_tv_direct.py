"""Remote control without Home Assistant (HA fallback, step 3).

With HA down, transport commands go to the device in its own protocol: Roku
ECP, LG webOS with the stored pairing key, Samsung with the stored token, Cast,
Music Assistant for speaker queues, and Wake-on-LAN for power on.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.execution import ha_health, tv_direct
from services.execution.handlers import mass_client, media, tv_stage

HA = "http://ha.local:8123"


def _dev(platform, **over):
    base = {"entity_id": "media_player.den_tv", "platform": platform, "ip": "192.168.2.60",
            "mac": "", "name": "Den TV", "webos_key": ""}
    base.update(over)
    return base


@pytest.mark.parametrize(
    ("record", "platform"),
    [
        ({"integration": "roku"}, "roku"),
        ({"integration": "webostv"}, "webos"),
        ({"integration": "samsungtv"}, "samsung"),
        ({"integration": "cast"}, "cast"),
        ({"integration": "androidtv"}, "android_tv"),
        ({"integration": "broadcast_radio"}, "unknown"),
        (None, "unknown"),
    ],
)
def test_the_platform_comes_from_the_device_registry(record, platform):
    assert tv_direct.platform_of(record) == platform


async def test_locate_prefers_the_tv_stages_remembered_lg_pairing(monkeypatch):
    from services.execution import device_registry

    monkeypatch.setattr(device_registry, "get_device", AsyncMock(return_value={"integration": "webostv", "ip": "10.0.0.9", "mac": "aa:bb"}))
    monkeypatch.setattr(tv_stage, "_webos_store", lambda: {"media_player.den_tv": {"host": "192.168.2.61", "client_key": "K"}})
    dev = await tv_direct.locate("media_player.den_tv")
    assert (dev["platform"], dev["ip"], dev["webos_key"], dev["mac"]) == ("webos", "192.168.2.61", "K", "aa:bb")


async def test_a_stale_registry_address_is_never_used(monkeypatch):
    from services.execution import device_registry

    monkeypatch.setattr(device_registry, "get_device", AsyncMock(return_value={"integration": "roku", "ip": "10.0.0.9", "ip_stale": 1}))
    monkeypatch.setattr(tv_stage, "_webos_store", dict)
    monkeypatch.setattr(tv_stage, "_samsung_hosts", dict)
    assert (await tv_direct.locate("media_player.den_tv"))["ip"] == ""


class _Post:
    def __init__(self, sink):
        self.sink = sink

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, **kw):
        self.sink.append(url)
        return _Resp()


class _Resp:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_a_roku_gets_an_ecp_keypress(monkeypatch):
    sent = []
    monkeypatch.setattr(tv_direct, "locate", AsyncMock(return_value=_dev("roku")))
    monkeypatch.setattr(tv_direct.aiohttp, "ClientSession", _Post(sent))
    result = await tv_direct.transport("media_player.den_tv", "home", SimpleNamespace())
    assert result["ok"] and result["route"] == "roku_ecp"
    assert sent == ["http://192.168.2.60:8060/keypress/Home"]


async def test_an_unpaired_lg_is_reported_not_prompted(monkeypatch):
    monkeypatch.setattr(tv_direct, "locate", AsyncMock(return_value=_dev("webos")))
    result = await tv_direct.transport("media_player.den_tv", "pause", SimpleNamespace())
    assert not result["ok"] and "never been paired" in result["message"]


async def test_a_samsung_gets_its_key(monkeypatch):
    keys = []
    monkeypatch.setattr(tv_direct, "locate", AsyncMock(return_value=_dev("samsung")))
    monkeypatch.setattr(tv_direct, "_samsung_sync", lambda dev, key: keys.append(key))
    result = await tv_direct.transport("media_player.den_tv", "back", SimpleNamespace())
    assert result["ok"] and keys == ["KEY_RETURN"]


async def test_power_on_wakes_by_mac(monkeypatch):
    woken = []
    monkeypatch.setattr(tv_direct, "locate", AsyncMock(return_value=_dev("samsung", mac="aa:bb:cc:dd:ee:ff")))
    monkeypatch.setattr(tv_direct, "wake_on_lan", lambda mac: woken.append(mac) or True)
    result = await tv_direct.transport("media_player.den_tv", "power_on", SimpleNamespace())
    assert result["route"] == "wake_on_lan" and woken == ["aa:bb:cc:dd:ee:ff"]


async def test_a_speaker_pauses_through_music_assistant(monkeypatch):
    sent = {}
    monkeypatch.setattr(tv_direct, "locate", AsyncMock(return_value=_dev("unknown", entity_id="media_player.kitchen", ip="")))
    monkeypatch.setattr(mass_client, "get_players", AsyncMock(return_value=[{"player_id": "ma_k", "display_name": "Kitchen"}]))

    async def cmd(url, token, command, args=None, **kw):
        sent["cmd"] = (command, args)

    monkeypatch.setattr(mass_client, "ma_command", cmd)
    ctx = SimpleNamespace(mass_url="http://ma:8095", mass_token="m")
    result = await tv_direct.transport("media_player.kitchen", "pause", ctx)
    assert result["ok"] and result["route"] == "mass_direct"
    assert sent["cmd"] == ("players/cmd_pause", {"player_id": "ma_k"})


async def test_with_ha_down_the_transport_route_goes_direct(monkeypatch):
    ha_health.record_unreachable(HA, "refused")
    ha_health.record_unreachable(HA, "refused")
    via_ha = AsyncMock()
    monkeypatch.setattr(media, "_transport_via_ha", via_ha)
    monkeypatch.setattr(
        tv_direct, "transport", AsyncMock(return_value={"ok": True, "message": "Sent Home to Den TV directly.", "route": "roku_ecp"})
    )
    req = SimpleNamespace(user_context=SimpleNamespace(ha_url=HA), entity_id="den_tv", command="home")
    result = await media.handle_media_transport(req)
    assert result.status == "SUCCESS" and result.detail == {"route": "roku_ecp"}
    via_ha.assert_not_awaited()
    assert tv_direct.transport.await_args.args[0] == "media_player.den_tv"
