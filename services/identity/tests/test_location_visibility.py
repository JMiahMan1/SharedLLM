"""The family map's location feed only returns people the viewer may see.

It used to hand every member's last position to every signed-in user, past
the sharing consent geo's people feed applies.
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

import services.identity.main as main
from services.identity.main import app
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
            session.add(GlobalSetting(key=f"user_location:{name}", value=json.dumps({"latitude": 33.4, "longitude": -112.0})))
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
