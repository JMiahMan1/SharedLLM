"""Screen-time rules: a child reads theirs, their parents set them and answer requests."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User

DAY = "2026-10-24"


@pytest.fixture
def family():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        people = {
            "dad": User(username="dad", family_role="father"),
            "mom": User(username="mom", family_role="mother"),
            "kid": User(username="kid", family_role="child", mother_username="mom"),
            "sib": User(username="sib", family_role="teen", mother_username="mom"),
            "grown": User(username="grown", family_role="adult_child"),
        }
        for person in people.values():
            session.add(person)
        session.commit()
    acting = {"as": "dad"}

    def caller():
        with Session(engine) as s:
            return s.get(User, acting["as"]) or next(u for u in s.query(User).all() if u.username == acting["as"])

    app.dependency_overrides[require_api_key] = caller
    app.dependency_overrides[require_internal] = lambda: True

    def act(name):
        acting["as"] = name
        return TestClient(app)

    yield act
    app.dependency_overrides = {}


def test_a_parent_sets_rules_and_the_child_can_read_them(family):
    r = family("dad").put("/api/screen-time/kid/rules", json={"daily_limit_min": 90, "bedtime_start": "21:00", "bedtime_end": "07:00"})
    assert r.status_code == 200
    mine = family("kid").get("/api/screen-time/kid/rules").json()
    assert mine["rules"]["daily_limit_min"] == 90 and mine["can_manage"] is False


def test_children_and_siblings_cannot_change_or_peek(family):
    assert family("kid").put("/api/screen-time/kid/rules", json={"daily_limit_min": 999}).status_code == 403
    assert family("sib").get("/api/screen-time/kid/rules").status_code == 403
    assert family("grown").put("/api/screen-time/kid/rules", json={"daily_limit_min": 60}).status_code == 403
    assert family("dad").put("/api/screen-time/grown/rules", json={"daily_limit_min": 60}).status_code == 403  # adults have no budgets


def test_bad_values_are_refused_not_kept(family):
    client = family("mom")
    assert client.put("/api/screen-time/kid/rules", json={"daily_limit_min": 5000}).status_code == 422
    assert client.put("/api/screen-time/kid/rules", json={"bedtime_start": "9pm"}).status_code == 422
    assert client.put("/api/screen-time/kid/rules", json={"school_days": ["funday"]}).status_code == 422


def test_ask_then_a_parent_grants_and_it_counts_as_bonus(family):
    asked = family("kid").post("/api/screen-time/requests", json={"minutes": 30, "day": DAY, "note": "finishing a level"})
    assert asked.status_code == 200
    assert set(asked.json()["notify"]) == {"dad", "mom"}
    assert family("kid").post("/api/screen-time/requests", json={"minutes": 30, "day": DAY}).status_code == 409

    pending = family("mom").get("/api/screen-time/requests").json()["requests"]
    assert [p["username"] for p in pending] == ["kid"]
    assert family("sib").get("/api/screen-time/requests").json()["requests"] == []

    rid = pending[0]["id"]
    assert family("sib").post(f"/api/screen-time/requests/{rid}", json={"grant": True}).status_code == 403
    granted = family("dad").post(f"/api/screen-time/requests/{rid}", json={"grant": True, "minutes": 20})
    assert granted.json()["request"]["granted_minutes"] == 20
    assert family("dad").post(f"/api/screen-time/requests/{rid}", json={"grant": True}).status_code == 409
    assert family("kid").get(f"/api/screen-time/kid/rules?day={DAY}").json()["bonus_min"] == 20


def test_bonus_and_pause(family):
    dad = family("dad")
    assert dad.post("/api/screen-time/kid/bonus", json={"minutes": 15, "day": DAY}).status_code == 200
    dad.put("/api/screen-time/kid/rules", json={"daily_limit_min": 60})
    dad.post("/api/screen-time/kid/pause", json={})
    rules = dad.get(f"/api/screen-time/kid/rules?day={DAY}").json()
    assert rules["rules"]["paused"] is True and rules["bonus_min"] == 15
    dad.put("/api/screen-time/kid/rules", json={"daily_limit_min": 45})
    assert dad.get("/api/screen-time/kid/rules").json()["rules"]["paused"] is True  # an edit keeps the pause
    dad.post("/api/screen-time/kid/pause", json={"resume": True})
    assert "paused" not in dad.get("/api/screen-time/kid/rules").json()["rules"]


def test_internal_status_read(family):
    family("dad").put("/api/screen-time/kid/rules", json={"daily_limit_min": 60})
    body = family("dad").get(f"/api/internal/screen-time/kid?day={DAY}").json()
    assert body["rules"]["daily_limit_min"] == 60 and body["managed"] is True


def _with_passwords(family):
    from services.identity.seed import hash_password

    with Session(main.engine) as s:
        for name in ("dad", "mom", "sib", "grown"):
            u = s.get(User, name) or next(x for x in s.query(User).all() if x.username == name)
            u.password_hash = hash_password(f"{name}-pass")
            s.add(u)
        s.commit()
    main._UNLOCK_FAILURES.clear()


def test_a_parent_login_unlocks_their_childs_phone(family):
    _with_passwords(family)
    r = family("kid").post("/api/guard/parent-unlock", json={"username": "Mom", "password": "mom-pass"})
    assert r.status_code == 200
    body = r.json()
    assert body["parent"] == "mom" and body["until"] and set(body["notify"]) == {"dad", "mom"}
    assert family("kid").get("/api/screen-time/kid/rules").json()["protected"] is True
    assert family("grown").get("/api/screen-time/grown/rules").json()["protected"] is False


def test_only_this_childs_parents_with_the_right_password_unlock(family):
    _with_passwords(family)
    kid = family("kid")
    assert kid.post("/api/guard/parent-unlock", json={"username": "mom", "password": "wrong"}).status_code == 401
    assert kid.post("/api/guard/parent-unlock", json={"username": "sib", "password": "sib-pass"}).status_code == 401
    assert kid.post("/api/guard/parent-unlock", json={"username": "grown", "password": "grown-pass"}).status_code == 401


def test_guessing_locks_the_unlock_screen(family):
    _with_passwords(family)
    kid = family("kid")
    for _ in range(5):
        assert kid.post("/api/guard/parent-unlock", json={"username": "mom", "password": "guess"}).status_code == 401
    # Even the right password waits now.
    assert kid.post("/api/guard/parent-unlock", json={"username": "mom", "password": "mom-pass"}).status_code == 429
    main._UNLOCK_FAILURES.clear()


def test_a_parent_blocks_a_lane_and_the_child_sees_it_off(family):
    assert family("kid").get("/api/recommendation-lanes/kid").json()["lanes"]["game"] is True
    r = family("mom").put("/api/recommendation-lanes/kid", json={"lanes": {"game": False}})
    assert r.status_code == 200 and r.json()["lanes"]["game"] is False and r.json()["lanes"]["music"] is True
    assert family("kid").get("/api/recommendation-lanes/kid").json()["lanes"]["game"] is False
    assert family("kid").get("/api/internal/recommendation-lanes/kid").json() == {"blocked": ["game"]}
    family("dad").put("/api/recommendation-lanes/kid", json={"lanes": {"game": True}})
    assert family("kid").get("/api/internal/recommendation-lanes/kid").json() == {"blocked": []}


def test_only_parents_change_lanes_and_values_are_checked(family):
    assert family("kid").put("/api/recommendation-lanes/kid", json={"lanes": {"game": True}}).status_code == 403
    assert family("sib").get("/api/recommendation-lanes/kid").status_code == 403
    assert family("dad").put("/api/recommendation-lanes/kid", json={"lanes": {"tiktok": False}}).status_code == 422
    assert family("dad").put("/api/recommendation-lanes/grown", json={"lanes": {"game": False}}).status_code == 403
