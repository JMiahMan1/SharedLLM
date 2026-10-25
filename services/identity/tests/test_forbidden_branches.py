"""GitHub "Protected Branches" round-trips through Identity.

The UI sends ``forbidden_branches`` (Identity.tsx) and the workspace runtime
reads it back from the resolved identity
(services/workspace_runtime/main.py:1446), but Identity had no storage, so the
runtime's default patterns always applied. These pin the store/return contract
(docs/INTEGRATIONS.md, GitHub > Protected Branches).
"""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import User


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1, username="default", display_name="Admin", is_admin=True, is_system_default=True))
        session.add(User(id=2, username="michele", display_name="Michele"))
        session.commit()
        yield session


def _act_as(username: str) -> None:
    def _require(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _require
    app.dependency_overrides[require_internal] = lambda: True


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    _act_as("michele")
    yield TestClient(app)
    app.dependency_overrides = {}


def test_protected_branches_round_trip_as_a_list(client):
    resp = client.patch("/api/users/me", json={"forbidden_branches": ["main", "release/*"]})
    assert resp.status_code == 200
    assert resp.json()["forbidden_branches"] == ["main", "release/*"]
    assert client.get("/api/users/me").json()["forbidden_branches"] == ["main", "release/*"]


def test_omitting_protected_branches_leaves_them_alone(client):
    client.patch("/api/users/me", json={"forbidden_branches": ["main"]})
    client.patch("/api/users/me", json={"github_user": "michele"})
    assert client.get("/api/users/me").json()["forbidden_branches"] == ["main"]


def test_clear_fields_erases_protected_branches(client):
    client.patch("/api/users/me", json={"forbidden_branches": ["main"]})
    cleared = client.patch("/api/users/me", json={"clear_fields": ["forbidden_branches"]})
    assert cleared.json()["forbidden_branches"] == []


def test_resolve_carries_protected_branches_to_the_runtime(client):
    client.patch("/api/users/me", json={"forbidden_branches": ["main", "master"]})

    resp = client.post("/api/resolve", json={"rag_user": "michele"})

    assert resp.status_code == 200
    assert resp.json()["forbidden_branches"] == ["main", "master"]
