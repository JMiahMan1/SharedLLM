"""Profile stats sharing and the admin's usage read (services/identity/profile_sharing.py)."""
import json
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity import profile_sharing
from services.identity.models import (
    AssistantConversation,
    CapabilityGap,
    Device,
    DeviceEvent,
    FeatureUsage,
    User,
    UserProfileSharing,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as session:
        for name in ("pat", "robin"):
            session.add(User(username=name, display_name=name.title()))
        session.commit()
    return eng


@pytest.fixture
def client(engine):
    def get_session():
        with Session(engine) as session:
            yield session

    def require_api_key(x_test_user: str = Header("pat")) -> User:
        with Session(engine) as session:
            user = session.exec(select(User).where(User.username == x_test_user)).first()
            assert user is not None
            session.expunge(user)
            return user

    def require_internal(x_internal_secret: str | None = Header(None)) -> None:
        if x_internal_secret != "ok":
            raise HTTPException(status_code=403, detail="internal only")

    app = FastAPI()
    app.include_router(profile_sharing.build_router(get_session, require_api_key, require_internal))
    return TestClient(app)


def test_private_by_default(client):
    body = client.get("/api/users/me/profile-sharing").json()
    assert body["areas"] == []
    assert body["available"] == list(profile_sharing.PROFILE_SHARE_AREAS)


def test_areas_with_their_own_switch_are_not_offered_here():
    # Health, activity, Wander and Bible already have switches; a second one would conflict.
    for area in ("health", "bible", "wander", "activity", "totals", "sleep"):
        assert area not in profile_sharing.PROFILE_SHARE_AREAS


def test_put_replaces_the_choice_and_keeps_a_fixed_order(client):
    resp = client.put("/api/users/me/profile-sharing", json={"areas": ["recipes", "garden", "garden"]})
    assert resp.status_code == 200
    assert resp.json()["areas"] == ["garden", "recipes"]
    assert client.get("/api/users/me/profile-sharing").json()["areas"] == ["garden", "recipes"]
    assert client.put("/api/users/me/profile-sharing", json={"areas": []}).json()["areas"] == []


def test_put_refuses_unknown_or_malformed_areas(client):
    assert client.put("/api/users/me/profile-sharing", json={"areas": ["bible"]}).status_code == 422
    assert client.put("/api/users/me/profile-sharing", json={"areas": "garden"}).status_code == 422
    assert client.put("/api/users/me/profile-sharing", json={}).status_code == 422


def test_each_person_has_their_own_choice(client):
    client.put("/api/users/me/profile-sharing", json={"areas": ["garden"]}, headers={"X-Test-User": "robin"})
    assert client.get("/api/users/me/profile-sharing").json()["areas"] == []
    internal = client.get("/api/internal/profile-sharing", params={"username": "Robin"}, headers={"X-Internal-Secret": "ok"})
    assert internal.json()["areas"] == ["garden"]


def test_internal_routes_need_the_secret(client):
    assert client.get("/api/internal/profile-sharing", params={"username": "pat"}).status_code == 403
    assert client.get("/api/internal/people/pat/usage").status_code == 403


def test_a_corrupt_row_reads_as_private(engine, client):
    with Session(engine) as session:
        session.add(UserProfileSharing(username="pat", data="not json"))
        session.commit()
    assert client.get("/api/users/me/profile-sharing").json()["areas"] == []


def test_usage_counts_conversations_without_their_text(engine, client):
    now = datetime.now()
    with Session(engine) as session:
        session.add(Device(device_key="phone-1", kind="phone", owner_username="pat", model="Test Phone", app_version="2.0.0",
                           last_seen_at=now.isoformat()))
        session.add(FeatureUsage(username="pat", feature="bible", uses=7))
        session.add(FeatureUsage(username="pat", feature="music", uses=3))
        session.add(CapabilityGap(username="pat", capability="set_oven", request="preheat the oven", occurrences=2))
        for days_ago in (0, 0, 1, 200):
            session.add(AssistantConversation(
                username="pat", turns=json.dumps([{"role": "user", "text": "a private question"}]),
                features=json.dumps(["weather"]), at=(now - timedelta(days=days_ago)).isoformat(),
            ))
        session.add(DeviceEvent(device_key="phone-1", username="pat", event="app_open", at=now.isoformat()))
        session.add(DeviceEvent(device_key="phone-1", username="robin", event="app_open", at=now.isoformat()))
        session.commit()
    body = client.get("/api/internal/people/pat/usage", params={"days": 30}, headers={"X-Internal-Secret": "ok"}).json()
    assert body["devices"][0]["app_version"] == "2.0.0"
    assert [f["feature"] for f in body["features"]] == ["bible", "music"]
    assert body["capability_gaps"][0]["capability"] == "set_oven"
    assert body["conversations"]["total"] == 3  # the one from 200 days ago is outside the window
    assert body["conversations"]["features"] == [{"feature": "weather", "count": 3}]
    assert "a private question" not in json.dumps(body)
    assert body["events"] == {"app_open": 1}


def test_usage_for_an_unknown_person_is_404(client):
    assert client.get("/api/internal/people/nobody/usage", headers={"X-Internal-Secret": "ok"}).status_code == 404


def test_the_real_app_mounts_the_routes():
    # Included routers are not listed in app.routes on this FastAPI, so ask the app:
    # a mounted internal route refuses a caller without the secret instead of 404.
    client = TestClient(identity_main.app)
    assert client.get("/api/internal/profile-sharing", params={"username": "pat"}).status_code in (401, 403)
    assert client.get("/api/internal/people/pat/usage").status_code in (401, 403)
