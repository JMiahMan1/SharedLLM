"""androidtv_remote: cert handover, on/off/app/volume state, keys, launch, remote.send_command."""
from __future__ import annotations

import os
import stat

import pytest
from androidtvremote2 import CannotConnect, InvalidAuth

from services.homehub.core import DeviceUnavailable, HubError, ServiceNotFound
from services.homehub.drivers.androidtv_remote import AndroidTVRemoteDriver, key_code
from services.homehub.drivers.media_common import F
from services.homehub.tests.conftest import make_hub

MP = "media_player.office_tv"
REMOTE = "remote.office_tv_remote"
MAC = "30:95:87:15:e7:6d"
CERT = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nBBBB\n-----END PRIVATE KEY-----\n"


class FakeRemote:
    def __init__(self, client_name, certfile, keyfile, host):
        self.client_name, self.certfile, self.keyfile, self.host = client_name, certfile, keyfile, host
        with open(certfile) as fh:
            self.cert_seen = fh.read()
        self.cert_mode = stat.S_IMODE(os.stat(keyfile).st_mode)
        self.is_on = True
        self.current_app = "com.netflix.ninja"
        self.volume_info = {"level": 10, "max": 100, "muted": False}
        self.keys: list[tuple[str, str]] = []
        self.launched: list[str] = []
        self.cbs: dict[str, list] = {"on": [], "app": [], "vol": [], "avail": []}
        self.fail: Exception | None = None
        self.reconnecting = False

    def add_is_on_updated_callback(self, cb):
        self.cbs["on"].append(cb)

    def add_current_app_updated_callback(self, cb):
        self.cbs["app"].append(cb)

    def add_volume_info_updated_callback(self, cb):
        self.cbs["vol"].append(cb)

    def add_is_available_updated_callback(self, cb):
        self.cbs["avail"].append(cb)

    async def async_connect(self):
        if self.fail:
            raise self.fail

    def keep_reconnecting(self):
        self.reconnecting = True

    def disconnect(self):
        pass

    def send_key_command(self, key, direction="SHORT"):
        self.keys.append((key, direction))

    def send_launch_app_command(self, link):
        self.launched.append(link)


def build(fail=None, data_extra=None):
    data = {"host": "192.168.2.88", "mac": MAC.upper(), "name": "Office TV", "cert_pem": CERT, "key_pem": KEY}
    data.update(data_extra or {})
    hub, entry = make_hub("androidtv_remote", data, [{"entity_id": MP, "unique_id": MAC},
                                                     {"entity_id": REMOTE, "unique_id": MAC}])
    drv = AndroidTVRemoteDriver(hub, entry)
    holder = {}

    def factory(*a):
        r = FakeRemote(*a)
        r.fail = fail
        holder["r"] = r
        return r
    drv.remote_factory = factory
    return hub, drv, holder


async def test_setup_uses_ha_cert_and_publishes_state():
    hub, drv, h = build()
    await drv.async_setup()
    r = h["r"]
    assert r.client_name == "Home Assistant"
    assert r.cert_seen == CERT
    assert r.cert_mode == 0o600
    assert r.reconnecting
    st = hub.states.get(MP)
    assert st.state == "on"
    assert st.attributes["app_id"] == "com.netflix.ninja"
    assert st.attributes["app_name"] == "Netflix"
    assert st.attributes["volume_level"] == 0.1
    assert st.attributes["supported_features"] & F.VOLUME_SET
    assert hub.states.get(REMOTE).attributes["current_activity"] == "Netflix"
    r.is_on = False
    for cb in r.cbs["on"]:
        cb(False)
    assert hub.states.get(MP).state == "off"
    await drv.async_unload()
    assert not os.path.exists(r.keyfile)


async def test_missing_cert_is_a_setup_error():
    _hub, drv, _ = build(data_extra={"cert_pem": None})
    with pytest.raises(HubError, match="cert_pem"):
        await drv.async_setup()


async def test_invalid_auth_raises_but_unreachable_is_unavailable():
    hub, drv, _ = build(fail=InvalidAuth("pair"))
    with pytest.raises(HubError, match="pairing"):
        await drv.async_setup()
    hub, drv, h = build(fail=CannotConnect("down"))
    await drv.async_setup()
    assert hub.states.get(MP).state == "unavailable"
    with pytest.raises(DeviceUnavailable):
        await drv.async_call(hub.entities[MP], "media_player", "media_pause", {})
    h["r"].fail = None
    await drv.async_poll()
    assert hub.states.get(MP).state == "on"


async def test_media_and_remote_services():
    hub, drv, h = build()
    await drv.async_setup()
    r = h["r"]
    mp, rem = hub.entities[MP], hub.entities[REMOTE]
    await drv.async_call(mp, "media_player", "media_pause", {})
    await drv.async_call(mp, "media_player", "turn_on", {})  # already on: nothing sent
    await drv.async_call(mp, "media_player", "turn_off", {})
    await drv.async_call(mp, "media_player", "volume_set", {"volume_level": 0.13})
    await drv.async_call(mp, "media_player", "volume_mute", {"is_volume_muted": True})
    assert [k for k, _ in r.keys] == ["MEDIA_PAUSE", "POWER", "VOLUME_UP", "VOLUME_UP", "VOLUME_UP", "VOLUME_MUTE"]
    await drv.async_call(mp, "media_player", "play_media", {"media_content_id": "https://www.youtube.com/watch?v=x",
                                                           "media_content_type": "url"})
    await drv.async_call(rem, "remote", "turn_on", {"activity": "YouTube"})
    assert r.launched == ["https://www.youtube.com/watch?v=x", "com.google.android.youtube.tv"]
    r.keys.clear()
    await drv.async_call(rem, "remote", "send_command", {"command": ["HOME", "dpad_up", "KEYCODE_BACK"],
                                                         "delay_secs": 0})
    assert [k for k, _ in r.keys] == ["HOME", "DPAD_UP", "BACK"]
    r.keys.clear()
    await drv.async_call(rem, "remote", "send_command", {"command": "HOME", "hold_secs": 0.01, "delay_secs": 0})
    assert r.keys == [("HOME", "START_LONG"), ("HOME", "END_LONG")]
    with pytest.raises(ServiceNotFound):
        await drv.async_call(mp, "media_player", "play_media", {"media_content_id": "http://x.mp3",
                                                               "media_content_type": "music"})


def test_key_codes():
    assert key_code("home") == "HOME"
    assert key_code("KEYCODE_DPAD_UP") == "DPAD_UP"
    assert key_code("MEDIA_PLAY") == "MEDIA_PLAY"
    assert key_code("ok") == "DPAD_CENTER"
