"""TV stage: discovered screens, per-platform display, and the live stream guard."""
import asyncio
import time
from typing import ClassVar

import pytest

from services.execution import stage_stream
from services.execution.handlers import tv_stage


def _ha_tv(eid, name, platform, **companions):
    return {
        "entity_id": eid, "name": name, "kind": "tv", "platform": platform,
        "state": "on", "companions": {k: v for k, v in companions.items() if v},
    }


def _cast(name, uuid, cast_type="cast", host="10.0.0.9"):
    return {"uuid": uuid, "name": name, "host": host, "port": 8009, "model": "Chromecast",
            "cast_type": cast_type, "manufacturer": "Google"}


# --------------------------------------------------------------------------- build_screens

def test_android_tv_and_its_cast_sibling_are_one_screen_with_cast_first():
    ha = [
        _ha_tv("media_player.office_tv", "Office TV", "android_tv",
               cast_sibling="media_player.office_tv_2", remote_sibling="remote.office_tv"),
        _ha_tv("media_player.office_tv_2", "Office TV", "cast"),
    ]
    screens = tv_stage.build_screens(ha, [_cast("Office TV", "u-1")])
    assert [s["id"] for s in screens] == ["ha:media_player.office_tv"]
    office = screens[0]
    assert office["methods"][:2] == ["cast_web", "android_link"]
    assert office["cast"]["host"] == "10.0.0.9"
    assert office["display"] == "web"


def test_cast_sibling_name_matches_mdns_even_when_tv_name_differs():
    ha = [_ha_tv("media_player.den", "Den Television", "android_tv", cast_sibling="media_player.den_cast")]
    states = [{"entity_id": "media_player.den_cast", "attributes": {"friendly_name": "Den Chromecast"}}]
    screens = tv_stage.build_screens(ha, [_cast("Den", "u-2")], states)
    assert len(screens) == 1 and screens[0]["cast"]["uuid"] == "u-2"


def test_audio_only_cast_and_groups_are_not_screens():
    casts = [_cast("Kitchen speaker", "a", "audio"), _cast("Whole house", "g", "group"), _cast("Bedroom TV", "b")]
    screens = tv_stage.build_screens([], casts)
    assert [s["name"] for s in screens] == ["Bedroom TV"]
    assert screens[0]["id"] == "cast:b" and screens[0]["methods"] == ["cast_web"]


def test_roku_gets_a_live_stream_and_is_labelled_video():
    screens = tv_stage.build_screens([_ha_tv("media_player.gracie_roku", "Gracie TV", "roku")], [])
    assert screens[0]["methods"] == ["roku_stream"]
    assert screens[0]["display"] == "video"


def test_lg_and_samsung_use_their_own_browsers():
    ha = [_ha_tv("media_player.lg", "Living Room", "webos"), _ha_tv("media_player.sam", "Loft", "samsung")]
    by_name = {s["name"]: s for s in tv_stage.build_screens(ha, [])}
    assert by_name["Living Room"]["methods"][:2] == ["webos_direct", "webos_browser"]
    assert by_name["Loft"]["methods"][0] == "samsung_browser"
    assert all(s["display"] == "web" for s in by_name.values())


def test_web_screens_sort_before_video_screens_and_speakers_are_ignored():
    ha = [
        _ha_tv("media_player.r", "A Roku", "roku"),
        _ha_tv("media_player.lg", "B LG", "webos"),
        {"entity_id": "media_player.spk", "name": "Speaker", "kind": "speaker", "platform": "cast"},
    ]
    names = [s["name"] for s in tv_stage.build_screens(ha, [])]
    assert names == ["B LG", "A Roku"]


def test_public_screen_omits_addresses_and_entities():
    screen = tv_stage.build_screens([], [_cast("TV", "u")])[0]
    pub = tv_stage.public_screen(screen)
    assert "cast" not in pub and "companions" not in pub and "host" not in str(pub)


# --------------------------------------------------------------------------- show

def test_validate_page_url_rejects_non_http():
    for bad in ("", "javascript:alert(1)", "file:///etc/passwd", "/tv/ABCD"):
        with pytest.raises(ValueError):
            tv_stage.validate_page_url(bad)
    assert tv_stage.validate_page_url("http://10.0.0.2:5001/tv/ABCD") == "http://10.0.0.2:5001/tv/ABCD"


def test_show_falls_back_to_the_next_method_and_reports_every_attempt(monkeypatch):
    screen = tv_stage.build_screens(
        [_ha_tv("media_player.office_tv", "Office TV", "android_tv",
                cast_sibling="media_player.office_tv_2", remote_sibling="remote.office_tv")], [],
    )[0]

    async def fake_find(screen_id, ha_url, ha_token):
        return screen

    async def cast_fails(*a):
        return {"ok": False, "detail": "Cast device did not answer"}

    async def link_ok(*a):
        return {"ok": True, "verified": False, "detail": "Opened the page link"}

    monkeypatch.setattr(tv_stage, "find_screen", fake_find)
    monkeypatch.setitem(tv_stage._HANDLERS, "cast_web", cast_fails)
    monkeypatch.setitem(tv_stage._HANDLERS, "android_link", link_ok)
    out = asyncio.run(tv_stage.show("ha:media_player.office_tv", "http://h:5001/tv/ZION", "u", "t"))
    assert out["success"] is True and out["method"] == "android_link" and out["display"] == "web"
    assert [a["method"] for a in out["attempts"]] == ["cast_web", "android_link"]
    assert out["attempts"][0]["ok"] is False


def test_show_reports_failure_honestly_when_nothing_works(monkeypatch):
    screen = tv_stage.build_screens([_ha_tv("media_player.r", "Roku", "roku")], [])[0]

    async def fake_find(*a):
        return screen

    async def fails(*a):
        return {"ok": False, "detail": "Roku IP not found"}

    monkeypatch.setattr(tv_stage, "find_screen", fake_find)
    monkeypatch.setitem(tv_stage._HANDLERS, "roku_stream", fails)
    out = asyncio.run(tv_stage.show("ha:media_player.r", "http://h/tv/A", "u", "t"))
    assert out["success"] is False and out["error"] == "Roku IP not found"


def test_show_unknown_screen(monkeypatch):
    async def none(*a):
        return None

    monkeypatch.setattr(tv_stage, "find_screen", none)
    out = asyncio.run(tv_stage.show("ha:media_player.nope", "http://h/tv/A", "u", "t"))
    assert out["success"] is False and "not found" in out["error"]


def test_webos_launches_the_browser_at_the_page(monkeypatch):
    calls = []

    async def call_service(ha_url, ha_token, domain, service, entity_id="", data=None, **kw):
        calls.append((domain, service, entity_id, data))
        return {"ok": True}

    async def get_state(ha_url, ha_token, eid):
        return {"state": "on", "attributes": {"source": "Web Browser"}}

    async def no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(tv_stage.ha_client, "call_service", call_service)
    monkeypatch.setattr(tv_stage.ha_client, "get_state", get_state)
    monkeypatch.setattr(tv_stage.asyncio, "sleep", no_sleep)
    screen = tv_stage.build_screens([_ha_tv("media_player.lg", "LG", "webos")], [])[0]
    assert screen["methods"] == ["webos_direct", "webos_browser"]
    res = asyncio.run(tv_stage.show_via_webos(screen, "http://h/tv/ZION", "u", "t"))
    assert res["ok"] and res["verified"]
    assert ("webostv", "command", "media_player.lg",
            {"command": "system.launcher/launch",
             "payload": {"id": "com.webos.app.browser", "target": "http://h/tv/ZION"}}) in calls


def test_android_link_sends_the_url_as_the_remote_activity(monkeypatch):
    calls = []

    async def call_service(ha_url, ha_token, domain, service, entity_id="", data=None, **kw):
        calls.append((domain, service, entity_id, data))
        return {"ok": True}

    async def get_state(*a):
        return {"state": "on"}

    monkeypatch.setattr(tv_stage.ha_client, "call_service", call_service)
    monkeypatch.setattr(tv_stage.ha_client, "get_state", get_state)
    screen = tv_stage.build_screens(
        [_ha_tv("media_player.atv", "ATV", "android_tv", remote_sibling="remote.atv")], [])[0]
    res = asyncio.run(tv_stage.show_via_android_link(screen, "http://h/tv/Z", "u", "t"))
    assert res["ok"]
    assert ("remote", "turn_on", "remote.atv", {"activity": "http://h/tv/Z"}) in calls


def test_lan_host_prefers_configuration(monkeypatch):
    monkeypatch.setenv("EXECUTION_EXTERNAL_HOST", "jarvis.lan")
    assert tv_stage.lan_host_for("10.0.0.5") == "jarvis.lan"


# --------------------------------------------------------------------------- stage_stream

def test_resolve_file_serves_only_live_streams_and_plain_names(tmp_path):
    s = stage_stream.StageStream(sid="abc", url="http://h/tv", directory=tmp_path)
    (tmp_path / "index.m3u8").write_text("#EXTM3U\n")
    (tmp_path / "seg00001.ts").write_bytes(b"\x47" * 188)
    stage_stream._streams["abc"] = s
    try:
        s.last_access = 0
        _path, mt = stage_stream.resolve_file("abc", "index.m3u8")
        assert mt == "application/vnd.apple.mpegurl" and s.last_access > 0
        assert stage_stream.resolve_file("abc", "seg00001.ts")[1] == "video/mp2t"
        for bad in ("../secret", "index.m3u8/../x", "x.ts", ".env"):
            assert stage_stream.resolve_file("abc", bad) is None
        assert stage_stream.resolve_file("nope", "index.m3u8") is None
    finally:
        stage_stream._streams.pop("abc", None)


def test_streams_expire_when_idle_or_too_old(tmp_path):
    s = stage_stream.StageStream(sid="x", url="u", directory=tmp_path)

    class P:
        returncode = None

    s.proc = P()  # type: ignore[assignment]
    now = time.time()
    s.last_access = now
    assert not stage_stream.expired(s, now)
    assert stage_stream.expired(s, now + stage_stream.IDLE_TIMEOUT_S + 1)
    s.started_at = now - stage_stream.MAX_AGE_S - 1
    assert stage_stream.expired(s, now)


def test_ffmpeg_command_writes_a_rolling_hls_playlist(tmp_path):
    cmd = stage_stream.ffmpeg_command(tmp_path)
    assert cmd[0] == "ffmpeg" and "-shortest" in cmd
    assert cmd[-1] == str(tmp_path / "index.m3u8")
    assert "delete_segments+independent_segments+omit_endlist" in cmd


# --------------------------------------------------------------------------- real-house cases

def test_master_bedroom_samsung_and_its_plugged_in_chromecast_are_both_listed():
    """A legacy Samsung with a Chromecast attached, plus that Chromecast's MA wrapper."""
    ha = [
        {**_ha_tv("media_player.master_bedroom_samsung", "Master Bedroom Samsung", "samsung",
                  cast_sibling="media_player.master_bedroom_tv", ma_sibling="media_player.master_bedroom_tv_2"),
         "os_version": "Samsung Orsay (Legacy)"},
        _ha_tv("media_player.master_bedroom_tv", "Master Bedroom TV", "cast"),
        _ha_tv("media_player.master_bedroom_tv_2", "Master Bedroom TV", "music_assistant",
               cast_sibling="media_player.master_bedroom_tv"),
    ]
    cc = _cast("Master Bedroom TV", "16b032a3-f048-710f-0506-ad3136885e30", host="192.168.1.240")
    uuids = {"media_player.master_bedroom_tv": "16b032a3f048710f0506ad3136885e30"}
    screens = tv_stage.build_screens(ha, [cc], None, uuids)
    by_id = {s["id"]: s for s in screens}
    assert set(by_id) == {"ha:media_player.master_bedroom_samsung", "ha:media_player.master_bedroom_tv"}
    for s in screens:  # either entry reaches the TV through the Chromecast, found on another subnet
        assert s["methods"] == ["cast_web"]
        assert s["cast"]["host"] == "192.168.1.240"


def test_cast_device_is_matched_by_cast_id_before_name():
    ha = [_ha_tv("media_player.den", "Den", "cast")]
    wrong_name = _cast("Something Else", "AAAA-1", host="10.0.0.5")
    screens = tv_stage.build_screens(ha, [wrong_name], None, {"media_player.den": "aaaa1"})
    assert [s["id"] for s in screens] == ["ha:media_player.den"]
    assert screens[0]["cast"]["host"] == "10.0.0.5"


def test_built_in_cast_is_still_one_screen():
    ha = [_ha_tv("media_player.gtv", "Den TV", "google_tv", cast_sibling="media_player.gtv_cast"),
          _ha_tv("media_player.gtv_cast", "Den TV", "cast")]
    assert [s["id"] for s in tv_stage.build_screens(ha, [])] == ["ha:media_player.gtv"]


# --------------------------------------------------------------------------- cross-subnet cast discovery

def test_neighbour_networks_cover_both_sides():
    import ipaddress
    nets = tv_stage.neighbour_networks({ipaddress.ip_network("192.168.2.0/24")})
    assert {str(n) for n in nets} == {"192.168.1.0/24", "192.168.3.0/24"}


def test_known_hosts_are_read_from_the_options_form():
    flow = {"flow_id": "f", "data_schema": [
        {"name": "known_hosts", "description": {"suggested_value": ["192.168.1.240", "bogus", "192.168.1.240"]}},
        {"name": "uuid", "description": {"suggested_value": ["x"]}},
    ]}
    assert tv_stage.known_hosts_from_flow(flow) == ["192.168.1.240"]


def test_eureka_answer_becomes_a_cast_device():
    info = {"name": "Master Bedroom TV", "device_info": {
        "ssdp_udn": "16b032a3-f048-710f-0506-ad3136885e30", "model_name": "Chromecast",
        "manufacturer": "Google", "capabilities": {"display_supported": True}}}
    dev = tv_stage.cast_from_eureka("192.168.1.240", info)
    assert dev["name"] == "Master Bedroom TV" and dev["cast_type"] == "cast" and dev["host"] == "192.168.1.240"
    speaker = tv_stage.cast_from_eureka("1.2.3.4", {"name": "Kitchen", "ssdp_udn": "u",
                                                     "device_info": {"capabilities": {"display_supported": False}}})
    assert speaker["cast_type"] == "audio"


def test_mdns_and_unicast_results_merge_by_cast_id():
    a = _cast("Office TV", "81df-ea8f", host="192.168.2.88")
    b = {**_cast("Office TV", "81DFEA8F", host="192.168.2.88"), "found_by": "unicast"}
    c = {**_cast("Bedroom", "16b0", host="192.168.1.240"), "found_by": "unicast"}
    merged = tv_stage.merge_cast_devices([a], [b, c])
    assert [d["host"] for d in merged] == ["192.168.2.88", "192.168.1.240"]


def test_cross_subnet_discovery_works_without_home_assistant(monkeypatch, tmp_path):
    import ipaddress
    monkeypatch.setattr(tv_stage, "TOKEN_DIR", tmp_path)
    monkeypatch.setattr(tv_stage, "_local_networks", lambda: {ipaddress.ip_network("192.168.2.0/24")})
    tv_stage._sweep_cache.update({"at": 0.0, "key": "", "devices": []})

    async def port_open(ip, port=8009):
        return ip == "192.168.1.240"

    async def info(ip):
        return tv_stage.cast_from_eureka(ip, {"name": "Master Bedroom TV", "ssdp_udn": "16b0"})

    async def no_router():
        return {}

    monkeypatch.setattr(tv_stage, "_port_open", port_open)
    monkeypatch.setattr(tv_stage, "_cast_info", info)
    monkeypatch.setattr(tv_stage, "_router_status", no_router)
    found = asyncio.run(tv_stage.discover_remote_cast_devices("", "", force=True))
    assert [d["host"] for d in found] == ["192.168.1.240"]
    assert tv_stage._learned_hosts() == ["192.168.1.240"]


def test_router_device_list_is_asked_instead_of_sweeping(monkeypatch, tmp_path):
    import ipaddress
    monkeypatch.setattr(tv_stage, "TOKEN_DIR", tmp_path)
    monkeypatch.setattr(tv_stage, "_local_networks", lambda: {ipaddress.ip_network("192.168.2.0/24")})
    tv_stage._sweep_cache.update({"at": 0.0, "key": "", "devices": []})
    probed = []

    async def port_open(ip, port=8009):
        probed.append(ip)
        return ip == "10.9.0.7"

    async def info(ip):
        return tv_stage.cast_from_eureka(ip, {"name": "Garage TV", "ssdp_udn": "g1"})

    async def router():
        return {"configured": True, "networks": [{"name": "lan", "cidr": "192.168.2.1/24"},
                                                 {"name": "iot", "cidr": "10.9.0.1/24"}],
                "devices": {"aa": {"name": "Garage TV", "ip": "10.9.0.7"}, "bb": {"name": "pc", "ip": "192.168.2.50"},
                            "cc": {"name": "lamp", "ip": "10.9.0.8"}}}

    monkeypatch.setattr(tv_stage, "_port_open", port_open)
    monkeypatch.setattr(tv_stage, "_cast_info", info)
    monkeypatch.setattr(tv_stage, "_router_status", router)
    found = asyncio.run(tv_stage.discover_remote_cast_devices("", "", force=True))
    assert [d["name"] for d in found] == ["Garage TV"]
    assert sorted(probed) == ["10.9.0.7", "10.9.0.8"]  # only the router's devices, no sweep


def test_router_view_reads_networks_and_addresses():
    nets, ips = tv_stage.router_view({"networks": [{"cidr": "192.168.1.1/24"}, {"cidr": "bogus"}],
                                      "devices": {"m": {"ip": "192.168.1.240"}, "n": {"ip": None}}})
    assert {str(n) for n in nets} == {"192.168.1.0/24"} and ips == ["192.168.1.240"]


def test_audio_only_players_are_not_offered_video():
    ha = [_ha_tv("media_player.a25_macbook", "[TV]Master", "music_assistant"),
          _ha_tv("media_player.rv_tv", "RV TV", "speaker")]
    assert tv_stage.build_screens(ha, []) == []


def test_a_plain_dlna_tv_gets_the_video_stream():
    screens = tv_stage.build_screens([_ha_tv("media_player.den_dlna", "Den", "dlna")], [])
    assert screens[0]["methods"] == ["media_stream"] and screens[0]["display"] == "video"


def test_show_stays_inside_its_time_budget(monkeypatch):
    screen = tv_stage.build_screens(
        [_ha_tv("media_player.atv", "ATV", "android_tv", cast_sibling="media_player.atv_cast", remote_sibling="remote.atv")],
        [])[0]

    async def fake_find(*a):
        return screen

    async def hangs(*a):
        await asyncio.sleep(30)

    monkeypatch.setattr(tv_stage, "find_screen", fake_find)
    monkeypatch.setattr(tv_stage, "SHOW_BUDGET_S", 0.3)
    monkeypatch.setitem(tv_stage._HANDLERS, "cast_web", hangs)
    monkeypatch.setitem(tv_stage._HANDLERS, "android_link", hangs)
    out = asyncio.run(tv_stage.show("ha:media_player.atv", "http://h/tv/A", "u", "t"))
    assert out["success"] is False
    assert all(not a["ok"] for a in out["attempts"])


def test_a_sleeping_chromecast_is_woken_and_found_again(monkeypatch):
    screen = tv_stage.build_screens(
        [_ha_tv("media_player.bed", "Master Bedroom Samsung", "samsung", cast_sibling="media_player.bed_cast")], [])[0]
    calls = []

    async def call_service(ha_url, ha_token, domain, service, entity_id="", data=None, **kw):
        calls.append((domain, service, entity_id))
        return {"ok": True}

    async def get_state(ha_url, ha_token, eid):
        return {"state": "on", "attributes": {"friendly_name": "Master Bedroom TV"}}

    rounds = iter([[], [_cast("Master Bedroom TV", "u-9", host="10.0.0.42")]])

    async def discover(timeout=3.0):
        return next(rounds, [])

    async def no_sleep(*a, **k):
        return None

    shown = {}

    def fake_cast(cast_dev, host, url):
        shown.update(cast=cast_dev, url=url)
        return True, "Showing on Master Bedroom TV"

    monkeypatch.setattr(tv_stage.ha_client, "call_service", call_service)
    monkeypatch.setattr(tv_stage.ha_client, "get_state", get_state)
    monkeypatch.setattr(tv_stage, "discover_cast_devices", discover)
    monkeypatch.setattr(tv_stage.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(tv_stage, "_cast_show_sync", fake_cast)
    res = asyncio.run(tv_stage.show_via_cast(screen, "http://h/tv/Z", "u", "t"))
    assert res["ok"] and shown["cast"]["host"] == "10.0.0.42"
    assert ("media_player", "turn_on", "media_player.bed_cast") in calls


def test_live_house_shape_lists_samsung_and_chromecast_and_drops_the_wrapper():
    """What HA really reports: no cast_sibling on the Samsung, Cast entities not classified."""
    ha = [
        {**_ha_tv("media_player.master_bedroom_samsung", "Master Bedroom Samsung", "samsung",
                  ma_sibling="media_player.master_bedroom_tv_2", remote_sibling="remote.master_bedroom_samsung"),
         "os_version": "Samsung Orsay (Legacy)"},
        _ha_tv("media_player.master_bedroom_tv_2", "Master Bedroom TV", "music_assistant",
               ma_sibling="media_player.master_bedroom_tv_2"),
    ]
    states = [{"entity_id": "media_player.master_bedroom_tv", "attributes": {"friendly_name": "Master Bedroom TV"}},
              {"entity_id": "media_player.master_bedroom_tv_2", "attributes": {"friendly_name": "Master Bedroom TV"}}]
    cc = {**_cast("Master Bedroom TV", "16b032a3-f048-710f-0506-ad3136885e30", host="192.168.1.240"),
          "found_by": "unicast"}
    uuids = {"media_player.master_bedroom_tv": "16b032a3f048710f0506ad3136885e30"}
    screens = tv_stage.build_screens(ha, [cc], states, uuids)
    by_id = {s["id"]: s for s in screens}
    assert set(by_id) == {"ha:media_player.master_bedroom_samsung", "ha:media_player.master_bedroom_tv"}
    sam = by_id["ha:media_player.master_bedroom_samsung"]
    assert sam["methods"] == ["cast_web"] and sam["cast_entity"] == "media_player.master_bedroom_tv"
    assert by_id["ha:media_player.master_bedroom_tv"]["platform"] == "cast"



def test_the_owning_integration_decides_the_platform_not_the_name():
    # Reported: the LG would not cast. Its Music Assistant AirPlay player is named
    # "[LG] webOS TV ..." and was treated as the LG; the real webOS entity
    # ("Living Room TV") was treated as a generic TV.
    ha = [_ha_tv("media_player.lg_webos_tv_un6955zuf_airplay", "[LG] webOS TV UN6955ZUF (AirPlay)", "webos"),
          _ha_tv("media_player.living_room_tv", "Living Room TV", "generic_tv")]
    platforms = {"media_player.lg_webos_tv_un6955zuf_airplay": "music_assistant",
                 "media_player.living_room_tv": "webos"}
    screens = {s["id"]: s for s in tv_stage.build_screens(ha, [], platforms=platforms)}
    assert "ha:media_player.lg_webos_tv_un6955zuf_airplay" not in screens  # an AirPlay speaker shows no page
    assert screens["ha:media_player.living_room_tv"]["methods"] == ["webos_direct", "webos_browser"]
    # A Roku reached through its Music Assistant entity keeps its stream (it goes to the Roku itself).
    roku = tv_stage.build_screens([_ha_tv("media_player.gracies_tv", "Gracies TV", "roku")], [],
                                  platforms={"media_player.gracies_tv": "music_assistant"})
    assert roku and roku[0]["methods"] == ["roku_stream"]


def _webos_fakes(monkeypatch, states):
    calls = []

    async def call_service(ha_url, ha_token, domain, service, entity_id="", data=None, **kw):
        calls.append((domain, service, entity_id))
        return {"ok": True}

    seq = iter(states)

    async def get_state(ha_url, ha_token, eid):
        return next(seq, states[-1])

    async def no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(tv_stage.ha_client, "call_service", call_service)
    monkeypatch.setattr(tv_stage.ha_client, "get_state", get_state)
    monkeypatch.setattr(tv_stage.asyncio, "sleep", no_sleep)
    return calls


def test_an_unreachable_lg_is_reported_not_claimed_as_opened(monkeypatch):
    calls = _webos_fakes(monkeypatch, [{"state": "unavailable", "attributes": {}}])
    screen = tv_stage.build_screens([_ha_tv("media_player.lg", "Living Room TV", "webos")], [])[0]
    res = asyncio.run(tv_stage.show_via_webos(screen, "http://h/tv/ZION", "u", "t"))
    assert not res["ok"] and "not connected" in res["detail"]
    assert ("media_player", "turn_on", "media_player.lg") in calls  # it tried to wake it
    assert not any(c[0] == "webostv" for c in calls)  # and sent nothing into the void


def test_a_sleeping_lg_is_woken_then_shown(monkeypatch):
    calls = _webos_fakes(monkeypatch, [{"state": "off"}, {"state": "off"},
                                       {"state": "on", "attributes": {"source": "Web Browser"}}])
    screen = tv_stage.build_screens([_ha_tv("media_player.lg", "Living Room TV", "webos")], [])[0]
    res = asyncio.run(tv_stage.show_via_webos(screen, "http://h/tv/ZION", "u", "t"))
    assert res["ok"] and res["verified"]
    assert calls[0] == ("media_player", "turn_on", "media_player.lg") and ("webostv", "command", "media_player.lg") in calls



def test_the_lg_is_found_by_its_network_name_even_after_its_address_changes():
    # Home Assistant still had 192.168.2.160; the TV had moved to 192.168.4.156.
    screen = {"name": "Living Room TV", "entity_id": "media_player.living_room_tv"}
    router = {"8c:19:b5:22:1a:7c": {"name": "LivingRoomTv", "ip": "192.168.4.156"},
              "10:a5:62:91:d8:15": {"name": "10:a5:62:91:d8:15", "ip": "192.168.4.215"}}
    assert tv_stage.webos_host_candidates(screen, router, remembered="192.168.4.100", ha_ip="192.168.2.160") == [
        "192.168.4.156", "192.168.4.100", "192.168.2.160"]


class _FakeWebOs:
    instances: ClassVar[list] = []

    def __init__(self, host, client_key=None, connect_timeout=2):
        self.host, self.client_key, self.requests = host, client_key, []
        _FakeWebOs.instances.append(self)

    async def connect(self):
        self.client_key = self.client_key or "granted-key"

    async def request(self, uri, payload=None):
        self.requests.append((uri, payload))
        return {}

    async def get_current_app(self):
        return "com.webos.app.browser"

    async def disconnect(self):
        return None


def test_the_lg_browser_opens_directly_and_the_pairing_key_is_kept(monkeypatch, tmp_path):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "aiowebostv", types.SimpleNamespace(WebOsClient=_FakeWebOs))
    monkeypatch.setattr(tv_stage, "TOKEN_DIR", tmp_path)

    async def host(*_a, **_k):
        return "192.168.4.156"

    async def no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(tv_stage, "_webos_find_host", host)
    monkeypatch.setattr(tv_stage.asyncio, "sleep", no_sleep)
    _FakeWebOs.instances.clear()
    screen = tv_stage.build_screens([_ha_tv("media_player.living_room_tv", "Living Room TV", "webos")], [])[0]
    res = asyncio.run(tv_stage.show_via_webos_direct(screen, "http://h/tv/ZION", "", ""))
    assert res["ok"] and res["verified"]
    assert _FakeWebOs.instances[0].requests[0] == ("system.launcher/open", {"target": "http://h/tv/ZION"})
    # Next time it connects with the key the TV granted: no prompt.
    asyncio.run(tv_stage.show_via_webos_direct(screen, "http://h/tv/ZION", "", ""))
    assert _FakeWebOs.instances[1].client_key == "granted-key"



def test_an_lg_that_answers_directly_is_listed_as_on(monkeypatch, tmp_path):
    monkeypatch.setattr(tv_stage, "TOKEN_DIR", tmp_path)
    tv_stage._webos_remember("media_player.living_room_tv", host="192.168.4.156", client_key="k")

    async def port_open(ip, port=8009):
        return ip == "192.168.4.156"

    monkeypatch.setattr(tv_stage, "_port_open", port_open)
    screens = [{"platform": "webos", "state": "unavailable", "entity_id": "media_player.living_room_tv"},
               {"platform": "webos", "state": "unavailable", "entity_id": "media_player.other_lg"}]
    asyncio.run(tv_stage._webos_live_state(screens))
    assert [s["state"] for s in screens] == ["on", "unavailable"]



def test_a_samsung_is_found_by_its_own_name_after_its_address_changes(monkeypatch, tmp_path):
    monkeypatch.setattr(tv_stage, "TOKEN_DIR", tmp_path)

    async def name_at(ip):
        return {"192.168.4.114": "[TV] Loft Samsung Q900", "192.168.4.50": "[TV] Other"}.get(ip, "")

    async def no_ip(*_a, **_k):
        return "192.168.2.77"  # Home Assistant's stale address: nothing answers there

    async def router():
        return {"devices": {"a": {"name": "LoftTV", "ip": "192.168.4.114"}, "b": {"name": "x", "ip": "192.168.4.50"}}}

    monkeypatch.setattr(tv_stage, "_samsung_name_at", name_at)
    monkeypatch.setattr(tv_stage, "_entity_ip", no_ip)
    monkeypatch.setattr(tv_stage, "_router_status", router)
    screen = {"name": "[TV] Loft Samsung Q900", "entity_id": "media_player.tv_loft_samsung_q900_2"}
    assert asyncio.run(tv_stage._samsung_find_host(screen, "u", "t")) == "192.168.4.114"
    tv_stage._samsung_remember(screen["entity_id"], "192.168.4.114")
    screens = [{"platform": "samsung", "state": "unavailable", "entity_id": screen["entity_id"]}]
    asyncio.run(tv_stage._webos_live_state(screens))
    assert screens[0]["state"] == "on"
