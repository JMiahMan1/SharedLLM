"""A locked child's device asks a parent to unlock it; only a parent's own
session answers, and only the asking device reads the answer."""
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import Device, GuardUnlockRequest, User

PC_KEY = "wg_test-pc-key-kid"
SIB_PC_KEY = "wg_test-pc-key-sib"
DAD_PC_KEY = "wg_test-pc-key-dad"


@pytest.fixture
def family():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        for person in (
            User(username="dad", family_role="father"),
            User(username="mom", family_role="mother"),
            User(username="kid", family_role="child", mother_username="mom", display_name="Kid"),
            User(username="sib", family_role="teen", mother_username="mom"),
            User(username="grown", family_role="adult_child"),
        ):
            session.add(person)
        session.commit()
        session.add(Device(device_key="pc-kid", kind="windows-pc", owner_username="kid", agent_key_hash=main.digest_secret(PC_KEY)))
        session.add(Device(device_key="pc-sib", kind="windows-pc", owner_username="sib", agent_key_hash=main.digest_secret(SIB_PC_KEY)))
        session.add(Device(device_key="pc-dad", kind="windows-pc", owner_username="dad", agent_key_hash=main.digest_secret(DAD_PC_KEY)))
        session.add(Device(device_key="phone-kid", kind="phone", owner_username="kid"))
        session.add(Device(device_key="phone-old", kind="phone", owner_username="kid", revoked=True))
        session.add(Device(device_key="phone-grown", kind="phone", owner_username="grown"))
        session.commit()
    acting = {"as": "dad"}

    def caller():
        with Session(engine) as s:
            return s.exec(select(User).where(User.username == acting["as"])).first()

    app.dependency_overrides[require_api_key] = caller
    app.dependency_overrides[require_internal] = lambda: True

    def act(name, key=None):
        """A client acting as ``name``; with ``key`` it presents a PC's device key
        (require_api_key resolves a device key to the PC's owner)."""
        acting["as"] = name
        return TestClient(app, headers={"Authorization": f"Bearer {key or name + '-own-key'}"})

    yield act
    app.dependency_overrides = {}


def _ask(family, minutes=30, **extra):
    r = family("kid", PC_KEY).post("/api/guard/unlock-requests", json={"minutes": minutes, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _age(request_id, created_ago=None, expires_ago=None):
    """Move a request into the past, the way time would."""
    with Session(main.engine) as s:
        row = s.get(GuardUnlockRequest, request_id)
        now = datetime.now(UTC)
        if created_ago is not None:
            row.created_at = (now - created_ago).isoformat()
        if expires_ago is not None:
            row.expires_at = (now - expires_ago).isoformat()
        s.add(row)
        s.commit()


def test_a_pc_asks_both_parents_hear_and_the_approval_reaches_only_that_pc(family):
    body = _ask(family, minutes=45, note="  finishing   homework ", lock_reason="bedtime")
    asked = body["request"]
    assert set(body["notify"]) == {"dad", "mom"} and body["display_name"] == "Kid"
    assert (asked["status"], asked["minutes_asked"], asked["device"], asked["note"], asked["lock_reason"]) == (
        "pending", 45, "PC", "finishing homework", "bedtime")
    assert "device_key" not in asked

    pending = family("mom").get("/api/guard/unlock-requests").json()["requests"]
    assert [p["id"] for p in pending] == [asked["id"]]
    assert family("sib").get("/api/guard/unlock-requests").json()["requests"] == []

    poll = family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{asked['id']}").json()
    assert poll["request"]["status"] == "pending" and "offline_codes" not in poll

    decided = family("mom").post(f"/api/guard/unlock-requests/{asked['id']}", json={"approve": True, "minutes": 20})
    assert decided.status_code == 200
    out = decided.json()
    assert out["request"]["status"] == "approved" and out["request"]["minutes_granted"] == 20
    assert set(out["notify"]) == {"dad", "mom"}
    given = datetime.fromisoformat(out["request"]["decided_at"])
    assert datetime.fromisoformat(out["request"]["until"]) == given + timedelta(minutes=20)

    seen = family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{asked['id']}").json()
    assert seen["request"]["status"] == "approved" and seen["request"]["until"] == out["request"]["until"]
    assert seen["offline_codes"] == []
    # Reading it again never extends it.
    again = family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{asked['id']}").json()
    assert again["request"]["until"] == out["request"]["until"]


def test_a_device_key_cannot_answer_even_on_a_parents_own_pc(family):
    rid = _ask(family)["request"]["id"]
    # The PC assigned to the father resolves as the father, but it is a device.
    r = family("dad", DAD_PC_KEY).post(f"/api/guard/unlock-requests/{rid}", json={"approve": True})
    assert r.status_code == 403 and "device" in r.json()["detail"].lower()
    # The child's own PC cannot approve itself either.
    assert family("kid", PC_KEY).post(f"/api/guard/unlock-requests/{rid}", json={"approve": True}).status_code == 403
    # Nor can a device key list requests.
    assert family("dad", DAD_PC_KEY).get("/api/guard/unlock-requests").status_code == 403


def test_the_child_a_sibling_and_a_grown_member_cannot_answer(family):
    rid = _ask(family)["request"]["id"]
    for who in ("kid", "sib", "grown"):
        assert family(who).post(f"/api/guard/unlock-requests/{rid}", json={"approve": True}).status_code == 403
    assert family("dad").get(f"/api/guard/unlock-requests/{rid}").status_code == 404  # only the device reads it


def test_another_device_cannot_read_the_answer(family):
    rid = _ask(family)["request"]["id"]
    family("dad").post(f"/api/guard/unlock-requests/{rid}", json={"approve": True})
    # A sibling's PC, the child's own key without the device, the child's
    # phone: none of them is the PC that asked.
    assert family("sib", SIB_PC_KEY).get(f"/api/guard/unlock-requests/{rid}").status_code == 404
    assert family("kid").get(f"/api/guard/unlock-requests/{rid}").status_code == 404
    assert family("kid").get(f"/api/guard/unlock-requests/{rid}?device_key=phone-kid").status_code == 404
    assert family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{rid}").status_code == 200


def test_a_phone_asks_with_its_own_registered_key(family):
    kid = family("kid")
    assert kid.post("/api/guard/unlock-requests", json={"minutes": 30}).status_code == 422  # which device?
    assert kid.post("/api/guard/unlock-requests", json={"minutes": 30, "device_key": "phone-grown"}).status_code == 403
    assert kid.post("/api/guard/unlock-requests", json={"minutes": 30, "device_key": "phone-old"}).status_code == 403
    r = kid.post("/api/guard/unlock-requests", json={"minutes": 30, "device_key": "phone-kid"})
    assert r.status_code == 200 and r.json()["request"]["device"] == "phone"
    rid = r.json()["request"]["id"]
    assert kid.get(f"/api/guard/unlock-requests/{rid}?device_key=phone-kid").json()["request"]["status"] == "pending"
    assert family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{rid}").status_code == 404


def test_deny_is_final_and_a_second_answer_is_refused(family):
    rid = _ask(family)["request"]["id"]
    assert family("mom").post(f"/api/guard/unlock-requests/{rid}", json={"approve": False}).json()["request"]["status"] == "denied"
    assert family("dad").post(f"/api/guard/unlock-requests/{rid}", json={"approve": True}).status_code == 409
    seen = family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{rid}").json()
    assert seen["request"]["status"] == "denied" and seen["request"]["until"] is None and "offline_codes" not in seen


def test_an_approval_is_given_once(family):
    rid = _ask(family)["request"]["id"]
    assert family("mom").post(f"/api/guard/unlock-requests/{rid}", json={"approve": True}).status_code == 200
    assert family("dad").post(f"/api/guard/unlock-requests/{rid}", json={"approve": True, "minutes": 240}).status_code == 409


def test_an_unanswered_request_expires(family):
    rid = _ask(family)["request"]["id"]
    _age(rid, expires_ago=timedelta(seconds=1))
    r = family("mom").post(f"/api/guard/unlock-requests/{rid}", json={"approve": True})
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{rid}").json()["request"]["status"] == "expired"
    assert family("mom").get("/api/guard/unlock-requests").json()["requests"] == []


def test_a_second_request_supersedes_the_first(family):
    first = _ask(family)["request"]["id"]
    _age(first, created_ago=timedelta(minutes=2))
    second = _ask(family, minutes=15)["request"]["id"]
    assert family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{first}").json()["request"]["status"] == "superseded"
    r = family("mom").post(f"/api/guard/unlock-requests/{first}", json={"approve": True})
    assert r.status_code == 409 and "newer" in r.json()["detail"]
    assert [p["id"] for p in family("mom").get("/api/guard/unlock-requests").json()["requests"]] == [second]


def test_a_device_is_rate_limited(family):
    rid = _ask(family)["request"]["id"]
    r = family("kid", PC_KEY).post("/api/guard/unlock-requests", json={"minutes": 30})
    assert r.status_code == 429
    _age(rid, created_ago=timedelta(minutes=1))
    with Session(main.engine) as s:
        now = datetime.now(UTC)
        for i in range(main.UNLOCK_REQUESTS_PER_HOUR):
            s.add(GuardUnlockRequest(username="kid", device_key="pc-kid", minutes_asked=30, status="expired",
                                     created_at=(now - timedelta(minutes=5 + i)).isoformat(), expires_at=now.isoformat()))
        s.commit()
    r = family("kid", PC_KEY).post("/api/guard/unlock-requests", json={"minutes": 30})
    assert r.status_code == 429 and "hour" in r.json()["detail"]
    # Another device is not held back by this one.
    assert family("kid").post("/api/guard/unlock-requests", json={"minutes": 30, "device_key": "phone-kid"}).status_code == 200


def test_bad_values_and_grown_members_are_refused(family):
    pc = family("kid", PC_KEY)
    for minutes in (0, 4, 241, "30", True, None):
        assert pc.post("/api/guard/unlock-requests", json={"minutes": minutes}).status_code == 422
    assert family("grown").post("/api/guard/unlock-requests", json={"minutes": 30, "device_key": "phone-grown"}).status_code == 409
    rid = _ask(family)["request"]["id"]
    assert family("mom").post(f"/api/guard/unlock-requests/{rid}", json={"approve": "yes"}).status_code == 422
    assert family("mom").post(f"/api/guard/unlock-requests/{rid}", json={"approve": True, "minutes": 999}).status_code == 422
    assert family("mom").post("/api/guard/unlock-requests/9999", json={"approve": True}).status_code == 404


def test_an_approval_lifts_a_pause_given_before_it(family):
    family("dad").post("/api/screen-time/kid/pause", json={})
    assert family("dad").get("/api/screen-time/kid/rules").json()["rules"]["paused"] is True
    rid = _ask(family)["request"]["id"]
    family("mom").post(f"/api/guard/unlock-requests/{rid}", json={"approve": True})
    assert "paused" not in family("dad").get("/api/screen-time/kid/rules").json()["rules"]


def test_offline_verifiers_reach_a_device_only_through_an_approval(family):
    assert family("mom").put("/api/guard/offline-code", json={"code": "48291735"}).status_code == 200
    assert "offline" not in str(family("kid").get("/api/screen-time/kid/rules").json())
    rid = _ask(family)["request"]["id"]
    assert "offline_codes" not in family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{rid}").json()

    family("dad").post(f"/api/guard/unlock-requests/{rid}", json={"approve": True})
    body = family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{rid}").json()
    [code] = body["offline_codes"]
    assert code["parent"] == "mom"
    digest = hashlib.pbkdf2_hmac("sha256", b"48291735", bytes.fromhex(code["salt"]), code["iterations"])
    assert digest.hex() == code["hash"]
    assert "48291735" not in str(body)
    # A denied request carries none.
    _age(rid, created_ago=timedelta(minutes=1))
    other = _ask(family)["request"]["id"]
    family("dad").post(f"/api/guard/unlock-requests/{other}", json={"approve": False})
    assert "offline_codes" not in family("kid", PC_KEY).get(f"/api/guard/unlock-requests/{other}").json()


def test_a_minor_is_protected_and_a_grown_member_is_not(family):
    assert family("kid").get("/api/screen-time/kid/rules").json()["protected"] is True
    assert family("grown").get("/api/screen-time/grown/rules").json()["protected"] is False


def test_the_old_password_unlock_is_gone(family):
    assert family("kid").post("/api/guard/parent-unlock", json={"username": "mom", "password": "x"}).status_code in (404, 405)
