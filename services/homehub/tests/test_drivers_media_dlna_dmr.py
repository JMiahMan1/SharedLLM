"""dlna_dmr: state from AVTransport/RenderingControl, play_media as SetAVTransportURI + Play."""
from __future__ import annotations

from xml.sax.saxutils import escape

import pytest

from services.homehub.core import HubError, ServiceNotFound
from services.homehub.drivers.dlna_dmr import DlnaDmrDriver, parse_didl
from services.homehub.drivers.media_common import F, didl, hms_to_seconds, mime_for, seconds_to_hms
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.media_fakes import AVT, RC, FakeHttp, device_xml, soap_reply, soap_router

URL = "http://192.0.2.115:9197/dmr"
ENT = "media_player.tv_loft_samsung_q900"


def build(state: str = "PLAYING", volume: str = "23", mute: str = "0"):
    hub, entry = make_hub("dlna_dmr", {"url": URL, "device_id": "uuid:x", "type": "MediaRenderer", "mac": "02:00:00:00:01:06"},
                          [{"entity_id": ENT, "unique_id": "uuid:00000000-0000-4000-8000-00000000c005"}])
    drv = DlnaDmrDriver(hub, entry)
    http = FakeHttp()
    drv.http = http
    drv.upnp.http = http
    http.route("GET", "/dmr", (200, device_xml([(AVT, "/upnp/control/AVTransport1"),
                                                  (RC, "/upnp/control/RenderingControl1")])))
    meta = escape(didl("http://x/song.mp3", "audio/mpeg", "Amazing Grace").replace(
        "</item>", "<upnp:artist>Choir</upnp:artist><upnp:album>Hymns</upnp:album></item>"))
    sent: list[str] = []

    def record(reply):
        def h(body: str):
            sent.append(body)
            return reply
        return h

    http.route("POST", "/AVTransport1", soap_router({
        "GetTransportInfo": soap_reply("GetTransportInfo", AVT, CurrentTransportState=state),
        "GetPositionInfo": soap_reply("GetPositionInfo", AVT, TrackDuration="0:04:05", RelTime="0:01:00",
                                      TrackURI="http://x/song.mp3", TrackMetaData=meta),
        "SetAVTransportURI": record(soap_reply("SetAVTransportURI", AVT)),
        "Play": record(soap_reply("Play", AVT)),
        "Pause": record(soap_reply("Pause", AVT)),
        "Stop": record(soap_reply("Stop", AVT)),
        "Seek": record(soap_reply("Seek", AVT)),
    }))
    http.route("POST", "/RenderingControl1", soap_router({
        "GetVolume": soap_reply("GetVolume", RC, CurrentVolume=volume),
        "GetMute": soap_reply("GetMute", RC, CurrentMute=mute),
        "SetVolume": record(soap_reply("SetVolume", RC)),
        "SetMute": record(soap_reply("SetMute", RC)),
    }))
    return hub, drv, http, sent


async def test_setup_publishes_playing_state_with_metadata():
    hub, drv, _http, _ = build()
    await drv.async_setup()
    st = hub.states.get(ENT)
    assert st.state == "playing"
    a = st.attributes
    assert a["volume_level"] == pytest.approx(0.23)
    assert a["is_volume_muted"] is False
    assert a["media_title"] == "Amazing Grace"
    assert a["media_artist"] == "Choir"
    assert a["media_album_name"] == "Hymns"
    assert a["media_duration"] == 245
    assert a["media_position"] == 60
    assert a["media_content_type"] == "music"
    assert a["supported_features"] & F.PLAY_MEDIA and a["supported_features"] & F.VOLUME_SET


async def test_unreachable_renderer_is_off_not_an_error():
    hub, drv, http, _ = build()
    http.down = True
    await drv.async_setup()
    assert hub.states.get(ENT).state == "off"
    assert hub.states.get(ENT).attributes["supported_features"] == 0


async def test_paused_and_stopped_states():
    hub, drv, _, _ = build(state="PAUSED_PLAYBACK")
    await drv.async_setup()
    assert hub.states.get(ENT).state == "paused"
    hub, drv, _, _ = build(state="NO_MEDIA_PRESENT")
    await drv.async_setup()
    assert hub.states.get(ENT).state == "idle"


async def test_play_media_sets_uri_with_didl_then_plays():
    hub, drv, _, sent = build()
    await drv.async_setup()
    ent = hub.entities[ENT]
    await drv.async_call(ent, "media_player", "play_media",
                         {"media_content_id": "http://jarvis/a.mp3?x=1&y=2", "media_content_type": "music"})
    set_uri = next(b for b in sent if "SetAVTransportURI" in b)
    assert "http://jarvis/a.mp3?x=1&amp;y=2" in set_uri
    assert "audio/mpeg" in set_uri and "DIDL-Lite" in set_uri
    assert any("<u:Play " in b for b in sent[sent.index(set_uri):])


async def test_volume_mute_seek_and_unknown_service():
    hub, drv, _, sent = build()
    await drv.async_setup()
    ent = hub.entities[ENT]
    await drv.async_call(ent, "media_player", "volume_set", {"volume_level": 0.5})
    assert "<DesiredVolume>50</DesiredVolume>" in sent[-1]
    await drv.async_call(ent, "media_player", "volume_up", {})
    assert "<DesiredVolume>33</DesiredVolume>" in sent[-1]
    await drv.async_call(ent, "media_player", "volume_mute", {"is_volume_muted": True})
    assert "<DesiredMute>1</DesiredMute>" in sent[-1]
    await drv.async_call(ent, "media_player", "media_seek", {"seek_position": 125})
    assert "<Target>0:02:05</Target>" in sent[-1]
    await drv.async_call(ent, "media_player", "media_play_pause", {})
    assert "<u:Pause " in sent[-1]
    with pytest.raises(ServiceNotFound):
        await drv.async_call(ent, "media_player", "turn_on", {})
    with pytest.raises(HubError):
        await drv.async_call(ent, "media_player", "play_media", {})


async def test_upnp_fault_is_reported():
    hub, drv, http, _ = build()
    await drv.async_setup()
    http.route("POST", "/AVTransport1", (500, '<s:Envelope xmlns:s="x"><s:Body><s:Fault><detail>'
                                              '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>701'
                                              '</errorCode><errorDescription>Transition not available'
                                              '</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>'))
    with pytest.raises(HubError, match="701"):
        await drv.async_call(hub.entities[ENT], "media_player", "media_stop", {})


def test_helpers():
    assert hms_to_seconds("1:00:01.5") == 3601.5
    assert hms_to_seconds("NOT_IMPLEMENTED") is None
    assert seconds_to_hms(3725) == "1:02:05"
    assert mime_for("music", "http://x/y") == "audio/mpeg"
    assert mime_for("url", "http://x/clip.webm?t=1") == "video/webm"
    assert mime_for("audio/wav", "http://x/y.mp3") == "audio/wav"
    assert parse_didl("garbage<") == {}
