"""samsungtv: legacy and websocket bridges, UPnP volume, WoL, remote keys."""
from __future__ import annotations

import json
from typing import ClassVar

import pytest

from services.homehub.core import DeviceEntry, HubError, ServiceNotFound
from services.homehub.drivers import samsungtv as mod
from services.homehub.drivers.media_common import F
from services.homehub.drivers.samsungtv import HA_LEGACY_ID, HA_NAME, LegacyBridge, SamsungTVDriver, WebsocketBridge, samsung_key
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.media_fakes import AVT, RC, FakeHttp, device_xml, soap_reply, soap_router

LEGACY = {"host": "192.168.2.82", "mac": "c4:73:1e:18:5b:54", "method": "legacy", "model": "UN46EH5300",
          "name": "UN46EH5300", "port": 55000, "manufacturer": "Samsung Electronics",
          "ssdp_main_tv_agent_location": "http://192.168.2.82:7676/smp_10_",
          "ssdp_rendering_control_location": "http://192.168.2.82:7676/smp_18_"}
WS = {"host": "192.168.2.115", "mac": "38:68:a4:9a:01:a9", "method": "websocket", "model": "QN75Q900RBFXZA",
      "port": 8002, "token": "12345678", "ssdp_main_tv_agent_location": None,
      "ssdp_rendering_control_location": "http://192.168.2.115:9197/dmr"}


class FakeLegacyRemote:
    instances: ClassVar[list[FakeLegacyRemote]] = []

    def __init__(self, host, *, port, name, description, remote_id, timeout):
        self.args = dict(host=host, port=port, name=name, description=description, remote_id=remote_id)
        self.keys: list[str] = []
        self.closed = False
        FakeLegacyRemote.instances.append(self)

    async def open(self):
        pass

    async def control(self, key):
        self.keys.append(key)

    async def close(self):
        self.closed = True


class FakeWSRemote:
    instances: ClassVar[list[FakeWSRemote]] = []

    def __init__(self, host, port, token, timeout, key_press_delay, name):
        self.args = dict(host=host, port=port, token=token, name=name)
        self.token = token
        self.sent: list[dict] = []
        self.alive = False
        FakeWSRemote.instances.append(self)

    async def open(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    async def send_commands(self, commands, key_press_delay=None):
        self.sent.extend(c.as_dict() if hasattr(c, "as_dict") else c for c in commands)

    async def close(self):
        self.alive = False


def rc_http(http: FakeHttp, base: str, volume="30", mute="0", with_avt=False):
    services = [(RC, "/upnp/control/RenderingControl1")]
    if with_avt:
        services.append((AVT, "/upnp/control/AVTransport1"))
    http.route("GET", base, (200, device_xml(services)))
    sent: list[str] = []

    def rec(reply):
        def h(body):
            sent.append(body)
            return reply
        return h

    http.route("POST", "/RenderingControl1", soap_router({
        "GetVolume": soap_reply("GetVolume", RC, CurrentVolume=volume),
        "GetMute": soap_reply("GetMute", RC, CurrentMute=mute),
        "SetVolume": rec(soap_reply("SetVolume", RC)),
        "SetMute": rec(soap_reply("SetMute", RC)),
    }))
    http.route("POST", "/AVTransport1", soap_router({
        "SetAVTransportURI": rec(soap_reply("SetAVTransportURI", AVT)),
        "Play": rec(soap_reply("Play", AVT)),
    }))
    return sent


def build(data, on=True, monkeypatch=None):
    hub, entry = make_hub("samsungtv", data, [
        {"entity_id": "media_player.tv", "unique_id": "0a21fe80-00aa-1000-9b13-f47b5e72fe9c"},
        {"entity_id": "remote.tv", "unique_id": "0a21fe80-00aa-1000-9b13-f47b5e72fe9c"},
    ])
    hub.devices["dev1"] = DeviceEntry(id="dev1", name="TV", connections=[["mac", "7e:20:51:5a:1a:89"],
                                                                         ["mac", data["mac"]]])
    drv = SamsungTVDriver(hub, entry)
    http = FakeHttp()
    drv.http = http
    if drv.upnp:
        drv.upnp.http = http
    if isinstance(drv.bridge, WebsocketBridge):
        drv.bridge.http = http
        drv.bridge.remote_cls = FakeWSRemote
        http.route("GET", ":8001/api/v2/", (200, json.dumps({"device": {"PowerState": "on" if on else "standby"}})))
    else:
        drv.bridge.remote_cls = FakeLegacyRemote

        async def is_on():
            return on
        drv.bridge.is_on = is_on
    return hub, drv, http


async def test_legacy_on_with_upnp_volume_and_keys():
    FakeLegacyRemote.instances.clear()
    hub, drv, http = build(LEGACY)
    sent = rc_http(http, "/smp_18_", volume="42", mute="1")
    await drv.async_setup()
    st = hub.states.get("media_player.tv")
    assert st.state == "on"
    assert st.attributes["volume_level"] == pytest.approx(0.42)
    assert st.attributes["is_volume_muted"] is True
    assert st.attributes["source_list"] == ["TV", "HDMI"]
    assert st.attributes["supported_features"] & F.TURN_ON and st.attributes["supported_features"] & F.VOLUME_SET
    assert hub.states.get("remote.tv").state == "on"

    await drv.async_call(hub.entities["remote.tv"], "remote", "send_command",
                         {"command": ["Home", "KEY_ENTER", "volume_up"], "delay_secs": 0})
    r = FakeLegacyRemote.instances[-1]
    assert r.args["name"] == HA_NAME and r.args["remote_id"] == HA_LEGACY_ID and r.args["port"] == 55000
    keys = [k for i in FakeLegacyRemote.instances for k in i.keys]
    assert keys == ["KEY_HOME", "KEY_ENTER", "KEY_VOLUP"]
    assert all(i.closed for i in FakeLegacyRemote.instances)

    await drv.async_call(hub.entities["media_player.tv"], "media_player", "volume_set", {"volume_level": 0.25})
    assert "<DesiredVolume>25</DesiredVolume>" in sent[-1]
    await drv.async_call(hub.entities["media_player.tv"], "media_player", "select_source", {"source": "HDMI"})
    assert FakeLegacyRemote.instances[-1].keys == ["KEY_HDMI"]


async def test_legacy_turn_off_uses_poweroff_and_holds_off():
    FakeLegacyRemote.instances.clear()
    hub, drv, http = build(LEGACY)
    rc_http(http, "/smp_18_")
    await drv.async_setup()
    await drv.async_call(hub.entities["media_player.tv"], "media_player", "turn_off", {})
    assert FakeLegacyRemote.instances[-1].keys == ["KEY_POWEROFF"]
    assert hub.states.get("media_player.tv").state == "off"  # still answering, but HA's grace window holds it off


async def test_off_tv_publishes_off_without_volume():
    hub, drv, http = build(LEGACY, on=False)
    rc_http(http, "/smp_18_")
    await drv.async_setup()
    st = hub.states.get("media_player.tv")
    assert st.state == "off"
    assert "volume_level" not in st.attributes


async def test_turn_on_sends_wol_to_every_mac(monkeypatch):
    hub, drv, http = build(LEGACY, on=False)
    rc_http(http, "/smp_18_")
    await drv.async_setup()
    sent: list[str] = []
    monkeypatch.setattr(mod, "known_macs", lambda d, e=None, h=None: ["c4:73:1e:18:5b:54", "7e:20:51:5a:1a:89"])

    async def fake_wol(macs, broadcast="255.255.255.255"):
        sent.extend(macs)
        return len(sent)
    monkeypatch.setattr(mod, "wake_on_lan", fake_wol)
    await drv.async_call(hub.entities["media_player.tv"], "media_player", "turn_on", {})
    assert sent == ["c4:73:1e:18:5b:54", "7e:20:51:5a:1a:89"]


async def test_websocket_power_state_keys_app_and_url():
    FakeWSRemote.instances.clear()
    hub, drv, http = build(WS)
    sent = rc_http(http, "/dmr", with_avt=True)
    await drv.async_setup()
    assert hub.states.get("media_player.tv").state == "on"
    ent = hub.entities["media_player.tv"]
    await drv.async_call(ent, "media_player", "media_pause", {})
    r = FakeWSRemote.instances[-1]
    assert r.args == {"host": "192.168.2.115", "port": 8002, "token": "12345678", "name": HA_NAME}
    assert r.sent[-1]["params"]["DataOfCmd"] == "KEY_PAUSE"
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "111299001912",
                                                             "media_content_type": "app"})
    assert r.sent[-1]["params"]["event"] == "ed.apps.launch"
    assert r.sent[-1]["params"]["data"]["appId"] == "111299001912"
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "7", "media_content_type": "channel"})
    assert [c["params"]["DataOfCmd"] for c in r.sent[-2:]] == ["KEY_7", "KEY_ENTER"]
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "http://j/a.mp3",
                                                             "media_content_type": "music"})
    assert any("SetAVTransportURI" in b for b in sent)
    assert len(FakeWSRemote.instances) == 1  # one live connection is reused
    await drv.async_call(ent, "media_player", "turn_off", {})
    assert r.sent[-1]["params"]["DataOfCmd"] == "KEY_POWER"


async def test_websocket_standby_is_off():
    hub, drv, http = build(WS, on=False)
    rc_http(http, "/dmr")
    await drv.async_setup()
    assert hub.states.get("media_player.tv").state == "off"


async def test_errors():
    hub, drv, http = build(LEGACY)
    rc_http(http, "/smp_18_")
    await drv.async_setup()
    ent = hub.entities["media_player.tv"]
    with pytest.raises(HubError):
        await drv.async_call(ent, "media_player", "select_source", {"source": "Netflix"})
    with pytest.raises(ServiceNotFound):
        await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "x", "media_content_type": "app"})
    with pytest.raises(ServiceNotFound):
        await drv.async_call(ent, "media_player", "shuffle_set", {})


def test_key_names():
    assert samsung_key("Home") == "KEY_HOME"
    assert samsung_key("VolumeUp") == "KEY_VOLUP"
    assert samsung_key("volume_up") == "KEY_VOLUP"
    assert samsung_key("KEY_CHUP") == "KEY_CHUP"
    assert samsung_key("Return") == "KEY_RETURN"
    assert samsung_key("3d") == "KEY_3D"
    assert LegacyBridge.power_key == "KEY_POWEROFF"
