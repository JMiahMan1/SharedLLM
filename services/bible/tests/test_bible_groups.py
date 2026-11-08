"""Shared reading groups: invites, the board, nudges, and the reminder line."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from services.bible import main as bible_main
from services.bible import reminders

FAMILY = ["jeremiah", "sam", "gracie", "michele"]


@pytest.fixture
def group_family(monkeypatch):
    """Identity cannot be read in tests; answer the family list locally."""

    async def fake_family():
        return list(FAMILY)

    monkeypatch.setattr(bible_main, "_family_or_none", fake_family)


@pytest.fixture
def sent(monkeypatch):
    """Notices never reach telemetry in tests; they are recorded here instead."""

    class FakeSend:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        async def __call__(self, payload, **_kwargs):
            self.payloads.append(payload)
            return True

    fake = FakeSend()
    monkeypatch.setattr(bible_main, "_notify_send", fake)
    return fake


@pytest.fixture
def no_stars(monkeypatch):
    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)


def _create(client, username, plan_id="nt-90", **body):
    resp = client.post(f"/plans/{plan_id}/groups", params={"username": username}, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_creating_a_group_notifies_the_invitees(loaded_client: TestClient, group_family, sent):
    body = _create(
        loaded_client, "jeremiah", name="Read With Me", members=["sam", "gracie"], started_on=date.today().isoformat()
    )
    board = body["group"]
    assert board["name"] == "Read With Me" and board["started_on"] == date.today().isoformat()
    assert body["invited"] == ["sam", "gracie"] and body["notified"] == 2
    assert board["counts"]["joined"] == 1 and board["counts"]["invited"] == 2
    assert [m["username"] for m in board["members"]] == ["jeremiah", "sam", "gracie"]
    invites = {p["user"]: p for p in sent.payloads}
    assert set(invites) == {"sam", "gracie"}
    for payload in sent.payloads:
        assert payload["category"] == "bible" and payload["kind"] == "bible_group_invite"
        assert payload["data"]["url"] == "/bible?tab=plans"
        assert payload["once"].startswith("bible-group-invite:")
    assert invites["sam"]["title"] == "jeremiah invited you to read together"


def test_unknown_invitees_are_refused_and_nothing_is_saved(loaded_client: TestClient, group_family, sent):
    resp = loaded_client.post(
        "/plans/nt-90/groups",
        params={"username": "jeremiah"},
        json={"members": ["sam", "nobody-such"]},
    )
    assert resp.status_code == 422
    assert "nobody-such" in resp.json()["detail"]
    assert loaded_client.get("/plans/groups", params={"username": "jeremiah"}).json()["groups"] == []
    assert sent.payloads == []


def test_an_invalid_start_date_is_422(loaded_client: TestClient, group_family, sent):
    resp = loaded_client.post(
        "/plans/nt-90/groups",
        params={"username": "jeremiah"},
        json={"members": [], "started_on": "yesterday-ish"},
    )
    assert resp.status_code == 422


def test_accepting_an_invite_brings_the_common_start_date(
    loaded_client: TestClient, group_family, sent, no_stars
):
    body = _create(loaded_client, "jeremiah", members=["sam"], started_on="2026-11-01")
    gid = body["group"]["id"]
    board = loaded_client.post(f"/plans/groups/{gid}/join", params={"username": "sam"}).json()
    assert board["my_status"] == "joined"
    assert board["counts"]["joined"] == 2 and board["counts"]["invited"] == 0
    mine = loaded_client.get("/plans", params={"username": "sam"}).json()["mine"]
    assert [m["started_on"] for m in mine] == ["2026-11-01"]
    # Sam's reading is paced from the group's shared start, not from the day they clicked join.
    assert loaded_client.get(f"/plans/groups/{gid}", params={"username": "sam"}).json()["started_on"] == "2026-11-01"


def test_joining_when_already_following_links_the_existing_plan(
    loaded_client: TestClient, group_family, sent, no_stars
):
    loaded_client.post("/plans/nt-90/start", params={"username": "sam"})
    body = _create(loaded_client, "jeremiah", members=["sam"])
    gid = body["group"]["id"]
    loaded_client.post(f"/plans/groups/{gid}/join", params={"username": "sam"})
    mine = loaded_client.get("/plans", params={"username": "sam"}).json()["mine"]
    assert len(mine) == 1, "joining must not start a second enrollment"
    board = loaded_client.get(f"/plans/groups/{gid}", params={"username": "jeremiah"}).json()
    sam = next(m for m in board["members"] if m["username"] == "sam")
    assert sam["status"] == "joined" and sam["started_on"] == date.today().isoformat()


def test_only_joined_members_see_the_board(loaded_client: TestClient, group_family, sent):
    body = _create(loaded_client, "jeremiah", members=["sam"])
    gid = body["group"]["id"]
    assert loaded_client.get("/plans/groups/999", params={"username": "jeremiah"}).status_code == 404
    assert loaded_client.get(f"/plans/groups/{gid}", params={"username": "outsider"}).status_code == 404
    assert loaded_client.get("/plans/groups", params={"username": "outsider"}).json()["groups"] == []


def test_declining_hides_the_board_for_the_person_who_declined(loaded_client: TestClient, group_family, sent):
    body = _create(loaded_client, "jeremiah", members=["sam"])
    gid = body["group"]["id"]
    assert loaded_client.post(f"/plans/groups/{gid}/decline", params={"username": "sam"}).status_code == 200
    assert loaded_client.get(f"/plans/groups/{gid}", params={"username": "sam"}).status_code == 404
    board = loaded_client.get(f"/plans/groups/{gid}", params={"username": "jeremiah"}).json()
    assert board["counts"]["declined"] == 1


def test_nudging_skips_those_who_read_and_leaves_out_invited_members(
    loaded_client: TestClient, group_family, sent, no_stars
):
    body = _create(loaded_client, "jeremiah", members=["sam", "gracie"], name="Read With Me")
    gid = body["group"]["id"]
    loaded_client.post(f"/plans/groups/{gid}/join", params={"username": "sam"})
    loaded_client.post("/plans/nt-90/days/1/complete", params={"username": "sam"})
    result = loaded_client.post(f"/plans/groups/{gid}/nudge", params={"username": "jeremiah"}, json={}).json()
    assert result["nudged"] == []
    reasons = {s["username"]: s["reason"] for s in result["skipped"]}
    assert reasons["sam"] == "already read today" and reasons["jeremiah"] == "that is you"
    assert not any(s["username"] == "gracie" for s in result["skipped"]), "invitees are not nudged, only joined members"
    # Sam joins fully later, reads nothing more today: a named nudge reaches them tomorrow, not with "already read".
    result2 = loaded_client.post(f"/plans/groups/{gid}/nudge", params={"username": "jeremiah"}, json={"username": "sam"}).json()
    assert result2["nudged"] == [] and result2["skipped"] == [{"username": "sam", "reason": "already read today"}]


def test_nudging_reaches_a_joined_member_who_has_not_read(
    loaded_client: TestClient, group_family, sent, no_stars
):
    body = _create(loaded_client, "jeremiah", members=["sam"], name="Read With Me")
    gid = body["group"]["id"]
    loaded_client.post(f"/plans/groups/{gid}/join", params={"username": "sam"})
    sent.payloads.clear()
    result = loaded_client.post(f"/plans/groups/{gid}/nudge", params={"username": "jeremiah"}, json={}).json()
    assert result["nudged"] == ["sam"]
    [payload] = sent.payloads
    assert payload["user"] == "sam" and payload["kind"] == "bible_encourage"
    assert payload["category"] == "bible" and "Read With Me" in payload["title"]
    assert payload["body"].startswith("Day 1: ")
    assert payload["once"] == f"bible-encourage:{gid}:{date.today().isoformat()}:sam"


def test_nudging_a_named_member_who_has_not_joined_is_a_404(loaded_client: TestClient, group_family, sent):
    body = _create(loaded_client, "jeremiah", members=["sam"])
    gid = body["group"]["id"]
    assert loaded_client.post(f"/plans/groups/{gid}/nudge", params={"username": "jeremiah"}, json={"username": "sam"}).status_code == 404


def test_a_finished_member_is_not_nudged(loaded_client: TestClient, group_family, sent, no_stars, monkeypatch):
    async def fake_announce(username, fresh):
        return {"posted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_announce", fake_announce)
    body = _create(loaded_client, "jeremiah", plan_id="romans-16", members=["sam"])
    gid = body["group"]["id"]
    loaded_client.post(f"/plans/groups/{gid}/join", params={"username": "sam"})
    for day in range(1, 17):
        loaded_client.post(f"/plans/romans-16/days/{day}/complete", params={"username": "sam"})
    result = loaded_client.post(f"/plans/groups/{gid}/nudge", params={"username": "jeremiah"}, json={}).json()
    assert result["nudged"] == []
    reasons = {s["username"]: s["reason"] for s in result["skipped"]}
    assert reasons["sam"] == "finished" and reasons["jeremiah"] == "that is you"


def test_leaving_keeps_the_reading_but_drops_the_board(loaded_client: TestClient, group_family, sent, no_stars):
    body = _create(loaded_client, "jeremiah", members=["sam"])
    gid = body["group"]["id"]
    loaded_client.post(f"/plans/groups/{gid}/join", params={"username": "sam"})
    loaded_client.post(f"/plans/groups/{gid}/leave", params={"username": "sam"})
    assert loaded_client.get(f"/plans/groups/{gid}", params={"username": "sam"}).status_code == 404
    assert loaded_client.get("/plans/groups", params={"username": "sam"}).json()["groups"] == []
    mine = loaded_client.get("/plans", params={"username": "sam"}).json()["mine"]
    assert mine and mine[0]["plan_id"] == "nt-90", "the reading continues without the group"


def test_only_the_creator_can_end_the_group(loaded_client: TestClient, group_family, sent):
    body = _create(loaded_client, "jeremiah", members=["sam"])
    gid = body["group"]["id"]
    loaded_client.post(f"/plans/groups/{gid}/join", params={"username": "sam"})
    assert loaded_client.delete(f"/plans/groups/{gid}", params={"username": "sam"}).status_code == 403
    assert loaded_client.delete(f"/plans/groups/{gid}", params={"username": "jeremiah"}).status_code == 200
    assert loaded_client.get(f"/plans/groups/{gid}", params={"username": "jeremiah"}).status_code == 404


def test_the_reminder_names_who_has_read_in_the_group(loaded_client: TestClient, group_family, sent, no_stars):
    body = _create(loaded_client, "jeremiah", members=["sam"], name="Read With Me")
    loaded_client.post(f"/plans/groups/{body['group']['id']}/join", params={"username": "sam"})
    loaded_client.post("/plans/nt-90/days/1/complete", params={"username": "jeremiah"})
    with Session(bible_main.app.state.engine) as session:
        note = reminders.message_for(session, "sam", date.today().isoformat())
    assert note["body"].startswith("Day 1: ")
    assert note["body"].endswith("· 1 of 2 in Read With Me have read today")
