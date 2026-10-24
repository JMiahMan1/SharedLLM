"""The weekly digest: averages over logged days only, best night, what changed."""

from __future__ import annotations

from services.health import weekly


def _day(day: str, **values) -> dict:
    return {"day": day, **values}


class TestDigest:
    def test_averages_skip_days_with_nothing_logged(self):
        this = [_day("2026-10-15", sleep_minutes=420), _day("2026-10-16"), _day("2026-10-17", sleep_minutes=480)]
        out = weekly.digest(this, [])
        assert out["averages"]["sleep_minutes"] == 450.0
        assert out["averages"]["mood"] is None
        assert out["days_logged"] == 2
        assert out["start"] == "2026-10-15" and out["end"] == "2026-10-17"

    def test_best_sleep_prefers_score_then_length(self):
        this = [
            _day("2026-10-15", sleep_minutes=500, sleep_score=70),
            _day("2026-10-16", sleep_minutes=450, sleep_score=88),
        ]
        assert weekly.digest(this, [])["best_sleep"] == {"day": "2026-10-16", "minutes": 450, "score": 88}
        unscored = [_day("2026-10-15", sleep_minutes=400), _day("2026-10-16", sleep_minutes=460)]
        assert weekly.digest(unscored, [])["best_sleep"]["day"] == "2026-10-16"
        assert weekly.digest([_day("2026-10-15")], [])["best_sleep"] is None

    def test_changes_name_only_real_moves(self):
        this = [_day("d1", sleep_minutes=480, mood=4, water_ml=2000)]
        last = [_day("d0", sleep_minutes=420, mood=4, water_ml=1900)]
        changes = weekly.digest(this, last)["changes"]
        assert [c["metric"] for c in changes] == ["sleep_minutes"]
        assert changes[0] == {"metric": "sleep_minutes", "this_week": 480.0, "last_week": 420.0, "delta": 60.0, "direction": "up"}

    def test_no_comparison_without_last_week(self):
        assert weekly.digest([_day("d1", energy=2)], [])["changes"] == []

    def test_changes_sorted_by_relative_size(self):
        this = [_day("d1", sleep_minutes=450, energy=2)]
        last = [_day("d0", sleep_minutes=420, energy=4)]
        assert [c["metric"] for c in weekly.digest(this, last)["changes"]] == ["energy", "sleep_minutes"]

    def test_kid_mode_leaves_calories_out(self):
        this = [_day("d1", calories=1800, mood=3)]
        last = [_day("d0", calories=1200, mood=3)]
        out = weekly.digest(this, last, kid_mode=True)
        assert "calories" not in out["averages"] and "calories" not in out["metrics"]
        assert "calories" not in out["days"][0]
        assert out["changes"] == []


class TestWeeklyRoute:
    def test_composes_logged_days(self, client):
        client.post("/sleep", json={"night": "2026-10-20", "duration_minutes": 420})
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 480, "source": "health_connect"})
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 300})
        client.post("/sleep", json={"night": "2026-10-12", "duration_minutes": 360})
        client.put("/checkins/2026-10-21", json={"energy": 4, "mood": 5})
        client.post("/food", json={"day": "2026-10-21", "name": "Oats", "calories": 300, "servings": 2})
        client.post("/metrics", json={"samples": [
            {"metric": "water", "value": 500, "unit": "ml", "recorded_at": "2026-10-21T15:00:00Z"},
            {"metric": "water", "value": 250, "unit": "ml", "recorded_at": "2026-10-21T18:00:00Z"},
        ]})

        out = client.get("/weekly?end=2026-10-21").json()
        assert out["start"] == "2026-10-15" and out["end"] == "2026-10-21"
        assert len(out["days"]) == 7
        today = out["days"][-1]
        assert today["sleep_minutes"] == 480  # the device night wins over the hand log
        assert today["calories"] == 600.0
        assert today["water_ml"] == 750.0
        assert (today["energy"], today["mood"]) == (4, 5)
        assert out["days"][0]["sleep_minutes"] is None
        assert out["averages"]["sleep_minutes"] == 450.0
        assert out["last_week"]["sleep_minutes"] == 360.0
        assert out["changes"][0]["metric"] == "sleep_minutes"
        assert out["best_sleep"]["day"] == "2026-10-21"

    def test_own_data_only(self, client):
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 480})
        out = client.get("/weekly?end=2026-10-21", headers={"X-User-Id": "michele"}).json()
        assert out["days_logged"] == 0
        assert out["best_sleep"] is None

    def test_rejects_a_bad_end(self, client):
        assert client.get("/weekly?end=2026-13-40").status_code == 422
