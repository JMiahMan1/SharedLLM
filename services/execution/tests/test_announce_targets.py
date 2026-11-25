"""An announcement always has a target, goes only where the hub can deliver, and says what it did.

Every room, device and person here is made up. The house has two rooms: in
the first an Echo-like speaker (two players on one device) beside a dead copy
of an older speaker; in the second a TV that is off and an unavailable "_2"
copy of it. A third room's only speaker is offline.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.execution import announce_targets as at
from services.execution import main as exec_main
from services.execution.capability_index import CapabilityIndex
from services.execution.schemas import AnnouncementRequest, EntitySearchRequest, ExecutionResult, UserContext

HUB = "http://192.0.2.10:8123"
AREAS = [{"area_id": "area_a", "name": "Room A"}, {"area_id": "area_b", "name": "Room B"},
         {"area_id": "area_c", "name": "Room C"}]


def _cap(eid, integration, area, abilities, *, device=None, available=True, tv=False):
    return {"entity_id": eid, "domain": eid.split(".", 1)[0], "integration": integration, "area_id": area,
            "device_id": device, "available": available, "abilities": sorted(abilities),
            "details": {"device_class": "tv"} if tv else {}}


ENTITIES = [
    _cap("assist_satellite.room_a_sat", "esphome", "area_a", set(), device="dev_sat"),
    _cap("media_player.room_a_sat_speaker", "esphome", "area_a", {"play_media", "announce"}, device="dev_sat"),
    _cap("media_player.room_a_old_speaker", "esphome", "area_a", {"play_media", "announce"}, device="dev_old",
         available=False),
    _cap("media_player.room_b_tv", "webostv", "area_b", {"play_media", "show_url", "power"}, device="dev_tv", tv=True),
    _cap("media_player.room_b_tv_2", "webostv", "area_b", {"play_media", "show_url", "power"}, device="dev_tv2",
         tv=True, available=False),
    _cap("media_player.room_c_speaker", "cast", "area_c", {"play_media"}, device="dev_c", available=False),
    _cap("media_player.all_speakers", "group", "area_a", {"play_media"}, device=None),
]


def _state(eid, state, name):
    return {"entity_id": eid, "state": state, "attributes": {"friendly_name": name}}


STATES = [
    _state("assist_satellite.room_a_sat", "idle", "Room A Satellite"),
    _state("media_player.room_a_sat_speaker", "idle", "Room A Speaker"),
    _state("media_player.room_a_old_speaker", "unavailable", "Room A Old Speaker"),
    _state("media_player.room_b_tv", "off", "Room B TV"),
    _state("media_player.room_b_tv_2", "unavailable", "Room B TV 2"),
    _state("media_player.room_c_speaker", "unavailable", "Room C Speaker"),
    _state("media_player.all_speakers", "idle", "All Speakers"),
    _state("sensor.room_a_speaker_ip_address", "unavailable", "Room A Speaker IP address"),
]
BY_STATE = {s["entity_id"]: s for s in STATES}


def _idx():
    return CapabilityIndex(list(ENTITIES), {}, list(AREAS))


# -- choosing targets ---------------------------------------------------------------------------------------------

def test_off_is_reachable_and_unavailable_is_not():
    assert at.reachable(BY_STATE["media_player.room_b_tv"])
    assert at.reachable(BY_STATE["media_player.room_a_sat_speaker"])
    assert not at.reachable(BY_STATE["media_player.room_b_tv_2"])
    assert not at.reachable(None)


@pytest.mark.parametrize("text", ["everywhere", "the whole house", "All Speakers", "the house"])
def test_whole_house_phrases(text):
    assert at.is_everywhere(text)


@pytest.mark.parametrize("text", ["Room A", "room b tv", "", None])
def test_a_device_or_room_is_not_everywhere(text):
    assert not at.is_everywhere(text)


def test_everywhere_is_one_working_speaker_per_room_and_names_the_offline_rooms():
    targets, skipped = at.everywhere(_idx(), BY_STATE)
    # Room A: the working speaker, not its dead neighbour or the group; Room B has only TVs (left alone);
    # Room C's only speaker is offline.
    assert targets == ["media_player.room_a_sat_speaker"]
    assert skipped == ["Room C Speaker"]


def test_the_satellite_spoken_to_announces_on_its_own_speaker():
    assert at.satellite_player(_idx(), "assist_satellite.room_a_sat", BY_STATE) == "media_player.room_a_sat_speaker"
    assert at.satellite_player(_idx(), None, BY_STATE) is None


def test_an_unavailable_device_gets_a_stand_in_in_its_room():
    assert at.alternative_for(_idx(), "media_player.room_a_old_speaker", BY_STATE) == "media_player.room_a_sat_speaker"
    assert at.alternative_for(_idx(), "media_player.room_c_speaker", BY_STATE) is None


def test_a_persons_speakers_come_before_their_tv_and_offline_ones_are_named():
    devices = [
        {"kind": "voice_satellite", "label": "Room A Satellite",
         "entity_ids": ["assist_satellite.room_a_sat", "media_player.room_a_sat_speaker"]},
        {"kind": "speaker", "label": "Room C Speaker", "entity_ids": ["media_player.room_c_speaker"]},
        {"kind": "tv", "label": "Room B TV", "entity_ids": ["media_player.room_b_tv"]},
    ]
    targets, skipped = at.person_players(_idx(), devices, BY_STATE)
    assert targets == ["media_player.room_a_sat_speaker"]
    assert skipped == ["Room C Speaker"]
    # With only a TV assigned, the TV (off, so it is turned on) is used.
    targets, _ = at.person_players(_idx(), devices[2:], BY_STATE)
    assert targets == ["media_player.room_b_tv"]


def test_a_satellite_without_its_own_player_uses_the_one_on_its_device():
    devices = [{"kind": "voice_satellite", "label": "Sat", "entity_ids": ["assist_satellite.room_a_sat"]}]
    assert at.person_players(_idx(), devices, BY_STATE)[0] == ["media_player.room_a_sat_speaker"]


def test_summary_says_what_was_done_and_what_was_skipped():
    text = at.summary(["Room A Speaker", "Room B TV"], [], ["Room C Speaker"], pushed="Sam")
    assert text == ("Announced on Room A Speaker and Room B TV. Sent it to Sam's phone. "
                    "Room C Speaker is offline, so I skipped it.")


def test_device_kind_words_are_not_a_person():
    assert at.names_a_device_kind("office speaker")
    assert at.names_a_device_kind("the den TV")
    assert not at.names_a_device_kind("Sam")


# -- the route ------------------------------------------------------------------------------------------------------

CTX = UserContext(user="parent", is_admin=True, ha_url=HUB, ha_token="t")


@pytest.fixture
def hub(monkeypatch):
    calls: list[str] = []

    async def one(req):
        calls.append(req.entity_id)
        return ExecutionResult(status="SUCCESS", message="ok", service="announce")

    async def routing_index(ha_url, ha_token, purpose):
        return _idx()

    monkeypatch.setattr(exec_main, "_announce_one", one)
    monkeypatch.setattr(exec_main.hardware_router, "hub_pair", lambda ctx: (HUB, "t"))
    monkeypatch.setattr(exec_main.ha_health, "is_down", lambda url: False)
    monkeypatch.setattr("services.execution.capability_index.routing_index", routing_index)
    monkeypatch.setattr(exec_main.ha_client, "get_states", AsyncMock(return_value=STATES))
    monkeypatch.setattr(exec_main, "_announce_person_lookup", AsyncMock(return_value=None))
    pushes = AsyncMock(return_value=True)
    monkeypatch.setattr(exec_main, "_announce_push", pushes)
    return calls, pushes


@pytest.mark.asyncio
async def test_no_device_from_chat_announces_everywhere_instead_of_failing(hub):
    calls, _ = hub
    result = await exec_main.execute_announce(AnnouncementRequest(user_context=CTX, message="Dinner"))
    assert result.status == "SUCCESS"
    assert calls == ["media_player.room_a_sat_speaker"]
    assert "Announced on Room A Speaker" in result.message
    assert "Room C Speaker is offline" in result.message


@pytest.mark.asyncio
async def test_no_device_by_voice_announces_on_the_satellite_spoken_to(hub):
    calls, _ = hub
    ctx = CTX.model_copy(update={"voice_satellite": "assist_satellite.room_a_sat"})
    result = await exec_main.execute_announce(AnnouncementRequest(user_context=ctx, message="Hello"))
    assert result.status == "SUCCESS"
    assert calls == ["media_player.room_a_sat_speaker"]


@pytest.mark.asyncio
async def test_announce_to_a_person_uses_their_devices_and_their_phone(hub, monkeypatch):
    calls, pushes = hub
    person = {"username": "kid", "name": "Kid", "devices": [
        {"kind": "tv", "label": "Room B TV", "entity_ids": ["media_player.room_b_tv"]}]}
    monkeypatch.setattr(exec_main, "_announce_person_lookup", AsyncMock(return_value=person))
    result = await exec_main.execute_announce(
        AnnouncementRequest(user_context=CTX, message="Bedtime", device_name="Kid"))
    assert result.status == "SUCCESS"
    assert calls == ["media_player.room_b_tv"]  # off, so _announce_one turns it on
    pushes.assert_awaited_once()
    assert "Sent it to Kid's phone" in result.message


@pytest.mark.asyncio
async def test_a_named_device_still_goes_straight_to_it(hub):
    calls, _ = hub
    await exec_main.execute_announce(
        AnnouncementRequest(user_context=CTX, message="Hi", device_name="Room B TV"))
    assert calls == [None]  # the single path resolves the name itself


@pytest.mark.asyncio
async def test_when_every_speaker_is_offline_it_says_so_after_checking(hub, monkeypatch):
    calls, _ = hub
    dead = [dict(s, state="unavailable") for s in STATES]
    monkeypatch.setattr(exec_main.ha_client, "get_states", AsyncMock(return_value=dead))
    result = await exec_main.execute_announce(
        AnnouncementRequest(user_context=CTX, message="Hi", everywhere=True))
    assert result.status == "FAILURE"
    assert calls == []
    assert "offline" in result.message


# -- entity search, as a model sees it ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_search_lists_reachable_players_first_without_sensors_or_dead_copies(monkeypatch):
    async def routing_index(ha_url, ha_token, purpose):
        return _idx()

    monkeypatch.setattr(exec_main.hardware_router, "hub_pair", lambda ctx: (HUB, "t"))
    monkeypatch.setattr(exec_main.ha_client, "get_states", AsyncMock(return_value=STATES))
    monkeypatch.setattr("services.execution.capability_index.routing_index", routing_index)
    req = EntitySearchRequest(user_context=CTX, query="room speaker")
    result = await exec_main.execute_entity_search_for_model(req)
    ids = [e["entity_id"] for e in result.detail["entities"]]
    assert "sensor.room_a_speaker_ip_address" not in ids
    assert ids.index("media_player.room_a_sat_speaker") < ids.index("media_player.room_c_speaker")
    assert result.detail["unavailable"] >= 1

    tvs = await exec_main.execute_entity_search_for_model(EntitySearchRequest(user_context=CTX, query="room b tv"))
    tv_ids = [e["entity_id"] for e in tvs.detail["entities"]]
    assert tv_ids[0] == "media_player.room_b_tv"  # an off TV is reachable, so it leads
    assert "media_player.room_b_tv_2" not in tv_ids  # its unavailable "_2" copy is folded away
    assert tvs.detail["duplicates_folded"] >= 1

    # The UI's route is unchanged: every match, in the old order.
    ui = await exec_main.execute_entity_search(EntitySearchRequest(user_context=CTX, query="room b tv"))
    assert {"media_player.room_b_tv", "media_player.room_b_tv_2"} <= {e["entity_id"] for e in ui.detail["entities"]}
