"""Cheers: fixed kind words, only between people who already see each other."""
from __future__ import annotations

from datetime import date

TODAY = date.today().isoformat()
MOM = {"X-User-Id": "mom"}
SAM = {"X-User-Id": "sam"}


def test_a_sharer_can_be_cheered_and_sees_it(client):
    # jeremiah shares sleep with mom, so mom may cheer jeremiah's day.
    assert client.put("/shares", json={"viewer": "mom", "scope": "sleep"}).status_code == 200
    assert client.get(f"/cheers?day={TODAY}", headers=MOM).json()["can_cheer"] == ["jeremiah"]
    sent = client.post("/cheers", json={"to_user": "jeremiah", "kind": "well_done", "day": TODAY}, headers=MOM).json()
    assert sent["emoji"] == "👏" and sent["duplicate"] is False
    again = client.post("/cheers", json={"to_user": "jeremiah", "kind": "well_done", "day": TODAY}, headers=MOM).json()
    assert again["duplicate"] is True
    received = client.get(f"/cheers?day={TODAY}").json()["received"]
    assert [(row["from_user"], row["label"]) for row in received] == [("mom", "Well done")]


def test_strangers_and_yourself_cannot_be_cheered(client):
    assert client.post("/cheers", json={"to_user": "sam", "kind": "proud", "day": TODAY}).status_code == 403
    assert client.post("/cheers", json={"to_user": "jeremiah", "kind": "proud", "day": TODAY}).status_code == 422
    assert client.post("/cheers", json={"to_user": "mom", "kind": "you_failed", "day": TODAY}, headers=SAM).status_code == 422


def test_challenge_co_members_can_cheer_each_other(client):
    challenge = client.post(
        "/challenges",
        json={"kind": "water_days", "day": TODAY, "target_days": 3},
        headers={"X-User-Is-Minor": "false"},
    ).json()
    assert client.post(f"/challenges/{challenge['id']}/join", headers=SAM).status_code == 200
    assert client.get(f"/cheers?day={TODAY}", headers=SAM).json()["can_cheer"] == ["jeremiah"]
    assert client.post("/cheers", json={"to_user": "sam", "kind": "keep_going", "day": TODAY}).status_code == 200
