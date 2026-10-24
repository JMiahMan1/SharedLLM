"""Per-metric on/off switches on the health profile."""
from __future__ import annotations

from datetime import date

from services.health import preferences


def test_every_switch_has_a_default_and_fasting_starts_off():
    resolved = preferences.resolve({}, minor=False)
    assert set(resolved) == set(preferences.SWITCHES)
    assert resolved["fasting"] is False
    assert all(value for key, value in resolved.items() if key != "fasting")


def test_adults_only_switches_stay_off_for_a_minor_whatever_is_stored():
    assert preferences.resolve({"fasting": True}, minor=True)["fasting"] is False
    assert "fasting" not in {row["key"] for row in preferences.catalog(minor=True)}


def test_a_new_profile_reports_the_defaults(client):
    body = client.get("/profile").json()
    assert body["metrics"]["sleep"] is True
    assert body["metrics"]["fasting"] is False
    assert body["metric_catalog"][0] == {"key": "readiness", "label": "Readiness score", "default": True}


def test_switches_save_partially_and_keep_earlier_choices(client):
    assert client.put("/profile", json={"metrics": {"water": False}}).status_code == 200
    body = client.put("/profile", json={"metrics": {"fasting": True}}).json()
    assert body["metrics"]["water"] is False
    assert body["metrics"]["fasting"] is True
    assert client.get("/profile").json()["metrics"]["water"] is False
    # A profile save without switches leaves them alone.
    assert client.put("/profile", json={"display_name": "J"}).json()["metrics"]["water"] is False


def test_unknown_or_non_boolean_switches_are_refused(client):
    assert client.put("/profile", json={"metrics": {"weight_loss": True}}).status_code == 422
    assert client.put("/profile", json={"metrics": {"water": "yes"}}).status_code == 422


def test_a_minor_cannot_turn_fasting_on(client):
    response = client.put("/profile", json={"kid_mode": True, "metrics": {"fasting": True}})
    assert response.status_code == 422
    assert "adults only" in response.json()["detail"]
    teen = client.put(
        "/profile",
        json={"birth_year": date.today().year - 15, "metrics": {"fasting": True}},
        headers={"X-User-Id": "teen"},
    )
    assert teen.status_code == 422


def test_turning_kid_mode_on_switches_fasting_off(client):
    client.put("/profile", json={"metrics": {"fasting": True}})
    body = client.put("/profile", json={"kid_mode": True}).json()
    assert body["metrics"]["fasting"] is False
