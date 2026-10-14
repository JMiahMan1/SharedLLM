"""Reading plans: exact schedules, progress, catching up, and plan badges."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from services.bible import plans
from services.bible.books import BOOKS


@pytest.mark.parametrize("plan", plans.catalogue(), ids=lambda p: p["id"])
def test_every_plan_reads_each_chapter_of_its_books_exactly_once(plan):
    seen: list[tuple[str, int]] = []
    for readings in plans.schedule(plan["id"]):
        assert readings, f"day with nothing to read in {plan['id']}"
        for r in readings:
            seen.extend((r["osis"], c) for c in range(r["chapter_start"], r["chapter_end"] + 1))
    full = plans.find(plan["id"])
    expected = sum(len(plans._chapters(t)) for t in full["tracks"])
    assert len(seen) == len(set(seen)) == expected


def test_the_year_plan_covers_the_whole_bible_evenly():
    loads = [sum(r["chapter_end"] - r["chapter_start"] + 1 for r in day) for day in plans.schedule("bible-year")]
    assert sum(loads) == sum(b["chapters"] for b in BOOKS) == 1189
    assert max(loads) - min(loads) <= 1


def test_progress_counts_what_is_behind_but_not_today():
    start = date(2026, 10, 1)
    today = date(2026, 10, 5)  # calendar day 5
    state = plans.progress("john-21", start.isoformat(), [1, 2], today)
    assert state["calendar_day"] == 5 and state["behind"] == 2
    assert state["current_day"] == 5 and state["next_day"] == 3


def test_catching_up_puts_the_next_unread_day_on_today():
    today = date(2026, 10, 13)
    start = plans.catch_up_start(4, today)
    assert plans.progress("john-21", start, [1, 2, 3], today)["calendar_day"] == 4
    assert start == (today - timedelta(days=3)).isoformat()


def test_starting_and_reading_a_plan(loaded_client: TestClient, monkeypatch):
    from services.bible import main as bible_main

    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    started = loaded_client.post("/plans/romans-16/start", params={"username": "sam"}).json()
    assert started["today"]["day"] == 1 and started["today"]["readings"][0]["ref"] == "Romans 1"
    done = loaded_client.post("/plans/romans-16/days/1/complete", params={"username": "sam"}).json()
    again = loaded_client.post("/plans/romans-16/days/1/complete", params={"username": "sam"}).json()
    assert done["completed"] == again["completed"] == 1
    mine = loaded_client.get("/plans", params={"username": "sam"}).json()["mine"]
    assert [m["plan_id"] for m in mine] == ["romans-16"]
    detail = loaded_client.get("/plans/romans-16", params={"username": "sam"}).json()
    assert detail["days"][0]["done"] is True and detail["days"][1]["done"] is False


def test_finishing_a_plan_earns_its_badge(loaded_client: TestClient, monkeypatch):
    from services.bible import main as bible_main

    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    loaded_client.post("/plans/romans-16/start", params={"username": "sam"})
    earned = set()
    for day in range(1, 17):
        body = loaded_client.post(f"/plans/romans-16/days/{day}/complete", params={"username": "sam"}).json()
        earned |= {b["id"] for b in body["newly_earned"]}
    assert body["finished"] is True and body["finished_on"]
    assert {"plan_days_7", "plan_completed_1"} <= earned


def test_marking_a_day_needs_the_plan_started_and_a_real_day(loaded_client: TestClient):
    assert loaded_client.post("/plans/romans-16/days/1/complete", params={"username": "sam"}).status_code == 404
    loaded_client.post("/plans/romans-16/start", params={"username": "sam"})
    assert loaded_client.post("/plans/romans-16/days/17/complete", params={"username": "sam"}).status_code == 400
    assert loaded_client.get("/plans/nope", params={"username": "sam"}).status_code == 404


def test_undo_and_stop(loaded_client: TestClient, monkeypatch):
    from services.bible import main as bible_main

    async def fake_award(username, grants):
        return {"granted": 0, "status": "ok"}

    monkeypatch.setattr(bible_main, "_award_stars", fake_award)
    loaded_client.post("/plans/john-21/start", params={"username": "sam"})
    loaded_client.post("/plans/john-21/days/1/complete", params={"username": "sam"})
    undone = loaded_client.delete("/plans/john-21/days/1", params={"username": "sam"}).json()
    assert undone["completed"] == 0
    assert loaded_client.delete("/plans/john-21", params={"username": "sam"}).status_code == 200
    assert loaded_client.get("/plans", params={"username": "sam"}).json()["mine"] == []


def test_another_readers_plan_is_their_own(loaded_client: TestClient):
    loaded_client.post("/plans/john-21/start", params={"username": "sam"})
    assert loaded_client.get("/plans", params={"username": "mia"}).json()["mine"] == []
    assert loaded_client.post("/plans/john-21/catch-up", params={"username": "mia"}).status_code == 404


def test_reading_along_shows_only_members_who_share(loaded_client: TestClient, monkeypatch):
    from services.bible import main as bible_main

    async def consent(owner, viewer, scope):
        return owner == "mia"

    monkeypatch.setattr(bible_main, "_consent", consent)
    for user in ("sam", "mia", "ben"):
        loaded_client.post("/plans/john-21/start", params={"username": user})
    members = loaded_client.get("/plans/john-21/family", params={"viewer": "sam"}).json()["members"]
    assert [m["username"] for m in members] == ["mia"]
