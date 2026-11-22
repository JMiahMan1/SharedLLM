"""Music Assistant playback is recorded under, and resumes from, the person's own MA account.

MA keeps play history and resume points per account and credits the account that started
playback. Voice playback goes through HomeHub, which signs in to MA with the household
integration's own login, so execution names the person (``user``, MA's impersonation
argument) whenever identity resolved them to an account on the household server. A
person on their own server, or a household request, names nobody. Values are made up.
"""
import asyncio
from unittest.mock import AsyncMock

from services.common import ma_identity
from services.execution.handlers import media as media_handler
from services.execution.schemas import MediaPlayRequest, UserContext

P_ROKU = "services.execution.handlers.roku."
P_SAMSUNG = "services.execution.handlers.samsung."
BOOK = "library://audiobook/9"


class _Calls:
    def __init__(self, search_reply=None):
        self.calls: list[dict] = []
        self.search_reply = search_reply

    async def __call__(self, ha_url, ha_token, domain, service, entity_id="", service_data=None, return_response=False):
        self.calls.append({"service": service, "service_data": service_data})
        if service == "search":
            return {"ok": True, "service_response": self.search_reply}
        return {"ok": True}


def _ctx(**extra) -> UserContext:
    return UserContext(user="sam", ha_url="http://hub.example.test:8123", ha_token="hub", **extra)


def _patch(monkeypatch, calls):
    monkeypatch.setattr(media_handler, "MASS_CONFIG_ENTRY_ID", "ce-test")
    monkeypatch.setattr(media_handler, "resolve_mass_entity", AsyncMock(return_value="media_player.den"))
    monkeypatch.setattr(f"{P_ROKU}is_roku_device", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{P_SAMSUNG}is_samsung_tv", AsyncMock(return_value=False))
    monkeypatch.setattr(media_handler.ha_client, "call_service", calls)


def _play(ctx, query):
    req = MediaPlayRequest(user_context=ctx, entity_id="media_player.den", query=query, enqueue="replace")
    return asyncio.run(media_handler._play_music_via_ha(req, "media_player.den", ctx))


def test_a_person_on_the_household_server_plays_and_resumes_as_themselves(monkeypatch):
    calls = _Calls()
    _patch(monkeypatch, calls)
    ctx = _ctx(mass_url="http://music.example.test:8095", mass_token="t-sam", mass_server="household",
               mass_user_id="u-sam")
    assert _play(ctx, BOOK).status == "SUCCESS"
    assert calls.calls == [{"service": "play_media",
                            "service_data": {"media_id": BOOK, "enqueue": "replace", "user": "u-sam"}}]


def test_the_search_and_the_play_both_name_the_person(monkeypatch):
    calls = _Calls(search_reply={"tracks": [{"uri": "library://track/3", "name": "Song"}]})
    _patch(monkeypatch, calls)
    ctx = _ctx(mass_url="http://music.example.test:8095", mass_token="t-sam", mass_server="household",
               mass_user_id="u-sam")
    assert _play(ctx, "song").status == "SUCCESS"
    assert [c["service_data"].get("user") for c in calls.calls] == ["u-sam", "u-sam"]


def test_nobody_is_named_for_an_own_server_or_a_household_request(monkeypatch):
    calls = _Calls()
    _patch(monkeypatch, calls)
    _play(_ctx(mass_url="http://mine.example.test:8095", mass_token="t", mass_server="own", mass_user_id="u-x"), BOOK)
    _play(_ctx(), BOOK)
    assert all("user" not in c["service_data"] for c in calls.calls)


def test_the_messages_say_why_and_whose():
    assert ma_identity.act_as({"mass_server": "household", "mass_user_id": "u-1"}) == {"user": "u-1"}
    assert ma_identity.act_as({"mass_server": "household"}) == {}
    assert ma_identity.unavailable_message({"mass_error": "Your Music Assistant at x has no access token."}) \
        == "Your Music Assistant at x has no access token."
    assert ma_identity.unavailable_message({}) == ma_identity.NO_MA_MESSAGE
    own = ma_identity.unreachable_message({"mass_server": "own", "mass_url": "http://mine.example.test:8095"}, "timeout")
    assert own.startswith("Your own Music Assistant at http://mine.example.test:8095 did not answer (timeout)")
    assert "household" in ma_identity.unreachable_message({"mass_server": "household", "mass_url": "u"}, "x")


def test_direct_playback_without_any_music_assistant_says_how_to_set_one_up(monkeypatch):
    ctx = _ctx(mass_error=ma_identity.NO_MA_MESSAGE)
    req = MediaPlayRequest(user_context=ctx, entity_id="media_player.den", query=BOOK)
    result = asyncio.run(media_handler.play_music_direct(req, "media_player.den", ctx, "down"))
    assert result.status == "FAILURE" and "add the Music Assistant integration in HomeHub" in result.message
