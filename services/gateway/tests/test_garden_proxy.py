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


_BINDING = {
    "id": "binding-1", "target_kind": "location", "target_id": "bed-1", "target_name": "Bed 1",
    "moisture_entity_id": "sensor.bed1_soil_moisture", "temperature_entity_id": None,
    "humidity_entity_id": None, "lux_entity_id": None, "grow_light_entity_id": None,
    "grow_light_on_time": None, "grow_light_off_time": None, "grow_light_enabled": False,
    "valve_entity_id": "valve.bed1_drip", "valve_mode": "notify_only",
    "moisture_override_enabled": False, "moisture_threshold_pct": 30.0,
    "max_run_minutes": 5, "daily_max_runs": 2, "rain_interlock": True,
    "active_run_started_at": None, "active_run_ends_at": None, "nfc_tag_id": "tag-bed1",
    "created_at": "2026-11-20T00:00:00+00:00", "updated_at": "2026-11-20T00:00:00+00:00",
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
        # Phase 4 routes.
        if path == "/plants/plant-1/harvest" and method == "POST":
            return Response(
                content='{"ok": true, "harvest_id": "harvest-1", "plant_id": "plant-1", "quantity": 2.0, "unit": "bunch", "disposition": "fresh"}',
                status_code=201,
                media_type="application/json",
            )
        if path == "/harvests" and method == "GET":
            return {"count": 2, "harvests": [{"id": "h1", "quantity": 1.0, "unit": "bunch", "disposition": "fresh"}, {"id": "h2", "quantity": 2.0, "unit": "lb", "disposition": "canned"}], "totals": [{"plant_id": "plant-1", "total_quantity": 3.0, "count": 2}]}
        if path.startswith("/harvests/") and method == "DELETE":
            return Response(status_code=204)
        if path == "/harvest/ready" and method == "GET":
            return {"count": 1, "ready": [{"plant_id": "plant-1", "nickname": "Sir Basil", "common_name": "Sweet Basil", "category": "herb", "species_ref": "basil_sweet", "culinary_aliases": ["basil"], "in_cooldown": False}]}
        if path == "/recipes/matches" and method == "GET":
            return {"ready_ingredients": [{"plant_id": "plant-1", "common_name": "Sweet Basil", "culinary_aliases": ["basil"]}], "matches": [{"path": "Recipes/pesto.md", "title": "Pesto", "matched_ingredients": ["basil"], "match_count": 1, "tags": [], "servings": 7}]}
        if path == "/recipes/draft" and method == "POST":
            return {"status": "SUCCESS", "draft": True, "servings": 7, "ingredients": ["basil"], "focus_crop": "", "markdown": "# Pesto\nCategory: Recipes\n\n- [ ] 2 cups fresh basil leaves\n1. Blend ingredients.\nServes: 7"}
        # Phase 5 routes.
        if path == "/profile/detect-zone" and method == "POST":
            return {"zone": "7b", "zone_source": "auto", "last_spring_frost_mmdd": "04-15", "first_fall_frost_mmdd": "10-15", "season_length_days": 170}
        if path == "/pantry-goals" and method == "GET":
            return {"count": 1, "goals": [{"id": "goal-1", "item": "Marinara sauce", "crop_refs_json": '[\"roma_tomato\", \"basil\"]', "target_qty": 40, "unit": "quarts", "method": "water_bath_canning", "season_year": 2027, "notes": "", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}]}
        if path == "/pantry-goals" and method == "POST":
            return Response(content='{"id": "goal-new", "item": "Salsa", "target_qty": 20, "unit": "quarts", "method": "water_bath_canning", "season_year": 2027}', status_code=201, media_type="application/json")
        if path.startswith("/pantry-goals/") and method == "PATCH":
            return {"id": path.rsplit("/", 1)[-1], "item": "Updated Sauce"}
        if path.startswith("/pantry-goals/") and method == "DELETE":
            return Response(status_code=204)
        if path == "/pantry-goals/calculate" and method == "GET":
            return {"item": "Marinara", "method": "water_bath_canning", "unit": "quarts", "target_qty": 40, "total_raw_lbs_needed": 120.0, "total_plant_count": 12, "total_sqft": 12.0, "crops": [{"species_ref": "roma_tomato", "common_name": "Roma Tomato", "raw_lbs_needed": 84.0, "plant_count": 8, "sqft": 8.0}]}
        # Phase 6 routes.
        if path == "/harvest/forecast" and method == "GET":
            return {"generated_on": "2026-11-20", "window_days": 28, "weather_used": True, "gdd_adjusted_count": 1, "count": 1, "items": [{"plant_id": "plant-1", "nickname": "Roma", "common_name": "Roma (Plum) Tomato", "category": "vegetable", "species_ref": "tomato_roma", "planted_date": "2026-06-01", "estimated_ready_date": "2026-11-25", "harvest_window_end": "2026-12-25", "days_until_ready": 5, "ready_now": False, "gdd_adjusted": True, "gdd_base_f": 50, "gdd_daily_rate": 10.0, "gdd_shift_days": -3, "days_to_maturity": 75, "harvest_window_days": 30, "culinary_aliases": ["tomato"], "preservation_methods": ["water_bath_canning", "dehydrating", "freezing"], "usda_acidity_class": "high_acid", "blanching_minutes": None, "location_id": "bed-1", "assigned_to": "mother"}]}
        if path == "/preservation/advice" and method == "GET":
            return {"generated_on": "2026-11-20", "crop_count": 1, "plant_count": 2, "crops": [{"species_ref": "green_bean_bush", "common_name": "Bush Green Bean", "category": "vegetable", "usda_acidity_class": "low_acid", "safety": {"level": "pressure_canner_required", "title": "Low-acid — Pressure Canner Required", "message": "Pressure canning is required.", "source": "USDA Complete Guide to Home Canning / NCHFP / Ball Blue Book"}, "methods": [{"method": "freezing", "recorded_as": "freezing", "label": "Freezing", "blanching_minutes": 3, "supplies": ["Freezer bags or containers"]}], "plant_count": 2}], "combos": [], "prep_checklist": {"horizon_days": 21, "crops": ["Bush Green Bean"], "methods": [{"method": "freezing", "label": "Freezing"}], "combos": [], "count": 1, "items": ["Freezer bags or containers"], "categories": [{"key": "freezer", "label": "Freezer", "items": ["Freezer bags or containers"]}]}, "safety_sources": ["USDA Complete Guide to Home Canning / NCHFP / Ball Blue Book"]}
        if path == "/pantry-batches" and method == "GET":
            return {"generated_on": "2026-11-20", "include_empty": False, "goal_id": None, "count": 1, "use_first_count": 1, "batches": [{"id": "batch-1", "goal_id": "goal-1", "label": "Marinara sauce", "method": "water_bath_canning", "method_label": "Water-bath canning", "batch_date": "2026-09-15", "qty_initial": 12, "qty_remaining": 8, "unit": "quarts", "storage_location": "Basement shelf", "use_by_date": "2027-12-15", "logged_by": "mother", "notes": None, "created_at": "2026-09-15T00:00:00Z", "updated_at": "2026-09-15T00:00:00Z", "days_until_use_by": 39, "past_use_by": False, "use_first": True, "oldest_in_label": True}], "goal_progress": [{"goal_id": "goal-1", "item": "Marinara sauce", "unit": "quarts", "target_qty": 40, "qty_put_up": 28, "qty_remaining": 8, "percent_complete": 70, "batch_count": 1, "unit_mismatch": False, "mismatched_units": [], "season_year": 2026}]}
        if path == "/pantry-batches" and method == "POST":
            return Response(content='{"id": "batch-new", "label": "Dill pickles", "method": "water_bath_canning", "qty_initial": 6, "qty_remaining": 6, "unit": "quarts"}', status_code=201, media_type="application/json")
        if path == "/pantry-batches/batch-1/use" and method == "POST":
            return {"id": "batch-1", "qty_initial": 12, "qty_remaining": 7, "unit": "quarts", "used_by": "child", "qty_used": 1.0}
        if path.startswith("/pantry-batches/") and method == "PATCH":
            return {"id": path.rsplit("/", 1)[-1], "label": "Marinara sauce", "storage_location": "Cold room"}
        if path.startswith("/pantry-batches/") and method == "DELETE":
            return Response(status_code=204)
        # Phase 8: Home Hub hardware, bindings, valves and NFC/QR tags.
        if path.endswith("/logs") and method == "GET":
            return {"plant_id": path.split("/")[2], "count": 0, "logs": []}
        if path == "/hardware/discover" and method == "GET":
            return {
                "available": True, "has_any_hardware": True,
                "moisture_sensors": [{"entity_id": "sensor.bed1_soil_moisture", "friendly_name": "Bed 1 Soil Moisture", "device_class": "moisture", "unit": "%", "state": "22"}],
                "temperature_sensors": [], "humidity_sensors": [], "lux_sensors": [],
                "valves": [{"entity_id": "valve.bed1_drip", "friendly_name": "Bed 1 Drip"}],
                "grow_lights": [],
                "tags": [{"tag_id": "tag-bed1", "name": "Bed 1", "last_scanned": None}],
            }
        if path == "/bindings" and method == "GET":
            return {"count": 1, "bindings": [{**_BINDING, "readings": {"moisture_pct": 22.0}, "runs_today": 0}]}
        if path.startswith("/bindings/") and path.endswith("/valve/run") and method == "POST":
            return {"binding_id": "binding-1", "minutes": 3, "runs_today": 1, "closes_at": "2026-11-20T12:03:00+00:00"}
        if path.startswith("/bindings/") and path.endswith("/valve/stop") and method == "POST":
            return {"binding_id": "binding-1", "stopped": True}
        if path.startswith("/bindings/") and method == "DELETE":
            return Response(status_code=204)
        if path.startswith("/bindings/") and method == "PUT":
            return {**_BINDING, **(json or {}), "readings": {}, "runs_today": 0}
        if path.startswith("/tags/") and method == "GET":
            return {
                "matched": True, "tag_id": path.rsplit("/", 1)[-1],
                "target_kind": "location", "target_id": "bed-1", "target_name": "Bed 1",
                "plant": None, "location": {"id": "bed-1", "name": "Bed 1", "kind": "raised_bed", "plant_count": 3},
                "readings": {"moisture_pct": 22.0}, "moisture": {"applies": False},
                "next_due_at": None, "due_today": False, "actions": ["water", "photo"],
            }
        if path == "/plans" and method == "GET":
            return {"count": 1, "plans": [{"id": "plan-1", "name": "Spring/Summer 2027", "season": "spring_summer", "year": 2027, "family_size": 7, "mode": "fresh_plus_preserving", "status": "draft", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}]}
        if path == "/plans" and method == "POST":
            return Response(content='{"id": "plan-new", "name": "Fall 2027", "season": "fall", "year": 2027, "family_size": 7, "mode": "fresh_only", "status": "draft"}', status_code=201, media_type="application/json")
        if path == "/plans/plan-1" and method == "GET":
            return {"plan": {"id": "plan-1", "name": "Spring/Summer 2027", "season": "spring_summer", "year": 2027, "family_size": 7, "mode": "fresh_plus_preserving", "status": "draft"}, "cells": [{"id": "cell-1", "plan_id": "plan-1", "location_id": "bed-1", "col": 0, "row": 0, "species_ref": "basil_sweet", "plant_id": None, "crop_family": "Lamiaceae", "quantity": 1, "notes": ""}]}
        if path.startswith("/plans/") and method == "PATCH":
            return {"id": "plan-1", "name": "Updated Plan"}
        if path.startswith("/plans/") and method == "DELETE":
            return Response(status_code=204)
        if path.endswith("/cells") and method == "GET":
            return {"plan_id": "plan-1", "bed_id": "bed-1", "cells": [{"id": "cell-1", "col": 0, "row": 0, "species_ref": "basil_sweet", "plant_id": None, "crop_family": "Lamiaceae", "quantity": 1, "notes": ""}]}
        if path.endswith("/cells") and method == "PUT":
            return {"plan_id": "plan-1", "bed_id": "bed-1", "cells_upserted": 3}
        if path.endswith("/auto-arrange") and method == "POST":
            return {"plan_id": "plan-1", "bed_id": "bed-1", "placements": [{"col": 0, "row": 0, "species_ref": "tomato_roma", "crop_family": "Solanaceae", "quantity": 4}]}
        if path.endswith("/checks") and method == "GET":
            return {"plan_id": "plan-1", "checks": [], "warning_count": 0}
        if path.endswith("/timeline") and method == "GET":
            return {"plan_id": "plan-1", "plan_name": "Spring/Summer 2027", "season": "spring_summer", "year": 2027, "spring_frost_mmdd": "04-15", "first_fall_frost_mmdd": "10-15", "timeline": [{"cell_id": "cell-1", "species_ref": "basil_sweet", "common_name": "Sweet Basil", "milestones": [{"stage": "Transplant / Direct sow", "week_offset": 0, "mmdd": "04-15"}]}]}
        if path == "/planner/sizing" and method == "GET":
            return {"mode": "fresh_only", "family_size": 7, "crops": [{"species_ref": "basil_sweet", "common_name": "Sweet Basil", "plant_count": 7, "sqft": 7.0}], "total_sqft": 7.0, "total_plants": 7}
        raise AssertionError(f"Unexpected path: {path} {method}")

    monkeypatch.setattr(gateway_main, "_acting_identity", acting_identity)
    monkeypatch.setattr(garden_routes, "_call", call)
    # Mock identity resolution for cookbook routes (recipe_matches/draft).
    async def resolve_identity(request, body=None):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return {"user": "mother", "is_admin": True, "family_role": "mother", "user_id": "mother"}
        if auth == "Bearer kid-key":
            return {"user": "child", "is_admin": False, "family_role": "child", "user_id": "child"}
        return None
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", resolve_identity)
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


# --------------------------------------------------------------------------- Phase 5 routes


def test_garden_detect_zone_proxy(client, calls):
    resp = client.post("/api/garden/profile/detect-zone", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["zone"] == "7b"
    assert data["zone_source"] == "auto"
    assert any(m == "POST" and p == "/profile/detect-zone" for m, p, _, _, _ in calls)


def test_garden_pantry_goals_proxy(client, calls):
    resp = client.get("/api/garden/pantry-goals", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == 1
    assert data["goals"][0]["item"] == "Marinara sauce"
    assert any(m == "GET" and p == "/pantry-goals" for m, p, _, _, _ in calls)


def test_garden_create_pantry_goal_proxy(client, calls):
    resp = client.post(
        "/api/garden/pantry-goals",
        headers=ADMIN_HEADERS,
        json={"item": "Salsa", "target_qty": 20, "unit": "quarts", "method": "water_bath_canning", "season_year": 2027},
    )
    assert resp.status_code == 201, resp.text
    assert any(m == "POST" and p == "/pantry-goals" for m, p, _, _, _ in calls)


def test_garden_delete_pantry_goal_proxy(client, calls):
    resp = client.delete("/api/garden/pantry-goals/goal-1", headers=ADMIN_HEADERS)
    assert resp.status_code == 204, resp.text
    assert any(m == "DELETE" and p == "/pantry-goals/goal-1" for m, p, _, _, _ in calls)


def test_garden_calculate_pantry_goal_proxy(client, calls):
    resp = client.get(
        "/api/garden/pantry-goals/calculate",
        headers=ADMIN_HEADERS,
        params={"item": "Marinara", "crop_refs": "roma_tomato,basil", "target_qty": 40, "unit": "quarts", "method": "water_bath_canning"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_raw_lbs_needed"] == 120.0
    assert data["total_plant_count"] == 12
    assert any(m == "GET" and p == "/pantry-goals/calculate" for m, p, _, _, _ in calls)


def test_garden_plans_proxy(client, calls):
    resp = client.get("/api/garden/plans", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == 1
    assert data["plans"][0]["name"] == "Spring/Summer 2027"
    assert any(m == "GET" and p == "/plans" for m, p, _, _, _ in calls)


def test_garden_create_plan_proxy(client, calls):
    resp = client.post(
        "/api/garden/plans",
        headers=ADMIN_HEADERS,
        json={"name": "Fall 2027", "season": "fall", "year": 2027, "family_size": 7, "mode": "fresh_only", "status": "draft"},
    )
    assert resp.status_code == 201, resp.text
    assert any(m == "POST" and p == "/plans" for m, p, _, _, _ in calls)


def test_garden_get_plan_proxy(client, calls):
    resp = client.get("/api/garden/plans/plan-1", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["plan"]["name"] == "Spring/Summer 2027"
    assert len(data["cells"]) == 1
    assert any(m == "GET" and p == "/plans/plan-1" for m, p, _, _, _ in calls)


def test_garden_delete_plan_proxy(client, calls):
    resp = client.delete("/api/garden/plans/plan-1", headers=ADMIN_HEADERS)
    assert resp.status_code == 204, resp.text
    assert any(m == "DELETE" and p == "/plans/plan-1" for m, p, _, _, _ in calls)


def test_garden_plan_cells_proxy(client, calls):
    resp = client.get("/api/garden/plans/plan-1/beds/bed-1/cells", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["bed_id"] == "bed-1"
    assert len(data["cells"]) == 1
    assert any(m == "GET" and p == "/plans/plan-1/beds/bed-1/cells" for m, p, _, _, _ in calls)


def test_garden_upsert_plan_cells_proxy(client, calls):
    resp = client.put(
        "/api/garden/plans/plan-1/beds/bed-1/cells",
        headers=ADMIN_HEADERS,
        json=[{"col": 0, "row": 0, "species_ref": "basil_sweet", "quantity": 1}],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cells_upserted"] == 3
    assert any(m == "PUT" and p == "/plans/plan-1/beds/bed-1/cells" for m, p, _, _, _ in calls)


def test_garden_auto_arrange_proxy(client, calls):
    resp = client.post(
        "/api/garden/plans/plan-1/beds/bed-1/auto-arrange", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["placements"]) == 1
    assert any(m == "POST" and p == "/plans/plan-1/beds/bed-1/auto-arrange" for m, p, _, _, _ in calls)


def test_garden_plan_checks_proxy(client, calls):
    resp = client.get("/api/garden/plans/plan-1/checks", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["warning_count"] == 0
    assert any(m == "GET" and p == "/plans/plan-1/checks" for m, p, _, _, _ in calls)


def test_garden_plan_timeline_proxy(client, calls):
    resp = client.get("/api/garden/plans/plan-1/timeline", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["plan_name"] == "Spring/Summer 2027"
    assert len(data["timeline"]) == 1
    assert any(m == "GET" and p == "/plans/plan-1/timeline" for m, p, _, _, _ in calls)


def test_garden_planner_sizing_proxy(client, calls):
    resp = client.get(
        "/api/garden/planner/sizing",
        headers=ADMIN_HEADERS,
        params={"mode": "fresh_only", "family_size": 7, "species_refs": "basil_sweet"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["family_size"] == 7
    assert data["total_plants"] == 7
    assert any(m == "GET" and p == "/planner/sizing" for m, p, _, _, _ in calls)


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
        json={"assigned_to": "child"},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == "child"
    assert any(
        m == "PATCH" and p == "/plants/plant-1/assign"
        for m, p, _, _, _ in calls
    )


@pytest.mark.parametrize(
    "fields",
    (
        {"skylight_sync_enabled": True},
        {"skylight_chore_id": "999"},
        {"assigned_to": "child", "skylight_assignee": "child"},
    ),
)
def test_skylight_fields_cannot_be_set_through_plain_assign(client, calls, fields):
    """They have rules (who, on the chart, the chore exists) only the chore routes apply."""
    resp = client.patch("/api/garden/plants/plant-1/assign", headers=ADMIN_HEADERS, json=fields)

    assert resp.status_code == 400
    assert "skylight-chore" in resp.json()["detail"]
    assert not any(p == "/plants/plant-1/assign" for _m, p, _, _, _ in calls)


@pytest.mark.parametrize("raw", (b"{not json", b"[1, 2]"))
def test_a_malformed_body_is_a_422(client, calls, raw):
    resp = client.post(
        "/api/garden/plants/plant-1/care",
        headers={**ADMIN_HEADERS, "Content-Type": "application/json"},
        content=raw,
    )

    assert resp.status_code == 422
    assert not any(p == "/plants/plant-1/care" for _m, p, _, _, _ in calls)


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


# --------------------------------------------------------------------------- _call regression tests
# These test garden_routes._call directly (mocking shared_http_client) so the
# json-module shadowing bug that took 100% of /api/garden/* down can never
# return silently.


class _FakeResponse:
    def __init__(self, status: int, payload=None, raise_json: bool = False):
        self.status = status
        self.payload = payload
        self.raise_json = raise_json

    async def json(self, content_type=None):
        if self.raise_json:
            raise ValueError("not json")
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, response: _FakeResponse, seen: list):
        self.response = response
        self.seen = seen

    def request(self, method, url, **kwargs):
        self.seen.append((method, url, kwargs))
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _install_fake_client(monkeypatch, response: _FakeResponse) -> list:
    seen: list = []

    def fake_shared_http_client():
        return _FakeClient(response, seen)

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_shared_http_client)
    return seen


def test_garden_call_get_serializes_json(monkeypatch):
    import asyncio

    seen = _install_fake_client(monkeypatch, _FakeResponse(200, {"ok": True, "zone": "7b"}))
    result = asyncio.run(garden_routes._call("GET", "/profile"))

    assert result.status_code == 200
    assert isinstance(result.body, bytes)
    assert b'"zone"' in result.body and b'"7b"' in result.body
    method, url, kwargs = seen[0]
    assert method == "GET"
    assert url.endswith("/profile")
    assert kwargs["json"] is None


def test_garden_call_post_serializes_json_body(monkeypatch):
    import asyncio

    seen = _install_fake_client(monkeypatch, _FakeResponse(201, {"id": "goal-1"}))
    result = asyncio.run(
        garden_routes._call("POST", "/pantry-goals", json={"item": "Salsa"})
    )

    assert result.status_code == 201
    assert b'"goal-1"' in result.body
    assert seen[0][2]["json"] == {"item": "Salsa"}


def test_garden_call_put_accepts_list_body(monkeypatch):
    import asyncio

    cells = [{"col": 0, "row": 0, "species_ref": "basil_sweet"}]
    seen = _install_fake_client(monkeypatch, _FakeResponse(200, {"cells_upserted": 1}))
    result = asyncio.run(
        garden_routes._call(
            "PUT", "/plans/plan-1/beds/bed-1/cells", json=cells
        )
    )

    assert result.status_code == 200
    assert b'"cells_upserted"' in result.body
    # The body must stay a list; typing it dict would reject this at runtime.
    assert seen[0][2]["json"] == cells


def test_garden_call_passes_through_204(monkeypatch):
    import asyncio

    _install_fake_client(monkeypatch, _FakeResponse(204))
    result = asyncio.run(garden_routes._call("DELETE", "/pantry-goals/goal-1"))

    assert result.status_code == 204


def test_garden_call_passes_through_4xx_detail(monkeypatch):
    import asyncio

    _install_fake_client(monkeypatch, _FakeResponse(403, {"detail": "forbidden"}))
    result = asyncio.run(garden_routes._call("POST", "/plans"))

    assert result.status_code == 403
    assert b"forbidden" in result.body


def test_garden_call_passes_through_5xx_even_without_json(monkeypatch):
    import asyncio

    _install_fake_client(monkeypatch, _FakeResponse(502, raise_json=True))
    result = asyncio.run(garden_routes._call("GET", "/health"))

    assert result.status_code == 502
    assert b"did not answer" in result.body




# --------------------------------------------------------------------------- Phase 6 routes


def test_garden_harvest_forecast_proxy(client, calls):
    resp = client.get("/api/garden/harvest/forecast", headers=KID_HEADERS, params={"days": 21})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["days_until_ready"] == 5
    assert data["items"][0]["gdd_adjusted"] is True
    assert any(m == "GET" and p == "/harvest/forecast" and params == {"days": "21"} for m, p, params, _, _ in calls)


def test_garden_harvest_forecast_drops_unknown_query_params(client, calls):
    resp = client.get("/api/garden/harvest/forecast", headers=KID_HEADERS, params={"days": 28, "evil": "1"})

    assert resp.status_code == 200, resp.text
    seen = [params for m, p, params, _, _ in calls if p == "/harvest/forecast"]
    assert seen == [{"days": "28"}]


def test_garden_preservation_advice_proxy(client, calls):
    resp = client.get("/api/garden/preservation/advice", headers=ADMIN_HEADERS)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["crops"][0]["safety"]["level"] == "pressure_canner_required"
    assert any("USDA" in source for source in data["safety_sources"])
    assert any(m == "GET" and p == "/preservation/advice" for m, p, _, _, _ in calls)


def test_garden_pantry_batches_proxy(client, calls):
    resp = client.get("/api/garden/pantry-batches", headers=KID_HEADERS, params={"include_empty": "true"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["batches"][0]["use_first"] is True
    assert data["goal_progress"][0]["percent_complete"] == 70
    assert any(m == "GET" and p == "/pantry-batches" and params == {"include_empty": "true"} for m, p, params, _, _ in calls)


def test_garden_create_pantry_batch_proxy(client, calls):
    resp = client.post(
        "/api/garden/pantry-batches",
        headers=ADMIN_HEADERS,
        json={"label": "Dill pickles", "method": "water_bath_canning", "qty_initial": 6, "unit": "quarts"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["label"] == "Dill pickles"
    assert any(m == "POST" and p == "/pantry-batches" for m, p, _, _, _ in calls)


def test_garden_use_pantry_batch_proxy_for_a_child(client, calls):
    # Taking a jar out of the pantry is open to every role, children included.
    resp = client.post("/api/garden/pantry-batches/batch-1/use", headers=KID_HEADERS, json={"qty_used": 1})

    assert resp.status_code == 200, resp.text
    assert resp.json()["qty_remaining"] == 7
    assert any(m == "POST" and p == "/pantry-batches/batch-1/use" for m, p, _, _, _ in calls)
    forwarded = next(headers for m, p, _, _, headers in calls if p == "/pantry-batches/batch-1/use")
    assert forwarded["X-User-Id"] == "child"


def test_garden_patch_pantry_batch_proxy(client, calls):
    resp = client.patch(
        "/api/garden/pantry-batches/batch-1",
        headers=ADMIN_HEADERS,
        json={"storage_location": "Cold room"},
    )

    assert resp.status_code == 200, resp.text
    assert any(m == "PATCH" and p == "/pantry-batches/batch-1" for m, p, _, _, _ in calls)


def test_garden_delete_pantry_batch_proxy(client, calls):
    resp = client.delete("/api/garden/pantry-batches/batch-1", headers=ADMIN_HEADERS)

    assert resp.status_code == 204, resp.text
    assert any(m == "DELETE" and p == "/pantry-batches/batch-1" for m, p, _, _, _ in calls)


def test_garden_pantry_needs_authentication(client):
    assert client.get("/api/garden/pantry-batches").status_code == 401
    assert client.get("/api/garden/harvest/forecast").status_code == 401


# --------------------------------------------------------------------------- Phase 8: hardware

def test_garden_hardware_discover_proxy(client, calls):
    resp = client.get("/api/garden/hardware/discover", headers=KID_HEADERS, params={"fresh": "true"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_any_hardware"] is True
    assert data["moisture_sensors"][0]["entity_id"] == "sensor.bed1_soil_moisture"
    assert any(
        m == "GET" and p == "/hardware/discover" and params == {"fresh": "true"}
        for m, p, params, _, _ in calls
    )


def test_garden_bindings_proxy_for_a_plant(client, calls):
    resp = client.get("/api/garden/bindings", headers=KID_HEADERS, params={"plant_id": "plant-1"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["bindings"][0]["valve_mode"] == "notify_only"
    forwarded = next(row for row in calls if row[1] == "/bindings")
    assert forwarded[2] == {"plant_id": "plant-1"}
    assert forwarded[4]["X-User-Id"] == "child"


def test_garden_upsert_binding_proxy(client, calls):
    resp = client.put(
        "/api/garden/bindings/location/bed-1",
        headers=ADMIN_HEADERS,
        json={"valve_entity_id": "valve.bed1_drip", "max_run_minutes": 8},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["max_run_minutes"] == 8
    forwarded = next(row for row in calls if row[0] == "PUT")
    assert forwarded[1] == "/bindings/location/bed-1"
    assert forwarded[3] == {"valve_entity_id": "valve.bed1_drip", "max_run_minutes": 8}


def test_garden_delete_binding_proxy(client, calls):
    resp = client.delete("/api/garden/bindings/location/bed-1", headers=ADMIN_HEADERS)

    assert resp.status_code == 204, resp.text
    assert any(m == "DELETE" and p == "/bindings/location/bed-1" for m, p, _, _, _ in calls)


def test_garden_valve_run_proxy_forwards_the_duration(client, calls):
    resp = client.post(
        "/api/garden/bindings/binding-1/valve/run", headers=ADMIN_HEADERS, json={"minutes": 3}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["minutes"] == 3
    forwarded = next(row for row in calls if row[1].endswith("/valve/run"))
    assert forwarded[3] == {"minutes": 3}
    assert forwarded[4]["X-User-Id"] == "mother"


def test_garden_valve_run_proxy_without_a_body(client, calls):
    resp = client.post("/api/garden/bindings/binding-1/valve/run", headers=ADMIN_HEADERS)

    assert resp.status_code == 200, resp.text
    forwarded = next(row for row in calls if row[1].endswith("/valve/run"))
    assert forwarded[3] == {}


def test_garden_valve_stop_proxy(client, calls):
    resp = client.post("/api/garden/bindings/binding-1/valve/stop", headers=ADMIN_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["stopped"] is True
    assert any(p == "/bindings/binding-1/valve/stop" for _, p, _, _, _ in calls)


def test_garden_tag_scan_proxy_is_open_to_a_child(client, calls):
    resp = client.get("/api/garden/tags/tag-bed1", headers=KID_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["target_name"] == "Bed 1"
    forwarded = next(row for row in calls if row[1] == "/tags/tag-bed1")
    assert forwarded[4]["X-User-Id"] == "child"


def test_garden_hardware_needs_authentication(client):
    assert client.get("/api/garden/hardware/discover").status_code == 401
    assert client.get("/api/garden/bindings").status_code == 401
    assert client.get("/api/garden/tags/tag-bed1").status_code == 401


# --------------------------------------------------------------------------- query forwarding

def test_water_all_forwards_due_only_and_source(client, calls):
    """The room button says "only what is due" and "a person did this"."""
    resp = client.post(
        "/api/garden/locations/loc-1/water-all",
        headers=KID_HEADERS,
        params={"due_only": "false", "source": "nfc_tag"},
    )

    assert resp.status_code == 200, resp.text
    forwarded = next(row for row in calls if row[1] == "/locations/loc-1/water-all")
    assert forwarded[2] == {"due_only": "false", "source": "nfc_tag"}


def test_a_plant_logs_limit_is_forwarded(client, calls):
    resp = client.get("/api/garden/plants/p-1/logs", headers=KID_HEADERS, params={"limit": "10"})

    assert resp.status_code == 200, resp.text
    forwarded = next(row for row in calls if row[1] == "/plants/p-1/logs")
    assert forwarded[2] == {"limit": 10}
