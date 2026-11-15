"""App vetting's age rule and the parents' per-app choices: who may see and change them."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import Device, User


@pytest.fixture
def family():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        for person in (
            User(username="dad", family_role="father"),
            User(username="mom", family_role="mother"),
            User(username="otherma", family_role="mother"),
            User(username="kid", family_role="child", mother_username="mom"),
            User(username="sib", family_role="teen", mother_username="mom"),
            User(username="grown", family_role="adult_child"),
            User(username="guest", family_role="other"),
            User(username="root", family_role="other", is_admin=True),
        ):
            session.add(person)
        session.commit()
    acting = {"as": "dad"}

    def caller():
        with Session(engine) as s:
            return next(u for u in s.exec(select(User)).all() if u.username == acting["as"])

    app.dependency_overrides[require_api_key] = caller
    app.dependency_overrides[require_internal] = lambda: True

    def act(name):
        acting["as"] = name
        return TestClient(app)

    act.engine = engine
    yield act
    app.dependency_overrides = {}


@pytest.mark.parametrize("who", ["dad", "mom", "root"])
def test_the_childs_parents_and_admins_read_the_policy(family, who):
    r = family(who).get("/api/apps/kid/policy")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["birth_date"] is None and body["effective_max_rating"] is None
    assert body["ratings"][0] == "Everyone" and body["unused_days"] == 30


@pytest.mark.parametrize("who", ["kid", "sib", "grown", "guest", "otherma"])
def test_nobody_else_reads_or_changes_it(family, who):
    assert family(who).get("/api/apps/kid/policy").status_code == 403
    assert family(who).put("/api/apps/kid/policy", json={"birth_date": "2010-01-01"}).status_code == 403
    assert family(who).put("/api/apps/kid/policy/apps/com.roblox.client", json={"mode": "allow"}).status_code == 403
    assert family(who).post("/api/apps/kid/policy/action", json={"package": "com.x.y", "action": "block"}).status_code == 403


def test_an_unknown_member_is_404(family):
    assert family("dad").get("/api/apps/nobody/policy").status_code == 404


def test_a_grown_members_apps_are_not_vetted(family):
    assert family("dad").get("/api/apps/grown/policy").status_code == 403


def test_a_birth_date_sets_the_age_and_default_limit(family):
    r = family("mom").put("/api/apps/kid/policy", json={"birth_date": "2014-06-01"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["birth_date"] == "2014-06-01" and isinstance(body["age"], int)
    assert body["default_max_rating"] == body["effective_max_rating"]
    with Session(family.engine) as s:
        assert s.exec(select(User).where(User.username == "kid")).one().birth_date == "2014-06-01"


@pytest.mark.parametrize("bad", [{"birth_date": "2014/06/01"}, {"birth_date": "2999-01-01"}, {"max_rating": "PG"},
                                 {"unused_days": 2}, {"unused_days": 999}, {"unused_days": True}])
def test_bad_values_are_refused(family, bad):
    assert family("dad").put("/api/apps/kid/policy", json=bad).status_code == 422


def test_a_parent_can_raise_the_limit_and_clear_it_again(family):
    family("dad").put("/api/apps/kid/policy", json={"birth_date": "2016-01-01"})
    raised = family("dad").put("/api/apps/kid/policy", json={"max_rating": "Teen"}).json()
    assert raised["max_rating"] == "Teen" and raised["effective_max_rating"] == "Teen"
    cleared = family("dad").put("/api/apps/kid/policy", json={"max_rating": None}).json()
    assert cleared["max_rating"] is None and cleared["effective_max_rating"] == cleared["default_max_rating"]


def test_per_app_choices_and_dismissals(family):
    client = family("dad")
    assert client.put("/api/apps/kid/policy/apps/com.roblox.client", json={"mode": "flag"}).json()["apps"] == {
        "com.roblox.client": "flag"}
    assert client.put("/api/apps/kid/policy/apps/com.roblox.client", json={"mode": "maybe"}).status_code == 422
    assert client.put("/api/apps/kid/policy/apps/not-a-package", json={"mode": "allow"}).status_code == 422
    assert client.put("/api/apps/kid/policy/apps/com.roblox.client", json={"mode": None}).json()["apps"] == {}
    kept = client.post("/api/apps/kid/policy/dismiss",
                       json={"package": "com.roblox.client", "kind": "flag", "fingerprint": "0123456789abcdef"}).json()
    assert kept["dismissed"] == {"com.roblox.client": "0123456789abcdef"}
    assert client.post("/api/apps/kid/policy/dismiss",
                       json={"package": "com.roblox.client", "kind": "flag"}).status_code == 422
    unused = client.post("/api/apps/kid/policy/dismiss", json={"package": "com.old.game", "kind": "unused"}).json()
    assert "com.old.game" in unused["kept"]
    undone = client.post("/api/apps/kid/policy/dismiss",
                         json={"package": "com.roblox.client", "kind": "flag", "undo": True}).json()
    assert undone["dismissed"] == {}


def test_block_edits_the_existing_rule_and_a_rules_edit_keeps_the_policy(family):
    client = family("dad")
    client.put("/api/apps/kid/policy/apps/com.roblox.client", json={"mode": "flag"})
    blocked = client.post("/api/apps/kid/policy/action", json={"package": "com.roblox.client", "action": "block"}).json()
    assert blocked["blocked_apps"] == ["com.roblox.client"]
    # The screen-time editor saves the whole rule set; the app policy survives it.
    client.put("/api/screen-time/kid/rules", json={"daily_limit_min": 60, "blocked_apps": ["com.roblox.client"]})
    assert client.get("/api/apps/kid/policy").json()["apps"] == {"com.roblox.client": "flag"}
    unblocked = client.post("/api/apps/kid/policy/action", json={"package": "com.roblox.client", "action": "unblock"}).json()
    assert unblocked["blocked_apps"] == []


def test_remove_needs_a_device_owner_phone(family):
    client = family("dad")
    refused = client.post("/api/apps/kid/policy/action", json={"package": "com.old.game", "action": "remove"})
    assert refused.status_code == 409 and "device owner" in refused.json()["detail"]
    with Session(family.engine) as s:
        s.add(Device(device_key="kidphonekey000001", owner_username="kid", kind="phone",
                     capabilities=json.dumps({"guard": {"deviceOwner": True}})))
        s.commit()
    assert client.get("/api/apps/kid/policy").json()["can_uninstall"] is True
    removed = client.post("/api/apps/kid/policy/action", json={"package": "com.old.game", "action": "remove"}).json()
    assert removed["uninstall_apps"] == ["com.old.game"]


def test_each_parent_reads_their_own_notify_level(family):
    family("mom").put("/api/screen-time/kid/notify-level", json={"level": "severe"})
    assert family("mom").get("/api/apps/kid/policy").json()["notify_level"] == "severe"
    assert family("dad").get("/api/apps/kid/policy").json()["notify_level"] == "high"


def test_telemetry_reads_the_inputs_with_the_children(family):
    family("dad").put("/api/apps/kid/policy", json={"birth_date": "2015-03-03", "unused_days": 45})
    children = {c["username"]: c for c in family("dad").get("/api/internal/guardian/children").json()["children"]}
    assert children["kid"]["birth_date"] == "2015-03-03"
    assert children["kid"]["app_policy"]["unused_days"] == 45
