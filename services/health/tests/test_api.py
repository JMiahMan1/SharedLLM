"""HTTP contract tests for the health service."""
from __future__ import annotations

from datetime import date, datetime

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
        summary = client.get(f"/summary?day=2026-10-21").json()
        assert summary["sleep"]["last_night"]["duration_minutes"] == 450
        assert summary["sleep"]["last_night"]["source"] == "health_connect"


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
