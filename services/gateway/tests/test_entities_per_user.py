"""Per-user Home Assistant credentials for /api/entities.

The endpoint used to call the execution service's /discovery/entities with no
credentials at all, so the execution service fell back to the system default
(user id 1) and every user saw -- and could act on -- the default account's Home
Assistant. The caller must now be authenticated, their own credentials must be
forwarded, and an unconfigured user must get a loud failure naming them.
"""
from unittest.mock import AsyncMock, patch

from services.gateway import main as gateway_main

ENTITY_FIXTURE = {
    "entities": [
        {
            "entity_id": "media_player.kitchen",
            "state": "idle",
            "attributes": {"friendly_name": "Kitchen"},
        }
    ]
}


class _Resp:
    def __init__(self, status=200, data=None):
        self.status = status
        self._data = data

    async def json(self):
        return self._data


class _Client:
    """Stands in for get_http_client(); records params and returns ENTITY_FIXTURE."""

    def __init__(self, status=200, data=None):
        self.status = status
        self._data = data if data is not None else ENTITY_FIXTURE
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        return _Resp(self.status, self._data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_entities_forward_the_callers_home_assistant(client, fake_identity):
    fake_client = _Client()

    with patch.object(gateway_main, "get_http_client", return_value=fake_client):
        resp = client.get("/api/entities")

    assert resp.status_code == 200
    body = resp.json()
    assert [e["entity_id"] for e in body["entities"]] == ["media_player.kitchen"]
    url, params = fake_client.calls[0]
    assert url.endswith("/discovery/entities")
    assert params == {"ha_url": fake_identity["ha_url"], "ha_token": fake_identity["ha_token"]}


def test_entities_fail_loudly_for_a_user_without_home_assistant(client, monkeypatch):
    async def _resolve(body):
        return {"user": "casey", "username": "casey", "is_admin": False, "ha_url": None, "ha_token": None}

    monkeypatch.setattr(gateway_main, "resolve_identity", _resolve)
    fake_client = _Client()

    with patch.object(gateway_main, "get_http_client", return_value=fake_client):
        resp = client.get("/api/entities")

    assert not fake_client.calls, "must not call the execution service without credentials"
    body = resp.json()
    assert body["entities"] == []
    assert body["status"] == "FAILURE"
    assert "casey" in body["message"]
    assert "Home Assistant" in body["message"]


def test_entities_require_authentication(client, monkeypatch):
    from fastapi import HTTPException  # noqa: F401  (documents the raised type)

    async def _deny(request):
        raise HTTPException(status_code=401, detail="Authentication required")

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _deny)
    fake_client = _Client()

    with patch.object(gateway_main, "get_http_client", return_value=fake_client):
        resp = client.get("/api/entities")

    assert resp.status_code == 401
    assert not fake_client.calls
