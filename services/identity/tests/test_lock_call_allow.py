"""A locked child may still call their parents: each parent, with their own
number in E.164, reaches the child's guard status as ``parent_contacts``.
Fake people and numbers only."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User

# 555-01xx numbers are reserved for fiction.
DAD = "+1 (555) 010-0101"
MOM = "555.010.0102"
OTHER_MOM = "+15550100103"
ADMIN = "+15550100104"


@pytest.fixture
def family(monkeypatch):
    monkeypatch.setattr(main, "HOUSEHOLD_PHONE_COUNTRY_CODE", "1")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        for person in (
            User(username="dad", display_name="Test Dad", family_role="father", is_admin=True),
            User(username="mom", display_name="Test Mom", family_role="mother"),
            User(username="mom2", family_role="mother"),
            User(username="helper", family_role="other", is_admin=True),
            User(username="kid", family_role="child", mother_username="mom"),
            User(username="grown", family_role="adult_child"),
        ):
            session.add(person)
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

    yield act
    app.dependency_overrides = {}


def _set_number(act, who, number):
    return act(who).patch("/api/users/me", json={"phone_number": number})


def _contacts(act, who="kid"):
    return act(who).get(f"/api/screen-time/{who}/rules").json()["parent_contacts"]


def test_a_member_saves_their_own_number_in_e164(family):
    r = _set_number(family, "dad", DAD)
    assert r.status_code == 200
    assert r.json()["phone_number"] == "+15550100101"
    # Written without a country code: the household's completes it.
    assert _set_number(family, "mom", MOM).json()["phone_number"] == "+15550100102"


def test_without_a_household_country_code_a_local_number_is_refused(family, monkeypatch):
    monkeypatch.setattr(main, "HOUSEHOLD_PHONE_COUNTRY_CODE", "")
    r = _set_number(family, "mom", MOM)
    assert r.status_code == 422 and "country code" in r.json()["detail"]
    assert _set_number(family, "mom", "+1 555 010 0102").json()["phone_number"] == "+15550100102"


def test_a_number_that_is_not_one_is_refused_and_the_old_one_kept(family):
    _set_number(family, "dad", DAD)
    for bad in ("call me", "12345", "+1234567890123456"):
        assert _set_number(family, "dad", bad).status_code == 422
    assert family("dad").get("/api/users/me").json()["phone_number"] == "+15550100101"


def test_a_blank_number_leaves_it_and_clear_fields_removes_it(family):
    _set_number(family, "dad", DAD)
    assert _set_number(family, "dad", "  ").json()["phone_number"] == "+15550100101"
    cleared = family("dad").patch("/api/users/me", json={"clear_fields": ["phone_number"]})
    assert cleared.json()["phone_number"] is None


def test_the_childs_status_carries_only_their_parents(family):
    _set_number(family, "dad", DAD)
    _set_number(family, "mom", MOM)
    _set_number(family, "mom2", OTHER_MOM)
    _set_number(family, "helper", ADMIN)
    # The father, then the child's own mother; not another mother, not an
    # admin who is not a parent.
    assert _contacts(family) == [
        {"name": "Test Dad", "phone": "+15550100101"},
        {"name": "Test Mom", "phone": "+15550100102"},
    ]


def test_a_parent_without_a_number_is_listed_with_an_empty_phone(family):
    _set_number(family, "mom", MOM)
    assert _contacts(family) == [
        {"name": "Test Dad", "phone": ""},
        {"name": "Test Mom", "phone": "+15550100102"},
    ]


def test_an_adult_has_no_parent_contacts(family):
    _set_number(family, "dad", DAD)
    assert _contacts(family, "grown") == []


def test_a_parent_adds_lock_suspend_apps_and_bad_names_are_refused(family):
    dad = family("dad")
    ok = dad.put("/api/screen-time/kid/rules", json={"lock_suspend_apps": ["com.example.watchlink", "com.example.watchlink"]})
    assert ok.status_code == 200
    assert ok.json()["rules"]["lock_suspend_apps"] == ["com.example.watchlink"]
    assert dad.put("/api/screen-time/kid/rules", json={"lock_suspend_apps": ["not a package"]}).status_code == 422
