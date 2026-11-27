"""Native Echo-to-Echo Drop In: hub calls, result events, mute gating and privacy."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from services.execution import capability_index, ha_client, hardware_router, room_presence
from services.execution.handlers import drop_in
from services.execution.satellite_timers import resolve_room_satellite
from services.execution.schemas import UserContext
from services.execution.schemas_drop_in import DropInRequest

HUB = "http://hub.example.test:8123"
TOKEN = "test-token"
SOURCE_SAT = "assist_satellite.kitchen_echo"
EVENT = "event.kitchen_drop_in_result"
OLD = "2026-01-01T12:00:00+00:00"
NEW = "2026-01-01T12:00:01+00:00"


def _states(*extra: dict, source_state: str = "idle", den_state: str = "idle") -> list[dict]:
    return [
        {"entity_id": "sensor.kitchen_drop_in", "state": source_state, "attributes": {"friendly_name": "Kitchen"}},
        {"entity_id": "button.kitchen_end_drop_in", "state": "unknown", "attributes": {}},
        {"entity_id": EVENT, "state": OLD, "attributes": {"event_type": "unknown"}},
        {"entity_id": SOURCE_SAT, "state": "idle", "attributes": {"friendly_name": "Kitchen"}},
        {"entity_id": "sensor.den_drop_in", "state": den_state, "attributes": {"friendly_name": "Den"}},
        {"entity_id": "button.den_end_drop_in", "state": "unknown", "attributes": {}},
        {"entity_id": "event.den_drop_in_result", "state": OLD, "attributes": {}},
        {"entity_id": "assist_satellite.den_echo", "state": "idle", "attributes": {"friendly_name": "Den"}},
        *extra,
    ]


def _result_poll(outcome: str, *, advance: bool = True):
    """The result event's ISO ``state`` fires once, then stays stale unless advanced."""
    async def get_state(_url, _token, entity_id):
        stamp = OLD if not advance or len(get_state.calls) == 0 else NEW
        get_state.calls.append(entity_id)
        return {"entity_id": entity_id, "state": stamp, "attributes": {"event_type": outcome}}

    get_state.calls = []
    return get_state


@pytest.fixture
def hub(monkeypatch):
    """A kitchen Echo asking for the den, with a quiet hub and a fresh identity check."""
    states = _states()
    monkeypatch.setattr(hardware_router, "hub_pair", lambda ctx: (HUB, TOKEN))
    monkeypatch.setattr(ha_client, "get_states", AsyncMock(return_value=states))
    monkeypatch.setattr(capability_index, "routing_index", AsyncMock(return_value=None))
    monkeypatch.setattr(ha_client, "call_service", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(ha_client, "get_state", AsyncMock(return_value=None))
    monkeypatch.setattr(room_presence, "may_see_room", AsyncMock(return_value=True))
    monkeypatch.setattr(room_presence, "resolve_room_target", AsyncMock(
        return_value=room_presence.RoomPresence("known", "alex", room="Den", area_id="den")))
    monkeypatch.setattr(drop_in, "DROP_IN_WAIT_S", 0.09)
    monkeypatch.setattr(drop_in, "DROP_IN_POLL_S", 0.005)
    return SimpleNamespace(states=states, listed=ha_client.get_states, call=ha_client.call_service,
                           poll=ha_client.get_state, see=room_presence.may_see_room,
                           resolve=room_presence.resolve_room_target)


def _ctx(satellite: str | None = SOURCE_SAT, user: str = "sam") -> UserContext:
    return UserContext(user=user, ha_url=HUB, ha_token=TOKEN, voice_satellite=satellite)


def _req(action: str = "start", **kwargs) -> DropInRequest:
    return DropInRequest(action=action, **kwargs)


def test_timestamp_reads_the_event_state_and_compares_aware_and_naive_stamps():
    before = drop_in._timestamp({"state": OLD})
    assert before == datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert drop_in._timestamp({"state": "2026-01-01T12:00:00Z"}) == before
    assert drop_in._timestamp({"state": "2026-01-01T12:00:00"}) == before
    assert drop_in._timestamp({"last_updated": NEW, "state": OLD}) == datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)
    assert drop_in._timestamp({}) is None and drop_in._timestamp(None) is None
    assert drop_in._advanced({"state": OLD}, before) is False  # the same stale event is not new
    assert drop_in._advanced({"state": NEW}, before) is True
    assert drop_in._advanced({"state": "2026-01-01T12:00:01"}, before) is True
    assert drop_in._advanced({"state": NEW}, None) is True
    assert drop_in._advanced({}, None) is False  # never mistake no timestamp for a new event
    assert drop_in._advanced({}, before) is False


@pytest.mark.parametrize(("outcome", "message"), [
    ("connected", "Dropping in on Den."),
    ("ringing", "Ringing Den."),
])
async def test_start_calls_the_source_and_waits_for_the_result_event(hub, outcome, message):
    hub.poll.side_effect = _result_poll(outcome)

    result = await drop_in.handle_drop_in(_req(target_room="den"), _ctx())

    assert (result.status, result.message) == ("SUCCESS", message)
    assert result.detail == {"event": outcome, "target": "den", "source": "kitchen"}
    hub.call.assert_awaited_once_with(HUB, TOKEN, "esphome", "kitchen_drop_in", service_data={"target": "den"})
    assert hub.poll.await_count >= 2
    assert all(call.args == (HUB, TOKEN, EVENT) for call in hub.poll.await_args_list)


@pytest.mark.parametrize(("outcome", "message"), [
    ("do_not_disturb", "Den is in Do Not Disturb."),
    ("busy", "Den is already on another call."),
    ("microphones_off", "Den has its microphones muted."),
    ("drop_in_off", "Drop In is turned off on Den."),
    ("no_answer", "Den did not answer."),
    ("declined", "Den did not answer."),
])
async def test_start_speaks_a_hub_result_that_refused_the_call(hub, outcome, message):
    hub.poll.side_effect = _result_poll(outcome)

    result = await drop_in.handle_drop_in(_req(target_room="den"), _ctx())

    assert (result.status, result.message) == ("FAILURE", message)
    assert result.detail["event"] == outcome
    assert result.detail["target"] == "den"


async def test_a_stale_result_event_is_not_accepted_as_the_answer(hub):
    hub.poll.side_effect = _result_poll("no_answer", advance=False)

    result = await drop_in.handle_drop_in(_req(target_room="den"), _ctx())

    assert result.status == "FAILURE" and result.message == "Den did not answer."
    assert result.detail["timed_out"] is True
    assert hub.poll.await_count >= 2


@pytest.mark.parametrize(("extra", "message"), [
    ([{"entity_id": "switch.den_do_not_disturb", "state": "on"}], "Den is in Do Not Disturb."),
    ([{"entity_id": "sensor.den_drop_in", "state": "connected"}], "Den is already on another call."),
    ([{"entity_id": "sensor.den_drop_in", "state": "off"}], "Drop In is turned off on Den."),
])
async def test_start_checks_the_destination_before_calling_the_hub(hub, extra, message):
    hub.listed.return_value = _states(*extra)

    result = await drop_in.handle_drop_in(_req(target_room="den"), _ctx())

    assert result.status == "FAILURE" and result.message == message
    hub.call.assert_not_awaited()


@pytest.mark.parametrize("node", ["kitchen", "den"])
@pytest.mark.parametrize(("entity", "power", "muted"), [
    ("microphone", "on", False),  # mic enabled is not muted
    ("mute", "on", True),
    ("microphone", "off", True),
    ("microphones_off", "on", True),
    ("mic_mute", "on", True),
])
def test_discover_reads_mute_and_microphone_switches_by_their_power(node, entity, power, muted):
    nodes = drop_in._discover(_states({"entity_id": f"switch.{node}_{entity}", "state": power}))
    assert nodes[node].muted is muted


@pytest.mark.parametrize(("entity", "power", "blocked"), [
    ("microphone", "on", False),
    ("mute", "on", True),
    ("microphone", "off", True),
])
async def test_a_live_microphone_switch_does_not_block_a_start(hub, entity, power, blocked):
    hub.listed.return_value = _states({"entity_id": f"switch.den_{entity}", "state": power})
    hub.poll.side_effect = _result_poll("connected")

    result = await drop_in.handle_drop_in(_req(target_room="den"), _ctx())

    if blocked:
        assert result.status == "FAILURE" and result.message == "Den has its microphones muted."
        hub.call.assert_not_awaited()
    else:
        assert result.status == "SUCCESS"
        hub.call.assert_awaited_once()


async def test_calling_your_own_room_is_refused(hub):
    result = await drop_in.handle_drop_in(_req(target_room="kitchen"), _ctx())

    assert result.status == "FAILURE" and result.message == "You are already in the Kitchen."
    hub.call.assert_not_awaited()


_PERSON_TARGETS = [
    ("target_user_id", "alex"),
    ("target_room", "room_of:alex"),
]


@pytest.mark.parametrize(("field", "value"), _PERSON_TARGETS)
async def test_an_allowed_viewer_can_drop_in_on_the_persons_room(hub, field, value):
    hub.poll.side_effect = _result_poll("connected")

    result = await drop_in.handle_drop_in(_req(**{field: value}), _ctx())

    assert result.status == "SUCCESS"
    hub.see.assert_awaited_once_with("sam", "alex")
    hub.resolve.assert_awaited_once_with("alex", HUB, TOKEN)
    hub.call.assert_awaited_once_with(HUB, TOKEN, "esphome", "kitchen_drop_in", service_data={"target": "den"})


@pytest.mark.parametrize(("field", "value"), _PERSON_TARGETS)
async def test_a_disallowed_viewer_is_refused_before_room_resolution(hub, field, value):
    hub.see.return_value = False
    hub.resolve.side_effect = AssertionError("presence lookup must not run before permission is checked")

    result = await drop_in.handle_drop_in(_req(**{field: value}), _ctx())

    assert result.status == "FAILURE" and result.message == "I can't share where alex is."
    assert "den" not in result.message.lower()  # never reveal the private room
    hub.resolve.assert_not_awaited()
    hub.call.assert_not_awaited()


async def test_a_person_whose_room_cannot_be_resolved_fails_loudly(hub):
    hub.resolve.side_effect = room_presence.RoomTargetError("room detection says alex is not home")

    result = await drop_in.handle_drop_in(_req(target_user_id="alex"), _ctx())

    assert result.status == "FAILURE" and "not home" in result.message
    hub.call.assert_not_awaited()


@pytest.mark.parametrize(("pair", "message"), [
    ((None, TOKEN), hardware_router.NO_HUB_MESSAGE),
    ((HUB, None), "The home hub token is not configured for this user."),
])
async def test_missing_hub_settings_fail_fast(monkeypatch, pair, message):
    monkeypatch.setattr(hardware_router, "hub_pair", lambda ctx: pair)

    result = await drop_in.handle_drop_in(_req(target_room="den"), _ctx())

    assert result.status == "FAILURE" and result.message == message


async def test_end_presses_the_active_nodes_button(hub):
    hub.listed.return_value = _states(source_state="connected")

    result = await drop_in.handle_drop_in(_req(action="end"), _ctx())

    assert (result.status, result.message) == ("SUCCESS", "Drop In ended.")
    assert result.detail == {"node": "kitchen"}
    hub.call.assert_awaited_once_with(HUB, TOKEN, "button", "press", entity_id="button.kitchen_end_drop_in")


async def test_end_uses_the_only_active_call_without_a_voice_satellite(hub):
    hub.listed.return_value = _states(den_state="ringing")

    result = await drop_in.handle_drop_in(_req(action="end"), _ctx(satellite=None))

    assert result.status == "SUCCESS" and result.detail == {"node": "den"}
    hub.call.assert_awaited_once_with(HUB, TOKEN, "button", "press", entity_id="button.den_end_drop_in")


async def test_end_without_an_active_call_fails_clearly(hub):
    result = await drop_in.handle_drop_in(_req(action="end"), _ctx())

    assert result.status == "FAILURE" and result.message == "No Drop-In is active."
    hub.call.assert_not_awaited()


async def test_status_is_idle_then_summarises_an_active_call(hub):
    idle = await drop_in.handle_drop_in(_req(action="status"), _ctx())
    assert idle.message == "No Drop-In calls are active." and idle.detail == {"calls": []}

    hub.listed.return_value = _states(
        {"entity_id": "sensor.den_drop_in_with", "state": "kitchen"}, den_state="connected"
    )
    active = await drop_in.handle_drop_in(_req(action="status"), _ctx())

    assert active.message == "Drop In active: Den is connected with Kitchen."
    assert active.detail == {"calls": [{"node": "den", "label": "Den", "state": "connected", "with": "kitchen"}]}
    hub.call.assert_not_awaited()


def test_room_satellite_uses_states_without_a_fallback_for_a_named_room():
    kitchen = {"entity_id": "assist_satellite.kitchen_echo", "attributes": {"friendly_name": "Kitchen"}}
    den = {"entity_id": "assist_satellite.den_echo", "attributes": {"friendly_name": "Den"}}

    assert resolve_room_satellite([kitchen, den], "den") == "assist_satellite.den_echo"
    assert resolve_room_satellite([kitchen, den], "The Den") == "assist_satellite.den_echo"
    assert resolve_room_satellite([kitchen], "den", "assist_satellite.kitchen_echo") is None
    assert resolve_room_satellite([], "", "assist_satellite.kitchen_echo") == "assist_satellite.kitchen_echo"
    assert resolve_room_satellite([], "", "light.den") is None


class _FakeIndex:
    def __init__(self, satellites: list[str]):
        self.satellites = satellites

    def require_area(self, room: str) -> str:
        if room.lower() != "den":
            raise capability_index.UnknownAreaError(f"There is no area {room!r} on the hub")
        return "den"

    def in_area(self, area_id: str, *, domain: str | None = None) -> list[str]:
        return list(self.satellites) if domain == "assist_satellite" and area_id == "den" else []


def test_room_satellite_through_the_index_never_falls_back_to_another_room():
    idx = _FakeIndex(["assist_satellite.den_echo"])
    assert resolve_room_satellite([], "den", "assist_satellite.kitchen_echo", idx) == "assist_satellite.den_echo"
    empty = _FakeIndex([])
    assert resolve_room_satellite([], "den", "assist_satellite.kitchen_echo", empty) is None
    assert resolve_room_satellite([], "", "assist_satellite.kitchen_echo", empty) == "assist_satellite.kitchen_echo"


@pytest.mark.parametrize(
    ("action_in", "action_out"),
    [
        ("hang_up", "end"),
        ("hangup", "end"),
        ("hang up", "end"),
        ("stop", "end"),
        ("end_call", "end"),
        ("leave", "end"),
        ("begin", "start"),
        ("call", "start"),
        ("drop_in", "start"),
        ("start_call", "start"),
        ("list", "status"),
        ("active", "status"),
    ],
)
def test_action_alias_mapping(action_in: str, action_out: str):
    req = DropInRequest(action=action_in)
    assert req.action == action_out


def test_unknown_action_still_raises():
    with pytest.raises(ValidationError):
        DropInRequest(action="jump")
