"""The internal name roster: names only, internal callers only.

The gateway's trainer scrubs household names out of mission logs with it, so it
must answer the internal secret and nobody else, and carry nothing but names.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

import services.identity.main as identity_main
from services.identity.main import app, get_session
from services.identity.models import User


@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1, username="default", display_name="Admin", is_admin=True, is_system_default=True))
        session.add(User(id=2, username="pp", display_name="Pat Placeholder", nextcloud_user="pat.p",
                         family_role="adult_child", avatar="preset:whale"))
        session.commit()

    def _session():
        with Session(engine) as session:
            yield session

    identity_main.engine = engine
    app.dependency_overrides[get_session] = _session
    yield TestClient(app)
    app.dependency_overrides = {}


def test_the_roster_lists_names_and_nothing_else(client):
    resp = client.get("/api/internal/name-roster", headers={"X-Internal-Secret": identity_main.INTERNAL_SECRET})

    assert resp.status_code == 200, resp.text
    people = {p["username"]: p for p in resp.json()}
    assert people["pp"] == {"username": "pp", "display_name": "Pat Placeholder", "nextcloud_user": "pat.p"}


def test_the_roster_refuses_callers_without_the_internal_secret(client):
    assert client.get("/api/internal/name-roster").status_code in (401, 403)
    assert client.get("/api/internal/name-roster", headers={"X-Internal-Secret": "wrong"}).status_code in (401, 403)
