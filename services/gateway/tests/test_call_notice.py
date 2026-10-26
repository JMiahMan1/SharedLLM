"""A family call tells the room's other members - once, and best effort."""
import services.gateway.cache as gateway_cache
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

PARTICIPANTS = [
    {"actor_id": "michele", "display_name": "Michele"},
    {"actor_id": "noah", "display_name": "Noah"},
    {"actor_id": "jarvis", "display_name": "Jarvis"},
]

USERS = [
    {"username": "michele", "nextcloud_user": "michele"},
    {"username": "noah", "nextcloud_user": "noah"},
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
        return {"user": "michele"}

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
    assert [n["user"] for n in notices] == ["noah"]
    assert notices[0]["category"] == "call"
    assert notices[0]["title"] == "Michele is on a family call"
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
