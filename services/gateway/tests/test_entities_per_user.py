"""Per-user Home Assistant credentials for /api/entities.

The endpoint used to call the execution service's /discovery/entities with no
credentials at all, so the execution service fell back to the system default
(user id 1) and every user saw -- and could act on -- the default account's Home
Assistant. The caller must now be authenticated, their own credentials must be
forwarded, and an unconfigured user must get a loud failure naming them.
"""
from unittest.mock import patch

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


def test_malformed_entity_ids_never_reach_a_dropdown(client):
    poison = "media_player." + ("id_1_username_default_display_name_" * 34)
    truncated_poison = (
        "media_player.id_1_username_default_display_name_shared_default_user_"
        "is_admin_true_is_system_default_true_nextcloud_url_https_cloud_example_"
        "com_nextcloud_user_rivera_ha_url_https_ha_example_com_github_url_"
        "github_user_alex_example_com_gitlab_url_git"
    )
    fake_client = _Client(data={"entities": [
        {"entity_id": "media_player.kitchen", "state": "idle", "attributes": {}},
        {"entity_id": poison, "state": "unavailable", "attributes": {}},
        {"entity_id": truncated_poison, "state": "unavailable", "attributes": {}},
        {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
    ]})

    with patch.object(gateway_main, "get_http_client", return_value=fake_client):
        resp = client.get("/api/entities")

    assert resp.status_code == 200
    assert [e["entity_id"] for e in resp.json()["entities"]] == [
        "media_player.kitchen",
        "light.kitchen",
    ]


def test_a_user_without_home_assistant_reads_through_the_hub_trimmed_to_what_they_may_see(client, monkeypatch):
    """No stored HA token: no login in the query (execution uses the hub's own) and only their entities."""
    from services.execution.entity_access import EntityPermissions
    from services.gateway import hub_access

    async def _resolve(body):
        return {"user": "casey", "username": "casey", "is_admin": False, "ha_url": None, "ha_token": None}

    async def _perms(username, is_admin=False):
        return EntityPermissions(assigned=frozenset({"light.kitchen"}))

    data = {"entities": [*ENTITY_FIXTURE["entities"],
                         {"entity_id": "light.kitchen", "state": "on", "attributes": {}}]}

    monkeypatch.setattr(gateway_main, "resolve_identity", _resolve)
    monkeypatch.setattr(hub_access, "load_entity_permissions", _perms)
    fake_client = _Client(data=data)

    with patch.object(gateway_main, "get_http_client", return_value=fake_client):
        resp = client.get("/api/entities")

    ((_url, params),) = fake_client.calls
    assert params == {}, "no login of their own: nothing travels in the query"
    assert [e["entity_id"] for e in resp.json()["entities"]] == ["light.kitchen"]


def test_entities_require_authentication(client, monkeypatch):
    from fastapi import HTTPException

    async def _deny(request):
        raise HTTPException(status_code=401, detail="Authentication required")

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _deny)
    fake_client = _Client()

    with patch.object(gateway_main, "get_http_client", return_value=fake_client):
        resp = client.get("/api/entities")

    assert resp.status_code == 401
    assert not fake_client.calls
