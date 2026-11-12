"""cast: discovery filtered by UUID, state from cast/media status, commands in threads."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import ClassVar
from uuid import UUID

import pytest
from pychromecast.controllers.media import MediaImage, MediaStatus

from services.homehub.core import DeviceUnavailable, ServiceNotFound
from services.homehub.drivers import cast as mod
from services.homehub.drivers.cast import CastDriver
from services.homehub.drivers.media_common import F
from services.homehub.tests.conftest import make_hub

OFFICE = "81dfea8f-d1d7-a0f2-3d19-5597d16ca2be"
BEDROOM = "16b032a3-f048-710f-0506-ad3136885e30"
STRANGER = "00000000-0000-0000-0000-000000000001"


class FakeBrowser:
    instances: ClassVar[list[FakeBrowser]] = []

    def __init__(self, listener, zconf, hosts):
        self.listener, self.zconf, self.hosts = listener, zconf, hosts
        self.devices: dict[UUID, SimpleNamespace] = {}
        self.started = self.stopped = False
        FakeBrowser.instances.append(self)

    def start_discovery(self):
        self.started = True

    def stop_discovery(self):
        self.stopped = True

    def appear(self, key: str, name: str):
        u = UUID(key)
        self.devices[u] = SimpleNamespace(uuid=u, friendly_name=name, host="192.168.2.50", port=8009)
        self.listener.add_cast(u, "svc")


class FakeMC:
    def __init__(self):
        self.status = MediaStatus()
        self.calls: list[tuple] = []
        self.listeners = []

    def register_status_listener(self, listener):
        self.listeners.append(listener)

    def __getattr__(self, name):
        def rec(*a, **kw):
            self.calls.append((name, a, kw))
        return rec


class FakeCast:
    def __init__(self, info):
        self.info = info
        self.status = SimpleNamespace(volume_level=0.4, volume_muted=False, app_id="CC1AD845",
                                      display_name="Default Media Receiver", volume_control_type="attenuation")
        self.media_controller = FakeMC()
        self.calls: list[tuple] = []
        self.listeners = []
        self.idle = False

    @property
    def is_idle(self):
        return self.idle

    @property
    def app_id(self):
        return self.status.app_id

    def register_status_listener(self, listener):
        self.listeners.append(listener)

    def register_connection_listener(self, listener):
        self.listeners.append(listener)

    def start(self):
        self.calls.append(("start",))

    def __getattr__(self, name):
        def rec(*a, **kw):
            self.calls.append((name, *a))
        return rec


@pytest.fixture
def setup(monkeypatch):
    FakeBrowser.instances.clear()
    monkeypatch.setattr(mod, "CastBrowser", FakeBrowser)
    hub, entry = make_hub("cast", {"known_hosts": ["192.168.1.213"], "uuid": [], "ignore_cec": [], "user_id": "u"}, [
        {"entity_id": "media_player.office_tv_chrome", "unique_id": OFFICE},
        {"entity_id": "media_player.master_bedroom_tv", "unique_id": BEDROOM},
    ])
    drv = CastDriver(hub, entry)
    drv.zeroconf_factory = lambda: "zc"
    made: dict[str, FakeCast] = {}

    def factory(info, zconf):
        c = FakeCast(info)
        made[str(info.uuid)] = c
        return c
    drv.cast_factory = factory
    return hub, drv, made


async def settle():
    for _ in range(20):
        await asyncio.sleep(0.01)


async def test_discovery_connects_only_wanted_uuids(setup):
    hub, drv, made = setup
    await drv.async_setup()
    b = FakeBrowser.instances[-1]
    assert b.started and b.hosts == ["192.168.1.213"]
    assert hub.states.get("media_player.office_tv_chrome").state == "unavailable"
    b.appear(OFFICE, "Office TV")
    b.appear(STRANGER, "Neighbour")
    await settle()
    assert set(made) == {OFFICE}
    st = hub.states.get("media_player.office_tv_chrome")
    assert st.state == "on"
    assert st.attributes["volume_level"] == 0.4
    assert st.attributes["app_name"] == "Default Media Receiver"
    assert st.attributes["supported_features"] & F.PLAY_MEDIA
    assert hub.states.get("media_player.master_bedroom_tv").state == "unavailable"
    await drv.async_unload()
    assert b.stopped


async def test_media_status_states_and_attributes(setup):
    hub, drv, made = setup
    await drv.async_setup()
    FakeBrowser.instances[-1].appear(OFFICE, "Office TV")
    await settle()
    cc = made[OFFICE]
    ms = cc.media_controller.status
    ms.player_state = "PLAYING"
    ms.content_id = "http://j/song.mp3"
    ms.content_type = "audio/mpeg"
    ms.duration = 200.0
    ms.current_time = 12.0
    ms.supported_media_commands = 1 | 2 | 64 | 128  # pause, seek, queue next, queue prev
    ms.media_metadata = {"metadataType": 3, "title": "Song", "artist": "Band", "albumName": "LP",
                         "images": [{"url": "http://img/x.jpg"}]}
    # listeners fire from pychromecast threads
    await asyncio.to_thread(cc.listeners[0].new_media_status, ms)
    await settle()
    st = hub.states.get("media_player.office_tv_chrome")
    assert st.state == "playing"
    a = st.attributes
    assert a["media_title"] == "Song" and a["media_artist"] == "Band" and a["media_album_name"] == "LP"
    assert a["media_duration"] == 200.0
    assert a["entity_picture"] == "http://img/x.jpg"
    assert a["media_content_type"] == "music"
    assert a["supported_features"] & F.PAUSE and a["supported_features"] & F.NEXT_TRACK
    ms.player_state = "PAUSED"
    drv.publish_key(OFFICE)
    assert hub.states.get("media_player.office_tv_chrome").state == "paused"
    ms.player_state = "UNKNOWN"
    cc.idle = True
    drv.publish_key(OFFICE)
    assert hub.states.get("media_player.office_tv_chrome").state == "off"
    await asyncio.to_thread(cc.listeners[-1].new_connection_status, SimpleNamespace(status="LOST"))
    await settle()
    assert hub.states.get("media_player.office_tv_chrome").state == "unavailable"


async def test_commands(setup):
    hub, drv, made = setup
    await drv.async_setup()
    FakeBrowser.instances[-1].appear(OFFICE, "Office TV")
    await settle()
    cc = made[OFFICE]
    ent = hub.entities["media_player.office_tv_chrome"]
    await drv.async_call(ent, "media_player", "play_media", {
        "media_content_id": "http://jarvis/announce.wav", "media_content_type": "music",
        "extra": {"title": "Hello"}})
    name, args, kw = cc.media_controller.calls[-1]
    assert name == "play_media" and args == ("http://jarvis/announce.wav", "audio/wav")
    assert kw["title"] == "Hello" and kw["stream_type"] == "BUFFERED"
    await drv.async_call(ent, "media_player", "volume_set", {"volume_level": 0.25})
    await drv.async_call(ent, "media_player", "volume_mute", {"is_volume_muted": True})
    await drv.async_call(ent, "media_player", "turn_off", {})
    await drv.async_call(ent, "media_player", "turn_on", {})
    assert cc.calls[-4:] == [("set_volume", 0.25), ("set_volume_muted", True), ("quit_app",), ("start_app", "CC1AD845")]
    await drv.async_call(ent, "media_player", "media_pause", {})
    await drv.async_call(ent, "media_player", "media_seek", {"seek_position": 30})
    assert [c[0] for c in cc.media_controller.calls[-2:]] == ["pause", "seek"]
    with pytest.raises(ServiceNotFound):
        await drv.async_call(ent, "media_player", "select_source", {"source": "x"})
    with pytest.raises(DeviceUnavailable):
        await drv.async_call(hub.entities["media_player.master_bedroom_tv"], "media_player", "media_play", {})


async def test_cast_app_quick_play(setup, monkeypatch):
    hub, drv, _made = setup
    await drv.async_setup()
    FakeBrowser.instances[-1].appear(OFFICE, "Office TV")
    await settle()
    seen = []
    import pychromecast.quick_play as qp
    monkeypatch.setattr(qp, "quick_play", lambda cast, app, data: seen.append((app, data)))
    await drv.async_call(hub.entities["media_player.office_tv_chrome"], "media_player", "play_media", {
        "media_content_id": json.dumps({"app_name": "youtube", "media_id": "dQw4w9WgXcQ"}),
        "media_content_type": "cast"})
    assert seen == [("youtube", {"media_id": "dQw4w9WgXcQ"})]


def test_media_image_type_is_what_pychromecast_builds():
    ms = MediaStatus()
    ms.media_metadata = {"images": [{"url": "u"}]}
    assert isinstance(ms.images[0], MediaImage)
