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


def test_a_weekly_schedule_is_checked_and_kept(family):
    act, _ = family
    ok = act("dad").put("/api/screen-time/kid/rules", json={"day_limits": {"sat": 180, "mon": 60}})
    assert ok.status_code == 200 and ok.json()["rules"]["day_limits"] == {"mon": 60, "sat": 180}
    assert act("dad").put("/api/screen-time/kid/rules", json={"day_limits": {"funday": 60}}).status_code == 422
    assert act("dad").put("/api/screen-time/kid/rules", json={"day_limits": {"mon": 2000}}).status_code == 422
