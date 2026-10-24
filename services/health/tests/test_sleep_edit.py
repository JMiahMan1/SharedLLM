"""Saved nights can be corrected in place; a device-use estimate ranks last."""

from __future__ import annotations


def _summary_night(client, day: str) -> dict:
    return client.get(f"/summary?day={day}").json()["sleep"]["last_night"]


class TestSleepEdit:
    def test_put_corrects_a_night_in_place(self, client):
        saved = client.post("/sleep", json={
            "night": "2026-10-21",
            "bedtime": "2026-10-20T22:40:00-07:00",
            "wake_time": "2026-10-21T06:10:00-07:00",
            "source": "usage_estimate",
        }).json()
        assert saved["duration_minutes"] == 450

        fixed = client.put(f"/sleep/{saved['id']}", json={
            "night": "2026-10-21",
            "bedtime": "2026-10-20T23:10:00-07:00",
            "wake_time": "2026-10-21T06:10:00-07:00",
            "quality": 4,
            "source": "manual",
        })
        assert fixed.status_code == 200
        assert fixed.json()["duration_minutes"] == 420
        assert fixed.json()["source"] == "manual"
        sessions = client.get("/sleep?days=365").json()["sessions"]
        assert [s["id"] for s in sessions] == [saved["id"]]

    def test_put_only_touches_your_own_nights(self, client):
        saved = client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 400}).json()
        other = client.put(
            f"/sleep/{saved['id']}", json={"night": "2026-10-21", "duration_minutes": 1},
            headers={"X-User-Id": "michele"},
        )
        assert other.status_code == 404
        assert client.put("/sleep/99999", json={"night": "2026-10-21", "duration_minutes": 5}).status_code == 404

    def test_put_validates_like_post(self, client):
        saved = client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 400}).json()
        assert client.put(f"/sleep/{saved['id']}", json={"night": "2026-10-21"}).status_code == 422

    def test_hand_log_beats_an_estimate_and_a_device_beats_both(self, client):
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 500, "source": "usage_estimate"})
        assert _summary_night(client, "2026-10-21")["source"] == "usage_estimate"
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 420})
        assert _summary_night(client, "2026-10-21")["source"] == "manual"
        client.post("/sleep", json={"night": "2026-10-21", "duration_minutes": 430, "source": "health_connect"})
        assert _summary_night(client, "2026-10-21")["source"] == "health_connect"
