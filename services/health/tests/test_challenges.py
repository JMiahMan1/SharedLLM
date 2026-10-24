"""Weekly family challenges: measured from logged data only, adults start them."""

from __future__ import annotations

from datetime import date

import pytest

from services.health import challenges

ADULT = {"X-User-Is-Minor": "false"}
# 2026-10-19 is a Monday; the challenge week runs to Sunday 2026-10-25.
WEEK_DAY = "2026-10-21"


def _as(user: str, **extra: str) -> dict:
    return {"X-User-Id": user, **extra}


class TestPure:
    def test_week_starts_on_monday(self):
        assert challenges.week_start(date(2026, 10, 21)) == date(2026, 10, 19)
        assert challenges.week_start(date(2026, 10, 19)) == date(2026, 10, 19)
        assert challenges.week_start(date(2026, 10, 25)) == date(2026, 10, 19)
        assert challenges.week_days(date(2026, 10, 19))[-1] == "2026-10-25"

    def test_missing_days_never_count(self):
        days = [
            {"day": "d1", "water_ml": 2100, "sleep_minutes": None, "recipe_dinner": False},
            {"day": "d2", "water_ml": None, "sleep_minutes": 500, "recipe_dinner": True},
            {"day": "d3", "water_ml": 1500, "sleep_minutes": 400, "recipe_dinner": False},
        ]
        goals = {"water_goal_ml": 2000, "sleep_need_minutes": 480}
        assert challenges.progress("water_days", days, **goals) == {"count": 1, "days": ["d1"]}
        assert challenges.progress("sleep_nights", days, **goals) == {"count": 1, "days": ["d2"]}
        assert challenges.progress("cook_nights", days, **goals) == {"count": 1, "days": ["d2"]}

    def test_unknown_kind_is_an_error(self):
        with pytest.raises(ValueError):
            challenges.day_counts("calories", {"day": "d1"}, water_goal_ml=1, sleep_need_minutes=1)

    def test_leaderboard_ranks_by_count_then_name(self):
        board = challenges.leaderboard(
            [{"user_id": "zoe", "count": 2}, {"user_id": "abe", "count": 2}, {"user_id": "mia", "count": 5}],
            target_days=5,
        )
        assert [row["user_id"] for row in board] == ["mia", "abe", "zoe"]
        assert [row["reached"] for row in board] == [True, False, False]

    def test_no_kind_is_about_calories_or_weight(self):
        text = " ".join(f"{kind} {meta['title']} {meta['detail']}" for kind, meta in challenges.KINDS.items()).lower()
        assert "calor" not in text and "weight" not in text and "kcal" not in text


class TestRoutes:
    def test_adult_creates_and_is_joined(self, client):
        res = client.post("/challenges", json={"kind": "water_days", "day": WEEK_DAY, "target_days": 5}, headers=ADULT)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["week_start"] == "2026-10-19" and body["week_end"] == "2026-10-25"
        assert body["joined"] is True
        assert body["leaderboard"] == [
            {"user_id": "jeremiah", "count": 0, "days": [], "guideline_goal": True, "reached": False}
        ]

    def test_minor_or_unknown_cannot_create(self, client):
        payload = {"kind": "water_days", "day": WEEK_DAY, "target_days": 3}
        assert client.post("/challenges", json=payload).status_code == 403
        assert client.post("/challenges", json=payload, headers={"X-User-Is-Minor": "true"}).status_code == 403
        listing = client.get("/challenges", params={"day": WEEK_DAY}).json()
        assert listing["can_create"] is False

    def test_kid_mode_profile_cannot_create_even_if_gateway_says_adult(self, client):
        client.put("/profile", json={"kid_mode": True})
        res = client.post("/challenges", json={"kind": "cook_nights", "day": WEEK_DAY, "target_days": 3}, headers=ADULT)
        assert res.status_code == 403

    def test_admin_may_create(self, client):
        res = client.post(
            "/challenges",
            json={"kind": "sleep_nights", "day": WEEK_DAY, "target_days": 4},
            headers={"X-User-Is-Admin": "true"},
        )
        assert res.status_code == 200

    def test_rejects_unmeasurable_kind_and_bad_target(self, client):
        assert client.post("/challenges", json={"kind": "steps", "day": WEEK_DAY, "target_days": 3}, headers=ADULT).status_code == 422
        assert client.post("/challenges", json={"kind": "water_days", "day": WEEK_DAY, "target_days": 8}, headers=ADULT).status_code == 422

    def test_one_per_kind_per_week(self, client):
        payload = {"kind": "cook_nights", "day": WEEK_DAY, "target_days": 3}
        assert client.post("/challenges", json=payload, headers=ADULT).status_code == 200
        again = client.post("/challenges", json={**payload, "day": "2026-10-25"}, headers=ADULT)
        assert again.status_code == 409
        listing = client.get("/challenges", params={"day": WEEK_DAY}).json()
        assert {k["kind"]: k["taken"] for k in listing["kinds"]} == {
            "water_days": False,
            "sleep_nights": False,
            "cook_nights": True,
        }
        # Next week is free again.
        assert client.post("/challenges", json={**payload, "day": "2026-10-26"}, headers=ADULT).status_code == 200

    def test_progress_comes_from_logged_data_only(self, client):
        created = client.post(
            "/challenges", json={"kind": "cook_nights", "day": WEEK_DAY, "target_days": 2}, headers=ADULT
        ).json()
        client.post("/challenges/%d/join" % created["id"], headers=_as("emma"))
        # A recipe dinner counts; a plain dinner, a recipe lunch and last week do not.
        recipe = {"meal": "dinner", "name": "Chili", "calories": 400, "recipe_path": "Recipes/Chili.md", "source": "recipe"}
        client.post("/food", json={**recipe, "day": "2026-10-19"})
        client.post("/food", json={**recipe, "day": "2026-10-19"})
        client.post("/food", json={**recipe, "day": "2026-10-20"})
        client.post("/food", json={**recipe, "day": "2026-10-18"})
        client.post("/food", json={"day": "2026-10-21", "meal": "dinner", "name": "Toast", "calories": 100})
        client.post("/food", json={**recipe, "day": "2026-10-21", "meal": "lunch"})
        client.post("/food", json={**recipe, "day": "2026-10-22"}, headers=_as("emma"))

        listing = client.get("/challenges", params={"day": WEEK_DAY}).json()
        board = listing["challenges"][0]["leaderboard"]
        assert board[0] == {
            "user_id": "jeremiah",
            "count": 2,
            "days": ["2026-10-19", "2026-10-20"],
            "guideline_goal": False,
            "reached": True,
        }
        assert board[1]["user_id"] == "emma" and board[1]["count"] == 1 and board[1]["reached"] is False

    def test_water_uses_the_members_own_goal(self, client):
        client.put("/profile", json={"water_goal_ml": 1000})
        created = client.post(
            "/challenges", json={"kind": "water_days", "day": WEEK_DAY, "target_days": 1}, headers=ADULT
        ).json()
        client.post(
            "/metrics",
            json={"samples": [
                {"metric": "water", "value": 600, "unit": "ml", "recorded_at": "2026-10-20T09:00:00+00:00"},
                {"metric": "water", "value": 500, "unit": "ml", "recorded_at": "2026-10-20T15:00:00+00:00"},
                {"metric": "water", "value": 900, "unit": "ml", "recorded_at": "2026-10-21T09:00:00+00:00"},
            ]},
        )
        row = client.get("/challenges", params={"day": WEEK_DAY}).json()["challenges"][0]
        assert row["id"] == created["id"]
        assert row["leaderboard"][0]["days"] == ["2026-10-20"]
        assert row["leaderboard"][0]["guideline_goal"] is False

    def test_join_is_idempotent_and_leave_works(self, client):
        created = client.post(
            "/challenges", json={"kind": "sleep_nights", "day": WEEK_DAY, "target_days": 3}, headers=ADULT
        ).json()
        path = f"/challenges/{created['id']}/join"
        client.post(path, headers=_as("emma"))
        joined = client.post(path, headers=_as("emma")).json()
        assert joined["joined"] is True
        assert [row["user_id"] for row in joined["leaderboard"]].count("emma") == 1
        left = client.delete(path, headers=_as("emma")).json()
        assert left["joined"] is False
        assert [row["user_id"] for row in left["leaderboard"]] == ["jeremiah"]
        assert client.post("/challenges/9999/join").status_code == 404

    def test_only_the_creator_or_an_admin_ends_it(self, client):
        created = client.post(
            "/challenges", json={"kind": "water_days", "day": WEEK_DAY, "target_days": 3}, headers=ADULT
        ).json()
        assert client.delete(f"/challenges/{created['id']}", headers=_as("emma")).status_code == 403
        assert client.delete(f"/challenges/{created['id']}").json() == {"deleted": created["id"]}
        assert client.get("/challenges", params={"day": WEEK_DAY}).json()["challenges"] == []

    def test_export_lists_joined_challenges(self, client):
        created = client.post(
            "/challenges", json={"kind": "water_days", "day": WEEK_DAY, "target_days": 3}, headers=ADULT
        ).json()
        exported = client.get("/export").json()
        assert [row["challenge_id"] for row in exported["challenges_joined"]] == [created["id"]]
