"""webostv: state from aiowebostv's tv_state, media services, webostv.* services, WoL."""
from __future__ import annotations

import pytest
from aiowebostv.models import WebOsTvState

from services.homehub.core import DeviceUnavailable, HubError
from services.homehub.drivers import webostv as mod
from services.homehub.drivers.webostv import WebOSDriver
from services.homehub.tests.conftest import make_hub

ENT = "media_player.living_room_tv"


class FakeClient:
    def __init__(self, host, client_key=None, connect_timeout=None):
        self.host, self.client_key = host, client_key
        self.reachable = True
        self._connected = False
        self.calls: list[tuple] = []
        self.callbacks = []
        self.tv_state = WebOsTvState(
            power_state={"state": "Active"}, current_app_id="netflix", volume=17, muted=False,
            apps={"netflix": {"id": "netflix", "title": "Netflix"},
                  "com.webos.app.livetv": {"id": "com.webos.app.livetv", "title": "Live TV"}},
            inputs={"com.webos.app.hdmi1": {"id": "HDMI_1", "appId": "com.webos.app.hdmi1", "label": "HDMI 1"}},
            media_state=[{"playState": "playing"}], is_on=True, is_screen_on=True,
            channels=[{"channelId": "1_2_3", "channelNumber": "7", "channelName": "KNXV"}],
        )

    async def register_state_update_callback(self, cb):
        self.callbacks.append(cb)

    def is_connected(self):
        return self._connected

    async def connect(self):
        if not self.reachable:
            raise OSError("host down")
        self._connected = True
        return True

    async def disconnect(self):
        self._connected = False

    def __getattr__(self, name):
        async def rec(*args):
            self.calls.append((name, *args))
            return {"returnValue": True}
        return rec


def build(reachable=True, options=None):
    hub, entry = make_hub("webostv", {"host": "192.168.2.160", "client_secret": "k" * 32},
                          [{"entity_id": ENT, "unique_id": "0cbb8aba-8c2e-216f-6841-88f0ccffa077"}],
                          options=options)
    drv = WebOSDriver(hub, entry)
    holder = {}

    def factory(host, client_key=None, connect_timeout=None):
        c = FakeClient(host, client_key, connect_timeout)
        c.reachable = reachable
        holder["c"] = c
        return c
    drv.client_factory = factory
    hub.drivers[entry.entry_id] = drv
    return hub, drv, holder


async def test_on_state_with_sources_and_volume():
    hub, drv, h = build()
    await drv.async_setup()
    assert h["c"].client_key == "k" * 32
    st = hub.states.get(ENT)
    assert st.state == "playing"
    a = st.attributes
    assert a["volume_level"] == 0.17
    assert a["source"] == "Netflix"
    assert a["source_list"] == ["HDMI 1", "Live TV", "Netflix"]
    assert a["app_id"] == "netflix"
    for svc in ("command", "button", "select_sound_output"):
        assert ("webostv", svc) in hub.services


async def test_unreachable_tv_is_off_and_setup_does_not_fail():
    hub, drv, h = build(reachable=False)
    await drv.async_setup()
    assert hub.states.get(ENT).state == "off"
    h["c"].reachable = True
    await drv.async_poll()
    assert hub.states.get(ENT).state == "playing"


async def test_media_services():
    hub, drv, h = build()
    await drv.async_setup()
    ent = hub.entities[ENT]
    c = h["c"]
    await drv.async_call(ent, "media_player", "volume_set", {"volume_level": 0.3})
    await drv.async_call(ent, "media_player", "volume_mute", {"is_volume_muted": True})
    await drv.async_call(ent, "media_player", "select_source", {"source": "HDMI 1"})
    await drv.async_call(ent, "media_player", "select_source", {"source": "netflix"})
    await drv.async_call(ent, "media_player", "media_next_track", {})
    await drv.async_call(ent, "media_player", "turn_off", {})
    assert c.calls == [("set_volume", 30), ("set_mute", True), ("set_input", "HDMI_1"), ("launch_app", "netflix"),
                       ("fast_forward",), ("power_off",)]
    c.tv_state.current_app_id = "com.webos.app.livetv"
    await drv.async_call(ent, "media_player", "media_next_track", {})
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "7", "media_content_type": "channel"})
    await drv.async_call(ent, "media_player", "play_media", {"media_content_id": "http://j/stage",
                                                             "media_content_type": "url"})
    assert c.calls[-3:] == [("channel_up",), ("set_channel", "1_2_3"),
                            ("request", "system.launcher/open", {"target": "http://j/stage"})]
    with pytest.raises(HubError):
        await drv.async_call(ent, "media_player", "select_source", {"source": "Nope"})


async def test_webostv_command_and_button_services():
    hub, drv, h = build()
    await drv.async_setup()
    await hub.call_service("webostv", "command", {
        "entity_id": ENT, "command": "system.launcher/launch",
        "payload": {"id": "com.webos.app.browser", "target": "http://j"}})
    await hub.call_service("webostv", "button", {"entity_id": ENT, "button": "HOME"})
    assert h["c"].calls == [("request", "system.launcher/launch", {"id": "com.webos.app.browser", "target": "http://j"}),
                            ("button", "HOME")]
    res = await hub.call_service("webostv", "command", {"entity_id": ENT, "command": "audio/getVolume"},
                                 return_response=True)
    assert res["response"] == {"returnValue": True}


async def test_service_on_an_off_tv_reports_unavailable():
    hub, drv, _h = build(reachable=False)
    await drv.async_setup()
    with pytest.raises(DeviceUnavailable):
        await hub.call_service("webostv", "button", {"entity_id": ENT, "button": "HOME"})


async def test_turn_on_uses_wol_from_options_mac(monkeypatch):
    sent = []

    async def wol(macs, broadcast="255.255.255.255"):
        sent.extend(macs)
        return len(macs)
    monkeypatch.setattr(mod, "wake_on_lan", wol)
    monkeypatch.setattr("services.homehub.drivers.media_common.arp_mac", lambda ip, path="": None)
    hub, drv, _h = build(reachable=False, options={"mac": "A8:23:FE:01:02:03"})
    await drv.async_setup()
    await drv.async_call(hub.entities[ENT], "media_player", "turn_on", {})
    assert sent == ["a8:23:fe:01:02:03"]
    hub2, drv2, _ = build(reachable=False)
    await drv2.async_setup()
    with pytest.raises(HubError, match="no MAC"):
        await drv2.async_call(hub2.entities[ENT], "media_player", "turn_on", {})


def test_a_failed_connection_reads_as_disconnected_not_an_error():
    from services.homehub.drivers.webostv import WebOSDriver

    class Raising:
        def is_connected(self):
            raise OSError(113, "Connect call failed")

    drv = WebOSDriver.__new__(WebOSDriver)
    drv.client = Raising()
    assert drv.connected is False
