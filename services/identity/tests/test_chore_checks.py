"""Earned time: the rules a parent sets, and a parent's OK on ticked-off chores."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import ChoreCheck, ScreenTimeRequest, User

DAY = "2026-10-21"
MONDAY = "2026-10-19"


@pytest.fixture
def family():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        for user in (
            User(username="dad", family_role="father"),
            User(username="mom", family_role="mother"),
            User(username="kid", family_role="child", mother_username="mom"),
            User(username="sib", family_role="teen", mother_username="mom"),
        ):
            session.add(user)
        session.commit()
    acting = {"as": "dad"}

    def caller():
        with Session(engine) as s:
            return next(u for u in s.query(User).all() if u.username == acting["as"])

    app.dependency_overrides[require_api_key] = caller
    app.dependency_overrides[require_internal] = lambda: True

    def act(name):
        acting["as"] = name
        return TestClient(app)

    act.engine = engine
    yield act
    app.dependency_overrides = {}


def sync(client, done, day=DAY):
    r = client.post("/api/internal/chore-checks/kid/sync", json={"day": day, "week_start": MONDAY, "done": done})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_parent_sets_earning_rules_and_chores_first(family):
    body = {"daily_limit_min": 60, "chores_first": True,
            "earn": {"steps_every": 1000, "steps_min": 5, "chore_min": 10, "max_min": 60}}
    r = family("dad").put("/api/screen-time/kid/rules", json=body)
    assert r.status_code == 200
    assert r.json()["rules"]["earn"] == body["earn"] and r.json()["rules"]["chores_first"] is True
    # Turning chores first off drops it rather than keeping false around.
    assert "chores_first" not in family("dad").put("/api/screen-time/kid/rules", json={"chores_first": False}).json()["rules"]


@pytest.mark.parametrize("earn", [
    {"steps_every": 1000},                     # how many minutes?
    {"exercise_min": 10},                      # for how much exercise?
    {"chore_min": 0},
    {"chore_min": True},
    {"max_min": 5000},
    {"steps_every": 10, "steps_min": 5},       # under 100 steps a block
    {"bogus": 1},
    "lots",
])
def test_earning_rules_out_of_range_are_refused(family, earn):
    assert family("dad").put("/api/screen-time/kid/rules", json={"earn": earn}).status_code == 422


def test_a_child_cannot_set_their_own_earning(family):
    assert family("kid").put("/api/screen-time/kid/rules", json={"earn": {"chore_min": 240}}).status_code == 403


def test_a_ticked_off_chore_waits_for_a_parent_and_they_hear_once(family):
    client = family("dad")
    first = sync(client, [{"id": "50", "title": "Trash"}])
    assert [c["title"] for c in first["new"]] == ["Trash"] and set(first["parents"]) == {"dad", "mom"}
    again = sync(client, [{"id": "50", "title": "Trash"}])
    assert again["new"] == [] and again["parents"] == []
    assert [c["status"] for c in again["checks"]] == ["pending"]


def test_unticked_before_a_parent_looked_stops_waiting(family):
    client = family("dad")
    sync(client, [{"id": "50", "title": "Trash"}])
    assert sync(client, [])["checks"] == []


def test_an_unread_chart_changes_nothing(family):
    client = family("dad")
    sync(client, [{"id": "50", "title": "Trash"}])
    assert len(sync(client, None)["checks"]) == 1


def test_a_parent_approves_or_asks_for_a_redo(family):
    first = sync(family("dad"), [{"id": "50", "title": "Trash"}, {"id": "51", "title": "Dishes"}])
    trash, dishes = (c["id"] for c in first["new"])
    mom = family("mom")
    assert mom.get("/api/screen-time/chore-checks").json()["checks"][0]["username"] == "kid"
    ok = mom.post(f"/api/screen-time/chore-checks/{trash}", json={"approve": True}).json()
    assert ok["check"]["status"] == "approved" and ok["untick"] is False and ok["notify"] == ["kid"]
    redo = mom.post(f"/api/screen-time/chore-checks/{dishes}", json={"approve": False}).json()
    assert redo["check"]["status"] == "declined" and redo["untick"] is True
    assert mom.post(f"/api/screen-time/chore-checks/{trash}", json={"approve": False}).status_code == 409
    # Ticked off again after a redo: it waits for a parent once more; the approved one stays.
    later = sync(family("dad"), [{"id": "50", "title": "Trash"}, {"id": "51", "title": "Dishes"}])
    assert [c["title"] for c in later["new"]] == ["Dishes"]
    assert sorted(c["status"] for c in later["checks"]) == ["approved", "declined", "pending"]


def test_only_a_parent_answers(family):
    check = sync(family("dad"), [{"id": "50", "title": "Trash"}])["new"][0]["id"]
    assert family("kid").post(f"/api/screen-time/chore-checks/{check}", json={"approve": True}).status_code == 403
    assert family("sib").post(f"/api/screen-time/chore-checks/{check}", json={"approve": True}).status_code == 403
    assert family("sib").get("/api/screen-time/chore-checks").json()["checks"] == []
    assert family("dad").post(f"/api/screen-time/chore-checks/{check}", json={"approve": "yes"}).status_code == 422
    assert family("dad").post("/api/screen-time/chore-checks/999", json={"approve": True}).status_code == 404


def test_the_week_comes_back_with_each_days_given_time(family):
    with Session(family.engine) as s:
        s.add(ScreenTimeRequest(username="kid", day=MONDAY, minutes=15, status="granted", granted_minutes=15))
        s.add(ScreenTimeRequest(username="kid", day="2026-10-12", minutes=15, status="granted", granted_minutes=15))
        s.add(ChoreCheck(username="kid", day="2026-10-12", chore_id="1", status="approved"))  # last week
        s.commit()
    got = sync(family("dad"), [], day=DAY)
    assert got["bonus_by_day"] == {MONDAY: 15} and got["checks"] == []


def test_bad_days_are_refused(family):
    r = family("dad").post("/api/internal/chore-checks/kid/sync", json={"day": "today", "done": []})
    assert r.status_code == 422


def test_the_assistant_lists_and_answers_as_the_parent_who_spoke(family):
    client = family("dad")
    check = sync(client, [{"id": "50", "title": "Trash"}])["new"][0]["id"]
    seen = client.get("/api/internal/chore-checks", params={"viewer": "mom"}).json()["checks"]
    assert [(c["id"], c["display_name"]) for c in seen] == [(check, "kid")]
    assert client.get("/api/internal/chore-checks", params={"viewer": "sib"}).json()["checks"] == []
    assert client.get("/api/internal/chore-checks", params={"viewer": "nobody"}).status_code == 404

    refused = client.post(f"/api/internal/chore-checks/{check}/decide", json={"by": "kid", "approve": True})
    assert refused.status_code == 403  # a child cannot approve their own, even through the assistant
    ok = client.post(f"/api/internal/chore-checks/{check}/decide", json={"by": "mom", "approve": True}).json()
    assert ok["check"]["status"] == "approved" and ok["check"]["decided_by"] == "mom"
    assert set(ok["parents"]) == {"dad", "mom"} and ok["display_name"] == "kid"
    again = client.post(f"/api/internal/chore-checks/{check}/decide", json={"by": "dad", "approve": False})
    assert again.status_code == 409
