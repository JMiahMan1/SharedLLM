"""TV stage: discovered screens, per-platform display, and the live stream guard."""
import asyncio
import time

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
    assert by_name["Living Room"]["methods"][0] == "webos_browser"
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

def test_master_bedroom_samsung_orsay_with_chromecast_is_one_cast_screen():
    """A legacy Samsung with a Chromecast attached, plus that Chromecast's MA wrapper."""
    ha = [
        {**_ha_tv("media_player.master_bedroom_samsung", "Master Bedroom Samsung", "samsung",
                  cast_sibling="media_player.master_bedroom_tv", ma_sibling="media_player.master_bedroom_tv_2"),
         "os_version": "Samsung Orsay (Legacy)"},
        _ha_tv("media_player.master_bedroom_tv_2", "Master Bedroom TV", "music_assistant",
               cast_sibling="media_player.master_bedroom_tv"),
    ]
    screens = tv_stage.build_screens(ha, [])
    assert [s["id"] for s in screens] == ["ha:media_player.master_bedroom_samsung"]
    assert screens[0]["methods"] == ["cast_web"]  # no Orsay browser, no pointless video stream


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
