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
    hub, entry = make_hub("webostv", {"host": "192.0.2.160", "client_secret": "k" * 32},
                          [{"entity_id": ENT, "unique_id": "00000000-0000-4000-8000-00000000c003"}],
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


@pytest.mark.parametrize("data", [
    {"media_content_id": "http://hub.example.com:8123/api/tts_proxy/abc.wav", "media_content_type": "music"},
    {"media_content_id": "http://192.0.2.5:8888/media/tts-1a2b3c4d?user=u&mt=t", "media_content_type": "url"},
    {"media_content_id": "http://192.0.2.5/chime.mp3", "media_content_type": "url"},
    {"media_content_id": "http://j/stage", "media_content_type": "url", "announce": True},
])
async def test_audio_and_announcements_are_refused_not_opened_in_the_browser(data):
    """The TV opened any URL in its web browser: a clip there is a blank page over the picture and no sound.
    The error names the TV's own AirPlay / Music Assistant player, found by the MAC both devices report."""
    from services.homehub.core import DeviceEntry, EntityEntry

    hub, drv, h = build()
    hub.devices["dev1"].connections = [["mac", "02:00:00:00:01:60"]]
    hub.devices["ma1"] = DeviceEntry(id="ma1", name="TV AirPlay", connections=[["mac", "02:00:00:00:01:60"]])
    hub.entities["media_player.tv_airplay"] = EntityEntry(
        entity_id="media_player.tv_airplay", platform="music_assistant", unique_id="ap0200000001",
        device_id="ma1", config_entry_id="ma")
    await drv.async_setup()
    with pytest.raises(HubError, match=r"cannot play audio.*media_player\.tv_airplay"):
        await drv.async_call(hub.entities[ENT], "media_player", "play_media", data)
    assert not any(c[0] == "request" for c in h["c"].calls)


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


def test_a_connected_tv_teaches_the_hub_its_mac(tmp_path):
    from services.homehub.drivers import media_common
    from services.homehub.tests.conftest import make_hub

    arp = tmp_path / "arp"
    arp.write_text("IP address HW type Flags HW address Mask Device\n"
                   "10.0.0.7 0x1 0x2 aa:bb:cc:dd:ee:ff * eth0\n")
    hub, entry = make_hub("webostv", data={"host": "10.0.0.7"})

    class D:
        pass

    drv = D()
    drv.hub, drv.entry = hub, entry
    drv.log = __import__("logging").getLogger("t")
    assert media_common.remember_mac(drv, "10.0.0.7", str(arp)) == "aa:bb:cc:dd:ee:ff"
    assert entry.options["mac"] == "aa:bb:cc:dd:ee:ff"
    entry.options["mac"] = "11:22:33:44:55:66"  # an operator's value is never overwritten
    assert media_common.remember_mac(drv, "10.0.0.7", str(arp)) is None
    assert entry.options["mac"] == "11:22:33:44:55:66"


async def test_every_lg_tv_gets_a_remote_with_the_standard_keys():
    hub, drv, _holder = build()
    await drv.async_setup()
    remotes = [e for e in hub.entities.values() if e.domain == "remote"]
    assert len(remotes) == 1
    remote = remotes[0]
    media = hub.entities[ENT]
    assert remote.unique_id == media.unique_id and remote.device_id == media.device_id
    st = hub.states.get(remote.entity_id)
    assert st.state == "on"
    cmds = st.attributes["command_list"]
    for key in ("up", "select", "back", "home", "volume_up", "mute", "channel_up", "power", "5", "NETFLIX"):
        assert key in cmds
    assert "input" not in cmds  # webOS inputs are sources
    await drv.async_setup()  # a second setup registers nothing new
    assert len([e for e in hub.entities.values() if e.domain == "remote"]) == 1


async def test_lg_remote_send_command_maps_standard_and_native_names():
    hub, drv, holder = build()
    await drv.async_setup()
    remote = next(e for e in hub.entities.values() if e.domain == "remote")
    await drv.async_call(remote, "remote", "send_command",
                         {"command": ["up", "select", "volume_down", "7", "RED", "exit"], "delay_secs": 0})
    buttons = [c[1] for c in holder["c"].calls if c[0] == "button"]
    assert buttons == ["UP", "ENTER", "VOLUMEDOWN", "7", "RED", "EXIT"]
    await drv.async_call(remote, "remote", "send_command", {"command": "power_off"})
    assert ("power_off",) in holder["c"].calls
    with pytest.raises(HubError):
        await drv.async_call(remote, "remote", "send_command", {"command": []})


async def test_lg_remote_power_on_is_wake_on_lan(monkeypatch):
    sent = []

    async def fake_wol(macs, broadcast="255.255.255.255"):
        sent.extend(macs)
        return len(list(macs))
    monkeypatch.setattr(mod, "wake_on_lan", fake_wol)
    hub, drv, _holder = build(reachable=False, options={"mac": "02:00:00:00:01:09"})
    await drv.async_setup()
    remote = next(e for e in hub.entities.values() if e.domain == "remote")
    assert hub.states.get(remote.entity_id).state == "off"
    await drv.async_call(remote, "remote", "send_command", {"command": "power_on"})
    assert "02:00:00:00:01:09" in sent
