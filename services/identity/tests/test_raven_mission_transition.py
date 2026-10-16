"""POST /api/raven/missions/{id}/transition: a status change that only
happens while the mission is still in the expected status, so two Approve
taps on a phone notice cannot both queue it."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity.main import app, get_session, require_internal
from services.identity.models import RavenMission


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(RavenMission(id=7, proposed_mission="Fix geo", coding_model="coder", status="pending"))
        session.commit()

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[require_internal] = lambda: None
    yield TestClient(app), engine
    app.dependency_overrides = {}


def _status(engine) -> str:
    with Session(engine) as session:
        return session.get(RavenMission, 7).status


def test_the_first_claim_wins_and_the_second_is_refused(client):
    http, engine = client
    body = {"from_status": "pending", "to_status": "queued"}
    first = http.post("/api/raven/missions/7/transition", json=body)
    assert first.status_code == 200 and first.json()["mission_status"] == "queued"
    second = http.post("/api/raven/missions/7/transition", json=body)
    assert second.status_code == 409 and "already queued" in second.json()["detail"]
    assert _status(engine) == "queued"


def test_an_unknown_mission_is_404(client):
    http, _ = client
    resp = http.post("/api/raven/missions/99/transition", json={"from_status": "pending", "to_status": "queued"})
    assert resp.status_code == 404


def test_it_is_internal_only():
    app.dependency_overrides = {}
    resp = TestClient(app).post("/api/raven/missions/7/transition",
                                json={"from_status": "pending", "to_status": "queued"})
    assert resp.status_code in (401, 403)
