"""Push data in identity (docs/PUSH.md): topics, preferences, UnifiedPush."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity import push
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import UnifiedPushRegistration, User, UserNotificationPreference


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="michele", display_name="Michele", is_admin=False))
        session.add(User(username="jeremiah", display_name="Jeremiah", is_admin=True))
        session.add(User(username="mom", display_name="Mom", family_role="mother"))
        session.add(User(username="gracie", display_name="Gracie", family_role="teen", mother_username="mom"))
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


def test_topic_is_created_once_and_kept(as_user, session):
    client = as_user("michele")
    first = client.get("/api/internal/push/topic", params={"username": "michele"}).json()["topic"]
    again = client.get("/api/internal/push/topic", params={"username": "michele"}).json()["topic"]
    assert first == again
    assert re.fullmatch(r"jarvis-michele-[A-Za-z0-9_-]{24}", first)
    stored = session.exec(select(User).where(User.username == "michele")).first()
    session.refresh(stored)
    assert stored.push_topic == first


def test_topic_for_an_unknown_person_is_404(as_user):
    assert as_user("michele").get("/api/internal/push/topic", params={"username": "nobody"}).status_code == 404


def test_topics_are_valid_ntfy_names_for_any_username():
    assert re.fullmatch(r"[-_A-Za-z0-9]{1,64}", push.new_push_topic("a.b c@d" * 10))
    assert re.fullmatch(r"up[-_A-Za-z0-9]{32}", push.new_up_topic())


def test_preferences_default_on_and_admin_only_for_admins(as_user):
    body = as_user("michele").get("/api/users/me/notification-preferences").json()
    assert "admin" not in body["preferences"]
    assert all(body["preferences"].values())
    assert set(body["preferences"]) == set(push.CATEGORIES) - {"admin"}

    admin = as_user("jeremiah").get("/api/users/me/notification-preferences").json()
    assert admin["preferences"]["admin"] is True


def test_put_changes_only_the_named_switches(as_user):
    client = as_user("michele")
    resp = client.put("/api/users/me/notification-preferences", json={"bible": False})
    assert resp.status_code == 200
    assert resp.json()["preferences"]["bible"] is False
    client.put("/api/users/me/notification-preferences", json={"preferences": {"jobs": False}})
    prefs = client.get("/api/users/me/notification-preferences").json()["preferences"]
    assert prefs["bible"] is False and prefs["jobs"] is False and prefs["chat"] is True


def test_put_rejects_unknown_categories_and_non_booleans(as_user):
    client = as_user("michele")
    assert client.put("/api/users/me/notification-preferences", json={"nope": False}).status_code == 422
    assert client.put("/api/users/me/notification-preferences", json={"chat": "off"}).status_code == 422
    # admin is not a switch a non-admin has
    assert client.put("/api/users/me/notification-preferences", json={"admin": True}).status_code == 422


def test_internal_preferences_cover_every_category(as_user):
    client = as_user("jeremiah")
    client.put("/api/users/me/notification-preferences", json={"raven": False})
    body = client.get("/api/internal/push/preferences", params={"username": "jeremiah"}).json()
    assert set(body["preferences"]) == set(push.CATEGORIES)
    assert body["preferences"]["raven"] is False and body["is_admin"] is True

    other = client.get("/api/internal/push/preferences", params={"username": "michele"}).json()
    assert other["preferences"]["admin"] is False and other["is_admin"] is False


def test_admins_lists_only_admins(as_user):
    assert as_user("michele").get("/api/internal/push/admins").json() == {"admins": ["jeremiah"]}


def _reg(**over):
    body = {"username": "michele", "device_id": "pixel-1", "app": "com.nextcloud.talk2", "token": "tok-1"}
    body.update(over)
    return body


def test_up_register_is_idempotent_and_listed(as_user):
    client = as_user("michele")
    first = client.post("/api/internal/push/up/register", json=_reg()).json()
    again = client.post("/api/internal/push/up/register", json=_reg()).json()
    assert first["topic"] == again["topic"] and first["topic"].startswith("up")
    other = client.post("/api/internal/push/up/register", json=_reg(device_id="tablet")).json()
    assert other["topic"] != first["topic"]

    everything = client.get("/api/internal/push/up", params={"username": "michele"}).json()["registrations"]
    assert [r["device_id"] for r in everything] == ["pixel-1", "tablet"]
    one = client.get("/api/internal/push/up", params={"username": "michele", "device_id": "tablet"}).json()
    assert [r["topic"] for r in one["registrations"]] == [other["topic"]]


def test_up_unregister_removes_only_that_registration(as_user, session):
    client = as_user("michele")
    client.post("/api/internal/push/up/register", json=_reg())
    client.post("/api/internal/push/up/register", json=_reg(app="org.matrix.client"))
    resp = client.post("/api/internal/push/up/unregister", json=_reg()).json()
    assert resp["removed"] == 1
    left = session.exec(select(UnifiedPushRegistration)).all()
    assert [r.app for r in left] == ["org.matrix.client"]


def test_up_unregister_without_a_device_matches_app_and_token(as_user, session):
    client = as_user("michele")
    client.post("/api/internal/push/up/register", json=_reg(device_id="tablet"))
    body = {"username": "michele", "app": "com.nextcloud.talk2", "token": "tok-1"}
    assert client.post("/api/internal/push/up/unregister", json=body).json()["removed"] == 1
    assert session.exec(select(UnifiedPushRegistration)).all() == []


@pytest.mark.parametrize("bad", [
    {"device_id": ""}, {"device_id": "a b"}, {"app": "x/y"}, {"token": ""}, {"token": "t" * 1025}, {"username": ""},
])
def test_up_register_validates(as_user, bad):
    assert as_user("michele").post("/api/internal/push/up/register", json=_reg(**bad)).status_code == 422


def test_up_register_for_unknown_person_is_404(as_user):
    assert as_user("michele").post("/api/internal/push/up/register", json=_reg(username="nobody")).status_code == 404


def test_forget_user_drops_their_push_data(as_user, session):
    client = as_user("michele")
    client.put("/api/users/me/notification-preferences", json={"chat": False})
    client.post("/api/internal/push/up/register", json=_reg())
    push.forget_user(session, "michele")
    assert session.exec(select(UserNotificationPreference)).all() == []
    assert session.exec(select(UnifiedPushRegistration)).all() == []


def test_internal_routes_need_the_internal_secret(session):
    main.engine = session.bind
    app.dependency_overrides = {}
    client = TestClient(app)
    for path in ("/api/internal/push/topic?username=michele", "/api/internal/push/admins",
                 "/api/internal/push/preferences?username=michele", "/api/internal/push/up?username=michele"):
        assert client.get(path).status_code in (401, 403), path
    assert client.post("/api/internal/push/up/register", json=_reg()).status_code in (401, 403)
    assert client.get("/api/users/me/notification-preferences").status_code == 401


def test_internal_put_switches_one_persons_category(as_user):
    """Geo carries its old presence switch over through this."""
    client = as_user("jeremiah")
    body = client.put("/api/internal/push/preferences", params={"username": "michele"},
                      json={"family_presence": False}).json()
    assert body["preferences"]["family_presence"] is False and body["preferences"]["chat"] is True
    assert client.put("/api/internal/push/preferences", params={"username": "michele"},
                      json={"nonsense": False}).status_code == 422
    assert client.put("/api/internal/push/preferences", params={"username": "nobody"},
                      json={"family_presence": False}).status_code == 404


def test_a_minor_cannot_switch_off_a_notice_their_parent_has_not_allowed(as_user):
    client = as_user("gracie")
    body = client.get("/api/users/me/notification-preferences").json()
    assert set(body["locked"]) == set(push.CATEGORIES) - {"admin"}
    resp = client.put("/api/users/me/notification-preferences",
                      json={"family_presence": False, "chat": False})
    assert resp.status_code == 200
    assert resp.json()["preferences"]["family_presence"] is True
    assert resp.json()["preferences"]["chat"] is True


def test_a_parents_allow_list_unlocks_only_the_named_categories(as_user):
    client = as_user("gracie")
    # Default (no rule): even the harmless switch is locked.
    resp = client.get("/api/users/me/notification-preferences").json()
    assert "family_presence" in resp["locked"]
    # A parent allows the arrivals switch for the teen.
    parent = as_user("mom")
    put = parent.put("/api/screen-time/gracie/rules",
                     json={"notification_toggle_allowed": ["family_presence"]})
    assert put.status_code == 200
    assert put.json()["rules"]["notification_toggle_allowed"] == ["family_presence"]
    client = as_user("gracie")  # the fixture rebinds the caller globally per client
    resp = client.get("/api/users/me/notification-preferences").json()
    assert "family_presence" not in resp["locked"]
    assert "chat" in resp["locked"]
    resp = client.put("/api/users/me/notification-preferences", json={"family_presence": False})
    assert resp.json()["preferences"]["family_presence"] is False
    # Switching the other one off is still refused (ignored, not errored).
    resp = client.put("/api/users/me/notification-preferences", json={"chat": False})
    assert resp.json()["preferences"]["chat"] is True


def test_family_safety_can_never_be_allowed_for_a_minor(as_user):
    parent = as_user("mom")
    assert parent.put("/api/screen-time/gracie/rules",
                      json={"notification_toggle_allowed": ["family_safety"]}).status_code == 422
    assert parent.put("/api/screen-time/gracie/rules",
                      json={"notification_toggle_allowed": ["admin"]}).status_code == 422
    assert parent.put("/api/screen-time/gracie/rules",
                      json={"notification_toggle_allowed": ["nonsense"]}).status_code == 422


def test_grown_ups_are_not_locked(as_user):
    body = as_user("michele").get("/api/users/me/notification-preferences").json()
    assert body["locked"] == []


def test_unknown_ids_in_a_stored_allow_list_are_dropped(as_user, session):
    """A row written outside the validator cannot loosen a lock: unknown and
    safety categories are dropped when the allow-list is read."""
    from services.identity.models import ScreenRules

    session.add(ScreenRules(username="gracie", data='{"notification_toggle_allowed": ["family_presence", "family_safety", "nope"]}'))
    session.commit()
    body = as_user("gracie").get("/api/users/me/notification-preferences").json()
    assert body["locked"] == [c for c in body["locked"] if c != "family_presence"]
    assert "family_presence" not in body["locked"]
    assert "family_safety" in body["locked"]


def test_a_parent_can_disable_and_enable_notifications_for_a_minor(as_user):
    parent = as_user("mom")
    put = parent.put(
        "/api/screen-time/gracie/rules",
        json={"notification_disabled": ["raven", "jobs"], "notification_toggle_allowed": ["bible"]},
    )
    assert put.status_code == 200
    assert put.json()["rules"]["notification_disabled"] == ["raven", "jobs"]
    assert put.json()["rules"]["notification_toggle_allowed"] == ["bible"]

    # Safety and admin categories cannot be disabled for a minor.
    assert parent.put("/api/screen-time/gracie/rules", json={"notification_disabled": ["family_safety"]}).status_code == 422
    assert parent.put("/api/screen-time/gracie/rules", json={"notification_disabled": ["admin"]}).status_code == 422
    assert parent.put("/api/screen-time/gracie/rules", json={"notification_disabled": ["nonsense"]}).status_code == 422

    # For the minor, disabled categories are off and locked; unlocked categories can be toggled; others are locked on.
    client = as_user("gracie")
    resp = client.get("/api/users/me/notification-preferences").json()
    assert resp["preferences"]["raven"] is False
    assert resp["preferences"]["jobs"] is False
    assert resp["preferences"]["chat"] is True
    assert resp["preferences"]["bible"] is True
    assert "raven" in resp["locked"]
    assert "jobs" in resp["locked"]
    assert "bible" not in resp["locked"]

    # The minor cannot turn a parent-disabled category back on, but can switch off an allowed category.
    resp = client.put("/api/users/me/notification-preferences", json={"raven": True, "bible": False}).json()
    assert resp["preferences"]["raven"] is False
    assert resp["preferences"]["bible"] is False

