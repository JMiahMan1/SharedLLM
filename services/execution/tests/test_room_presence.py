"""Room presence from the hub's Bluetooth room detection: where-is, targeting, privacy.

Fakes only: the hub's states are a list in memory, identity's raw-data rule a dict.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.execution import room_presence
from services.execution.handlers import location
from services.execution.handlers.location import LocationRequest, handle_location
from services.execution.room_presence import (
    RoomTargetError,
    expand_room_targets,
    in_room,
    presence_from_states,
    room_of_target,
)
from services.execution.schemas import UserContext

NOW = 1_800_000_000.0


def _iso(ts: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, UTC).isoformat()


def _states(room: str = "Den", seen_ago: float | None = 30, area_id: str = "den", with_sensor: bool = True,
            person_room: str | None = "Den") -> list[dict]:
    person_attrs: dict = {"friendly_name": "Alex", "device_trackers": ["device_tracker.alex_pixel"]}
    if person_room is not None:
        person_attrs.update(room=person_room, room_area_id=area_id)
    out = [{"entity_id": "person.alex", "state": "home", "attributes": person_attrs},
           {"entity_id": "person.sam", "state": "home", "attributes": {"friendly_name": "Sam", "device_trackers": []}}]
    if with_sensor:
        attrs: dict = {"area_id": area_id, "source": "ble", "distance_m": 1.2, "scanner": "den_proxy",
                       "method": "fingerprint", "confidence": 0.9}
        if seen_ago is not None:
            attrs["last_seen"] = _iso(NOW - seen_ago)
        out.append({"entity_id": "sensor.alex_pixel_room", "state": room, "attributes": attrs})
    return out


class TestPresenceFromStates:
    def test_a_fresh_room_is_known(self):
        p = presence_from_states(_states(), "alex", now=NOW, max_age_s=600)
        assert (p.status, p.room, p.area_id) == ("known", "Den", "den")
        assert p.sensor == "sensor.alex_pixel_room"
        assert p.extra == {"method": "fingerprint", "confidence": 0.9}

    def test_an_old_room_is_stale_not_current(self):
        p = presence_from_states(_states(seen_ago=3600), "alex", now=NOW, max_age_s=600)
        assert p.status == "stale" and p.room == "Den"

    def test_a_room_with_no_last_seen_is_not_called_current(self):
        assert presence_from_states(_states(seen_ago=None), "alex", now=NOW).status == "stale"

    def test_unknown_and_not_home(self):
        assert presence_from_states(_states(room="unknown"), "alex", now=NOW).status == "unknown"
        assert presence_from_states(_states(room="not_home"), "alex", now=NOW).status == "away"

    def test_no_beacon_is_not_set_up(self):
        p = presence_from_states(_states(with_sensor=False, person_room=None), "alex", now=NOW)
        assert p.status == "not_set_up"
        assert presence_from_states(_states(), "sam", now=NOW).status == "not_set_up"

    def test_the_person_attribute_alone_is_the_hubs_answer(self):
        p = presence_from_states(_states(with_sensor=False), "alex", now=NOW)
        assert (p.status, p.room, p.area_id) == ("known", "Den", "den")

    def test_an_unknown_name_is_no_person(self):
        assert presence_from_states(_states(), "robin", now=NOW).status == "no_person"

    def test_the_freshest_phone_wins(self):
        states = _states(room="Loft", seen_ago=900, area_id="loft")
        states[0]["attributes"]["device_trackers"].append("device_tracker.alex_tablet")
        states.append({"entity_id": "sensor.alex_tablet_room", "state": "Den",
                       "attributes": {"area_id": "den", "last_seen": _iso(NOW - 20), "source": "ble"}})
        p = presence_from_states(states, "alex", now=NOW, max_age_s=600)
        assert (p.status, p.room) == ("known", "Den")


def test_wording():
    assert in_room("Den") == "in the Den"
    assert in_room("Alex's Room") == "in Alex's Room"
    assert in_room("the Loft") == "in the Loft"
    assert room_of_target("room_of:Alex") == "alex"
    assert room_of_target("Den") is None


# -- privacy -----------------------------------------------------------------------------------

class _Resp:
    def __init__(self, status: int, body: dict):
        self.status, self._body = status, body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _IdentityClient:
    """identity's may-view-raw: ``rules[(viewer, subject)]`` -> allowed."""

    def __init__(self, rules: dict, calls: list, status: int = 200):
        self.rules, self.calls, self.status = rules, calls, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, params=None, headers=None, timeout=None):
        assert url.endswith("/api/internal/may-view-raw")
        self.calls.append((params["viewer"], params["subject"]))
        return _Resp(self.status, {"allowed": self.rules.get((params["viewer"], params["subject"]), False)})


@pytest.fixture
def identity(monkeypatch):
    """Alex and Sam are parents; Robin is their child. The raw-data rule as identity applies it."""
    rules = {("alex", "robin"): True, ("sam", "robin"): True, ("alex", "sam"): True, ("sam", "alex"): True}
    calls: list = []
    monkeypatch.setattr("services.common.http.get_client", lambda *a, **k: _IdentityClient(rules, calls))
    return calls


class TestMaySeeRoom:
    @pytest.mark.asyncio
    async def test_everyone_sees_their_own_without_asking(self, identity):
        assert await room_presence.may_see_room("robin", "robin") is True
        assert identity == []

    @pytest.mark.asyncio
    async def test_parents_see_a_childs_room(self, identity):
        assert await room_presence.may_see_room("alex", "robin") is True
        assert await room_presence.may_see_room("Sam", "Robin") is True

    @pytest.mark.asyncio
    async def test_a_child_does_not_see_a_parents_room(self, identity):
        assert await room_presence.may_see_room("robin", "alex") is False

    @pytest.mark.asyncio
    async def test_identity_down_fails_closed(self, monkeypatch):
        monkeypatch.setattr("services.common.http.get_client",
                            lambda *a, **k: _IdentityClient({("alex", "robin"): True}, [], status=503))
        assert await room_presence.may_see_room("alex", "robin") is False

    @pytest.mark.asyncio
    async def test_no_viewer_sees_nothing(self, identity):
        assert await room_presence.may_see_room("", "robin") is False


# -- "where is" ------------------------------------------------------------------------------------

def _ctx(user: str) -> UserContext:
    return UserContext(user=user, ha_url="http://192.0.2.10:8123", ha_token="fake-token")


class _GeoClient:
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, *a, **k):
        return _Resp(self.status, self.body)


@pytest.fixture
def hub(monkeypatch):
    holder = {"states": _states(), "fail": False}

    async def fake_states(url, token, account="", strict=False):
        if holder["fail"]:
            raise RuntimeError("hub down")
        return holder["states"]

    monkeypatch.setattr("services.execution.ha_client.get_states", fake_states)
    monkeypatch.setattr(room_presence.time, "time", lambda: NOW)
    return holder


def _geo(monkeypatch, status=200, body=None):
    body = body if body is not None else {"status": "ok", "speech": "Alex is at Home.", "friendly_name": "Alex"}
    monkeypatch.setattr("services.common.http.get_client_insecure", lambda *a, **k: _GeoClient(status, body))


class TestWhereIs:
    @pytest.mark.asyncio
    async def test_the_room_answers_when_known(self, hub, identity, monkeypatch):
        _geo(monkeypatch)
        r = await handle_location(LocationRequest(user_context=_ctx("alex"), user="alex"))
        assert r.message == "Alex is in the Den (Bluetooth room detection, heard just now)."
        assert r.detail["answered_by"] == "room"
        assert r.detail["room"]["area_id"] == "den"

    @pytest.mark.asyncio
    async def test_a_parent_hears_the_childs_room(self, hub, identity, monkeypatch):
        hub["states"] = [{**s, "entity_id": s["entity_id"].replace("alex", "robin")} for s in _states()]
        hub["states"][0]["attributes"] = {**hub["states"][0]["attributes"],
                                          "device_trackers": ["device_tracker.robin_pixel"]}
        _geo(monkeypatch)
        r = await handle_location(LocationRequest(user_context=_ctx("sam"), user="robin"))
        assert "Robin is in the Den" in r.message

    @pytest.mark.asyncio
    async def test_a_child_asking_about_a_parent_gets_geo_and_no_room(self, hub, identity, monkeypatch):
        _geo(monkeypatch)
        r = await handle_location(LocationRequest(user_context=_ctx("robin"), user="alex"))
        assert r.message == "Alex is at Home."
        assert "Den" not in r.message and "room" not in r.message.lower()

    @pytest.mark.asyncio
    async def test_stale_falls_back_to_geo_and_says_which_source(self, hub, identity, monkeypatch):
        hub["states"] = _states(seen_ago=3600)
        _geo(monkeypatch)
        r = await handle_location(LocationRequest(user_context=_ctx("alex"), user="alex"))
        assert r.message.startswith("Alex is at Home. (That is from their phone's location;")
        assert "last heard Alex in the Den 1 hour ago" in r.message
        assert r.detail["answered_by"] == "geo"

    @pytest.mark.asyncio
    async def test_unknown_room_and_no_geo_says_so(self, hub, identity, monkeypatch):
        hub["states"] = _states(room="unknown")
        _geo(monkeypatch, status=404, body={"detail": "none"})
        r = await handle_location(LocationRequest(user_context=_ctx("alex"), user="alex"))
        assert r.message == ("I don't have a current location for Alex right now. "
                             "Room detection can't tell which room Alex is in right now.")

    @pytest.mark.asyncio
    async def test_no_beacon_keeps_the_plain_geo_answer(self, hub, identity, monkeypatch):
        _geo(monkeypatch, body={"status": "ok", "speech": "Sam is at Home.", "friendly_name": "Sam"})
        r = await handle_location(LocationRequest(user_context=_ctx("sam"), user="sam"))
        assert r.message == "Sam is at Home."

    @pytest.mark.asyncio
    async def test_a_hub_that_cannot_be_read_is_said(self, hub, identity, monkeypatch):
        hub["fail"] = True
        _geo(monkeypatch)
        r = await handle_location(LocationRequest(user_context=_ctx("alex"), user="alex"))
        assert "room detection could not be read" in r.message

    @pytest.mark.asyncio
    async def test_speed_questions_do_not_ask_the_hub(self, hub, identity, monkeypatch):
        hub["fail"] = True
        _geo(monkeypatch, body={"status": "ok", "friendly_name": "Alex", "current_speed_mph": 0,
                                "top_speed_mph": 30, "is_moving": False})
        r = await handle_location(LocationRequest(user_context=_ctx("alex"), user="alex", detail="speed"))
        assert "room" not in r.message.lower()

    @pytest.mark.asyncio
    async def test_the_espresense_fallback_respects_privacy_too(self, monkeypatch, identity):
        class Tracker:
            async def get_user_presence(self, user):
                return {"room": "loft"}

        monkeypatch.setattr("services.execution.presence.get_presence_tracker", lambda: Tracker())
        _geo(monkeypatch, status=404, body={})
        r = await handle_location(LocationRequest(user_context=UserContext(user="robin"), user="alex"))
        assert "loft" not in r.message.lower()
        r = await handle_location(LocationRequest(user_context=UserContext(user="alex"), user="robin"))
        assert r.message == "Robin is in the loft."


# -- targeting ---------------------------------------------------------------------------------

class TestTargets:
    @pytest.mark.asyncio
    async def test_room_of_becomes_the_area(self, hub):
        rooms, found = await expand_room_targets(["Kitchen", "room_of:alex"], "http://192.0.2.10", "t")
        assert rooms == ["Kitchen", "den"]
        assert found[0].room == "Den"

    @pytest.mark.asyncio
    async def test_unknown_or_stale_raises_with_the_reason(self, hub):
        hub["states"] = _states(seen_ago=3600)
        with pytest.raises(RoomTargetError, match="can't say they are still there"):
            await expand_room_targets(["room_of:alex"], "http://192.0.2.10", "t")
        hub["states"] = _states(room="unknown")
        with pytest.raises(RoomTargetError, match="can't tell which room"):
            await expand_room_targets(["room_of:alex"], "http://192.0.2.10", "t")

    @pytest.mark.asyncio
    async def test_no_hub_credentials_is_said(self):
        with pytest.raises(RoomTargetError, match="not configured"):
            await expand_room_targets(["room_of:alex"], "", "")

    @pytest.mark.asyncio
    async def test_resolve_targets_routes_the_area_through_the_capability_index(self, hub, monkeypatch):
        from services.execution import targets

        async def no_map():
            return {}

        seen = {}

        class Idx:
            def require_area(self, room):
                seen["room"] = room
                return room

            def media_targets(self, area_id):
                return [f"media_player.{area_id}_speaker"]

        async def fake_index(url, token, purpose):
            return Idx()

        monkeypatch.setattr(targets, "fetch_room_speakers", no_map)
        monkeypatch.setattr(targets.capability_index, "routing_index", fake_index)
        out = await targets.resolve_targets(rooms=["room_of:alex"], ha_url="http://192.0.2.10", ha_token="t")
        assert out == ["media_player.den_speaker"]
        assert seen["room"] == "den"

    @pytest.mark.asyncio
    async def test_an_announcement_to_an_unknown_room_says_why(self, hub, monkeypatch):
        from services.execution.announce_routes import run_announcement

        hub["states"] = _states(room="unknown")

        async def must_not_announce(*a, **k):
            raise AssertionError("nothing may be announced to a guessed room")

        req = SimpleNamespace(target_devices=None, target_entity_ids=None, target_rooms=["room_of:alex"],
                              group_id=None, message="Dinner", volume=None)
        out = await run_announcement(req, _ctx("sam"), announce_fn=must_not_announce)
        assert out["status"] == "FAILURE"
        assert out["message"] == "Not announced: room detection can't tell which room Alex is in right now."

    @pytest.mark.asyncio
    async def test_a_timer_rings_where_the_person_is_when_it_rings(self, hub):
        from services.execution.handlers.timer import room_of_ring_target

        assert await room_of_ring_target("room_of:alex", "http://192.0.2.10", "t") == ("den", None)
        assert await room_of_ring_target("Kitchen", "http://192.0.2.10", "t") == ("Kitchen", None)
        hub["states"] = _states(seen_ago=3600)
        target, why = await room_of_ring_target("room_of:alex", "http://192.0.2.10", "t")
        assert target == "room_of:alex" and "can't say they are still there" in why

    @pytest.mark.asyncio
    async def test_intercom_to_a_person_uses_the_hub_room(self, hub):
        from services.execution.handlers.intercom import _room_for_intercom

        assert await _room_for_intercom(None, "alex", "http://192.0.2.10", "t") == ("den", "alex")
        assert await _room_for_intercom("Loft", "alex", "http://192.0.2.10", "t") == ("Loft", None)
        hub["states"] = _states(room="unknown")
        with pytest.raises(RoomTargetError):
            await _room_for_intercom("room_of:alex", None, "http://192.0.2.10", "t")
        with pytest.raises(RoomTargetError):
            await _room_for_intercom(None, "alex", "http://192.0.2.10", "t")

    @pytest.mark.asyncio
    async def test_intercom_falls_back_to_espresense_only_without_a_beacon(self, hub, monkeypatch):
        from services.execution.handlers import intercom

        async def esp(user):
            return "kitchen"

        monkeypatch.setattr(intercom, "_resolve_user_room", esp)
        assert await intercom._room_for_intercom(None, "sam", "http://192.0.2.10", "t") == ("kitchen", "sam")


def test_location_module_keeps_its_public_names():
    assert callable(location._room_answer)
