"""HTTP contract tests for the health service."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.health import main as health_main

TODAY = date.today().isoformat()


class TestGuard:
    def test_calls_without_the_internal_secret_are_refused(self, engine):
        health_main.app.state.engine = engine
        with TestClient(health_main.app) as client:
            assert client.get("/profile").status_code == 403
        health_main.app.state.engine = None

    def test_the_secret_alone_does_not_name_a_user(self, client):
        response = client.get("/profile", headers={"X-User-Id": ""})
        assert response.status_code == 401

    def test_health_is_reachable_without_a_secret(self, client):
        assert client.get("/health", headers={"X-Internal-Secret": ""}).status_code == 200


class TestProfile:
    def test_profile_starts_absent_not_fabricated(self, client):
        body = client.get("/profile").json()
        assert body["exists"] is False
        assert body["birth_year"] is None
        assert body["sleep_need_minutes"] is None

    def test_profile_round_trips(self, client):
        saved = client.put(
            "/profile",
            json={
                "display_name": "Jeremiah",
                "birth_year": 1985,
                "height_cm": 180,
                "sex": "male",
                "activity_level": "active",
                "goal": "maintain",
                "units": "imperial",
                "kid_mode": False,
                "sleep_need_minutes": 450,
                "water_goal_ml": 2500,
                "weekly_active_minutes": 180,
            },
        )
        assert saved.status_code == 200
        body = client.get("/profile").json()
        assert body["exists"] is True
        assert body["height_cm"] == 180
        assert body["sleep_need_minutes"] == 450

    def test_bad_height_is_rejected(self, client):
        response = client.put("/profile", json={"height_cm": 12})
        assert response.status_code == 422

    def test_bad_activity_level_is_rejected(self, client):
        response = client.put("/profile", json={"activity_level": "olympian"})
        assert response.status_code == 422

    def test_other_sex_is_rejected(self, client):
        response = client.put("/profile", json={"sex": "other"})
        assert response.status_code == 422
        for value in ("female", "male", "unspecified"):
            assert client.put("/profile", json={"sex": value}).status_code == 200


class TestMetrics:
    def test_weight_in_pounds_is_stored_in_kilograms(self, client):
        response = client.post(
            "/metrics",
            json={
                "samples": [
                    {
                        "metric": "weight",
                        "value": 154,
                        "unit": "lb",
                        "recorded_at": "2026-10-20T07:30:00-07:00",
                        "source": "scale",
                        "external_id": "scale-1-20261020",
                    }
                ]
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] == 1
        assert body["samples"][0]["unit"] == "kg"
        assert body["samples"][0]["value"] == pytest.approx(69.85322498)

    def test_replayed_external_id_is_a_duplicate(self, client):
        sample = {
            "metric": "resting_hr",
            "value": 58,
            "unit": "bpm",
            "recorded_at": "2026-10-21T06:00:00-07:00",
            "source": "health_connect",
            "external_id": "hc-123",
        }
        first = client.post("/metrics", json={"samples": [sample]}).json()
        second = client.post("/metrics", json={"samples": [sample]}).json()
        assert first["accepted"] == 1
        assert second["accepted"] == 0
        assert second["duplicates"] == 1
        history = client.get("/metrics/resting_hr?days=365").json()
        assert len(history["samples"]) == 1

    def test_manual_readings_may_repeat(self, client):
        sample = {
            "metric": "water",
            "value": 250,
            "unit": "ml",
            "recorded_at": "2026-10-21T10:00:00-07:00",
            "source": "manual",
        }
        assert client.post("/metrics", json={"samples": [sample]}).json()["accepted"] == 1
        assert client.post("/metrics", json={"samples": [sample]}).json()["accepted"] == 1

    def test_unknown_metric_names_the_valid_set(self, client):
        response = client.post(
            "/metrics",
            json={"samples": [{"metric": "vibes", "value": 1, "unit": "x"}]},
        )
        assert response.status_code == 422
        assert "valid metrics" in response.json()["detail"]

    def test_out_of_range_reading_is_rejected(self, client):
        response = client.post(
            "/metrics",
            json={"samples": [{"metric": "spo2", "value": 12, "unit": "%"}]},
        )
        assert response.status_code == 422
        assert "spo2" in response.json()["detail"]

    def test_history_is_newest_first(self, client):
        client.post(
            "/metrics",
            json={
                "samples": [
                    {"metric": "weight", "value": 80, "unit": "kg", "recorded_at": "2026-10-01T08:00:00Z"},
                    {"metric": "weight", "value": 79, "unit": "kg", "recorded_at": "2026-10-10T08:00:00Z"},
                ]
            },
        )
        samples = client.get("/metrics/weight?days=365").json()["samples"]
        assert [s["value"] for s in samples] == [79, 80]

    def test_a_long_history_is_capped_and_says_so(self, client):
        """A year of water is thousands of rows, so the read is bounded now."""
        client.post(
            "/metrics",
            json={
                "samples": [
                    {"metric": "water", "value": 100, "unit": "ml", "recorded_at": "2026-10-01T08:00:00Z"},
                    {"metric": "water", "value": 200, "unit": "ml", "recorded_at": "2026-10-02T08:00:00Z"},
                    {"metric": "water", "value": 300, "unit": "ml", "recorded_at": "2026-10-03T08:00:00Z"},
                ]
            },
        )
        body = client.get("/metrics/water?days=365&limit=2").json()
        assert body["truncated"] is True
        assert body["limit"] == 2
        assert [s["value"] for s in body["samples"]] == [300, 200]
        assert body["latest"]["value"] == 300

    def test_deleting_someone_elses_reading_is_a_404(self, client):
        created = client.post(
            "/metrics",
            json={"samples": [{"metric": "weight", "value": 80, "unit": "kg"}]},
        ).json()["samples"][0]
        response = client.delete(
            f"/metrics/{created['id']}", headers={"X-User-Id": "michele"}
        )
        assert response.status_code == 404


class TestSleep:
    def test_duration_is_computed_from_bedtime_and_wake(self, client):
        response = client.post(
            "/sleep",
            json={
                "night": "2026-10-21",
                "bedtime": "2026-10-20T22:30:00-07:00",
                "wake_time": "2026-10-21T06:30:00-07:00",
                "quality": 4,
                "source": "manual",
            },
        )
        assert response.status_code == 200
        assert response.json()["duration_minutes"] == 480

    def test_cross_midnight_bare_times_do_not_go_negative(self, client):
        response = client.post(
            "/sleep",
            json={"night": "2026-10-21", "bedtime": "22:30", "wake_time": "06:30"},
        )
        assert response.status_code == 200
        assert response.json()["duration_minutes"] == 480

    def test_bare_bedtime_keeps_the_household_offset(self, client):
        response = client.post(
            "/sleep",
            json={"night": "2026-10-21", "bedtime": "22:30", "wake_time": "06:30"},
        )
        bedtime = datetime.fromisoformat(response.json()["bedtime"])
        assert bedtime.hour == 22
        assert bedtime.utcoffset() == datetime.now().astimezone().utcoffset()

    def test_a_night_needs_a_duration_or_both_times(self, client):
        response = client.post("/sleep", json={"night": "2026-10-21"})
        assert response.status_code == 422

    def test_device_replay_is_idempotent(self, client):
        payload = {
            "night": "2026-10-21",
            "duration_minutes": 430,
            "source": "health_connect",
            "external_id": "hc-sleep-1",
        }
        assert client.post("/sleep", json=payload).json()["duplicate"] is False
        assert client.post("/sleep", json=payload).json()["duplicate"] is True
        assert len(client.get("/sleep").json()["sessions"]) == 1

    def test_device_session_wins_over_a_manual_one_for_the_same_night(self, client):
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 300})
        client.post(
            "/sleep",
            json={
                "night": "2026-10-21",
                "duration_minutes": 450,
                "source": "health_connect",
                "external_id": "hc-1",
            },
        )
        summary = client.get("/summary?day=2026-10-21").json()
        assert summary["sleep"]["last_night"]["duration_minutes"] == 450
        assert summary["sleep"]["last_night"]["source"] == "health_connect"

    def test_a_second_manual_log_for_the_same_night_replaces_it(self, client):
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 300})
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 450})
        sessions = client.get("/sleep").json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["duration_minutes"] == 450

    def test_a_night_far_in_the_future_is_refused(self, client):
        far = (date.today() + timedelta(days=5)).isoformat()
        response = client.post("/sleep", json={"night": far, "duration_minutes": 400})
        assert response.status_code == 422
        assert "future" in response.json()["detail"]


class TestCheckins:
    def test_checkin_round_trips_and_updates(self, client):
        first = client.put("/checkins/2026-10-21", json={"energy": 3, "mood": 4}).json()
        assert first["energy"] == 3
        second = client.put("/checkins/2026-10-21", json={"energy": 5, "soreness": 2}).json()
        assert second["energy"] == 5
        assert second["mood"] == 4  # untouched answers stay

    def test_an_empty_checkin_is_refused(self, client):
        assert client.put("/checkins/2026-10-21", json={}).status_code == 422

    def test_scores_outside_one_to_five_are_rejected(self, client):
        assert client.put("/checkins/2026-10-21", json={"energy": 9}).status_code == 422

    def test_a_checkin_cannot_be_for_a_future_day(self, client):
        far = (date.today() + timedelta(days=5)).isoformat()
        assert client.put(f"/checkins/{far}", json={"energy": 3}).status_code == 422


class TestNutrition:
    def test_food_totals_multiply_by_servings(self, client):
        assert client.post(
            "/food",
            json={
                "day": "2026-10-21",
                "meal": "breakfast",
                "name": "Oats",
                "calories": 150,
                "protein_g": 5,
                "carbs_g": 27,
                "fat_g": 3,
                "servings": 2,
            },
        ).status_code == 200
        body = client.get("/nutrition/day?day=2026-10-21").json()
        assert body["logged"] is True
        assert body["totals"]["calories"] == 300
        assert body["totals"]["protein_g"] == 10
        assert body["meals"]["breakfast"]["calories"] == 300

    def test_the_food_log_is_capped_and_says_so(self, client):
        """Without a day or a window the read had no filter at all."""
        for name in ("Oats", "Eggs", "Toast"):
            assert client.post(
                "/food",
                json={"day": "2026-10-21", "meal": "breakfast", "name": name, "calories": 100},
            ).status_code == 200
        body = client.get("/food?limit=2").json()
        assert body["truncated"] is True
        assert len(body["entries"]) == 2
        assert client.get("/food?limit=10").json()["truncated"] is False

    def test_a_day_with_no_entries_is_not_a_fake_zero_day(self, client):
        body = client.get("/nutrition/day?day=2019-01-01").json()
        assert body["logged"] is False
        assert body["entries_count"] == 0
        assert body["totals"]["calories"] == 0

    def test_target_says_which_profile_fields_are_missing(self, client):
        body = client.get("/nutrition/day?day=2026-10-21").json()
        assert body["target"]["kind"] == "unavailable"
        assert set(body["target"]["missing"]) == {"birth year", "height", "weight", "sex"}
        assert body["target"]["protein_g"] is None

    def test_target_uses_the_latest_weight_reading(self, client):
        client.put(
            "/profile",
            json={
                "birth_year": 1985,
                "height_cm": 180,
                "sex": "male",
                "activity_level": "moderate",
                "goal": "maintain",
            },
        )
        client.post(
            "/metrics",
            json={"samples": [{"metric": "weight", "value": 80, "unit": "kg", "recorded_at": "2026-10-20T07:00:00Z"}]},
        )
        body = client.get("/nutrition/day?day=2026-10-21").json()
        assert body["target"]["kind"] == "adult_range"
        assert body["target"]["min"] < body["target"]["max"]
        assert body["target"]["protein_g"] > 0

    def test_density_is_computed_only_with_a_serving_weight(self, client):
        with_weight = client.post(
            "/food",
            json={
                "day": "2026-10-21",
                "name": "Apple",
                "calories": 95,
                "serving_grams": 182,
            },
        ).json()
        assert with_weight["density"]["band"] == "green"
        without = client.post(
            "/food", json={"day": "2026-10-21", "name": "Mystery", "calories": 95}
        ).json()
        assert without["density"] is None

    def test_negative_calories_are_rejected(self, client):
        response = client.post(
            "/food", json={"day": "2026-10-21", "name": "Nothing", "calories": -1}
        )
        assert response.status_code == 422


def _off_item(name: str = "Broccoli Soup") -> dict:
    return {
        "code": "123",
        "name": name,
        "brand": "Brand",
        "image": None,
        "serving_size": None,
        "serving_grams": None,
        "source": "openfoodfacts",
        "per_100g": {
            "calories": 50,
            "protein_g": 1.0,
            "carbs_g": 9.0,
            "fat_g": 1.0,
            "fiber_g": 1.0,
            "sugar_g": 2.0,
            "sodium_mg": 100,
        },
    }


class TestFoodSearch:
    def test_merges_seed_then_openfoodfacts(self, client, mini_seed_path, monkeypatch):
        monkeypatch.setenv("FDC_SEED_PATH", str(mini_seed_path))

        async def fake_search(query, limit):
            assert query == "broccoli"
            assert limit == 4  # appended after the single seed hit
            return [_off_item()]

        monkeypatch.setattr(health_main, "_off_search", fake_search)
        resp = client.get("/food/search", params={"q": "broccoli", "limit": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "broccoli"
        assert body["sources"] == ["usda_fdc", "openfoodfacts"]
        assert body["offline_sources"] == []
        assert body["seed"] == {"present": True, "results": 1}
        assert [item["source"] for item in body["results"]] == ["usda_fdc", "openfoodfacts"]
        assert body["results"][0]["code"] == "foundation:1111"
        assert body["results"][1]["name"] == "Broccoli Soup"

    def test_seed_filling_the_limit_skips_openfoodfacts(self, client, mini_seed_path, monkeypatch):
        monkeypatch.setenv("FDC_SEED_PATH", str(mini_seed_path))

        async def boom(query, limit):
            raise AssertionError("OFF must not be called when the seed fills the limit")

        monkeypatch.setattr(health_main, "_off_search", boom)
        resp = client.get("/food/search", params={"q": "broccoli", "limit": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["sources"] == ["usda_fdc"]
        assert len(body["results"]) == 1

    def test_openfoodfacts_failure_degrades_to_seed(self, client, mini_seed_path, monkeypatch):
        monkeypatch.setenv("FDC_SEED_PATH", str(mini_seed_path))

        async def fail(query, limit):
            raise health_main.HTTPException(status_code=503, detail="down")

        monkeypatch.setattr(health_main, "_off_search", fail)
        resp = client.get("/food/search", params={"q": "broccoli"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["sources"] == ["usda_fdc"]
        assert body["offline_sources"] == ["openfoodfacts"]
        assert body["seed"] == {"present": True, "results": 1}

    def test_missing_seed_answers_from_openfoodfacts(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("FDC_SEED_PATH", str(tmp_path / "nope.db"))

        async def fake_search(query, limit):
            return [_off_item()]

        monkeypatch.setattr(health_main, "_off_search", fake_search)
        resp = client.get("/food/search", params={"q": "soup"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["sources"] == ["openfoodfacts"]
        assert body["seed"] == {"present": False, "results": 0}
        assert len(body["results"]) == 1

    def test_both_sources_down_is_an_error(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("FDC_SEED_PATH", str(tmp_path / "nope.db"))

        async def fail(query, limit):
            raise health_main.HTTPException(status_code=503, detail="OpenFoodFacts is unreachable")

        monkeypatch.setattr(health_main, "_off_search", fail)
        resp = client.get("/food/search", params={"q": "soup"})
        assert resp.status_code == 503


class TestFoodBarcode:
    def test_barcode_is_cached_after_the_first_lookup(self, client, monkeypatch):
        calls = []

        async def fake_get(path, params=None):
            calls.append(path)
            return {
                "status": 1,
                "product": {
                    "code": "3017620422003",
                    "product_name": "Nutella",
                    "brands": "Ferrero",
                    "serving_size": "15 g",
                    "serving_quantity": "15",
                    "nutriments": {"energy-kcal_100g": 539, "proteins_100g": 6.3, "sodium_100g": 0.0428},
                },
            }

        monkeypatch.setattr(health_main, "_off_get", fake_get)
        first = client.get("/food/barcode/3017620422003")
        second = client.get("/food/barcode/3017620422003")
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        body = second.json()
        assert body["name"] == "Nutella"
        assert body["source"] == "openfoodfacts"
        assert body["serving_grams"] == 15
        assert body["per_100g"]["sodium_mg"] == pytest.approx(42.8)
        assert len(calls) == 1  # the second lookup came from the cache

    def test_unknown_barcode_is_a_404(self, client, monkeypatch):
        async def fake_get(path, params=None):
            return {"status": 0, "status_verbose": "product not found"}

        monkeypatch.setattr(health_main, "_off_get", fake_get)
        resp = client.get("/food/barcode/00000000000000")
        assert resp.status_code == 404
        assert "not in OpenFoodFacts" in resp.json()["detail"]

    def test_product_without_a_name_is_unusable(self, client, monkeypatch):
        async def fake_get(path, params=None):
            return {"status": 1, "product": {"code": "1234", "nutriments": {"energy-kcal_100g": 20}}}

        monkeypatch.setattr(health_main, "_off_get", fake_get)
        resp = client.get("/food/barcode/1234")
        assert resp.status_code == 404
        assert "no usable nutrition data" in resp.json()["detail"]

    def test_invalid_barcode_is_rejected(self, client):
        resp = client.get("/food/barcode/ab")
        assert resp.status_code == 422


class TestSummary:
    def test_summary_composes_sleep_checkin_and_readiness(self, client):
        client.put(
            "/profile",
            json={"sleep_need_minutes": 480, "water_goal_ml": 2000, "sex": "male"},
        )
        client.post(
            "/sleep",
            json={"night": "2026-10-21", "duration_minutes": 460, "quality": 4},
        )
        client.put("/checkins/2026-10-21", json={"energy": 4, "soreness": 2})
        summary = client.get("/summary?day=2026-10-21").json()
        assert summary["sleep"]["score"]["score"] > 80
        assert summary["readiness"]["score"] is not None
        assert summary["checkin"]["energy"] == 4
        assert summary["defaults_used"] == []
        assert summary["activity"] is None

    def test_summary_names_the_guideline_defaults_in_use(self, client):
        summary = client.get(f"/summary?day={TODAY}").json()
        assert "sleep_need_minutes" in summary["defaults_used"]
        assert "water_goal_ml" in summary["defaults_used"]
        assert summary["sleep"]["need_minutes"] == 480

    def test_water_distinguishes_none_logged_from_zero(self, client):
        empty = client.get("/summary?day=2026-10-21").json()
        assert empty["water"]["logged"] is False
        client.post(
            "/metrics",
            json={"samples": [{"metric": "water", "value": 500, "unit": "ml", "recorded_at": "2026-10-21T09:00:00Z"}]},
        )
        logged = client.get("/summary?day=2026-10-21").json()
        assert logged["water"]["logged"] is True
        assert logged["water"]["ml"] == 500

    def test_water_drunk_late_counts_on_the_day_it_was_drunk(self, client):
        """A 6pm glass is stored with a UTC date of the next day.

        The day boundary used to be a raw string compare on recorded_at, so
        anything logged after 5pm local (the container runs
        TZ=America/Phoenix) landed on tomorrow: today's ring, the weekly water
        average and the streak all read the wrong day.
        """
        import os
        import time

        original = os.environ.get("TZ")
        os.environ["TZ"] = "America/Phoenix"
        time.tzset()
        try:
            # 20:00 Phoenix on 2026-10-21 is 03:00Z on the 22nd.
            client.post(
                "/metrics",
                json={"samples": [{"metric": "water", "value": 500, "unit": "ml", "recorded_at": "2026-10-22T03:00:00Z"}]},
            )
            drunk = client.get("/summary?day=2026-10-21").json()
            assert drunk["water"]["logged"] is True
            assert drunk["water"]["ml"] == 500
            assert client.get("/summary?day=2026-10-22").json()["water"]["logged"] is False
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_readiness_names_missing_baseline(self, client):
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 420})
        client.post(
            "/metrics",
            json={"samples": [{"metric": "resting_hr", "value": 60, "unit": "bpm", "recorded_at": "2026-10-21T06:00:00Z"}]},
        )
        summary = client.get("/summary?day=2026-10-21").json()
        assert summary["readiness"] is not None
        assert summary["resting_hr_baseline"] == 60
        assert summary["metrics"]["resting_hr"]["value"] == 60


class TestSharingAndExport:
    def test_family_only_projects_explicitly_shared_scopes(self, client):
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 450})
        client.post(
            "/food",
            json={"day": "2026-10-21", "name": "Toast", "calories": 120},
        )
        assert client.put("/shares", json={"viewer": "michele", "scope": "sleep"}).status_code == 200
        body = client.get("/family?day=2026-10-21", headers={"X-User-Id": "michele"}).json()
        assert body["people"] == [
            {
                "user_id": "jeremiah",
                "scopes": ["sleep"],
                "sleep": {
                    "duration_minutes": 450,
                    "score": client.get("/summary?day=2026-10-21").json()["sleep"]["score"]["score"],
                    "label": client.get("/summary?day=2026-10-21").json()["sleep"]["score"]["label"],
                    "logged": True,
                },
            }
        ]

    def test_sharing_off_hides_again(self, client):
        client.put("/shares", json={"viewer": "michele", "scope": "sleep"})
        client.put("/shares", json={"viewer": "michele", "scope": "sleep", "enabled": False})
        body = client.get("/family?day=2026-10-21", headers={"X-User-Id": "michele"}).json()
        assert body["people"] == []

    def test_a_digital_scope_projects_as_a_grant_only(self, client):
        # An adult opens their own digital wellbeing to someone. The health
        # store holds only the grant; the gateway fills the number in from
        # telemetry, so nothing about the phone is invented here.
        assert client.put("/shares", json={"viewer": "michele", "scope": "digital"}).status_code == 200
        body = client.get("/family?day=2026-10-21", headers={"X-User-Id": "michele"}).json()
        assert body["people"] == [
            {"user_id": "jeremiah", "scopes": ["digital"], "digital": {"shared": True}}
        ]

    def test_a_minor_cannot_open_their_phone_use(self, client):
        client.put("/profile", json={"kid_mode": True})
        response = client.put("/shares", json={"viewer": "michele", "scope": "digital"})
        assert response.status_code == 422
        assert "adults" in response.json()["detail"]

    def test_you_cannot_share_with_yourself(self, client):
        response = client.put("/shares", json={"viewer": "jeremiah", "scope": "sleep"})
        assert response.status_code == 422

    def test_export_contains_every_store(self, client):
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 450})
        client.put("/checkins/2026-10-21", json={"energy": 4})
        body = client.get("/export").json()
        assert body["user_id"] == "jeremiah"
        assert len(body["sleep"]) == 1
        assert len(body["checkins"]) == 1
        assert "activity" in body["notes"]


def _cycle_profile(client, *, birth_year: int | None = 1990, sex: str = "female", kid_mode: bool = False):
    payload = {"sex": sex, "kid_mode": kid_mode}
    if birth_year is not None:
        payload["birth_year"] = birth_year
    response = client.put("/profile", json=payload)
    assert response.status_code == 200


def _enable_cycle(client, **extra):
    response = client.put("/cycle/settings", json={"enabled": True, **extra})
    assert response.status_code == 200, response.text
    return response.json()


class TestCycleApi:
    def test_cycle_is_hidden_for_a_male_profile(self, client):
        _cycle_profile(client, sex="male")
        body = client.get("/cycle").json()
        assert body["applicable"] is False
        assert body["enabled"] is False
        assert body["summary"] is None
        assert body["periods"] == []
        assert client.put("/cycle/settings", json={"enabled": True}).status_code == 422
        assert client.post("/cycle/periods", json={"start_day": TODAY}).status_code == 422
        assert client.put(f"/cycle/logs/{TODAY}", json={"symptoms": []}).status_code == 422

    def test_a_missing_sex_hides_the_tracker_until_female_is_chosen(self, client):
        client.put("/profile", json={"birth_year": 1990})
        body = client.get("/cycle").json()
        assert body["applicable"] is False
        assert body["enabled"] is False
        assert "Female" in body["reason"]
        assert client.put("/cycle/settings", json={"enabled": True}).status_code == 422

        client.put("/profile", json={"sex": "female"})
        assert client.get("/cycle").json()["applicable"] is True

    def test_switching_away_from_female_hides_but_keeps_cycle_data(self, client):
        _cycle_profile(client)
        _enable_cycle(client)
        assert client.post(
            "/cycle/periods",
            json={"start_day": (date.today() - timedelta(days=4)).isoformat()},
        ).status_code == 200

        client.put("/profile", json={"sex": "unspecified"})
        hidden = client.get("/cycle").json()
        assert hidden["applicable"] is False
        assert hidden["summary"] is None
        assert hidden["periods"] == []

        client.put("/profile", json={"sex": "female"})
        restored = client.get("/cycle").json()
        assert restored["applicable"] is True
        assert restored["enabled"] is True
        assert len(restored["periods"]) == 1

    def test_opt_in_logs_periods_and_learns_the_gap(self, client):
        _cycle_profile(client)
        before = client.get("/cycle").json()
        assert before["applicable"] is True
        assert before["enabled"] is False

        enabled = _enable_cycle(client)
        assert enabled["enabled"] is True

        prior = (date.today() - timedelta(days=32)).isoformat()
        latest = (date.today() - timedelta(days=4)).isoformat()
        assert client.post("/cycle/periods", json={"start_day": prior}).status_code == 200
        saved = client.post(
            "/cycle/periods",
            json={"start_day": latest, "end_day": (date.today() - timedelta(days=2)).isoformat(), "flow": "heavy"},
        )
        assert saved.status_code == 200
        assert saved.json()["period"]["flow"] == "heavy"

        body = client.get("/cycle").json()
        assert len(body["periods"]) == 2
        summary = body["summary"]
        assert summary["has_data"] is True
        assert summary["cycle_day"] == 5
        assert summary["cycle_length_used"] == 28
        assert summary["phase"] == "follicular"
        assert summary["next_period_start"] == (
            date.today() - timedelta(days=4) + timedelta(days=28)
        ).isoformat()

    def test_period_and_log_validation(self, client):
        _cycle_profile(client)
        _enable_cycle(client)
        today = date.today().isoformat()
        future = (date.today() + timedelta(days=1)).isoformat()
        assert client.post("/cycle/periods", json={"start_day": future}).status_code == 422
        assert (
            client.post(
                "/cycle/periods",
                json={"start_day": today, "end_day": (date.today() - timedelta(days=3)).isoformat()},
            ).status_code
            == 422
        )
        assert client.post("/cycle/periods", json={"start_day": today, "flow": "torrential"}).status_code == 422
        assert client.put(f"/cycle/logs/{future}", json={"mood": 3}).status_code == 422
        assert client.put(f"/cycle/logs/{today}", json={"symptoms": ["vibes"]}).status_code == 422
        assert client.put(f"/cycle/logs/{today}", json={"bbt_c": 55}).status_code == 422

    def test_symptom_log_round_trips(self, client):
        _cycle_profile(client)
        _enable_cycle(client)
        today = date.today().isoformat()
        saved = client.put(
            f"/cycle/logs/{today}",
            json={
                "symptoms": ["cramps", "fatigue", "cramps"],
                "mood": 3,
                "energy": 2,
                "bbt_c": 36.6,
                "ovulation_test": "negative",
                "note": "long day",
            },
        )
        assert saved.status_code == 200
        log = saved.json()["log"]
        assert log["symptoms"] == ["cramps", "fatigue"]
        fetched = client.get(f"/cycle/logs/{today}").json()["log"]
        assert fetched["mood"] == 3
        assert fetched["bbt_c"] == 36.6
        assert client.delete(f"/cycle/logs/{today}").status_code == 200
        assert client.get(f"/cycle/logs/{today}").json()["log"] is None

    def test_a_minor_can_only_share_with_the_designated_guardian(self, client):
        _cycle_profile(client, birth_year=date.today().year - 14)
        _enable_cycle(client)
        client.post("/cycle/periods", json={"start_day": TODAY})

        # No guardian designated yet: sharing is refused before any viewer is
        # considered, and the message asks for a guardian rather than leaking
        # which viewers would otherwise be allowed.
        assert client.put("/shares", json={"viewer": "dad", "scope": "cycle"}).status_code == 422
        assert client.put("/shares", json={"viewer": "mom", "scope": "cycle"}).status_code == 422
        guardian = client.put("/cycle/settings", json={"guardian_username": "mom"})
        assert guardian.status_code == 200
        assert guardian.json()["guardian_username"] == "mom"
        assert client.put("/shares", json={"viewer": "dad", "scope": "cycle"}).status_code == 403
        assert client.put("/shares", json={"viewer": "mom", "scope": "cycle"}).status_code == 200

        family = client.get("/family", headers={"X-User-Id": "mom"}).json()
        assert family["people"][0]["cycle"]["phase"] == "menstrual"
        support = family["people"][0]["cycle"]["support"]
        assert support["phase"] == "menstrual"
        assert support["title"]
        assert len(support["tips"]) == 3
        assert "symptoms" not in family["people"][0]["cycle"]
        assert "periods" not in family["people"][0]

        # Moving the guardian drops the old grant instead of leaving it behind.
        assert client.put("/cycle/settings", json={"guardian_username": "grandma"}).status_code == 200
        assert client.get("/family", headers={"X-User-Id": "mom"}).json()["people"] == []

    def test_a_mother_without_a_birth_year_can_share_with_the_father(self, client):
        # The mother's account is adult by family role in the app; her health
        # profile simply has no birth year yet. kid_mode is the minor signal,
        # so she can choose her husband as a viewer.
        _cycle_profile(client, birth_year=None, kid_mode=False)
        _enable_cycle(client)
        client.post("/cycle/periods", json={"start_day": TODAY})
        assert client.put("/shares", json={"viewer": "dad", "scope": "cycle"}).status_code == 200
        family = client.get("/family", headers={"X-User-Id": "dad"}).json()
        entry = family["people"][0]["cycle"]
        assert entry["enabled"] is True
        assert entry["phase"] == "menstrual"
        assert entry["support"]["tips"]

    def test_unknown_birth_year_in_kid_mode_stays_guardian_only(self, client):
        _cycle_profile(client, birth_year=None, kid_mode=True)
        # Tracking is available; the minor signal only narrows who may view it.
        assert client.put("/cycle/settings", json={"enabled": True}).status_code == 200
        client.post("/cycle/periods", json={"start_day": TODAY})
        assert client.put("/shares", json={"viewer": "dad", "scope": "cycle"}).status_code == 422
        assert client.put("/cycle/settings", json={"guardian_username": "mom"}).status_code == 200
        assert client.put("/shares", json={"viewer": "dad", "scope": "cycle"}).status_code == 403
        assert client.put("/shares", json={"viewer": "mom", "scope": "cycle"}).status_code == 200

    def test_an_adult_can_share_a_minimal_cycle_summary(self, client):
        _cycle_profile(client)
        _enable_cycle(client)
        client.post("/cycle/periods", json={"start_day": TODAY})
        assert client.put("/shares", json={"viewer": "michele", "scope": "cycle"}).status_code == 200
        family = client.get("/family", headers={"X-User-Id": "michele"}).json()
        cycle_entry = family["people"][0]["cycle"]
        assert cycle_entry["enabled"] is True
        assert cycle_entry["phase"] == "menstrual"
        support = cycle_entry["support"]
        assert support["phase"] == "menstrual"
        assert support["title"]
        assert len(support["tips"]) == 3
        for private_field in ("flow", "symptoms", "bbt_c", "note", "periods", "logs"):
            assert private_field not in cycle_entry
            assert private_field not in family["people"][0]

    def test_the_cycle_projection_never_reveals_period_dates(self, client):
        # Phase and next period only: the cycle day (today minus it is the
        # last period's start) and the owner's own estimate text stay home.
        _cycle_profile(client)
        _enable_cycle(client)
        started = (date.today() - timedelta(days=3)).isoformat()
        client.post("/cycle/periods", json={"start_day": started, "flow": "heavy", "note": "zq-owner-note"})
        client.put(f"/cycle/logs/{TODAY}", json={"symptoms": ["cramps"], "note": "zq-owner-note"})
        assert client.put("/shares", json={"viewer": "dad", "scope": "cycle"}).status_code == 200
        entry = client.get("/family", headers={"X-User-Id": "dad"}).json()["people"][0]["cycle"]
        assert set(entry) == {
            "applicable",
            "enabled",
            "phase",
            "phase_label",
            "next_period_start",
            "days_until_next_period",
            "confidence",
            "support",
        }
        assert started not in json.dumps(entry)
        assert "zq-owner-note" not in json.dumps(entry)
        assert "cramps" not in json.dumps(entry)

    def test_a_viewer_in_kid_mode_never_sees_calories(self, client):
        client.post("/food", json={"day": TODAY, "name": "Toast", "calories": 120})
        assert client.put("/shares", json={"viewer": "kid", "scope": "nutrition"}).status_code == 200
        client.put("/profile", json={"kid_mode": True}, headers={"X-User-Id": "kid"})
        entry = client.get("/family", headers={"X-User-Id": "kid"}).json()["people"][0]["nutrition"]
        assert entry == {"logged": True, "calories": None}

    def test_cycle_sharing_requires_tracking_to_be_on(self, client):
        _cycle_profile(client)
        assert client.put("/shares", json={"viewer": "michele", "scope": "cycle"}).status_code == 422

    def test_turning_tracking_off_removes_cycle_grants(self, client):
        _cycle_profile(client)
        _enable_cycle(client)
        client.post("/cycle/periods", json={"start_day": TODAY})
        assert client.put("/shares", json={"viewer": "michele", "scope": "cycle"}).status_code == 200
        assert client.put("/cycle/settings", json={"enabled": False}).status_code == 200
        assert client.get("/family", headers={"X-User-Id": "michele"}).json()["people"] == []

    def test_tracking_is_available_to_a_young_profile_too(self, client):
        # Tracking follows the person's own choice; only sharing narrows for a
        # known minor (or a kid_mode profile with no age).
        _cycle_profile(client, birth_year=date.today().year - 8, kid_mode=True)
        assert client.put("/cycle/settings", json={"enabled": True}).status_code == 200

    def test_export_includes_cycle_history(self, client):
        _cycle_profile(client)
        _enable_cycle(client)
        client.post("/cycle/periods", json={"start_day": TODAY})
        client.put(f"/cycle/logs/{TODAY}", json={"symptoms": ["cramps"]})
        body = client.get("/export").json()
        assert body["cycle"]["settings"]["enabled"] is True
        assert len(body["cycle"]["periods"]) == 1
        assert len(body["cycle"]["logs"]) == 1
        assert body["cycle"]["logs"][0]["symptoms"] == ["cramps"]
