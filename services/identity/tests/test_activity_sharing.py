import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="
os.environ["DEFAULT_ADMIN_PASSWORD"] = "changeme"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User, UserActivitySharing


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    main.engine = session.bind
    assert main.engine is not None
    SQLModel.metadata.create_all(main.engine)

    from services.identity.seed import seed_from_env
    seed_from_env(session, force=True)

    admin_user = session.exec(select(User).where(User.username == "default")).first()
    assert admin_user is not None
    admin_user.is_admin = True
    session.add(admin_user)
    session.commit()

    app.dependency_overrides[require_api_key] = lambda: admin_user
    app.dependency_overrides[require_internal] = lambda: True

    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


def test_sharing_defaults_to_private(client: TestClient):
    resp = client.get("/api/users/me/activity-sharing")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["enabled"] is False
    assert body["audience"] == "circle"
    assert body["user_ids"] == []
    assert body["share"] == ["totals"]


def test_put_enables_and_persists(client: TestClient):
    payload = {
        "enabled": True,
        "audience": "users",
        "user_ids": ["sam"],
        "share": ["totals", "achievements"],
    }
    resp = client.put("/api/users/me/activity-sharing", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["audience"] == "users"
    assert body["user_ids"] == ["sam"]
    assert body["share"] == ["achievements", "totals"]  # sorted, stable

    again = client.get("/api/users/me/activity-sharing").json()
    assert again["enabled"] is True
    assert again["audience"] == "users"
    assert again["user_ids"] == ["sam"]


def test_put_only_touches_provided_keys(client: TestClient):
    client.put(
        "/api/users/me/activity-sharing",
        json={"enabled": True, "share": ["workouts"]},
    )
    resp = client.put("/api/users/me/activity-sharing", json={"audience": "users"})
    body = resp.json()
    assert body["enabled"] is True  # preserved
    assert body["share"] == ["workouts"]  # preserved
    assert body["audience"] == "users"


def test_put_rejects_invalid_values(client: TestClient):
    assert (
        client.put("/api/users/me/activity-sharing", json={"enabled": "yes"}).status_code
        == 422
    )
    assert (
        client.put("/api/users/me/activity-sharing", json={"audience": "everyone"}).status_code
        == 422
    )
    assert (
        client.put("/api/users/me/activity-sharing", json={"user_ids": "sam"}).status_code
        == 422
    )
    assert (
        client.put("/api/users/me/activity-sharing", json={"share": []}).status_code == 422
    )
    assert (
        client.put("/api/users/me/activity-sharing", json={"share": ["secrets"]}).status_code
        == 422
    )


def test_put_ignores_unknown_keys(client: TestClient):
    resp = client.put(
        "/api/users/me/activity-sharing",
        json={"enabled": True, "evil": "nope"},
    )
    assert resp.status_code == 200
    assert "evil" not in resp.json()


def test_internal_listing_returns_only_saved_rows(client: TestClient):
    resp = client.get("/api/internal/activity-sharing")
    assert resp.status_code == 200
    assert resp.json()["users"] == []  # nobody has opted in yet

    client.put(
        "/api/users/me/activity-sharing",
        json={"enabled": True, "audience": "circle", "share": ["totals"]},
    )
    listing = client.get("/api/internal/activity-sharing").json()["users"]
    assert len(listing) == 1
    assert listing[0]["username"] == "default"
    assert listing[0]["enabled"] is True


def test_sharing_stored_per_user_row(client: TestClient, session: Session):
    client.put("/api/users/me/activity-sharing", json={"enabled": True})
    row = session.exec(select(UserActivitySharing)).first()
    assert row is not None
    assert '"enabled": true' in (row.data or "")
