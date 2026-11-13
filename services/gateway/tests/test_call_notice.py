"""A family call tells the room's other members - once, and best effort."""
import services.gateway.cache as gateway_cache
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

PARTICIPANTS = [
    {"actor_id": "sam", "display_name": "Sam"},
    {"actor_id": "jordan", "display_name": "Jordan"},
    {"actor_id": "jarvis", "display_name": "Jarvis"},
]

USERS = [
    {"username": "sam", "nextcloud_user": "sam"},
    {"username": "jordan", "nextcloud_user": "jordan"},
    # The bot is a room member too, and must never be notified.
    {"username": "jarvis", "nextcloud_user": "jarvis", "is_system_default": True},
]


class _Resp:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload


class _Client:
    """Stands in for the pooled session shared_http_client yields."""

    async def get(self, url, **kwargs):
        return _Resp(USERS)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Redis:
    def __init__(self):
        self.keys: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


def _setup(monkeypatch):
    notices: list[dict] = []

    async def fake_identity(request, body=None):
        return {"user": "sam"}

    async def fake_proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        if (payload or {}).get("action") == "participants":
            return JSONResponse(
                status_code=200,
                content={"status": "SUCCESS", "detail": {"participants": PARTICIPANTS}},
            )
        return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {}})

    async def fake_send_many(users, make, **kw):
        for user in users:
            notices.append(make(user))
        return len(users)

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", fake_identity)
    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", fake_proxy)
    monkeypatch.setattr(gateway_main._notify, "send_many", fake_send_many)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())
    # One store for the whole test, or the claim would never be seen twice.
    redis = _Redis()
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: redis)
    return notices


def test_a_call_tells_the_rooms_other_members_once(monkeypatch):
    notices = _setup(monkeypatch)
    client = TestClient(app)

    first = client.post("/api/communication/talk/call", json={"token": "room-family"})
    assert first.status_code == 200
    assert [n["user"] for n in notices] == ["jordan"]
    assert notices[0]["category"] == "call"
    assert notices[0]["title"] == "Sam is on a family call"
    assert notices[0]["data"]["url"] == "/family?tab=calls"
    assert notices[0]["tag"] == "call:room-family"

    # Everybody else tapping Join must not produce a notice each.
    notices.clear()
    client.post("/api/communication/talk/call", json={"token": "room-family"})
    assert notices == []


def test_leaving_a_call_announces_nothing(monkeypatch):
    notices = _setup(monkeypatch)
    client = TestClient(app)

    client.post("/api/communication/talk/call", json={"token": "room-family", "leave": True})
    assert notices == []


def test_a_failed_join_announces_nothing(monkeypatch):
    notices = _setup(monkeypatch)

    async def failing_proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        return JSONResponse(status_code=502, content={"status": "FAILURE", "message": "no"})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", failing_proxy)
    client = TestClient(app)

    client.post("/api/communication/talk/call", json={"token": "room-family"})
    assert notices == []


def test_ringing_a_call_rings_the_rooms_other_members(monkeypatch):
    import asyncio

    from services.gateway import calls

    notices = _setup(monkeypatch)
    monkeypatch.setattr(calls, "hub", calls.CallHub())
    client = TestClient(app)

    # Nobody can ring a call they are not in.
    assert client.post("/api/communication/calls/room-family/ring").status_code == 409
    assert notices == []

    class _Socket:
        async def send_json(self, payload):
            pass

    peer = calls.Peer(id="p1", room="room-family", username="sam", name="Sam", websocket=_Socket())
    asyncio.run(calls.hub.join(peer, "Family"))

    first = client.post("/api/communication/calls/room-family/ring")
    assert first.json()["ringing"] == ["jordan"]
    [notice] = notices
    assert notice["user"] == "jordan"
    assert notice["category"] == "call"
    assert notice["title"] == "Sam is calling"
    assert notice["priority"] == 4
    assert notice["data"]["url"] == "/family?tab=calls&answer=room-family"

    notices.clear()
    assert client.post("/api/communication/calls/room-family/ring").json() == {"ringing": []}
    assert notices == []


def _joined(room, username="sam"):
    import asyncio

    from services.gateway import calls

    class _Socket:
        async def send_json(self, payload):
            pass

    peer = calls.Peer(id="p1", room=room, username=username, name=username.title(), websocket=_Socket())
    asyncio.run(calls.hub.join(peer, "Call"))


def test_a_ring_reaches_a_member_whose_talk_id_differs_in_case(monkeypatch):
    """Nextcloud keeps "Jordan" while Identity stores "jordan": he must still ring."""
    from services.gateway import calls

    notices = _setup(monkeypatch)
    monkeypatch.setattr(calls, "hub", calls.CallHub())
    monkeypatch.setitem(
        globals(),
        "PARTICIPANTS",
        [{"actor_id": "sam", "display_name": "Sam"}, {"actor_id": "Jordan", "display_name": "Jordan"}],
    )
    _joined("room-jordan")

    reply = TestClient(app).post("/api/communication/calls/room-jordan/ring").json()
    assert reply["ringing"] == ["jordan"]
    assert [n["user"] for n in notices] == ["jordan"]


def test_a_one_to_one_room_rings_even_when_its_member_list_is_unreadable(monkeypatch, caplog):
    from services.gateway import calls

    notices = _setup(monkeypatch)
    monkeypatch.setattr(calls, "hub", calls.CallHub())

    async def proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        action = (payload or {}).get("action")
        if action == "participants":
            return JSONResponse(status_code=200, content={"status": "FAILURE", "message": "Forbidden", "detail": None})
        if action == "open":
            return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {
                "conversation": {"token": "room-dm", "type": 1, "name": "jordan", "display_name": "Jordan"},
            }})
        return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {}})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", proxy)
    _joined("room-dm")

    with caplog.at_level("WARNING"):
        reply = TestClient(app).post("/api/communication/calls/room-dm/ring").json()
    assert reply["ringing"] == ["jordan"]
    assert [n["user"] for n in notices] == ["jordan"]
    assert "Talk gave no members" in caplog.text and "Forbidden" in caplog.text


def test_a_one_to_one_room_rings_the_other_person_even_after_they_left_it(monkeypatch):
    """Talk keeps a one-to-one room under the other person's name after they leave it, and lists only the
    caller. That person is still the one to ring: joining a Jarvis call needs no Talk membership."""
    from services.gateway import calls

    notices = _setup(monkeypatch)
    monkeypatch.setattr(calls, "hub", calls.CallHub())

    async def proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        action = (payload or {}).get("action")
        if action == "participants":
            return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {
                "participants": [{"actor_type": "users", "actor_id": "sam", "display_name": "Sam"}]}})
        if action == "open":
            return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {
                "conversation": {"token": "room-dm", "type": 1, "name": "jordan", "display_name": "Jordan"}}})
        return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {}})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", proxy)
    _joined("room-dm")
    reply = TestClient(app).post("/api/communication/calls/room-dm/ring").json()
    assert reply["ringing"] == ["jordan"]
    assert [n["user"] for n in notices] == ["jordan"]


def test_a_group_room_with_only_the_caller_left_rings_nobody(monkeypatch):
    from services.gateway import calls

    notices = _setup(monkeypatch)
    monkeypatch.setattr(calls, "hub", calls.CallHub())

    async def proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        action = (payload or {}).get("action")
        if action == "participants":
            return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {
                "participants": [{"actor_type": "users", "actor_id": "sam", "display_name": "Sam"}]}})
        if action == "open":
            return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {
                "conversation": {"token": "room-group", "type": 2, "name": "Family"}}})
        return JSONResponse(status_code=200, content={"status": "SUCCESS", "detail": {}})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", proxy)
    _joined("room-group")
    reply = TestClient(app).post("/api/communication/calls/room-group/ring").json()
    assert reply["ringing"] == [] and "Jarvis account" in reply["reason"]
    assert notices == []


def test_a_ring_that_reaches_nobody_says_why(monkeypatch, caplog):
    from services.gateway import calls

    notices = _setup(monkeypatch)
    monkeypatch.setattr(calls, "hub", calls.CallHub())

    async def proxy(request, endpoint, payload=None, *, method="POST", as_user=None):
        return JSONResponse(status_code=502, content={"status": "FAILURE", "message": "Nextcloud is down"})

    monkeypatch.setattr(gateway_main, "_proxy_execution_with_identity", proxy)
    _joined("room-group")

    with caplog.at_level("WARNING"):
        reply = TestClient(app).post("/api/communication/calls/room-group/ring").json()
    assert reply["ringing"] == []
    assert reply["reason"]
    assert notices == []
    assert "Nextcloud is down" in caplog.text


def test_members_without_a_family_account_are_logged_not_rung(monkeypatch, caplog):
    from services.gateway import calls

    notices = _setup(monkeypatch)
    monkeypatch.setattr(calls, "hub", calls.CallHub())
    monkeypatch.setitem(
        globals(),
        "PARTICIPANTS",
        [{"actor_id": "sam", "display_name": "Sam"}, {"actor_id": "stranger", "display_name": "Guest"}],
    )
    _joined("room-odd")

    with caplog.at_level("WARNING"):
        reply = TestClient(app).post("/api/communication/calls/room-odd/ring").json()
    assert reply["ringing"] == [] and reply["reason"]
    assert notices == []
    assert "stranger" in caplog.text


def test_talk_recipients_skips_guests_and_the_system_account():
    from services.gateway.calls import talk_recipients

    display, recipients, unmatched = talk_recipients(
        [
            {"actor_type": "users", "actor_id": "Sam", "display_name": "Mum"},
            {"actor_type": "users", "actor_id": "JORDAN"},
            {"actor_type": "users", "actor_id": "jarvis"},
            {"actor_type": "emails", "actor_id": "abc123"},
        ],
        USERS,
        "sam",
    )
    assert display == "Mum"
    assert recipients == {"jordan"}
    assert unmatched == ["jarvis"]
