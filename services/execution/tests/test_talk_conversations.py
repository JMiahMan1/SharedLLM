"""Creating, deleting and managing the members of Talk conversations."""
import pytest

from services.execution.handlers import talk as talk_handler


class FakeProvider:
    username = "jeremiah"
    password = "p"
    base_url = "https://cloud.test"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(talk_handler, "resolve_personal_data_provider", lambda _ctx: FakeProvider())


def request(action, **kwargs):
    from services.execution.schemas import TalkRequest

    return TalkRequest(user_context={"user": "jeremiah"}, action=action, **kwargs)


def _talk(monkeypatch, answers):
    calls = []

    async def fake(_provider, method, endpoint, *, params=None, data=None, timeout=30):
        calls.append({"method": method, "endpoint": endpoint, "params": params, "data": data})
        for (m, suffix), answer in answers.items():
            if m == method and endpoint.endswith(suffix):
                return answer
        return True, {}, ""

    monkeypatch.setattr(talk_handler, "_talk_request_with_retry", fake)
    return calls


async def test_a_group_is_created_then_each_person_invited(provider, monkeypatch):
    calls = _talk(monkeypatch, {("POST", "/api/v4/room"): (True, {"token": "abc", "displayName": "Family", "type": 2, "participantType": 1}, "")})
    result = await talk_handler.handle_talk(request("create_group", message="Family", participants=["michele", "kids"]))
    assert result.status == "SUCCESS"
    assert calls[0]["data"] == {"roomType": "2", "roomName": "Family"}
    invited = [c["data"]["newParticipant"] for c in calls[1:]]
    assert invited == ["michele", "kids"] and all(c["endpoint"].endswith("/room/abc/participants") for c in calls[1:])
    assert result.detail["conversation"]["can_moderate"] is True


async def test_a_group_needs_a_name_and_people(provider, monkeypatch):
    _talk(monkeypatch, {})
    assert (await talk_handler.handle_talk(request("create_group", message=" ", participants=["m"]))).status == "FAILURE"
    assert (await talk_handler.handle_talk(request("create_group", message="X", participants=[]))).status == "FAILURE"


async def test_deleting_falls_back_to_leaving_when_not_a_moderator(provider, monkeypatch):
    calls = _talk(monkeypatch, {("DELETE", "/room/abc"): (False, None, "403 forbidden")})
    result = await talk_handler.handle_talk(request("delete_conversation", token="abc"))
    assert result.status == "SUCCESS" and result.detail == {"deleted": False, "left": True}
    assert calls[-1]["endpoint"].endswith("/room/abc/participants/self")


async def test_members_are_listed_added_and_removed(provider, monkeypatch):
    calls = _talk(
        monkeypatch,
        {("GET", "/room/abc/participants"): (True, [{"attendeeId": 5, "actorType": "users", "actorId": "michele", "displayName": "Michele", "participantType": 3}], "")},
    )
    listed = await talk_handler.handle_talk(request("participants", token="abc"))
    assert listed.detail["participants"] == [
        {"attendee_id": 5, "actor_type": "users", "actor_id": "michele", "display_name": "Michele", "is_moderator": False}
    ]
    assert (await talk_handler.handle_talk(request("add_participant", token="abc", target_user="kids"))).status == "SUCCESS"
    assert calls[-1]["data"] == {"newParticipant": "kids", "source": "users"}
    assert (await talk_handler.handle_talk(request("remove_participant", token="abc", attendee_id=5))).status == "SUCCESS"
    assert calls[-1]["method"] == "DELETE" and calls[-1]["params"] == {"attendeeId": "5"}


async def test_people_search_returns_users_only(provider, monkeypatch):
    _talk(monkeypatch, {("GET", "/core/autocomplete/get"): (True, [{"id": "michele", "label": "Michele", "source": "users"}, {"id": "g", "label": "Group", "source": "groups"}], "")})
    result = await talk_handler.handle_talk(request("people", search="mi"))
    assert result.detail["people"] == [{"id": "michele", "label": "Michele"}]
