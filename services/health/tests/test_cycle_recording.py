"""Cycle recording is decoupled from the opt-in flag.

A period or symptom log is written even when ``CycleSettings.enabled`` is
false; the flag only governs whether the owner is shown the panel and whether
the scope can be shared. These tests pin that boundary so a future change does
not quietly turn recording back into an opt-in. The arithmetic itself lives in
``test_cycle.py``.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

TODAY = date.today().isoformat()


def _female_profile(client, *, birth_year: int | None = 1990, kid_mode: bool = False) -> None:
    payload: dict = {"sex": "female", "kid_mode": kid_mode}
    if birth_year is not None:
        payload["birth_year"] = birth_year
    response = client.put("/profile", json=payload)
    assert response.status_code == 200


class TestRecordingWithoutOptIn:
    def test_a_period_is_stored_while_tracking_is_off(self, client):
        _female_profile(client)
        assert client.get("/cycle").json()["enabled"] is False

        start = (date.today() - timedelta(days=4)).isoformat()
        saved = client.post("/cycle/periods", json={"start_day": start, "flow": "heavy"})
        assert saved.status_code == 200

        # Stored, not merely accepted: the owner's own list endpoint sees it.
        periods = client.get("/cycle/periods").json()["periods"]
        assert [row["start_day"] for row in periods] == [start]
        assert periods[0]["flow"] == "heavy"

    def test_a_symptom_log_is_stored_while_tracking_is_off(self, client):
        _female_profile(client)
        assert client.get("/cycle").json()["enabled"] is False

        saved = client.put(f"/cycle/logs/{TODAY}", json={"symptoms": ["cramps"], "mood": 3})
        assert saved.status_code == 200

        log = client.get(f"/cycle/logs/{TODAY}").json()["log"]
        assert log["symptoms"] == ["cramps"]
        assert log["mood"] == 3

    def test_a_disabled_child_profile_still_records(self, client):
        # A young profile that never opted in: the row is kept anyway.
        _female_profile(client, birth_year=date.today().year - 12, kid_mode=True)
        assert client.get("/cycle").json()["enabled"] is False

        assert client.post("/cycle/periods", json={"start_day": TODAY}).status_code == 200
        assert client.put(f"/cycle/logs/{TODAY}", json={"symptoms": ["cramps"]}).status_code == 200

        export = client.get("/export").json()["cycle"]
        # No settings row was ever created: the opt-in was never touched, yet
        # both rows are on disk.
        assert export["settings"] is None
        assert len(export["periods"]) == 1
        assert len(export["logs"]) == 1

    def test_data_recorded_while_off_appears_once_tracking_is_turned_on(self, client):
        _female_profile(client)
        start = (date.today() - timedelta(days=4)).isoformat()
        client.post("/cycle/periods", json={"start_day": start})

        # Off: the panel is hidden and the feature is not shown to the owner.
        hidden = client.get("/cycle").json()
        assert hidden["enabled"] is False
        assert hidden["periods"] == []
        assert hidden["logs"] == []
        assert hidden["summary"] is None

        # Switching it on reveals the already-recorded history.
        assert client.put("/cycle/settings", json={"enabled": True}).status_code == 200
        shown = client.get("/cycle").json()
        assert shown["enabled"] is True
        assert [row["start_day"] for row in shown["periods"]] == [start]
        assert shown["summary"]["has_data"] is True


class TestExposureGatesUnchanged:
    def test_cycle_sharing_still_requires_tracking_to_be_on(self, client):
        _female_profile(client)
        client.post("/cycle/periods", json={"start_day": TODAY})
        # Recorded while off, but the share is still refused until the owner
        # opts in: sharing is an exposure decision, not a recording one.
        assert client.put("/shares", json={"viewer": "michele", "scope": "cycle"}).status_code == 422

        assert client.put("/cycle/settings", json={"enabled": True}).status_code == 200
        assert client.put("/shares", json={"viewer": "michele", "scope": "cycle"}).status_code == 200

    def test_turning_tracking_off_still_drops_cycle_grants(self, client):
        _female_profile(client)
        client.post("/cycle/periods", json={"start_day": TODAY})
        client.put("/cycle/settings", json={"enabled": True})
        assert client.put("/shares", json={"viewer": "michele", "scope": "cycle"}).status_code == 200

        assert client.put("/cycle/settings", json={"enabled": False}).status_code == 200
        assert client.get("/family", headers={"X-User-Id": "michele"}).json()["people"] == []
        # The period itself survives the toggle.
        assert len(client.get("/cycle/periods").json()["periods"]) == 1

    def test_a_male_profile_is_still_refused(self, client):
        client.put("/profile", json={"sex": "male", "birth_year": 1990})
        assert client.post("/cycle/periods", json={"start_day": TODAY}).status_code == 422
        assert client.put(f"/cycle/logs/{TODAY}", json={"symptoms": []}).status_code == 422

    def test_the_family_projection_still_hides_private_fields(self, client):
        _female_profile(client)
        client.put("/cycle/settings", json={"enabled": True})
        started = (date.today() - timedelta(days=3)).isoformat()
        client.post("/cycle/periods", json={"start_day": started, "flow": "heavy", "note": "zq-owner-note"})
        client.put(f"/cycle/logs/{TODAY}", json={"symptoms": ["cramps"], "note": "zq-owner-note"})
        assert client.put("/shares", json={"viewer": "dad", "scope": "cycle"}).status_code == 200

        entry = client.get("/family", headers={"X-User-Id": "dad"}).json()["people"][0]["cycle"]
        assert started not in json.dumps(entry)
        assert "zq-owner-note" not in json.dumps(entry)
        assert "cramps" not in json.dumps(entry)
