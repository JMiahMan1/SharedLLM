"""The family member page's Devices section: the father's and an admin's only."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import Device, DeviceEvent, GuardOfflineCode, User


@pytest.fixture
def family():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        for person in (
            User(username="dad", family_role="father"),
            User(username="mom", family_role="mother"),
            User(username="boss", family_role="other", is_admin=True),
            User(username="kid", family_role="child", mother_username="mom"),
            User(username="sib", family_role="teen", mother_username="mom"),
            User(username="grown", family_role="adult_child"),
        ):
            session.add(person)
        session.commit()
        session.add(Device(device_key="kid-phone-0000000001", owner_username="kid", model="Pixel 7", os_version="14",
                           os_build="AP1A", app_version="1.9.0", app_build="512", last_ip_address="10.0.0.9",
                           capabilities='{"guard": {"adminActive": true, "guardEnabled": false}}',
                           last_seen_at="2026-10-24T08:00:00"))
        session.add(Device(device_key="kid-old-phone-000002", owner_username="kid", revoked=True))
        session.add(Device(device_key="sib-phone-0000000003", owner_username="sib"))
        session.add(GuardOfflineCode(username="dad", salt="00", iterations=1, hash="00"))
        session.commit()
    acting = {"as": "dad"}

    def caller():
        with Session(engine) as s:
            return s.exec(select(User).where(User.username == acting["as"])).first()

    app.dependency_overrides[require_api_key] = caller
    app.dependency_overrides[require_internal] = lambda: True

    def act(name):
        acting["as"] = name
        return TestClient(app)

    yield act, engine
    app.dependency_overrides = {}


def test_the_father_sees_the_childs_devices_and_tamper_reports(family):
    act, _ = family
    told = act("dad").post("/api/internal/guard/tamper/kid", json={"event": "guard_disabled"}).json()
    assert set(told["parents"]) >= {"dad", "mom"}

    body = act("dad").get("/api/family/devices/kid").json()

    assert [d["device_key"] for d in body["devices"]] == ["kid-phone-0000000001"]  # revoked and siblings' left out
    phone = body["devices"][0]
    assert (phone["model"], phone["app_version"], phone["app_build"], phone["os_build"]) == ("Pixel 7", "1.9.0", "512", "AP1A")
    assert phone["capabilities"]["guard"]["guardEnabled"] is False
    assert body["tamper"][0]["extra"] == {"event": "guard_disabled"}
    assert body["offline_code"]["dad"] is True and body["offline_code"]["mom"] is False


def test_an_admin_sees_them_too(family):
    act, _ = family
    assert act("boss").get("/api/family/devices/kid").status_code == 200


@pytest.mark.parametrize("viewer", ["mom", "sib", "grown", "kid"])
def test_everyone_else_is_refused(family, viewer):
    act, _ = family
    # The mother sees every personal detail, never the technical side; a
    # sibling or another adult sees nothing; a child does not browse this.
    assert act(viewer).get("/api/family/devices/kid").status_code == 403


def test_an_adults_devices_are_not_a_parents(family):
    act, _ = family
    assert act("dad").get("/api/family/devices/grown").status_code == 403


def test_the_rules_say_who_sees_the_technical_side(family):
    act, _ = family
    assert act("dad").get("/api/screen-time/kid/rules").json()["technical"] is True
    assert act("mom").get("/api/screen-time/kid/rules").json()["technical"] is False
    assert act("kid").get("/api/screen-time/kid/rules").json()["technical"] is True
    assert act("dad").get("/api/internal/may-view-raw", params={"viewer": "mom", "subject": "kid"}).json() == {
        "allowed": True,
        "technical": False,
        "comms": True,  # the mother sees a child's calls and messages
    }
    # Every admin is a parent: the child's calls and messages too.
    assert act("dad").get("/api/internal/may-view-raw", params={"viewer": "boss", "subject": "kid"}).json() == {
        "allowed": True,
        "technical": True,
        "comms": True,
    }


def test_a_tamper_report_is_kept_on_the_childs_record(family):
    act, engine = family
    act("dad").post("/api/internal/guard/tamper/kid", json={"event": "uninstall_attempt", "device_key": "kid-phone-0000000001"})
    with Session(engine) as s:
        row = s.exec(select(DeviceEvent).where(DeviceEvent.event == "guard_tamper")).first()
    assert (row.username, row.device_key) == ("kid", "kid-phone-0000000001")


def test_a_tamper_report_keeps_what_it_was_about(family):
    act, _ = family
    act("dad").post("/api/internal/guard/tamper/kid", json={"event": "bypass_app", "detail": "Tor Browser"})
    body = act("dad").get("/api/family/devices/kid").json()
    assert body["tamper"][0]["extra"] == {"event": "bypass_app", "detail": "Tor Browser"}


@pytest.mark.parametrize("owner", ["grown", "dad", "mom"])
def test_a_tamper_report_from_a_grown_member_is_dropped(family, owner):
    """Only a minor is monitored: a grown member's own device report is dropped
    (nothing stored, nobody told) and the answer says so."""
    act, engine = family
    body = act("dad").post(f"/api/internal/guard/tamper/{owner}", json={"event": "guard_disabled"}).json()
    assert body["dropped"] == "not_minor" and body["notify"] == [] and body["parents"] == []
    with Session(engine) as s:
        assert s.exec(select(DeviceEvent).where(DeviceEvent.event == "guard_tamper")).first() is None


@pytest.mark.parametrize("owner", ["kid", "sib"])
def test_a_minor_tamper_report_is_still_kept_and_tells_the_parents(family, owner):
    act, engine = family
    body = act("dad").post(f"/api/internal/guard/tamper/{owner}", json={"event": "guard_disabled"}).json()
    assert body.get("dropped") is None
    assert {"dad", "mom"} <= set(body["notify"])
    with Session(engine) as s:
        row = s.exec(select(DeviceEvent).where(DeviceEvent.username == owner, DeviceEvent.event == "guard_tamper")).first()
    assert row is not None


def test_a_weekly_schedule_is_checked_and_kept(family):
    act, _ = family
    ok = act("dad").put("/api/screen-time/kid/rules", json={"day_limits": {"sat": 180, "mon": 60}})
    assert ok.status_code == 200 and ok.json()["rules"]["day_limits"] == {"mon": 60, "sat": 180}
    assert act("dad").put("/api/screen-time/kid/rules", json={"day_limits": {"funday": 60}}).status_code == 422
    assert act("dad").put("/api/screen-time/kid/rules", json={"day_limits": {"mon": 2000}}).status_code == 422


def test_the_father_and_the_mother_see_each_other(family):
    act, _ = family
    # The father reads the mother's rules and devices; neither may change the other's.
    rules = act("dad").get("/api/screen-time/mom/rules").json()
    assert rules["can_manage"] is False and rules["technical"] is True
    assert act("dad").get("/api/family/devices/mom").status_code == 200
    assert act("dad").put("/api/screen-time/mom/rules", json={"daily_limit_min": 60}).status_code == 403
    # The mother reads the father's rules, without the technical side.
    rules = act("mom").get("/api/screen-time/dad/rules").json()
    assert rules["can_manage"] is False and rules["technical"] is False
    assert act("mom").get("/api/family/devices/dad").status_code == 403
    # Another grown member's stay their own.
    assert act("dad").get("/api/screen-time/grown/rules").status_code == 403


def test_the_father_clears_the_tamper_log_and_the_rows_are_kept(family):
    act, engine = family
    for event in ("guard_disabled", "uninstall_attempt", "dns_changed"):
        act("dad").post("/api/internal/guard/tamper/kid", json={"event": event})
    assert len(act("dad").get("/api/family/devices/kid").json()["tamper"]) == 3

    assert act("dad").delete("/api/family/devices/kid/tamper").json() == {"status": "SUCCESS", "cleared": 3}
    assert act("dad").get("/api/family/devices/kid").json()["tamper"] == []
    with Session(engine) as s:
        kept = s.exec(select(DeviceEvent).where(DeviceEvent.event == "guard_tamper_cleared")).all()
    assert len(kept) == 3 and all('"cleared_by": "dad"' in row.extra for row in kept)

    # A new report after clearing shows on its own.
    act("dad").post("/api/internal/guard/tamper/kid", json={"event": "guard_disabled"})
    assert len(act("dad").get("/api/family/devices/kid").json()["tamper"]) == 1


@pytest.mark.parametrize("viewer", ["mom", "kid", "sib"])
def test_only_those_who_see_the_log_may_clear_it(family, viewer):
    act, _ = family
    act("dad").post("/api/internal/guard/tamper/kid", json={"event": "guard_disabled"})
    assert act(viewer).delete("/api/family/devices/kid/tamper").status_code == 403
    assert len(act("dad").get("/api/family/devices/kid").json()["tamper"]) == 1


def _give(engine, owner, key, kind):
    with Session(engine) as s:
        s.add(Device(device_key=key, owner_username=owner, kind=kind))
        s.commit()


def test_the_rules_name_the_members_device_kinds(family):
    """The page words its text from these: a tablet is never called a phone."""
    act, engine = family
    assert act("dad").get("/api/screen-time/kid/rules").json()["device_kinds"] == ["phone"]  # the revoked one is left out
    _give(engine, "kid", "kid-tablet-000000004", "tablet")
    _give(engine, "kid", "kid-watch-0000000005", "watch")
    assert act("dad").get("/api/screen-time/kid/rules").json()["device_kinds"] == ["phone", "tablet"]
    assert act("grown").get("/api/screen-time/grown/rules").json()["device_kinds"] == []


def test_the_guardian_list_and_notices_say_tablet_for_a_tablet(family):
    act, engine = family
    with Session(engine) as s:
        sib = s.exec(select(Device).where(Device.device_key == "sib-phone-0000000003")).one()
        sib.kind = "tablet"
        s.add(sib)
        s.commit()
    children = {c["username"]: c for c in act("dad").get("/api/internal/guardian/children").json()["children"]}
    assert children["sib"]["device_kinds"] == ["tablet"] and children["kid"]["device_kinds"] == ["phone"]
    assert act("dad").get("/api/internal/guard/parents/sib").json()["device"] == "tablet"
    told = act("dad").post("/api/internal/guard/tamper/sib", json={"event": "guard_disabled"}).json()
    assert told["device"] == "tablet"
    # The device that sent the report decides when it is registered.
    _give(engine, "sib", "sib-phone-0000000006", "phone")
    assert act("dad").get("/api/internal/guard/parents/sib").json()["device"] == "phone or tablet"
    told = act("dad").post("/api/internal/guard/tamper/sib",
                           json={"event": "guard_disabled", "device_key": "sib-phone-0000000006"}).json()
    assert told["device"] == "phone"
    paused = act("dad").post("/api/screen-time/sib/pause", json={}).json()
    assert paused["device"] == "phone or tablet"
    refused = act("kid").post("/api/screen-time/sib/pause", json={})
    assert refused.status_code == 403 and refused.json()["detail"] == "Only a parent can pause a minor's phone or tablet"
