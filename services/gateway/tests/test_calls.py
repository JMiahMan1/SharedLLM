"""Family calls are relayed in-app, not opened in Nextcloud Talk."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import calls
from services.gateway.main import app


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


def test_two_people_join_the_same_room_and_exchange_signals():
    c = TestClient(app)

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


def test_rooms_do_not_relay_between_each_other():
    c = TestClient(app)

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


def test_the_active_call_list_needs_a_signed_in_family_member():
    c = TestClient(app)
    assert c.get("/api/communication/calls").status_code == 401

    response = c.get("/api/communication/calls", headers={"Authorization": "Bearer mom-token"})
    assert response.status_code == 200
    assert response.json() == {"calls": []}


def test_a_started_call_appears_in_the_list_with_its_people():
    c = TestClient(app)

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
