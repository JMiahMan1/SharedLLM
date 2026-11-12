"""Telemetry snapshots carry the outdoor conditions, and the analysis sees them (docs/WEATHER.md)."""
import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app
from services.identity.models import GlobalSetting


@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    yield TestClient(app), engine


def secret():
    return {"X-Internal-Secret": os.getenv("INTERNAL_SECRET", main.INTERNAL_SECRET)}


def test_snapshots_keep_outdoor_conditions_only_when_given(client):
    http, engine = client
    for body in ({"entity_id": "switch.heater", "power_w": 1500, "outdoor_temp": 28.4, "weather": "snow"},
                 {"entity_id": "switch.heater", "power_w": 0}):
        assert http.post("/api/telemetry/snapshot", json=body, headers=secret()).status_code == 200
    with Session(engine) as s:
        row = s.exec(select(GlobalSetting).where(GlobalSetting.key == "telemetry_data:switch.heater")).first()
        points = json.loads(row.value)
    assert points[0]["outdoor_temp"] == 28.4 and points[0]["weather"] == "snow"
    assert "outdoor_temp" not in points[1] and "weather" not in points[1]


def test_a_person_sets_and_clears_their_own_weather_place(client):
    from sqlmodel import Session as S

    from services.identity.main import require_api_key
    from services.identity.models import User

    http, engine = client
    with S(engine) as s:
        s.add(User(username="jordan", hashed_password="x"))
        s.commit()
        jordan = s.exec(select(User).where(User.username == "jordan")).first()
    app.dependency_overrides[require_api_key] = lambda: jordan
    try:
        assert http.get("/api/users/me/weather").json() == {"status": "SUCCESS", "location": None, "units": None,
                                                         "follow": False, "live": False}
        got = http.put("/api/users/me/weather", json={"location": {"name": "Grandma's", "latitude": 35.12345,
                                                                   "longitude": -85.3}, "units": "metric"}).json()
        assert got["location"] == {"name": "Grandma's", "latitude": 35.1234, "longitude": -85.3}
        assert got["units"] == "metric"
        assert http.put("/api/users/me/weather", json={"location": {"latitude": 99, "longitude": 0}}).status_code == 422
        assert http.put("/api/users/me/weather", json={"units": "kelvin"}).status_code == 422
        internal = http.get("/api/internal/weather/prefs?username=jordan", headers=secret()).json()
        assert internal["units"] == "metric"
        cleared = http.put("/api/users/me/weather", json={"location": None}).json()
        assert cleared["location"] is None and cleared["units"] == "metric"
    finally:
        app.dependency_overrides = {}


def test_following_the_phone_uses_its_recent_fix_else_the_chosen_place(client):
    import time

    from services.identity.main import require_api_key
    from services.identity.models import User

    http, engine = client
    with Session(engine) as s:
        s.add(User(username="taylor", hashed_password="x"))
        s.commit()
        taylor = s.exec(select(User).where(User.username == "taylor")).first()

    def put_fix(age_s):
        with Session(engine) as s:
            key = "user_location:taylor"
            row = s.exec(select(GlobalSetting).where(GlobalSetting.key == key)).first() or GlobalSetting(key=key)
            row.value = json.dumps({"latitude": 36.16271, "longitude": -86.78159, "timestamp": time.time() - age_s})
            s.add(row)
            s.commit()

    internal = lambda: http.get("/api/internal/weather/prefs?username=taylor", headers=secret()).json()  # noqa: E731
    app.dependency_overrides[require_api_key] = lambda: taylor
    try:
        got = http.put("/api/users/me/weather", json={"follow": True, "location": {"name": "Barn", "latitude": 35,
                                                                                    "longitude": -85}}).json()
        assert got["follow"] is True and got["live"] is False  # no fix yet
        assert internal()["location"]["name"] == "Barn" and internal()["live"] is False

        put_fix(60)
        assert http.get("/api/users/me/weather").json()["live"] is True
        live = internal()
        assert live["live"] is True
        assert live["location"] == {"name": "Where you are", "latitude": 36.16, "longitude": -86.78}

        put_fix(7 * 3600)  # stale: back to the chosen place
        assert internal()["location"]["name"] == "Barn"

        put_fix(60)
        assert http.put("/api/users/me/location-pause", json={"minutes": 60}).status_code == 200
        assert internal()["location"]["name"] == "Barn"  # paused sharing is respected

        assert http.put("/api/users/me/weather", json={"follow": "yes"}).status_code == 422
        off = http.put("/api/users/me/weather", json={"follow": False}).json()
        assert off["follow"] is False and internal()["location"]["name"] == "Barn"
    finally:
        app.dependency_overrides = {}
