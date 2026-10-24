"""The fasting window: adults only, started and stopped by the person."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from services.health import fasting


def test_progress_against_a_target():
    start = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
    half = fasting.progress(start, start + timedelta(hours=8), 16)
    assert half == {"elapsed_minutes": 480, "target_minutes": 960, "percent": 50, "remaining_minutes": 480, "reached": False}
    done = fasting.progress(start, start + timedelta(hours=17), 16)
    assert done["reached"] is True and done["percent"] == 100 and done["remaining_minutes"] == 0


def test_progress_without_a_target_is_elapsed_time_only():
    start = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
    assert fasting.progress(start, start + timedelta(minutes=90), None)["percent"] is None


def test_start_and_stop_a_fast(client):
    started = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    body = client.post("/fasting/start", json={"started_at": started, "target_hours": 14}).json()
    assert body["target_hours"] == 14
    assert 179 <= body["elapsed_minutes"] <= 181
    assert client.post("/fasting/start", json={}).status_code == 409
    state = client.get("/fasting").json()
    assert state["allowed"] is True and state["active"]["id"] == body["id"]
    stopped = client.post("/fasting/stop", json={}).json()
    assert stopped["ended_at"] is not None
    state = client.get("/fasting").json()
    assert state["active"] is None and len(state["recent"]) == 1
    assert client.delete(f"/fasting/{body['id']}").status_code == 200


def test_a_fast_cannot_start_in_the_future_or_days_ago(client):
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert client.post("/fasting/start", json={"started_at": future}).status_code == 422
    old = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    assert client.post("/fasting/start", json={"started_at": old}).status_code == 422
    assert client.post("/fasting/start", json={"target_hours": 40}).status_code == 422


def test_minors_never_get_fasting(client):
    client.put("/profile", json={"kid_mode": True})
    assert client.get("/fasting").json() == {"allowed": False, "active": None, "recent": [], "presets": [12, 14, 16]}
    assert client.post("/fasting/start", json={}).status_code == 403
    teen = {"X-User-Id": "teen"}
    client.put("/profile", json={"birth_year": date.today().year - 16}, headers=teen)
    assert client.post("/fasting/start", json={}, headers=teen).status_code == 403


def test_stopping_with_nothing_running_is_a_404(client):
    assert client.post("/fasting/stop", json={}).status_code == 404
