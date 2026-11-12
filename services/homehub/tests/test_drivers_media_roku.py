"""roku: ECP state mapping, sensors, keys, launch and play_media."""
from __future__ import annotations

import pytest

from services.homehub.core import ServiceNotFound
from services.homehub.drivers.media_common import F
from services.homehub.drivers.roku import RokuDriver, ecp_key
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.media_fakes import FakeHttp

SERIAL = "2N0000000001"
MP = "media_player.bedroom_roku_tv"
REMOTE = "remote.bedroom_roku_tv"

DEVICE_INFO = f"""<device-info><serial-number>{SERIAL}</serial-number><user-device-name>Taylors TV</user-device-name>
<power-mode>{{power}}</power-mode><supports-find-remote>true</supports-find-remote><headphones-connected>false</headphones-connected>
<supports-airplay>true</supports-airplay><supports-ethernet>false</supports-ethernet></device-info>"""
APPS = """<apps><app id="12" type="appl" version="5">Netflix</app><app id="837" type="appl" version="2">YouTube</app>
<app id="tvinput.hdmi1" type="tvin" version="1">AV</app></apps>"""


def build(power="PowerOn", active='<active-app><app id="12" type="appl">Netflix</app></active-app>',
          player='<player error="false" state="play"><position>61000 ms</position><duration>120000 ms</duration>'
                 '<is_live>false</is_live></player>'):
    hub, entry = make_hub("roku", {"host": "192.0.2.166"}, [
        {"entity_id": MP, "unique_id": SERIAL},
        {"entity_id": REMOTE, "unique_id": SERIAL},
        {"entity_id": "binary_sensor.bedroom_roku_tv_supports_airplay", "unique_id": f"{SERIAL}_supports_airplay"},
        {"entity_id": "binary_sensor.bedroom_roku_tv_headphones_connected", "unique_id": f"{SERIAL}_headphones_connected"},
        {"entity_id": "sensor.bedroom_roku_tv_active_app", "unique_id": f"{SERIAL}_active_app"},
        {"entity_id": "sensor.bedroom_roku_tv_active_app_id", "unique_id": f"{SERIAL}_active_app_id"},
    ])
    entry.unique_id = SERIAL
    drv = RokuDriver(hub, entry)
    drv.settle_delay = 0
    http = FakeHttp()
    drv.http = http
    http.route("GET", "/query/device-info", (200, DEVICE_INFO.format(power=power)))
    http.route("GET", "/query/apps", (200, APPS))
    http.route("GET", "/query/active-app", (200, active))
    http.route("GET", "/query/media-player", (200, player))
    http.route("POST", ":8060/", (200, ""))
    return hub, drv, http


async def test_playing_netflix_state_and_sensors():
    hub, drv, _ = build()
    await drv.async_setup()
    st = hub.states.get(MP)
    assert st.state == "playing"
    assert st.attributes["source"] == "Netflix"
    assert st.attributes["app_id"] == "12"
    assert st.attributes["source_list"] == ["Home", "AV", "Netflix", "YouTube"]
    assert st.attributes["media_position"] == 61
    assert st.attributes["media_duration"] == 120
    assert st.attributes["supported_features"] & F.SELECT_SOURCE
    assert hub.states.get(REMOTE).state == "on"
    assert hub.states.get("binary_sensor.bedroom_roku_tv_supports_airplay").state == "on"
    assert hub.states.get("binary_sensor.bedroom_roku_tv_headphones_connected").state == "off"
    assert hub.states.get("sensor.bedroom_roku_tv_active_app").state == "Netflix"
    assert hub.states.get("sensor.bedroom_roku_tv_active_app_id").state == "12"
    assert ("roku", "search") in hub.services


async def test_standby_home_and_paused():
    hub, drv, _ = build(power="DisplayOff")
    await drv.async_setup()
    assert hub.states.get(MP).state == "standby"
    assert hub.states.get(REMOTE).state == "off"
    hub, drv, _ = build(active="<active-app><app>Roku</app></active-app>")
    await drv.async_setup()
    assert hub.states.get(MP).state == "idle"
    assert hub.states.get(MP).attributes["source"] == "Home"
    hub, drv, _ = build(player='<player error="false" state="pause"><position>1 ms</position></player>')
    await drv.async_setup()
    assert hub.states.get(MP).state == "paused"
    hub, drv, _ = build(player='<player error="false" state="close"/>')
    await drv.async_setup()
    assert hub.states.get(MP).state == "on"


async def test_unreachable_roku_goes_unavailable():
    hub, drv, http = build()
    http.down = True
    await drv.async_setup()
    assert hub.states.get(MP).state == "unavailable"


async def test_remote_send_command_and_power():
    hub, drv, http = build()
    await drv.async_setup()
    await drv.async_call(hub.entities[REMOTE], "remote", "send_command",
                         {"command": ["home", "Select", "volume_up"], "num_repeats": 2, "delay_secs": 0})
    keys = [c["url"].rsplit("/", 1)[-1] for c in http.posted("/keypress/")]
    assert keys == ["Home", "Select", "VolumeUp"] * 2
    await drv.async_call(hub.entities[MP], "media_player", "turn_off", {})
    assert http.posted()[-1]["url"].endswith("/keypress/PowerOff")
    await drv.async_call(hub.entities[MP], "media_player", "turn_on", {})
    assert http.posted()[-1]["url"].endswith("/keypress/PowerOn")


async def test_pause_only_when_playing():
    hub, drv, http = build()
    await drv.async_setup()
    await drv.async_call(hub.entities[MP], "media_player", "media_play", {})
    assert not http.posted("/keypress/")  # already playing
    await drv.async_call(hub.entities[MP], "media_player", "media_pause", {})
    assert http.posted()[-1]["url"].endswith("/keypress/Play")


async def test_select_source_and_play_media():
    hub, drv, http = build()
    await drv.async_setup()
    await drv.async_call(hub.entities[MP], "media_player", "select_source", {"source": "YouTube"})
    assert http.posted()[-1]["url"].endswith("/launch/837")
    await drv.async_call(hub.entities[MP], "media_player", "select_source", {"source": "Home"})
    assert http.posted()[-1]["url"].endswith("/keypress/Home")
    await drv.async_call(hub.entities[MP], "media_player", "play_media",
                         {"media_content_id": "http://jarvis/v.mp4", "media_content_type": "video",
                          "extra": {"name": "Clip"}})
    call = http.posted()[-1]
    assert call["url"].endswith("/input/15985")
    assert call["params"] == {"t": "v", "u": "http://jarvis/v.mp4", "videoName": "Clip"}
    await drv.async_call(hub.entities[MP], "media_player", "play_media",
                         {"media_content_id": "http://jarvis/a.mp3", "media_content_type": "music"})
    assert http.posted()[-1]["params"]["t"] == "a"
    await drv.async_call(hub.entities[MP], "media_player", "play_media",
                         {"media_content_id": "782875", "media_content_type": "app"})
    assert http.posted()[-1]["url"].endswith("/launch/782875")
    with pytest.raises(ServiceNotFound):
        await drv.async_call(hub.entities[MP], "media_player", "repeat_set", {})


async def test_roku_search_service():
    hub, drv, http = build()
    hub.drivers[drv.entry.entry_id] = drv
    await drv.async_setup()
    await hub.call_service("roku", "search", {"entity_id": MP, "keyword": "bluey"})
    assert http.posted()[-1]["params"] == {"keyword": "bluey"}


def test_ecp_key_names():
    assert ecp_key("home") == "Home"
    assert ecp_key("VolumeUp") == "VolumeUp"
    assert ecp_key("power_off") == "PowerOff"
    assert ecp_key("Lit_a") == "Lit_a"
