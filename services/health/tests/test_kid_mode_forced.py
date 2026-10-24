"""Every minor is in kid mode (decided 2026-10-24).

The gateway sends ``X-User-Is-Minor`` from the family role on every call. A
``true`` switches the stored profile into kid mode and the profile reports it
as locked; a birth year under 18 locks it too. ``unknown`` (Identity down)
never changes what was saved.
"""
from __future__ import annotations

from datetime import date

KID = {"X-User-Id": "emma", "X-User-Is-Minor": "true"}


def test_a_minor_profile_is_switched_into_kid_mode_and_locked(client):
    client.put("/profile", json={"kid_mode": False}, headers={"X-User-Id": "emma", "X-User-Is-Minor": "unknown"})
    body = client.get("/profile", headers=KID).json()
    assert body["kid_mode"] is True
    assert body["kid_mode_locked"] is True


def test_a_minor_cannot_save_kid_mode_off(client):
    body = client.put("/profile", json={"kid_mode": False, "display_name": "Emma"}, headers=KID).json()
    assert body["kid_mode"] is True
    assert body["display_name"] == "Emma"


def test_a_minor_with_no_profile_yet_is_reported_in_kid_mode(client):
    body = client.get("/profile", headers={"X-User-Id": "newkid", "X-User-Is-Minor": "true"}).json()
    assert body["exists"] is False
    assert body["kid_mode"] is True and body["kid_mode_locked"] is True


def test_a_birth_year_under_18_locks_kid_mode_too(client):
    teen_year = date.today().year - 15
    body = client.put("/profile", json={"birth_year": teen_year, "kid_mode": False}, headers={"X-User-Id": "sam"}).json()
    assert body["kid_mode"] is True and body["kid_mode_locked"] is True


def test_an_adult_keeps_their_own_choice(client):
    adult = {"X-User-Id": "dad", "X-User-Is-Minor": "false"}
    assert client.put("/profile", json={"kid_mode": False}, headers=adult).json()["kid_mode"] is False
    body = client.get("/profile", headers=adult).json()
    assert body["kid_mode"] is False and body["kid_mode_locked"] is False


def test_an_identity_outage_does_not_change_the_saved_choice(client):
    adult_unknown = {"X-User-Id": "dad", "X-User-Is-Minor": "unknown"}
    client.put("/profile", json={"kid_mode": False}, headers=adult_unknown)
    assert client.get("/profile", headers=adult_unknown).json()["kid_mode"] is False
