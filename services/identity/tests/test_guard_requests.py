"""Asking a parent first: new apps, app permissions and blocked sites."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User


@pytest.fixture
def family():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        for person in (
            User(username="dad", family_role="father"),
            User(username="mom", family_role="mother"),
            User(username="kid", family_role="child", mother_username="mom"),
            User(username="sib", family_role="teen", mother_username="mom"),
            User(username="grown", family_role="adult_child"),
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

    yield act
    app.dependency_overrides = {}


def test_a_new_app_waits_and_both_parents_hear_once(family):
    first = family("kid").post("/api/guard/requests", json={"kind": "app", "target": "com.roblox.client", "label": "Roblox"})
    assert first.status_code == 200, first.text
    assert set(first.json()["notify"]) == {"dad", "mom"}
    again = family("kid").post("/api/guard/requests", json={"kind": "app", "target": "com.roblox.client"})
    assert again.json()["notify"] == [] and again.json()["request"]["id"] == first.json()["request"]["id"]
    rules = family("kid").get("/api/screen-time/kid/rules").json()
    assert rules["approvals"]["apps"] == {"com.roblox.client": "pending"}


def test_only_the_childs_parents_answer_and_the_phone_reads_the_answer(family):
    rid = family("kid").post("/api/guard/requests", json={"kind": "app", "target": "com.roblox.client"}).json()["request"]["id"]
    assert family("sib").post(f"/api/guard/requests/{rid}", json={"approve": True}).status_code == 403
    assert family("kid").post(f"/api/guard/requests/{rid}", json={"approve": True}).status_code == 403
    assert [r["id"] for r in family("mom").get("/api/guard/requests").json()["requests"]] == [rid]
    assert family("sib").get("/api/guard/requests").json()["requests"] == []
    decided = family("mom").post(f"/api/guard/requests/{rid}", json={"approve": False})
    assert decided.json()["request"]["status"] == "denied" and decided.json()["notify"] == ["kid"]
    assert family("dad").post(f"/api/guard/requests/{rid}", json={"approve": True}).status_code == 409
    assert family("kid").get("/api/screen-time/kid/rules").json()["approvals"]["apps"] == {"com.roblox.client": "denied"}
    # An answered app is not asked again.
    assert family("kid").post("/api/guard/requests", json={"kind": "app", "target": "com.roblox.client"}).json()["notify"] == []


def test_a_permission_is_asked_per_app_and_group(family):
    ok = family("kid").post("/api/guard/requests", json={"kind": "permission", "target": "com.snapchat.android|camera"})
    assert ok.status_code == 200
    assert family("kid").post("/api/guard/requests", json={"kind": "permission", "target": "com.snapchat.android|teleport"}).status_code == 422
    rid = ok.json()["request"]["id"]
    family("dad").post(f"/api/guard/requests/{rid}", json={"approve": True})
    assert family("kid").get("/api/screen-time/kid/rules").json()["approvals"]["permissions"] == {"com.snapchat.android|camera": "approved"}


def test_a_site_allowed_for_an_hour_lands_in_allow_until(family):
    rid = family("kid").post("/api/guard/requests", json={"kind": "site", "target": "www.Minecraft.net", "note": "for a school project"}).json()["request"]["id"]
    assert family("dad").post(f"/api/guard/requests/{rid}", json={"approve": True}).status_code == 422  # how long?
    decided = family("dad").post(f"/api/guard/requests/{rid}", json={"approve": True, "minutes": 60}).json()["request"]
    until = datetime.fromisoformat(decided["until"])
    assert timedelta(minutes=59) < until - datetime.now(UTC) <= timedelta(minutes=60)
    rules = family("kid").get("/api/screen-time/kid/rules").json()["rules"]
    assert rules["web_filter"]["allow_until"] == {"minecraft.net": decided["until"]}
    # A site can be asked again once answered (the hour runs out).
    assert family("kid").post("/api/guard/requests", json={"kind": "site", "target": "minecraft.net"}).json()["notify"] != []


def test_a_site_allowed_always_moves_from_block_to_allow(family):
    family("mom").put("/api/screen-time/kid/rules", json={"web_filter": {"enabled": True, "block": ["minecraft.net"]}})
    rid = family("kid").post("/api/guard/requests", json={"kind": "site", "target": "minecraft.net"}).json()["request"]["id"]
    family("mom").post(f"/api/guard/requests/{rid}", json={"approve": True, "always": True})
    wf = family("kid").get("/api/screen-time/kid/rules").json()["rules"]["web_filter"]
    assert wf["allow"] == ["minecraft.net"] and wf["block"] == [] and wf["enabled"] is True


def test_an_adult_has_nobody_to_ask_and_bad_targets_are_refused(family):
    assert family("grown").post("/api/guard/requests", json={"kind": "site", "target": "example.com"}).status_code == 409
    assert family("kid").post("/api/guard/requests", json={"kind": "app", "target": "not a package"}).status_code == 422
    assert family("kid").post("/api/guard/requests", json={"kind": "wish", "target": "pony"}).status_code == 422


def test_rules_keep_new_keys_and_drop_runout_allows(family):
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    r = family("dad").put("/api/screen-time/kid/rules", json={
        "install_approval": False,
        "permission_gating": ["camera", "location"],
        "web_filter": {"allow_until": {"a.example": past, "b.example": future}},
    })
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert rules["install_approval"] is False and rules["permission_gating"] == ["camera", "location"]
    assert rules["web_filter"]["allow_until"] == {"b.example": future}
    assert family("dad").put("/api/screen-time/kid/rules", json={"permission_gating": ["xray"]}).status_code == 422
    assert family("dad").put("/api/screen-time/kid/rules", json={"install_approval": "yes"}).status_code == 422


def test_asking_a_parent_to_unblock_an_app_allows_for_an_hour_or_always(family):
    family("dad").put("/api/screen-time/kid/rules", json={
        "blocked_apps": ["com.roblox.client"],
        "app_limits": {"com.google.android.youtube": 30},
    })
    ask = family("kid").post("/api/guard/requests", json={
        "kind": "app_unblock",
        "target": "com.roblox.client",
        "label": "Roblox",
        "note": "playing with cousins",
    })
    assert ask.status_code == 200
    assert set(ask.json()["notify"]) == {"dad", "mom"}
    rid = ask.json()["request"]["id"]
    decided = family("dad").post(f"/api/guard/requests/{rid}", json={"approve": True, "minutes": 60}).json()["request"]
    until = datetime.fromisoformat(decided["until"])
    assert timedelta(minutes=59) < until - datetime.now(UTC) <= timedelta(minutes=60)
    rules = family("kid").get("/api/screen-time/kid/rules").json()["rules"]
    assert rules["app_allow_until"] == {"com.roblox.client": decided["until"]}

    # Once answered, the child can ask again later; approving with always=True removes the block and limit.
    rid2 = family("kid").post("/api/guard/requests", json={
        "kind": "app_unblock",
        "target": "com.roblox.client",
    }).json()["request"]["id"]
    family("mom").post(f"/api/guard/requests/{rid2}", json={"approve": True, "always": True})
    rules2 = family("kid").get("/api/screen-time/kid/rules").json()["rules"]
    assert rules2["blocked_apps"] == []
    assert "app_allow_until" not in rules2
