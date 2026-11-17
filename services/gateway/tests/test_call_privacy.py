"""Calls are private: only a conversation's members join or see its call, and the gateway leaks nothing.

Fake people throughout: Robin and Casey are the parents with a private chat, Kit is their child, Alex is an
admin who is not in the private chat.
"""
import asyncio
import json
import logging

import pytest
from starlette.websockets import WebSocketDisconnect

from services.gateway import calls

PEOPLE = {
    "robin-token": {"username": "robin", "full_name": "Robin", "family_role": "mother", "nextcloud_user": "Robin"},
    "casey-token": {"username": "casey", "full_name": "Casey", "family_role": "father", "nextcloud_user": "casey"},
    "kit-token": {"username": "kit", "full_name": "Kit", "family_role": "child", "nextcloud_user": "kit"},
    "alex-token": {"username": "alex", "full_name": "Alex", "is_admin": True, "nextcloud_user": "alex"},
}
PRIVATE = {"pc-room": ["robin", "casey"]}
#: Each person's own Talk conversations (what Talk lists to them).
TALK = {
    "robin": {"family", "robin-kit", "pc-room"},
    "casey": {"family", "pc-room"},
    "kit": {"family", "robin-kit"},
    "alex": {"family"},
}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(calls, "hub", calls.CallHub())
    monkeypatch.setattr(calls, "_membership_cache", {})
    asked: list[str] = []

    async def fake_user(token):
        person = PEOPLE.get(token)
        return dict(person) if person else None

    async def conversations(api_key):
        who = PEOPLE[api_key]["username"]
        asked.append(who)
        tokens = TALK.get(who)
        return None if tokens is None else set(tokens)

    monkeypatch.setattr(calls, "_user_for_token", fake_user)
    monkeypatch.setattr(calls, "_private_members", lambda room: list(PRIVATE[room]) if room in PRIVATE else None)
    monkeypatch.setattr(calls, "_talk_conversations_as", conversations)
    return asked


def _url(who, room):
    return f"/api/communication/calls/ws?token={who}-token&room={room}&name=Call"


def _refused(client, who, room):
    """Join and expect a refusal: the error message, then close code 1008 with the reason."""
    with client.websocket_connect(_url(who, room)) as ws:
        error = ws.receive_json()
        assert error["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1008
    assert closed.value.reason
    return error["message"]


def _calls_for(c, who):
    return c.get("/api/communication/calls", headers={"Authorization": f"Bearer {who}-token"}).json()["calls"]


# --- the private chat ---------------------------------------------------------


def test_a_child_cannot_join_the_parents_private_chat_call(one_loop_client, world):
    message = _refused(one_loop_client, "kit", "pc-room")
    assert message == "Only the two people in this private chat can join its calls."


def test_an_admin_who_is_not_in_the_private_chat_cannot_join_it_either(one_loop_client, world):
    TALK["alex"].add("pc-room")  # even if Talk were to list it to them
    try:
        assert "private chat" in _refused(one_loop_client, "alex", "pc-room")
    finally:
        TALK["alex"].discard("pc-room")


def test_the_two_parents_join_their_private_call_and_it_says_it_is_private(one_loop_client, world):
    c = one_loop_client
    with c.websocket_connect(_url("robin", "pc-room")) as robin:
        welcome = robin.receive_json()
        assert welcome["type"] == "welcome" and welcome["private"] is True
        with c.websocket_connect(_url("casey", "pc-room")) as casey:
            assert casey.receive_json()["private"] is True
            robin.receive_json()  # peer-joined
            # Listed to the two of them, flagged private, and to nobody else (not even before any ring).
            [listed] = _calls_for(c, "casey")
            assert listed["private"] is True
            assert _calls_for(c, "kit") == [] and _calls_for(c, "alex") == []
            casey.send({"type": "websocket.disconnect", "code": 1000})
            robin.receive_json()  # peer-left
        robin.send({"type": "websocket.disconnect", "code": 1000})
    # The private chat never asks Talk who is in it.
    assert world == []


def test_a_ring_never_widens_a_private_calls_members(one_loop_client, world):
    c = one_loop_client
    with c.websocket_connect(_url("robin", "pc-room")) as robin:
        robin.receive_json()
        assert asyncio.run(calls.hub.ring("pc-room", "robin", "Robin", {"casey", "kit"})) is True
        assert _calls_for(c, "kit") == []
        assert [x["token"] for x in _calls_for(c, "casey")] == ["pc-room"]
        robin.send({"type": "websocket.disconnect", "code": 1000})
    # And being rung is not a way in.
    assert "private chat" in _refused(c, "kit", "pc-room")


# --- Talk conversations -------------------------------------------------------


def test_someone_who_is_not_in_the_talk_conversation_is_refused(one_loop_client, world):
    message = _refused(one_loop_client, "casey", "robin-kit")
    assert message == "Only the people in this conversation can join its call."


def test_a_conversation_member_joins_and_the_answer_is_cached_briefly(one_loop_client, world):
    c = one_loop_client
    for _ in range(2):
        with c.websocket_connect(_url("kit", "robin-kit")) as kit:
            assert kit.receive_json()["type"] == "welcome"
            kit.send({"type": "websocket.disconnect", "code": 1000})
    assert world == ["kit"]  # Talk was asked once


def test_when_talk_cannot_be_asked_nobody_gets_in(one_loop_client, world, monkeypatch):
    async def unreachable(api_key):
        return None

    monkeypatch.setattr(calls, "_talk_conversations_as", unreachable)
    assert _refused(one_loop_client, "kit", "robin-kit") == "Could not check that you are in this conversation. Try again."


def test_someone_the_call_rang_may_join_without_being_in_the_talk_room(one_loop_client, world):
    """A one-to-one room the other person left still rings them; the ring is what lets them in."""
    c = one_loop_client
    with c.websocket_connect(_url("robin", "robin-kit")) as robin:
        robin.receive_json()
        assert _refused(c, "casey", "robin-kit")
        assert asyncio.run(calls.hub.ring("robin-kit", "robin", "Robin", {"casey"})) is True
        with c.websocket_connect(_url("casey", "robin-kit")) as casey:
            assert casey.receive_json()["type"] == "welcome"
            casey.send({"type": "websocket.disconnect", "code": 1000})
        robin.send({"type": "websocket.disconnect", "code": 1000})


def test_the_call_list_never_shows_a_call_to_a_non_member(one_loop_client, world):
    c = one_loop_client
    with c.websocket_connect(_url("robin", "robin-kit")) as robin:
        robin.receive_json()
        # Before anyone rang: only the person in it.
        assert _calls_for(c, "kit") == [] and _calls_for(c, "casey") == []
        assert len(_calls_for(c, "robin")) == 1
        asyncio.run(calls.hub.ring("robin-kit", "robin", "Robin", {"kit"}))
        assert [x["token"] for x in _calls_for(c, "kit")] == ["robin-kit"]
        assert _calls_for(c, "casey") == []
        robin.send({"type": "websocket.disconnect", "code": 1000})


def test_a_drop_in_still_answers_by_itself_for_whoever_allows_the_caller(one_loop_client, world):
    c = one_loop_client
    with c.websocket_connect(_url("robin", "family")) as robin:
        robin.receive_json()
        asyncio.run(calls.hub.ring("family", "robin", "Robin", {"kit", "casey"}, {"kit"}))
        [for_kit] = _calls_for(c, "kit")
        assert for_kit["ringing"] and for_kit["drop_in"] is True
        with c.websocket_connect(_url("kit", "family")) as kit:
            assert kit.receive_json()["type"] == "welcome"
            kit.send({"type": "websocket.disconnect", "code": 1000})
        robin.send({"type": "websocket.disconnect", "code": 1000})


# --- the fingerprint proof is relayed, opaque ---------------------------------


def test_auth_messages_are_relayed_as_they_came_and_nothing_else_rides_along(one_loop_client, world):
    c = one_loop_client
    with c.websocket_connect(_url("robin", "pc-room")) as robin:
        robin_id = robin.receive_json()["self"]["id"]
        with c.websocket_connect(_url("casey", "pc-room")) as casey:
            casey.receive_json()
            robin.receive_json()
            proof = {"v": 1, "step": "proof", "mac": "bWFj"}
            casey.send_json({"type": "auth", "to": robin_id, "auth": proof, "sdp": "v=0 sneaky"})
            got = robin.receive_json()
            assert got["type"] == "auth" and got["auth"] == proof and "sdp" not in got
            # An oversized or malformed proof is dropped, never relayed.
            casey.send_json({"type": "auth", "to": robin_id, "auth": {"mac": "x" * 5000}})
            casey.send_json({"type": "auth", "to": robin_id, "auth": "not an object"})
            casey.send_json({"type": "ping"})
            assert casey.receive_json() == {"type": "pong"}
            casey.send({"type": "websocket.disconnect", "code": 1000})
            assert robin.receive_json()["type"] == "peer-left"
        robin.send({"type": "websocket.disconnect", "code": 1000})


# --- logs ---------------------------------------------------------------------


def test_the_gateway_never_logs_sdp_candidates_or_the_api_key(one_loop_client, world, caplog):
    c = one_loop_client
    sdp = "v=0\r\na=fingerprint:sha-256 AA:BB\r\nc=IN IP4 198.51.100.23\r\n"
    candidate = {"candidate": "candidate:1 1 udp 2122260223 203.0.113.77 54321 typ host", "sdpMid": "0"}
    with caplog.at_level(logging.DEBUG):
        with c.websocket_connect(_url("robin", "pc-room")) as robin:
            robin_id = robin.receive_json()["self"]["id"]
            with c.websocket_connect(_url("casey", "pc-room")) as casey:
                casey.receive_json()
                robin.receive_json()
                casey.send_json({"type": "offer", "to": robin_id, "sdp": {"type": "offer", "sdp": sdp}})
                casey.send_json({"type": "ice", "to": robin_id, "candidate": candidate})
                assert robin.receive_json()["type"] == "offer"
                assert robin.receive_json()["type"] == "ice"
                casey.send({"type": "websocket.disconnect", "code": 1000})
                robin.receive_json()
            robin.send({"type": "websocket.disconnect", "code": 1000})
        _refused(c, "kit", "pc-room")
    text = caplog.text
    for secret in ("198.51.100.23", "203.0.113.77", "fingerprint", "robin-token", "casey-token", "kit-token"):
        assert secret not in text


def test_the_websocket_handshake_line_has_its_token_masked():
    """uvicorn logs a WebSocket handshake (with ?token=) on uvicorn.error, not uvicorn.access."""
    from services.common.logging_utils import redact_access_log

    redact_access_log()
    record = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" [accepted]',
        ("192.0.2.9:5000", "/api/communication/calls/ws?token=sk-family-secret-key&room=pc-room"), None,
    )
    for f in logging.getLogger("uvicorn.error").filters:
        f.filter(record)
    line = record.getMessage()
    assert "sk-family-secret-key" not in line and "token=***" in line


# --- STUN on the home coturn --------------------------------------------------


def test_stun_comes_from_the_home_coturn_when_turn_is_set_up(monkeypatch):
    monkeypatch.delenv("CALL_ICE_SERVERS", raising=False)
    monkeypatch.setenv("TURN_SECRET", "s3cret")
    monkeypatch.setenv("TURN_URLS", "turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp")
    servers = calls.ice_servers_for("robin")
    assert servers[0] == {"urls": ["stun:turn.example.com:3478"]}
    assert servers[1]["urls"][0].startswith("turn:turn.example.com")
    everything = json.dumps(servers)
    assert "google" not in everything and "cloudflare" not in everything


def test_stun_urls_follow_each_turn_listener():
    assert calls.stun_urls_from_turn(["turn:a.example:3479?transport=tcp", "turns:a.example:5349"]) == [
        "stun:a.example:3479"]
    assert calls.stun_urls_from_turn(["turns:b.example:5349"]) == ["stun:b.example:3478"]
    assert calls.stun_urls_from_turn(["turn:[2001:db8::1]:3478", "nonsense"]) == ["stun:[2001:db8::1]:3478"]
    assert calls.stun_urls_from_turn(["turn:c.example"]) == ["stun:c.example:3478"]


def test_public_stun_only_when_nothing_is_configured_and_it_warns_once(monkeypatch, caplog):
    monkeypatch.delenv("CALL_ICE_SERVERS", raising=False)
    monkeypatch.delenv("TURN_SECRET", raising=False)
    monkeypatch.delenv("TURN_URLS", raising=False)
    monkeypatch.setattr(calls, "_warned_public_stun", False)
    with caplog.at_level(logging.WARNING, logger="gateway.calls"):
        assert calls.ice_servers_for("robin") == calls.PUBLIC_STUN_SERVERS
        assert calls.ice_servers_for("casey") == calls.PUBLIC_STUN_SERVERS
    assert caplog.text.count("public STUN") == 1


def test_a_hand_set_ice_list_is_used_as_given(monkeypatch):
    monkeypatch.setenv("CALL_ICE_SERVERS", json.dumps([{"urls": "stun:stun.home.example:3478"}]))
    monkeypatch.delenv("TURN_SECRET", raising=False)
    assert calls.ice_servers_for("robin") == [{"urls": "stun:stun.home.example:3478"}]
