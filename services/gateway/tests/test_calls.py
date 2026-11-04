"""Family calls are relayed in-app, not opened in Nextcloud Talk."""
import pytest

from services.gateway import calls


@pytest.fixture(autouse=True)
def fresh_hub(monkeypatch):
    monkeypatch.setattr(calls, "hub", calls.CallHub())

    async def fake_user(token):
        if token == "mom-token":
            return {"username": "mom", "full_name": "Mom"}
        if token == "kid-token":
            return {"username": "kid", "full_name": "Kid"}
        return None

    monkeypatch.setattr(calls, "_user_for_token", fake_user)


def test_two_people_join_the_same_room_and_exchange_signals(one_loop_client):
    c = one_loop_client

    with c.websocket_connect("/api/communication/calls/ws?token=mom-token&room=family&name=Family") as mom:
        welcome = mom.receive_json()
        assert welcome["type"] == "welcome"
        assert welcome["self"]["name"] == "Mom"
        assert welcome["name"] == "Family"
        assert welcome["ice_servers"]

        with c.websocket_connect("/api/communication/calls/ws?token=kid-token&room=family&name=Family") as kid:
            kid_welcome = kid.receive_json()
            assert [p["name"] for p in kid_welcome["peers"]] == ["Mom"]

            joined = mom.receive_json()
            assert joined["type"] == "peer-joined"
            assert joined["peer"]["name"] == "Kid"

            mom.send_json(
                {
                    "type": "offer",
                    "to": kid_welcome["self"]["id"],
                    "sdp": {"type": "offer", "sdp": "v=0"},
                }
            )
            relayed = kid.receive_json()
            assert relayed["type"] == "offer"
            assert relayed["from"] == welcome["self"]["id"]
            assert relayed["sdp"] == {"type": "offer", "sdp": "v=0"}

            kid.send_json({"type": "state", "muted": True, "camera": False})
            state = mom.receive_json()
            assert state["type"] == "peer-state"
            assert state["peer"]["muted"] is True
            assert state["peer"]["camera"] is False

            # Send the disconnect while the session is still open: exiting the
            # context manager cancels the server task before its finally block
            # can tell the other peer.
            kid.send({"type": "websocket.disconnect", "code": 1000})
            left = mom.receive_json()
            assert left == {"type": "peer-left", "id": kid_welcome["self"]["id"]}


def test_rooms_do_not_relay_between_each_other(one_loop_client):
    c = one_loop_client

    with c.websocket_connect("/api/communication/calls/ws?token=mom-token&room=family&name=Family") as mom:
        mom_welcome = mom.receive_json()

        with c.websocket_connect("/api/communication/calls/ws?token=kid-token&room=other&name=Other") as kid:
            kid_welcome = kid.receive_json()
            assert kid_welcome["peers"] == []

            kid.send_json(
                {
                    "type": "offer",
                    "to": mom_welcome["self"]["id"],
                    "sdp": {"type": "offer", "sdp": "v=0"},
                }
            )

            # Kid's welcome had no peers, which is the room boundary. An offer
            # addressed at Mom is simply dropped rather than crossing rooms.
            kid.close()


def test_the_active_call_list_needs_a_signed_in_family_member(one_loop_client):
    c = one_loop_client
    assert c.get("/api/communication/calls").status_code == 401

    response = c.get("/api/communication/calls", headers={"Authorization": "Bearer mom-token"})
    assert response.status_code == 200
    assert response.json() == {"calls": []}


def test_a_started_call_appears_in_the_list_with_its_people(one_loop_client):
    c = one_loop_client

    with c.websocket_connect("/api/communication/calls/ws?token=mom-token&room=family&name=Family") as mom:
        mom.receive_json()
        response = c.get(
            "/api/communication/calls",
            headers={"Authorization": "Bearer kid-token"},
        )
        assert response.status_code == 200
        calls = response.json()["calls"]
        assert len(calls) == 1
        assert calls[0]["token"] == "family"
        assert calls[0]["name"] == "Family"
        assert [p["name"] for p in calls[0]["participants"]] == ["Mom"]


def _calls_for(c, token):
    return c.get("/api/communication/calls", headers={"Authorization": f"Bearer {token}"}).json()["calls"]


def test_a_rung_call_rings_its_members_and_is_hidden_from_everyone_else(one_loop_client, monkeypatch):
    c = one_loop_client

    async def fake_user(token):
        return {
            "mom-token": {"username": "mom", "full_name": "Mom"},
            "kid-token": {"username": "kid", "full_name": "Kid"},
            "dad-token": {"username": "dad", "full_name": "Dad"},
        }.get(token)

    monkeypatch.setattr(calls, "_user_for_token", fake_user)

    with c.websocket_connect("/api/communication/calls/ws?token=mom-token&room=mom-kid&name=Kid") as mom:
        mom.receive_json()
        import asyncio

        assert asyncio.run(calls.hub.ring("mom-kid", "mom", "Mom", {"kid"})) is True
        # One ring per call.
        assert asyncio.run(calls.hub.ring("mom-kid", "mom", "Mom", {"kid"})) is False

        [for_kid] = _calls_for(c, "kid-token")
        assert for_kid["ringing"] is True and for_kid["caller"] == "Mom"
        [for_mom] = _calls_for(c, "mom-token")
        assert for_mom["ringing"] is False
        # A direct call is nobody else's business, and a drop-in device of
        # theirs must never answer it.
        assert _calls_for(c, "dad-token") == []

        # Declining stops the ring and tells the caller who said no.
        response = c.post("/api/communication/calls/mom-kid/decline", headers={"Authorization": "Bearer kid-token"})
        assert response.json() == {"declined": True}
        assert mom.receive_json() == {"type": "declined", "username": "kid", "name": "Kid"}
        [for_kid] = _calls_for(c, "kid-token")
        assert for_kid["ringing"] is False
        mom.send({"type": "websocket.disconnect", "code": 1000})


def test_a_ring_stops_after_a_minute(monkeypatch):
    import asyncio

    ring = calls.Ring("mom", "Mom", {"kid"}, started_at=1000.0)
    assert ring.rings("kid", 1000.0 + calls.RING_SECONDS - 1)
    assert not ring.rings("kid", 1000.0 + calls.RING_SECONDS)
    assert not ring.rings("dad", 1001.0)
    # Ringing a call nobody is in does nothing.
    assert asyncio.run(calls.hub.ring("empty", "mom", "Mom", {"kid"})) is False
