"""The internal secret is a header of its own, and nothing else.

Observed: `require_internal` and `require_admin_or_internal` also accepted
`Authorization: Bearer <INTERNAL_SECRET>`. That is the header every browser and
phone sends their *own* key in, so the household's master secret could travel in
the one header that ends up in logs, bug reports and curl examples. It has its
own header; the Bearer form is refused, and a real administrator's key still
works because that is what it is for.
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity.main import app, get_session, require_internal
from services.identity.models import User

SECRET = os.environ["INTERNAL_SECRET"]


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="default", is_admin=True, is_system_default=True, api_key="key-default"))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_the_secret_is_accepted_in_its_own_header(client):
    resp = client.post("/api/resolve", json={"household": True}, headers={"X-Internal-Secret": SECRET})
    assert resp.status_code == 200


def test_the_secret_is_refused_as_a_bearer_token(client):
    resp = client.post(
        "/api/resolve", json={"household": True}, headers={"Authorization": f"Bearer {SECRET}"}
    )
    assert resp.status_code in (401, 403)


def test_an_anonymous_caller_is_refused(client):
    resp = client.post("/api/resolve", json={"household": True})
    assert resp.status_code in (401, 403)
