"""What still works when Home Assistant is down (steps 1-2 of the HA fallback work).

* Music on a speaker goes straight to Music Assistant (it is its own server;
  HA's music_assistant integration only relays to it).
* A thermostat on an ESPHome board is set directly, like lights already were.
* A timer alarm whose HA route failed reaches the ESPHome fallback (HA failures
  are returned, not raised, and the old code `continue`d past it regardless).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.execution import ha_health
from services.execution.handlers import climate, mass_client, media
from services.execution.schemas import ExecutionResult, MediaPlayRequest

HA = "http://ha.local:8123"
PLAYERS = [
    {"player_id": "ma_kitchen", "display_name": "Kitchen Speaker", "available": True},
    {"player_id": "ma_office", "display_name": "Office", "available": True},
    {"player_id": "ma_garage", "display_name": "Garage", "available": False},
]


def _ctx(**over):
    base = dict(user="casey", ha_url=HA, ha_token="t", mass_url="http://192.0.2.20:8095", mass_token="m")
    base.update(over)
    return SimpleNamespace(**base)


def _mark_down():
    ha_health.record_unreachable(HA, "connection refused")
    ha_health.record_unreachable(HA, "connection refused")
    assert ha_health.is_down(HA)


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        ("media_player.kitchen_speaker", "ma_kitchen"),
        ("media_player.kitchen_speaker_2", "ma_kitchen"),  # HA's collision suffix
        ("media_player.office", "ma_office"),
        ("media_player.garage", None),  # unavailable players cannot play
        ("media_player.attic", None),
    ],
)
def test_ma_players_are_matched_to_ha_entities_by_name(entity_id, expected):
    player = mass_client.match_player(PLAYERS, entity_id)
    assert (player or {}).get("player_id") == expected


def test_the_best_hit_follows_the_ha_paths_category_order():
    hits = [
        {"type": "artist", "uri": "library://artist/1"},
        {"type": "track", "uri": "library://track/9"},
    ]
    assert mass_client.best_uri(hits) == ("library://track/9", "track")
    assert mass_client.best_uri([]) is None


@pytest.fixture
def ma(monkeypatch):
    calls = {}
    monkeypatch.setattr(mass_client, "get_players", AsyncMock(return_value=PLAYERS))
    monkeypatch.setattr(
        mass_client, "search", AsyncMock(return_value=[{"type": "track", "uri": "library://track/7"}])
    )

    async def fake_play(url, token, player_id, uri, option="replace"):
        calls["play"] = (player_id, uri, option)

    monkeypatch.setattr(mass_client, "play_media", fake_play)
    return calls


async def test_with_ha_down_music_goes_straight_to_music_assistant(ma, monkeypatch):
    _mark_down()
    via_ha = AsyncMock()
    monkeypatch.setattr(media, "_play_music_via_ha", via_ha)
    req = MediaPlayRequest(user_context={"user": "casey"}, query="Hymns", entity_id="media_player.kitchen_speaker")
    result = await media.play_music(req, "media_player.kitchen_speaker", _ctx())
    assert result.status == "SUCCESS", result.message
    assert result.detail["route"] == "mass_direct"
    assert ma["play"] == ("ma_kitchen", "library://track/7", "replace")
    via_ha.assert_not_awaited()


async def test_ha_failing_mid_request_falls_back_too(ma, monkeypatch):
    async def ha_goes_down(req, entity_id, ctx):
        _mark_down()
        return ExecutionResult(status="FAILURE", message="Home Assistant is unreachable: timed out", service="media_play")

    monkeypatch.setattr(media, "_play_music_via_ha", ha_goes_down)
    req = MediaPlayRequest(user_context={"user": "casey"}, query="library://album/3", entity_id="media_player.office")
    result = await media.play_music(req, "media_player.office", _ctx())
    assert result.status == "SUCCESS"
    assert ma["play"] == ("ma_office", "library://album/3", "replace")


async def test_an_ha_error_that_is_not_an_outage_does_not_bypass_ha(ma, monkeypatch):
    monkeypatch.setattr(
        media, "_play_music_via_ha",
        AsyncMock(return_value=ExecutionResult(status="FAILURE", message="No match for 'x'.", service="media_play")),
    )
    req = MediaPlayRequest(user_context={"user": "casey"}, query="x", entity_id="media_player.office")
    result = await media.play_music(req, "media_player.office", _ctx())
    assert result.message == "No match for 'x'."
    assert "play" not in ma


async def test_no_music_assistant_login_says_so(ma):
    _mark_down()
    req = MediaPlayRequest(user_context={"user": "casey"}, query="Hymns", entity_id="media_player.office")
    result = await media.play_music(req, "media_player.office", _ctx(mass_url=None, mass_token=None))
    assert result.status == "FAILURE"
    assert "unreachable" in result.message and "Music Assistant login" in result.message


async def test_a_speaker_named_by_device_name_resolves_without_ha():
    _mark_down()
    req = MediaPlayRequest(user_context={"user": "casey"}, query="Hymns", device_name="Kitchen Speaker")
    assert await media.resolve_entity(req, HA, "t", "music") == "media_player.kitchen_speaker"


async def test_climate_goes_through_the_router_and_reports_a_direct_route(monkeypatch):
    monkeypatch.setattr("services.execution.entity_access.verify_entity_access", AsyncMock(return_value=True))
    router = AsyncMock(
        return_value=ExecutionResult(status="SUCCESS", message="ok", service="climate", detail={"route": "esphome_direct"})
    )
    monkeypatch.setattr(climate.hardware_router, "execute_device_command", router)
    req = climate.ClimateRequest(
        user_context={"user": "alex", "ha_url": HA, "ha_token": "t"}, entity_id="climate.hall", temperature=70
    )
    result = await climate.handle_climate(req)
    assert result.status == "SUCCESS" and "directly" in result.message
    args = router.await_args
    assert args.args[1:4] == ("climate", "set_temperature", "climate.hall")
    assert args.args[4] == {"temperature": 70}


# --------------------------------------------------------------------------- #
# Announcements without HA: Music Assistant first, then Cast by name.         #
# --------------------------------------------------------------------------- #

from services.execution import direct_announce  # noqa: E402
from services.execution.handlers import tv_stage  # noqa: E402

CASTS = [
    {"name": "Office TV Chromecast", "host": "192.0.2.50", "port": 8009},
    {"name": "Living Room speaker", "host": "192.0.2.51", "port": 8009},
]


def test_cast_devices_are_found_by_the_entity_name():
    assert direct_announce.find_cast(CASTS, "media_player.office_tv")["host"] == "192.0.2.50"
    assert direct_announce.find_cast(CASTS, "media_player.living_room_speaker")["host"] == "192.0.2.51"
    assert direct_announce.find_cast(CASTS, "media_player.attic") is None


async def test_an_announcement_goes_to_music_assistant_first(monkeypatch):
    sent = {}
    monkeypatch.setattr(mass_client, "get_players", AsyncMock(return_value=PLAYERS))

    async def fake_announce(url, token, player_id, media_url, volume_level=None):
        sent["args"] = (player_id, media_url, volume_level)

    monkeypatch.setattr(mass_client, "play_announcement", fake_announce)
    discover = AsyncMock(return_value=CASTS)
    monkeypatch.setattr(tv_stage, "discover_cast_devices", discover)
    result = await direct_announce.announce_direct("media_player.kitchen_speaker", "http://jarvis/a.wav", 0.6, _ctx())
    assert result == {"ok": True, "route": "mass_direct", "target": "Kitchen Speaker"}
    assert sent["args"] == ("ma_kitchen", "http://jarvis/a.wav", 60)
    discover.assert_not_awaited()


async def test_a_speaker_music_assistant_does_not_know_is_reached_over_cast(monkeypatch):
    monkeypatch.setattr(mass_client, "get_players", AsyncMock(return_value=PLAYERS))
    monkeypatch.setattr(tv_stage, "discover_cast_devices", AsyncMock(return_value=CASTS))
    played = {}

    def fake_cast(dev, url, volume):
        played["dev"] = dev["host"]
        return True, "Playing"

    monkeypatch.setattr(direct_announce, "_cast_play_sync", fake_cast)
    result = await direct_announce.announce_direct("media_player.office_tv", "http://jarvis/a.wav", 0.5, _ctx())
    assert result["ok"] and result["route"] == "cast_direct"
    assert played["dev"] == "192.0.2.50"


async def test_when_nothing_reaches_the_speaker_the_error_says_why(monkeypatch):
    monkeypatch.setattr(mass_client, "get_players", AsyncMock(side_effect=mass_client.MACommandError("refused")))
    monkeypatch.setattr(tv_stage, "discover_cast_devices", AsyncMock(return_value=[]))
    result = await direct_announce.announce_direct("media_player.attic", "http://jarvis/a.wav", 0.5, _ctx())
    assert result["ok"] is False
    assert "Music Assistant: refused" in result["error"] and "Cast" in result["error"]


# --------------------------------------------------------------------------- #
# Timer alarms: an HA failure must reach the direct fallbacks.                #
# --------------------------------------------------------------------------- #


@pytest.fixture
def trigger(monkeypatch):
    import services.execution.main as m
    from services.execution import esphome_client

    rang = []
    monkeypatch.setattr(m, "resolve_internal_user", AsyncMock(return_value={
        "user": "alex", "ha_url": HA, "ha_token": "t", "mass_url": "http://ma:8095", "mass_token": "m",
    }))
    monkeypatch.setattr(m.timer, "notify_timer_done", AsyncMock())
    monkeypatch.setattr(m.timer, "get_redis", AsyncMock(side_effect=RuntimeError("no redis in tests")))
    monkeypatch.setattr(m, "text_to_speech", AsyncMock(return_value=b""))
    monkeypatch.setattr(m, "cache_temp_audio", lambda *a, **k: None)
    monkeypatch.setattr(esphome_client, "get_devices", AsyncMock(return_value={}))
    monkeypatch.setattr(esphome_client, "find_device_for_ha_entity", AsyncMock(return_value="kitchen_panel"))

    async def ring(name, **kw):
        rang.append((name, kw.get("media_url")))

    monkeypatch.setattr(esphome_client, "trigger_device_alert", ring)
    return m, rang


async def test_a_timer_whose_ha_route_failed_rings_on_the_esphome_fallback(trigger, monkeypatch):
    m, rang = trigger
    import services.execution.announce_handlers as ah

    monkeypatch.setattr(ah, "dispatch_announce", AsyncMock(return_value={"ok": False, "error": "HA returned 500"}))
    monkeypatch.setattr(m.ha_client, "get_state", AsyncMock(return_value=None))
    monkeypatch.setattr(m.ha_client, "call_service", AsyncMock(return_value={"ok": False, "error": "HA returned 500"}))
    result = await m.execute_trigger({"user_id": "alex", "timer": {"title": "Pasta", "target_device": "media_player.kitchen"}})
    assert [name for name, media_url in rang if media_url] == ["kitchen_panel"], rang
    assert "esphome:kitchen_panel" in result.message


async def test_with_ha_down_a_timer_skips_ha_and_reaches_a_speaker_directly(trigger, monkeypatch):
    m, _rang = trigger
    from services.execution import esphome_client

    monkeypatch.setattr(esphome_client, "find_device_for_ha_entity", AsyncMock(return_value=None))
    _mark_down()
    get_state = AsyncMock()
    monkeypatch.setattr(m.ha_client, "get_state", get_state)
    direct = AsyncMock(return_value={"ok": True, "route": "mass_direct", "target": "Kitchen"})
    monkeypatch.setattr(direct_announce, "announce_direct", direct)
    result = await m.execute_trigger({"user_id": "alex", "timer": {"title": "Pasta", "target_device": "media_player.kitchen"}})
    get_state.assert_not_awaited()
    assert direct.await_args.args[0] == "media_player.kitchen"
    assert "mass_direct:media_player.kitchen" in result.message


# --------------------------------------------------------------------------- #
# Last-known states: answered with their age, never passed off as current.    #
# --------------------------------------------------------------------------- #

from services.execution import ha_snapshot  # noqa: E402


def test_a_snapshot_keeps_only_what_answers_questions():
    slim = ha_snapshot.slim([{
        "entity_id": "cover.garage", "state": "closed", "last_changed": "t",
        "attributes": {"friendly_name": "Garage", "entity_picture": "/big.png", "current_position": 0},
    }, {"no": "entity"}])
    assert slim == [{"entity_id": "cover.garage", "state": "closed", "last_changed": "t", "last_updated": None,
                     "attributes": {"friendly_name": "Garage", "current_position": 0}}]


def test_age_phrases_read_naturally():
    import time

    now = time.time()
    assert ha_snapshot.age_phrase(now - 10) == "just now"
    assert ha_snapshot.age_phrase(now - 4 * 60) == "4 minutes ago"
    assert ha_snapshot.age_phrase(now - 3600) == "1 hour ago"
    assert ha_snapshot.age_phrase(now - 3 * 86400) == "3 days ago"


def test_saves_are_throttled_per_host(monkeypatch):
    saved = []

    async def fake_save(url, states):
        saved.append(url)

    monkeypatch.setattr(ha_snapshot, "save", fake_save)

    async def go():
        ha_snapshot.remember(HA, [{"entity_id": "a.b"}])
        ha_snapshot.remember(HA, [{"entity_id": "a.b"}])
        import asyncio

        await asyncio.sleep(0)

    import asyncio

    asyncio.run(go())
    assert saved == [HA]


async def test_entity_search_with_ha_down_answers_from_the_last_snapshot(monkeypatch):
    import time

    import services.execution.main as m

    _mark_down()
    monkeypatch.setattr(
        ha_snapshot, "recall",
        AsyncMock(return_value=([{"entity_id": "cover.garage", "state": "closed",
                                  "attributes": {"friendly_name": "Garage Door"}}], time.time() - 240)),
    )
    from services.execution.schemas import EntitySearchRequest

    req = EntitySearchRequest(user_context={"user": "alex", "is_admin": True, "ha_url": HA, "ha_token": "t"}, query="garage")
    result = await m.execute_entity_search(req)
    assert result.status == "SUCCESS"
    assert result.detail["stale"] is True and result.detail["entities"][0]["state"] == "closed"
    assert "Home Assistant is down" in result.message and "4 minutes ago" in result.message


async def test_entity_search_without_a_snapshot_still_says_it_could_not_reach_ha(monkeypatch):
    import services.execution.main as m

    _mark_down()
    monkeypatch.setattr(ha_snapshot, "recall", AsyncMock(return_value=None))
    from services.execution.schemas import EntitySearchRequest

    req = EntitySearchRequest(user_context={"user": "alex", "is_admin": True, "ha_url": HA, "ha_token": "t"}, query="garage")
    result = await m.execute_entity_search(req)
    assert result.status == "FAILURE" and "Could not reach Home Assistant" in result.message
