"""Calendar settings carry the phone reminder lead and time zone; the home
alert mapping is a seeded setting."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import DEFAULT_GLOBAL_SETTINGS, User


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    main.engine = session.bind
    from services.identity.seed import seed_from_env
    seed_from_env(session, force=True)
    user = session.exec(select(User).where(User.username == "default")).first()
    app.dependency_overrides[require_api_key] = lambda: user
    app.dependency_overrides[require_internal] = lambda: True
    yield TestClient(app)
    app.dependency_overrides = {}


def test_reminder_minutes_and_timezone_are_kept_with_the_rest(client):
    client.put("/api/calendar/settings", json={"ical_urls": ["https://x/feed.ics"]})
    resp = client.put("/api/calendar/settings", json={"reminder_minutes": 15, "timezone": "America/New_York"})
    assert resp.status_code == 200
    settings = client.get("/api/calendar/settings").json()["settings"]
    assert settings == {"ical_urls": ["https://x/feed.ics"], "reminder_minutes": 15, "timezone": "America/New_York"}
    assert client.put("/api/calendar/settings", json={"reminder_minutes": 0}).status_code == 200


@pytest.mark.parametrize("body", [
    {"reminder_minutes": -5}, {"reminder_minutes": 5000}, {"reminder_minutes": "30"}, {"reminder_minutes": True},
    {"timezone": "Mars/Base"},
])
def test_nonsense_is_refused_and_nothing_is_stored(client, body):
    assert client.put("/api/calendar/settings", json=body).status_code == 422
    assert client.get("/api/calendar/settings").json()["settings"] == {}


def test_the_home_alert_mapping_is_a_default_setting():
    (row,) = [s for s in DEFAULT_GLOBAL_SETTINGS if s["key"] == "home_alert_entities"]
    assert row["value"] == "[]"
