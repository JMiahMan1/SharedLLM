"""The family map's location feed only returns people the viewer may see.

It used to hand every member's last position to every signed-in user, past
the sharing consent geo's people feed applies.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key
from services.identity.models import GlobalSetting, User, UserActivitySharing

HEADERS = {"X-Internal-Secret": "test-secret"}


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        for person in (
            User(username="dad", family_role="father"),
            User(username="mom", family_role="mother"),
            User(username="kid", family_role="child"),
            User(username="grown", family_role="adult_child"),
            User(username="aunt", family_role="other"),
            User(username="hermit", family_role="other"),
        ):
            session.add(person)
        sharing = {
            "kid": {"enabled": True, "audience": "circle"},
            "grown": {"enabled": True, "audience": "users", "user_ids": ["aunt"]},
            "hermit": {"enabled": False, "audience": "circle"},
        }
        for name, data in sharing.items():
            session.add(UserActivitySharing(username=name, data=json.dumps(data)))
        for name in ("dad", "mom", "kid", "grown", "aunt", "hermit", "default"):
            session.add(GlobalSetting(key=f"user_location:{name}", value=json.dumps({"latitude": 40.0, "longitude": -105.5})))
        session.commit()
    return TestClient(app)


def _seen(client, **params):
    resp = client.get("/api/users/location/all", params=params, headers=HEADERS)
    assert resp.status_code == 200
    return sorted(resp.json())


def test_a_service_still_gets_everyone(client):
    assert _seen(client) == ["aunt", "dad", "default", "grown", "hermit", "kid", "mom"]


def test_an_admin_sees_everyone(client):
    assert "hermit" in _seen(client, viewer="aunt", is_admin="true")


def test_the_viewer_sees_themselves_and_whoever_shares_with_them(client):
    # kid shares with the circle; grown names aunt; hermit shares with no one.
    assert _seen(client, viewer="aunt") == ["aunt", "grown", "kid"]


def test_parents_see_each_other_and_their_children(client):
    assert _seen(client, viewer="mom") == ["dad", "kid", "mom"]


def test_no_one_but_an_admin_gets_the_unowned_default_row(client):
    assert "default" not in _seen(client, viewer="dad")
    assert "hermit" not in _seen(client, viewer="dad")


# --- pausing location sharing ------------------------------------------------

def _as(client, name):
    def caller():
        with Session(main.engine) as s:
            return s.exec(select(User).where(User.username == name)).first()
    app.dependency_overrides[require_api_key] = caller
    return client


@pytest.fixture
def paused_client(client):
    yield client
    app.dependency_overrides = {}


def test_an_adult_pauses_and_the_family_sees_paused_not_where(paused_client):
    c = _as(paused_client, "grown")
    assert c.put("/api/users/me/location-pause", json={"minutes": 60}).json()["paused"] is True
    seen = c.get("/api/users/location/all", params={"viewer": "aunt"}, headers=HEADERS).json()
    assert seen["grown"] == {"paused": True, "paused_until": seen["grown"]["paused_until"]}
    assert "latitude" not in seen["grown"]
    # Admins too: a pause is the person's own privacy.
    assert "latitude" not in c.get("/api/users/location/all", params={"viewer": "dad", "is_admin": "true"}, headers=HEADERS).json()["grown"]
    # The person still sees their own position, and a service still gets it.
    assert "latitude" in c.get("/api/users/location/all", params={"viewer": "grown"}, headers=HEADERS).json()["grown"]
    assert "latitude" in c.get("/api/users/location/all", headers=HEADERS).json()["grown"]
    # Back on.
    c.delete("/api/users/me/location-pause")
    assert "latitude" in c.get("/api/users/location/all", params={"viewer": "aunt"}, headers=HEADERS).json()["grown"]


def test_children_and_teens_cannot_pause(paused_client):
    c = _as(paused_client, "kid")
    assert c.put("/api/users/me/location-pause", json={"minutes": None}).status_code == 403
    assert c.get("/api/users/me/location-pause").json() == {"paused": False, "until": None, "allowed": False}


def test_a_pause_that_ran_out_is_over(paused_client):
    import json as _json
    import time as _time

    with Session(main.engine) as s:
        s.add(GlobalSetting(key="location_pause:grown", value=_json.dumps({"until": _time.time() - 5})))
        s.commit()
    seen = paused_client.get("/api/users/location/all", params={"viewer": "aunt"}, headers=HEADERS).json()
    assert "latitude" in seen["grown"]


def test_geo_hears_who_is_paused(paused_client):
    c = _as(paused_client, "grown")
    c.put("/api/users/me/location-pause", json={"minutes": None})
    app.dependency_overrides[main.require_internal] = lambda: True
    users = {u["username"]: u for u in c.get("/api/internal/activity-sharing").json()["users"]}
    assert users["grown"]["paused"] is True and users["kid"]["paused"] is False
