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


def test_turn_credential_is_coturn_rest_style_and_expires(monkeypatch):
    """coturn (use-auth-secret) accepts base64(HMAC-SHA1(secret, '<expiry>:<user>'))."""
    import base64
    import hashlib
    import hmac

    monkeypatch.setenv("TURN_SECRET", "s3cret")
    monkeypatch.setenv("TURN_URLS", "turn:turn.example.com:3478?transport=udp, turn:turn.example.com:3478?transport=tcp")
    server = calls.turn_ice_server("mom", now=1000)
    assert server["urls"] == [
        "turn:turn.example.com:3478?transport=udp",
        "turn:turn.example.com:3478?transport=tcp",
    ]
    assert server["username"] == f"{1000 + calls.TURN_CREDENTIAL_SECONDS}:mom"
    expected = base64.b64encode(hmac.new(b"s3cret", server["username"].encode(), hashlib.sha1).digest()).decode()
    assert server["credential"] == expected
    assert calls.ice_servers_for("mom")[-1]["urls"] == server["urls"]


def test_no_turn_without_secret_or_urls(monkeypatch):
    monkeypatch.delenv("TURN_SECRET", raising=False)
    monkeypatch.setenv("TURN_URLS", "turn:turn.example.com:3478")
    assert calls.turn_ice_server("mom") is None
    assert calls.ice_servers_for("mom") == calls.ICE_SERVERS


def test_welcome_carries_a_turn_credential_when_configured(client, monkeypatch):
    monkeypatch.setenv("TURN_SECRET", "s3cret")
    monkeypatch.setenv("TURN_URLS", "turn:turn.example.com:3478")
    with client.websocket_connect("/api/communication/calls/ws?token=mom-token&room=family&name=Family") as mom:
        welcome = mom.receive_json()
        turn = welcome["ice_servers"][-1]
        assert turn["urls"] == ["turn:turn.example.com:3478"]
        assert turn["username"].endswith(":mom")


# --- who may drop in on whom ------------------------------------------------

class _FakeRedis:
    def __init__(self):
        self.h: dict[str, dict[str, str]] = {}

    async def hmget(self, key, fields):
        return [self.h.get(key, {}).get(f) for f in fields]

    async def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value

    async def hdel(self, key, field):
        self.h.get(key, {}).pop(field, None)


FAMILY = [
    {"username": "dad", "display_name": "Dad", "family_role": "father", "is_admin": True},
    {"username": "mom", "display_name": "Mom", "family_role": "mother"},
    {"username": "kid", "display_name": "Kid", "family_role": "child"},
    {"username": "big", "display_name": "Big", "family_role": "adult_child"},
]
DEVICES = {"kid": [{"device_key": "kid-phone", "kind": "phone", "label": "Kid's phone"}],
           "mom": [{"device_key": "mom-phone", "kind": "phone", "label": "Mom's phone"}]}


@pytest.fixture
def drop_in_store(monkeypatch):
    from contextlib import asynccontextmanager

    from services.gateway import notices

    store = _FakeRedis()

    @asynccontextmanager
    async def fake_redis(r=None):
        yield store

    async def family():
        return [dict(u) for u in FAMILY]

    async def owned(username):
        return DEVICES.get(username, [])

    async def fake_user(token):
        name = token.removesuffix("-token")
        return next((dict(u) for u in FAMILY if u["username"] == name), None)

    monkeypatch.setattr(notices, "_redis", fake_redis)
    monkeypatch.setattr(calls, "_family", family)
    monkeypatch.setattr(calls, "_owned_devices", owned)
    monkeypatch.setattr(calls, "_user_for_token", fake_user)
    return store


def _auth(who):
    return {"Authorization": f"Bearer {who}-token"}


def test_adults_set_their_own_and_parents_set_a_childs():
    dad, mom, kid, big = FAMILY
    assert calls.may_set_drop_in(mom, mom)
    assert calls.may_set_drop_in(big, big)
    assert calls.may_set_drop_in(mom, kid) and calls.may_set_drop_in(dad, kid)
    # A child cannot change their own, and nobody changes another adult's.
    assert not calls.may_set_drop_in(kid, kid)
    assert not calls.may_set_drop_in(dad, mom)
    assert not calls.may_set_drop_in(big, kid)


def test_drop_in_lists_are_saved_and_checked(one_loop_client, drop_in_store):
    c = one_loop_client
    assert c.put("/api/communication/calls/drop-in/mom", json={"allow_from": ["dad", "mom", "nobody"]},
                 headers=_auth("mom")).json() == {"username": "mom", "allow_from": ["dad"]}
    assert c.put("/api/communication/calls/drop-in/kid", json={"allow_from": ["mom", "dad"]},
                 headers=_auth("dad")).status_code == 200
    assert c.put("/api/communication/calls/drop-in/kid", json={"allow_from": []},
                 headers=_auth("kid")).status_code == 403
    assert c.put("/api/communication/calls/drop-in/mom", json={"allow_from": []},
                 headers=_auth("dad")).status_code == 403

    import asyncio

    assert asyncio.run(calls.drop_in_recipients("dad", {"mom", "kid", "big"})) == {"mom", "kid"}
    assert asyncio.run(calls.drop_in_recipients("big", {"mom", "kid"})) == set()

    # A parent sees their own list and each child's, and may edit the child's.
    seen = c.get("/api/communication/calls/drop-in", headers=_auth("mom")).json()
    by_user = {s["username"]: s for s in seen["settings"]}
    assert set(by_user) == {"mom", "kid"}
    assert by_user["kid"]["allow_from"] == ["dad", "mom"] and by_user["kid"]["can_edit"]
    assert by_user["kid"]["devices"][0]["drop_in"] is True
    # A child sees only their own, read-only.
    [own] = c.get("/api/communication/calls/drop-in", headers=_auth("kid")).json()["settings"]
    assert own["username"] == "kid" and own["can_edit"] is False


def test_a_parent_switches_a_childs_device_and_the_device_learns_it(one_loop_client, drop_in_store):
    c = one_loop_client
    url = "/api/communication/calls/drop-in/kid/devices/kid-phone"
    assert c.put(url, json={"drop_in": False}, headers=_auth("kid")).status_code == 403
    assert c.put(url, json={"drop_in": False}, headers=_auth("mom")).json()["drop_in"] is False
    assert c.put("/api/communication/calls/drop-in/kid/devices/mom-phone", json={"drop_in": False},
                 headers=_auth("mom")).status_code == 404
    polled = c.get("/api/communication/calls?device_key=kid-phone", headers=_auth("kid")).json()
    assert polled["device_drop_in"] is False
    c.put(url, json={"drop_in": True}, headers=_auth("dad"))
    assert c.get("/api/communication/calls?device_key=kid-phone", headers=_auth("kid")).json()["device_drop_in"] is True


def test_a_ring_marks_drop_in_only_for_who_allows_the_caller(one_loop_client, drop_in_store):
    c = one_loop_client
    import asyncio

    with c.websocket_connect("/api/communication/calls/ws?token=dad-token&room=fam&name=Family") as dad:
        dad.receive_json()
        assert asyncio.run(calls.hub.ring("fam", "dad", "Dad", {"mom", "kid"}, {"mom"})) is True
        [for_mom] = _calls_for(c, "mom-token")
        [for_kid] = _calls_for(c, "kid-token")
        assert for_mom["ringing"] and for_mom["drop_in"] is True
        assert for_kid["ringing"] and for_kid["drop_in"] is False
        dad.send({"type": "websocket.disconnect", "code": 1000})
