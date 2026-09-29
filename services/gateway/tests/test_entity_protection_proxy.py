"""Gateway proxy for the entity-protection admin surface.

The UI reaches Identity through the gateway, so both the read and the
admin-only write need a route. These tests assert the exact upstream method,
path, body, the forwarded Authorization header, and — because the id is
spliced into a URL — that it is percent-encoded on the way out.
"""
import json
from contextlib import asynccontextmanager

from services.gateway import main as gateway_main


def _patch_client(monkeypatch, status: int, payload, captured: dict):
    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload)

        async def json(self):
            return payload

    class _Client:
        async def get(self, url, **kwargs):
            captured["method"] = "get"
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            return _Resp()

        async def put(self, url, **kwargs):
            captured["method"] = "put"
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            captured["json"] = kwargs.get("json")
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake_shared_client():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_shared_client)
    return captured


def test_get_entity_protection_proxies_to_identity(client, monkeypatch):
    captured = _patch_client(
        monkeypatch,
        200,
        [
            {
                "entity_id": "climate.hallway",
                "permitted_usernames": ["alice"],
                "granted_by": "default",
                "granted_at": "2026-01-01T00:00:00+00:00",
                "note": "upstairs",
            }
        ],
        {},
    )

    resp = client.get(
        "/api/entity-protection",
        headers={"Authorization": "Bearer admin-key"},
    )

    assert resp.status_code == 200
    assert resp.json()[0]["entity_id"] == "climate.hallway"
    assert captured["method"] == "get"
    assert captured["url"].endswith("/api/entity-protection")
    assert captured["headers"].get("Authorization") == "Bearer admin-key"


def test_put_entity_protection_forwards_the_permit_list(client, monkeypatch):
    captured = _patch_client(
        monkeypatch,
        200,
        {
            "entity_id": "climate.hallway",
            "permitted_usernames": ["alice", "bob"],
            "granted_by": "default",
        },
        {},
    )

    resp = client.put(
        "/api/entity-protection/climate.hallway",
        json={
            "protected": True,
            "permitted_usernames": ["alice", "bob"],
            "note": "guests may use the hallway dial",
        },
        headers={"Authorization": "Bearer admin-key"},
    )

    assert resp.status_code == 200
    assert resp.json()["permitted_usernames"] == ["alice", "bob"]
    assert captured["method"] == "put"
    assert captured["url"].endswith("/api/entity-protection/climate.hallway")
    assert captured["json"] == {
        "protected": True,
        "permitted_usernames": ["alice", "bob"],
        "note": "guests may use the hallway dial",
    }
    assert captured["headers"].get("Authorization") == "Bearer admin-key"


def test_put_entity_protection_escapes_the_entity_id(client, monkeypatch):
    """The id lands in a URL, so a query-string character must be encoded.

    Left raw, ``climate.hallway?x=1`` would truncate the upstream path and lock
    a different entity than the caller named.
    """
    captured = _patch_client(monkeypatch, 200, {"entity_id": "?"}, {})

    resp = client.put(
        "/api/entity-protection/climate.hallway%3Fx%3D1",
        json={"protected": True, "permitted_usernames": []},
        headers={"Authorization": "Bearer admin-key"},
    )

    assert resp.status_code == 200
    assert captured["url"].endswith("/api/entity-protection/climate.hallway%3Fx%3D1")


def test_put_entity_protection_locks_exactly_the_entity_in_the_path(client, monkeypatch):
    """No second guess about the id: the path names the entity, so we forward it.

    A ``..`` segment is resolved by URL normalisation before the request is
    sent, so this arrives as ``light.kitchen`` and that is what gets forwarded.
    A slash that survives as an encoded ``%2F`` matches no route at all,
    because the id is a single segment.
    """
    captured = _patch_client(monkeypatch, 200, {"entity_id": "light.kitchen"}, {})
    resp = client.put(
        "/api/entity-protection/climate.hallway/../light.kitchen",
        json={"protected": True, "permitted_usernames": []},
        headers={"Authorization": "Bearer admin-key"},
    )
    assert resp.status_code == 200
    assert captured["url"].endswith("/api/entity-protection/light.kitchen")

    captured = _patch_client(monkeypatch, 200, {}, {})
    resp = client.put(
        "/api/entity-protection/climate.hallway%2F..%2Flight.kitchen",
        json={"protected": True, "permitted_usernames": []},
        headers={"Authorization": "Bearer admin-key"},
    )
    assert resp.status_code == 404
    assert "url" not in captured


def test_a_non_admin_gets_identitys_403_verbatim(client, monkeypatch):
    """The gateway deliberately does no role check; Identity is the authority."""
    _patch_client(monkeypatch, 403, {"detail": "Only an admin can manage entity protection."}, {})

    resp = client.get(
        "/api/entity-protection",
        headers={"Authorization": "Bearer user-key"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Only an admin can manage entity protection."


def test_an_unknown_permitted_user_surfaces_identitys_400(client, monkeypatch):
    """A typo'd permit must be a visible refusal, not a silently stored grant."""
    _patch_client(
        monkeypatch,
        400,
        {"detail": "Unknown username(s) in the permit list: alise."},
        {},
    )

    resp = client.put(
        "/api/entity-protection/climate.hallway",
        json={"protected": True, "permitted_usernames": ["alise"]},
        headers={"Authorization": "Bearer admin-key"},
    )

    assert resp.status_code == 400
    assert "alise" in resp.json()["detail"]
