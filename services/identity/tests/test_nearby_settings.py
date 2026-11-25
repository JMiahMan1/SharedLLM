"""Settings > Nearby & offline: grown members choose, a minor's parents choose for them."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity import nearby_settings
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User, UserNearbySetting


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="pat", display_name="Pat", family_role="father"))
        session.add(User(username="mom", display_name="Mom", family_role="mother"))
        session.add(User(username="robin", display_name="Robin", family_role="child", mother_username="mom"))
        session.add(User(username="casey", display_name="Casey", family_role="adult_child"))
        session.commit()
        yield session


@pytest.fixture(name="as_user")
def as_user_fixture(session: Session):
    main.engine = session.bind
    app.dependency_overrides[require_internal] = lambda: True

    def _as(username: str) -> TestClient:
        user = session.exec(select(User).where(User.username == username)).first()
        app.dependency_overrides[require_api_key] = lambda: user
        return TestClient(app)

    yield _as
    app.dependency_overrides = {}


def test_defaults_keep_the_old_behaviour(as_user):
    body = as_user("casey").get("/api/users/me/nearby-settings").json()
    assert body["settings"] == {"nearby": False, "chat": False, "offline": False, "car": True}
    assert body["can_change"] is True
    assert body["managed_by_parent"] is False
    assert body["always_on"] == []
    assert body["chosen"] is False  # only defaults: the phone keeps what it already runs


def test_a_saved_choice_is_marked_chosen(as_user):
    client = as_user("casey")
    client.put("/api/users/me/nearby-settings", json={"settings": {"nearby": True}})
    assert client.get("/api/users/me/nearby-settings").json()["chosen"] is True


def test_a_grown_member_switches_their_own(as_user):
    client = as_user("casey")
    body = client.put("/api/users/me/nearby-settings", json={"settings": {"nearby": True, "car": False}}).json()
    assert body["settings"] == {"nearby": True, "chat": False, "offline": False, "car": False}
    # Only the parts sent change.
    body = client.put("/api/users/me/nearby-settings", json={"chat": True}).json()
    assert body["settings"] == {"nearby": True, "chat": True, "offline": False, "car": False}
    assert client.get("/api/users/me/nearby-settings").json()["settings"]["chat"] is True


def test_unknown_parts_and_non_bools_are_refused(as_user):
    client = as_user("casey")
    assert client.put("/api/users/me/nearby-settings", json={"wifi": True}).status_code == 422
    assert client.put("/api/users/me/nearby-settings", json={"chat": "yes"}).status_code == 422


def test_a_child_reads_but_cannot_change(as_user):
    client = as_user("robin")
    body = client.get("/api/users/me/nearby-settings").json()
    assert body["managed_by_parent"] is True
    assert body["can_change"] is False
    assert body["always_on"] == ["parent_link"]
    assert client.put("/api/users/me/nearby-settings", json={"car": False}).status_code == 403
    assert client.put("/api/users/robin/nearby-settings", json={"car": False}).status_code == 403


def test_a_parent_sets_a_childs_parts(as_user, session):
    client = as_user("mom")
    body = client.get("/api/users/robin/nearby-settings").json()
    assert body["can_change"] is True
    body = client.put("/api/users/robin/nearby-settings", json={"nearby": True, "offline": True}).json()
    assert body["settings"]["nearby"] is True and body["settings"]["offline"] is True
    row = session.get(UserNearbySetting, "robin")
    session.refresh(row)
    assert row.updated_by == "mom"
    # The child's phone reads what the parent chose, still with the parent link on.
    mine = as_user("robin").get("/api/users/me/nearby-settings").json()
    assert mine["settings"]["offline"] is True
    assert mine["always_on"] == ["parent_link"]


def test_nobody_else_reads_or_sets_a_members_parts(as_user):
    assert as_user("casey").get("/api/users/robin/nearby-settings").status_code == 403
    assert as_user("casey").put("/api/users/robin/nearby-settings", json={"car": False}).status_code == 403
    # A grown member's are their own, even to a parent.
    assert as_user("mom").get("/api/users/casey/nearby-settings").status_code == 403
    assert as_user("mom").put("/api/users/casey/nearby-settings", json={"car": False}).status_code == 403
    assert as_user("mom").get("/api/users/nobody/nearby-settings").status_code == 404


def test_forget_user_drops_the_row(session):
    nearby_settings.save(session, "casey", {"chat": True}, "casey")
    nearby_settings.forget_user(session, "casey")
    assert session.get(UserNearbySetting, "casey") is None


def test_a_childs_status_carries_their_parts_for_the_phones_guard(as_user):
    as_user("mom").put("/api/users/robin/nearby-settings", json={"chat": True})
    rules = as_user("robin").get("/api/screen-time/robin/rules").json()
    assert rules["nearby"]["chat"] is True
    # A grown member's status has none: their own phone reads Settings.
    assert "nearby" not in as_user("casey").get("/api/screen-time/casey/rules").json()
