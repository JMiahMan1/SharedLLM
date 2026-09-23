import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="
os.environ["DEFAULT_ADMIN_PASSWORD"] = "changeme"

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User, UserThemeSetting


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


def test_get_theme_defaults_to_aurora(client: TestClient):
    resp = client.get("/api/users/me/theme")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["theme_id"] == "aurora"
    assert body["packs"] == []


def test_put_theme_persists_theme_id(client: TestClient):
    resp = client.put("/api/users/me/theme", json={"theme_id": "neon"})
    assert resp.status_code == 200
    assert resp.json()["theme_id"] == "neon"

    again = client.get("/api/users/me/theme")
    assert again.json()["theme_id"] == "neon"


def test_put_theme_rejects_empty_theme_id(client: TestClient):
    resp = client.put("/api/users/me/theme", json={"theme_id": "   "})
    assert resp.status_code == 422


def test_put_theme_rejects_non_string_theme_id(client: TestClient):
    resp = client.put("/api/users/me/theme", json={"theme_id": 42})
    assert resp.status_code == 422


def test_put_theme_rejects_invalid_packs(client: TestClient):
    resp = client.put("/api/users/me/theme", json={"packs": [{"kind": "wrong"}]})
    assert resp.status_code == 422
    resp = client.put(
        "/api/users/me/theme",
        json={"packs": [{"kind": "jarvis.health-theme-pack", "schemaVersion": 99}]},
    )
    assert resp.status_code == 422


def test_put_theme_accepts_valid_packs(client: TestClient):
    pack = {
        "schemaVersion": 1,
        "kind": "jarvis.health-theme-pack",
        "id": "user-theme-pack",
        "name": "User Theme Pack",
        "version": "1.0.0",
        "themes": [
            {
                "schemaVersion": 1,
                "id": "user-theme",
                "name": "User Theme",
                "version": "1.0.0",
                "tokens": {
                    "bg": "#000000",
                    "surface": "#111111",
                    "text": "#FFFFFF",
                    "textMuted": "#AAAAAA",
                    "border": "#333333",
                    "accent": "#863BFF",
                    "onAccent": "#FFFFFF",
                    "progress": "#863BFF",
                    "ring": "#863BFF",
                    "radius": 16,
                },
            }
        ],
    }
    resp = client.put(
        "/api/users/me/theme",
        json={"theme_id": "user-theme", "packs": [pack]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["theme_id"] == "user-theme"
    assert len(body["packs"]) == 1
    assert body["packs"][0]["id"] == "user-theme-pack"


def test_put_theme_ignores_unknown_keys(client: TestClient):
    resp = client.put(
        "/api/users/me/theme",
        json={"theme_id": "bloom", "evil": "nope", "other": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["theme_id"] == "bloom"
    # unknown keys must not be stored
    row = client.app.dependency_overrides  # silence lint about unused if any
    assert "evil" not in resp.json()


def test_theme_stored_per_user_row(client: TestClient, session: Session):
    client.put("/api/users/me/theme", json={"theme_id": "tron"})
    row = session.exec(select(UserThemeSetting)).first()
    assert row is not None
    assert "tron" in (row.data or "")
