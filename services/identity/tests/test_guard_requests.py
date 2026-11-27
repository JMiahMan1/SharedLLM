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


def test_web_filter_categories_are_validated_and_saved(family):
    ok = family("dad").put("/api/screen-time/kid/rules", json={
        "web_filter": {"enabled": True, "categories": ["social", "gaming", "social"]},
    })
    assert ok.status_code == 200
    assert ok.json()["rules"]["web_filter"]["categories"] == ["social", "gaming"]
    bad = family("dad").put("/api/screen-time/kid/rules", json={
        "web_filter": {"enabled": True, "categories": ["unknown_category"]},
    })
    assert bad.status_code == 422


def test_uninstall_install_and_kiosk_rules_are_validated_and_saved(family):
    ok = family("dad").put("/api/screen-time/kid/rules", json={
        "uninstall_apps": ["com.roblox.client", "com.roblox.client"],
        "install_apps": ["org.khanacademy.android"],
        "kiosk_mode": True,
        "kiosk_apps": ["org.khanacademy.android"],
    })
    assert ok.status_code == 200
    rules = ok.json()["rules"]
    assert rules["uninstall_apps"] == ["com.roblox.client"]
    assert rules["install_apps"] == ["org.khanacademy.android"]
    assert rules["kiosk_mode"] is True
    assert rules["kiosk_apps"] == ["org.khanacademy.android"]
    bad = family("dad").put("/api/screen-time/kid/rules", json={
        "uninstall_apps": ["not a package"],
    })
    assert bad.status_code == 422



def test_locked_sections_are_validated_saved_and_offered_to_the_parent(family):
    ok = family("dad").put("/api/screen-time/kid/rules", json={"locked_sections": ["chat", "bible", "bible", "chores"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["rules"]["locked_sections"] == ["chat", "bible", "chores"]
    bad = family("dad").put("/api/screen-time/kid/rules", json={"locked_sections": ["settings"]})
    assert bad.status_code == 422
    assert family("dad").put("/api/screen-time/kid/rules", json={"locked_sections": "chat"}).status_code == 422
    # The child cannot widen their own locked mode.
    assert family("kid").put("/api/screen-time/kid/rules", json={"locked_sections": ["music"]}).status_code == 403
    read = family("dad").get("/api/screen-time/kid/rules").json()
    assert read["rules"]["locked_sections"] == ["chat", "bible", "chores"]
    ids = [c["id"] for c in read["locked_section_choices"]]
    assert ids[0] == "chat" and "bible" in ids and "devices" in ids


def test_an_app_request_carries_the_apps_own_profile(family):
    """The phone sends what it knows about the app, and the parent's card reads it back."""
    profile = {
        "label": "Star Chart",
        "version_name": "4.2.1",
        "version_code": 4210,
        "installer": None,  # not from a store
        "size_bytes": 12 * 1024 * 1024,
        "permissions": ["camera", "location", "not-a-group"],
        "capabilities": ["sms_send", "made_up"],
        "special_access": ["accessibility"],
        "cert_sha1": ["AB" * 20],
        "apk_sha256": "a" * 64,
        "apk_hash_state": "ok",
        # A phone cannot rename the app it is asking about.
        "package": "com.somewhere.else",
    }
    body = family("kid").post("/api/guard/requests", json={
        "kind": "app", "target": "com.example.starchart", "label": "Star Chart", "profile": profile,
    }).json()

    meta = body["request"]["meta"]
    assert meta["version_name"] == "4.2.1" and meta["version_code"] == 4210
    assert meta["installer"] is None
    assert meta["permissions"] == ["camera", "location"]
    assert meta["capabilities"] == ["sms_send"]
    assert meta["special_access"] == ["accessibility"]
    assert meta["apk_sha256"] == "a" * 64
    assert meta["package"] == "com.example.starchart"
    # The answer a parent gives is unaffected: it is still per package.
    assert family("kid").get("/api/screen-time/kid/rules").json()["approvals"]["apps"] == {
        "com.example.starchart": "pending"
    }


def test_a_profile_that_makes_no_sense_is_dropped_not_guessed(family):
    rid = family("kid").post("/api/guard/requests", json={
        "kind": "app", "target": "com.example.puzzle", "label": "Puzzle",
        "profile": {"version_name": "x" * 500, "version_code": "not a number", "size_bytes": -5,
                    "permissions": "camera", "cert_sha1": ["nope"], "apk_sha256": "zz"},
    }).json()["request"]
    assert rid["meta"]["version_name"] == "x" * 40  # bounded, not refused
    assert rid["meta"]["version_code"] is None and rid["meta"]["size_bytes"] is None
    assert rid["meta"]["permissions"] == [] and rid["meta"]["cert_sha1"] == [] and rid["meta"]["apk_sha256"] is None

    site = family("kid").post("/api/guard/requests", json={
        "kind": "site", "target": "example.com", "profile": {"version_name": "1.0"},
    }).json()["request"]
    assert site["meta"] == {}  # a site has no app profile


def test_a_request_waiting_from_before_the_phone_knew_its_profile_gains_one(family):
    first = family("kid").post("/api/guard/requests", json={
        "kind": "app", "target": "com.example.notes", "label": "Notes",
    }).json()["request"]
    assert first["meta"] == {}
    again = family("kid").post("/api/guard/requests", json={
        "kind": "app", "target": "com.example.notes", "label": "Notes",
        "profile": {"version_name": "1.1", "version_code": 11},
    }).json()["request"]
    assert again["id"] == first["id"] and again["meta"]["version_name"] == "1.1"
    # Once it is filled, the profile stays: it is the version the parent was asked
    # about, not whatever the app looks like by the next refresh.
    assert family("kid").post("/api/guard/requests", json={
        "kind": "app", "target": "com.example.notes",
        "profile": {"version_name": "9.9"},
    }).json()["request"]["meta"]["version_name"] == "1.1"
