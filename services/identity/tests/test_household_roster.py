"""The household roster, for services that size something by family.

Garden plants a garden for "how many are we feeding"; Identity owns that number
and serves it to the stack's services on the internal secret, so nobody keeps a
second copy that goes stale when a member is added.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

import services.identity.main as main
from services.identity.main import app, require_internal
from services.identity.models import User


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    with Session(engine) as session:
        for username in ("dad", "mom", "kid", "sib"):
            session.add(User(username=username))
        # The stack's own default admin is not a person to feed.
        session.add(User(username="default", is_system_default=True, is_admin=True))
        session.commit()

    app.dependency_overrides[require_internal] = lambda: True
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_roster_is_the_household_size(client):
    resp = client.get("/api/internal/household")

    assert resp.status_code == 200
    body = resp.json()
    assert body["members"] == 4
    assert sorted(body["usernames"]) == ["dad", "kid", "mom", "sib"]
    assert "default" not in body["usernames"]


def test_it_is_not_open_without_the_internal_secret(client):
    app.dependency_overrides.clear()
    resp = TestClient(app).get("/api/internal/household")
    assert resp.status_code in (401, 403)


def test_a_smaller_household_reports_itself(client, monkeypatch):
    """A member leaving is reflected at once: no cached copy on this side."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    with Session(engine) as session:
        session.add(User(username="only"))
        session.commit()

    assert client.get("/api/internal/household").json()["members"] == 1
