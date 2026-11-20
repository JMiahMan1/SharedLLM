"""Garden proxy routes (docs/PLANT_CARE.md Phase 1): proxies /api/garden/* to
the Garden service, forwarding X-Internal-Secret and user/role headers.

Identity is resolved exclusively via gateway._acting_identity (Bearer token),
never from client-supplied headers, so clients cannot spoof admin or role
claims.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import garden_routes
from services.gateway.main import app

ADMIN_HEADERS = {
    "Authorization": "Bearer admin-key",
    # Client-supplied role headers are intentionally spoofed/wrong to verify
    # the proxy ignores them and resolves identity from the Bearer token only.
    "X-User-Id": "imposter",
    "X-User-Role": "child",
    "X-Is-Admin": "true",
    "X-Family-Manage": "false",
}
KID_HEADERS = {
    "Authorization": "Bearer kid-key",
    "X-User-Id": "child",
    "X-User-Role": "child",
    "X-Is-Admin": "false",
    "X-Family-Manage": "false",
}


@pytest.fixture
def calls(monkeypatch):
    class Seen(list):
        pass

    seen = Seen()

    # Resolve identity from Bearer token only (never from client headers).
    async def acting_identity(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return {"user": "mother", "is_admin": True, "family_role": "mother"}
        if auth == "Bearer kid-key":
            return {"user": "child", "is_admin": False, "family_role": "child"}
        return None

    # The mock _call: returns different responses based on path and method,
    # exercising 200, 201, 204, 403, 409 passthrough.
    async def call(method, path, *, params=None, json=None, headers=None):
        seen.append((method, path, params, json, dict(headers or {})))
        if path == "/health":
            return {"status": "ok", "service": "garden", "seed_present": True}
        if path == "/species/search":
            return {
                "query": params.get("q", ""),
                "category": params.get("category"),
                "usda_zone": params.get("usda_zone"),
                "count": 1,
                "results": [
                    {
                        "key": "basil_sweet",
                        "category": "herb",
                        "common_name": "Sweet Basil",
                    }
                ],
                "seed_present": True,
            }
        if path == "/species":
            return {"category": None, "usda_zone": None, "count": 1, "results": [], "seed_present": True}
        if path == "/species/basil_sweet":
            return {"key": "basil_sweet", "present": True, "species": {"common_name": "Sweet Basil"}}
        if path == "/profile":
            return {"user_id": "household", "family_size": 7}
        if path == "/locations":
            return [{"id": "loc-1", "name": "Living Room", "kind": "indoor_room"}]
        if path == "/plants":
            if method == "POST":
                return Response(
                    content='{"id": "plant-new", "category": "houseplant", "species_ref": "monstera_deliciosa"}',
                    status_code=201,
                    media_type="application/json",
                )
            return [
                {
                    "id": "plant-1",
                    "category": "houseplant",
                    "species_ref": "monstera_deliciosa",
                    "nickname": "Monty",
                    "species": {"common_name": "Monstera"},
                }
            ]
        if path == "/today":
            return {"date": "2026-11-19", "due_count": 0, "by_room": {}, "all": []}
        # 403/409 passthrough paths (checked before generic fallthroughs).
        if path == "/plants/plant-1/care" and method == "POST" and json and json.get("action") == "forbidden":
            return Response(
                content='{"detail": "Forbidden"}',
                status_code=403,
                media_type="application/json",
            )
        if path == "/locations/loc-conflict" and method == "DELETE":
            return Response(
                content='{"detail": "Conflict"}',
                status_code=409,
                media_type="application/json",
            )
        if path.startswith("/locations/") and path.endswith("/water-all"):
            return {"ok": True, "watered_count": 0}
        if path.startswith("/locations/") and method == "DELETE":
            return Response(status_code=204)
        if path == "/plants/plant-1/care":
            if method == "POST":
                return Response(
                    content='{"ok": true, "action": "water"}',
                    status_code=200,
                    media_type="application/json",
                )
        # Phase 2 routes.
        if path == "/locations/loc-1/assign" and method == "PATCH":
            return {"id": "loc-1", "name": "Living Room", "kind": "indoor_room", "assigned_to": "child", "skylight_sync_enabled": True}
        if path == "/plants/plant-1/assign" and method == "PATCH":
            return {"id": "plant-1", "nickname": "Monty", "assigned_to": "child", "skylight_sync_enabled": True}
        if path == "/streaks" and method == "GET":
            return {"user_id": "child", "current_streak": 3, "longest_streak": 7, "total_logs": 12}
        if path == "/notices/test" and method == "POST":
            return {"ok": True, "digest": {"member": "mother", "due_count": 2, "overdue_count": 1, "rooms": {}}}
        if path == "/plants/plant-1/care" and method == "POST":
            return {"ok": True, "action": "water", "plant_id": "plant-1", "logged_by": "mother"}
        if path == "/plants/plant-1/snooze" and method == "POST":
            return {"ok": True, "plant_id": "plant-1", "still_damp": True, "postpone_days": 2, "calibration_factor": 1.1, "next_due_at": "2026-11-21T10:00:00Z"}
        # Phase 3 routes.
        if path == "/species/lookup" and method == "POST":
            return {
                "draft": True,
                "source": "wiki_ai_draft",
                "species": {
                    "key": "rare_plant_x",
                    "category": "houseplant",
                    "common_name": "Rare Plant X",
                    "scientific_name": "Rarus plantus",
                    "watering_interval_days": 10.0,
                    "winter_multiplier": 1.2,
                    "frost_tolerance": "tender",
                    "toxicity_level": "non_toxic",
                },
                "warnings": ["LLM draft unavailable — basic manual draft shown."],
            }
        if path == "/species/custom" and method == "POST":
            return {"ok": True, "key": "thai_holy_basil", "source": "manual", "warnings": []}
        if path == "/species/custom/thai_holy_basil" and method == "DELETE":
            return {"ok": True, "key": "thai_holy_basil"}
        if path == "/hooks/weather" and method == "POST":
            return {"evaluated": True, "weather": None}
        raise AssertionError(f"Unexpected path: {path} {method}")

    monkeypatch.setattr(gateway_main, "_acting_identity", acting_identity)
    monkeypatch.setattr(garden_routes, "_call", call)
    return seen


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- health


def test_garden_health(client, calls):
    resp = client.get("/api/garden/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --------------------------------------------------------------------------- species


def test_garden_species_search(client, calls):
    resp = client.get(
        "/api/garden/species/search",
        headers=ADMIN_HEADERS,
        params={"q": "basil", "category": "herb"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["results"][0]["common_name"] == "Sweet Basil"
    # Forwarded headers use resolved identity ("mother"), NOT the spoofed
    # client header ("imposter").
    assert any(
        h.get("X-User-Id") == "mother" and h.get("X-User-Role") == "mother"
        for _, _, _, _, h in calls
        if h
    )


def test_garden_species_get(client, calls):
    resp = client.get("/api/garden/species/basil_sweet", headers=KID_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["key"] == "basil_sweet"


def test_garden_species_list_category(client, calls):
    resp = client.get(
        "/api/garden/species",
        headers=ADMIN_HEADERS,
        params={"category": "houseplant"},
    )
    assert resp.status_code == 200


# --------------------------------------------------------------------------- profile


def test_garden_profile_get(client, calls):
    resp = client.get("/api/garden/profile", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["family_size"] == 7


def test_garden_profile_put(client, calls):
    resp = client.put(
        "/api/garden/profile",
        headers=ADMIN_HEADERS,
        json={"family_size": 4},
    )
    assert resp.status_code == 200
    assert any(
        m == "PUT" and p == "/profile"
        for m, p, _, _, _ in calls
    )


# --------------------------------------------------------------------------- locations


def test_garden_locations(client, calls):
    resp = client.get("/api/garden/locations", headers=KID_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_garden_create_location(client, calls):
    resp = client.post(
        "/api/garden/locations",
        headers=ADMIN_HEADERS,
        json={"name": "Patio", "kind": "container"},
    )
    assert resp.status_code == 200
    assert any(
        m == "POST" and p == "/locations" and j == {"name": "Patio", "kind": "container"}
        for m, p, _, j, _ in calls
    )


# --------------------------------------------------------------------------- plants


def test_garden_plants(client, calls):
    resp = client.get("/api/garden/plants", headers=KID_HEADERS)
    assert resp.status_code == 200
    assert resp.json()[0]["nickname"] == "Monty"


def test_garden_create_plant(client, calls):
    resp = client.post(
        "/api/garden/plants",
        headers=ADMIN_HEADERS,
        json={
            "category": "houseplant",
            "species_ref": "monstera_deliciosa",
            "nickname": "Monty",
        },
    )
    assert resp.status_code == 201  # POST returns 201
    assert any(m == "POST" and p == "/plants" for m, p, _, _, _ in calls)


# --------------------------------------------------------------------------- today


def test_garden_today(client, calls):
    resp = client.get("/api/garden/today", headers=KID_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["due_count"] == 0


# --------------------------------------------------------------------------- status code passthrough (201, 204, 403, 409)


def test_garden_proxy_passes_through_201(client, calls):
    """POST /plants returning 201 must pass through as 201."""
    resp = client.post(
        "/api/garden/plants",
        headers=ADMIN_HEADERS,
        json={"category": "houseplant", "species_ref": "monstera_deliciosa"},
    )
    assert resp.status_code == 201


def test_garden_proxy_passes_through_204(client, calls):
    """DELETE /locations/{id} returning 204 must pass through as 204."""
    resp = client.delete(
        "/api/garden/locations/loc-1", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 204


def test_garden_proxy_passes_through_403(client, calls):
    """Garden service 403 must pass through as 403, not 502."""
    resp = client.post(
        "/api/garden/plants/plant-1/care",
        headers=KID_HEADERS,
        json={"action": "forbidden"},
    )
    assert resp.status_code == 403


def test_garden_proxy_passes_through_409(client, calls):
    """Garden service 409 must pass through as 409, not 502."""
    resp = client.delete(
        "/api/garden/locations/loc-conflict", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------- spoofed header rejection


def test_garden_rejects_spoofed_admin_header(client, calls):
    """A client that sends X-Is-Admin: true with a kid Bearer token must NOT
    be treated as admin. The proxy resolves identity from the Bearer token
    only, so forwarded headers show kid role, not admin."""
    resp = client.get(
        "/api/garden/plants",
        headers={
            "Authorization": "Bearer kid-key",
            "X-Is-Admin": "true",  # spoofed
            "X-User-Id": "mother",  # spoofed
        },
    )
    assert resp.status_code == 200
    # Forwarded headers must reflect the kid identity, not the spoofed admin.
    assert any(
        h.get("X-User-Id") == "child" and h.get("X-User-Role") == "child"
        for _, _, _, _, h in calls
        if h
    )
    # Must NOT forward the spoofed admin headers.
    assert not any(
        h.get("X-Is-Admin") == "true"
        for _, _, _, _, h in calls
        if h
    )


# --------------------------------------------------------------------------- auth enforcement


def test_garden_requires_authentication(client, calls):
    resp = client.get("/api/garden/plants")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- Phase 2 routes


def test_garden_assign_location(client, calls):
    resp = client.patch(
        "/api/garden/locations/loc-1/assign",
        headers=ADMIN_HEADERS,
        json={"assigned_to": "child", "skylight_sync_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == "child"
    assert resp.json()["skylight_sync_enabled"] is True
    assert any(
        m == "PATCH" and p == "/locations/loc-1/assign"
        for m, p, _, _, _ in calls
    )


def test_garden_assign_plant(client, calls):
    resp = client.patch(
        "/api/garden/plants/plant-1/assign",
        headers=ADMIN_HEADERS,
        json={"assigned_to": "child", "skylight_sync_enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == "child"
    assert any(
        m == "PATCH" and p == "/plants/plant-1/assign"
        for m, p, _, _, _ in calls
    )


def test_garden_streaks(client, calls):
    resp = client.get("/api/garden/streaks", headers=KID_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "child"
    assert data["current_streak"] == 3
    assert data["longest_streak"] == 7
    assert data["total_logs"] == 12


def test_garden_notices_test(client, calls):
    resp = client.post(
        "/api/garden/notices/test",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["digest"]["member"] == "mother"
    assert any(
        m == "POST" and p == "/notices/test"
        for m, p, _, _, _ in calls
    )


# --------------------------------------------------------------------------- garden_done / garden_snooze push callback proxy


def test_garden_done_proxy(client, calls):
    """garden_done proxies plant care logging with plant_id in params."""
    resp = client.post(
        "/api/garden/plants/plant-1/care",
        headers=ADMIN_HEADERS,
        json={"action": "water"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert any(
        m == "POST" and p == "/plants/plant-1/care"
        for m, p, _, _, _ in calls
    )


def test_garden_snooze_proxy(client, calls):
    """garden_snooze proxies plant snooze with query params."""
    resp = client.post(
        "/api/garden/plants/plant-1/snooze",
        headers=ADMIN_HEADERS,
        params={"still_damp": "true", "postpone_days": 2},
    )
    assert resp.status_code == 200
    assert resp.json()["still_damp"] is True
    assert any(
        m == "POST" and p == "/plants/plant-1/snooze"
        for m, p, _, _, _ in calls
    )


# --------------------------------------------------------------------------- spoofed header rejection for garden callbacks


def test_garden_callback_rejects_spoofed_admin(client, calls):
    """A kid with a spoofed X-Is-Admin header is not treated as admin for
    garden callbacks — identity comes from Bearer token only."""
    resp = client.post(
        "/api/garden/plants/plant-1/care",
        headers={
            "Authorization": "Bearer kid-key",
            "X-Is-Admin": "true",  # spoofed
            "X-User-Id": "mother",  # spoofed
        },
        json={"action": "water"},
    )
    assert resp.status_code == 200
    # Forwarded headers must reflect kid identity, not spoofed admin.
    assert any(
        h.get("X-User-Id") == "child" and h.get("X-User-Role") == "child"
        for _, _, _, _, h in calls
        if h
    )
    assert not any(
        h.get("X-Is-Admin") == "true"
        for _, _, _, _, h in calls
        if h
    )


# --------------------------------------------------------------------------- Phase 3 routes


def test_garden_species_lookup_proxy(client, calls):
    resp = client.post(
        "/api/garden/species/lookup",
        headers=ADMIN_HEADERS,
        json={"name": "Rare Plant X"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["draft"] is True
    assert data["source"] == "wiki_ai_draft"
    assert data["species"]["common_name"] == "Rare Plant X"
    assert any(m == "POST" and p == "/species/lookup" for m, p, _, _, _ in calls)


def test_garden_species_custom_proxy(client, calls):
    resp = client.post(
        "/api/garden/species/custom",
        headers=ADMIN_HEADERS,
        json={"common_name": "Thai Holy Basil", "category": "herb"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["key"] == "thai_holy_basil"
    assert any(m == "POST" and p == "/species/custom" for m, p, _, _, _ in calls)


def test_garden_species_custom_delete_proxy(client, calls):
    resp = client.delete(
        "/api/garden/species/custom/thai_holy_basil", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert any(
        m == "DELETE" and p == "/species/custom/thai_holy_basil"
        for m, p, _, _, _ in calls
    )


def test_garden_hooks_weather_proxy(client, calls):
    resp = client.post(
        "/api/garden/hooks/weather",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["evaluated"] is True
    assert any(m == "POST" and p == "/hooks/weather" for m, p, _, _, _ in calls)
