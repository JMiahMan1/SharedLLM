import json
import os
import time

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity import main as identity_main
from services.identity.main import app, require_internal
from services.identity.models import GlobalSetting

_test_engine = None

def _get_test_engine():
    global _test_engine
    if _test_engine is None:
        _test_engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        SQLModel.metadata.create_all(_test_engine)
    return _test_engine

@pytest.fixture(name="session")
def session_fixture():
    test_engine = _get_test_engine()
    identity_main.engine = test_engine
    with Session(test_engine) as session:
        yield session

@pytest.fixture(name="test_client")
def test_client_fixture(session: Session):
    app.dependency_overrides[require_internal] = lambda: True
    with TestClient(app) as client:
        yield client

def test_telemetry_summary_peak_duration(test_client: TestClient, session: Session):
    entity_id = "sensor.test_power"
    key = f"telemetry_data:{entity_id}"

    # 1. Momentary peak: power is 25W for exactly one point, other points are 10W
    now = time.time()
    data_points = [
        {"recorded_at": now - 300, "power_w": 10.0, "is_available": True},
        {"recorded_at": now - 200, "power_w": 10.0, "is_available": True},
        {"recorded_at": now - 100, "power_w": 25.0, "is_available": True}, # Peak
        {"recorded_at": now,       "power_w": 10.0, "is_available": True},
    ]

    # Save the telemetry data in db
    setting = GlobalSetting(key=key, value=json.dumps(data_points))
    session.add(setting)
    session.commit()

    # Call the API
    resp = test_client.get(
        f"/api/telemetry/summary/{entity_id}",
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["peak_power_w"] == 25.0
    # Peak duration should be 0.0 because it's a momentary peak (only one point >= 95% of peak)
    assert summary["peak_duration_seconds"] == 0.0

    # 2. Sustained peak: power is 25W for two consecutive points, 24W for another (which is >= 23.75W threshold)
    # 24W is 96% of 25W. So it should be included!
    data_points_sustained = [
        {"recorded_at": now - 400, "power_w": 10.0, "is_available": True},
        {"recorded_at": now - 300, "power_w": 24.0, "is_available": True}, # Part of peak (96%)
        {"recorded_at": now - 200, "power_w": 25.0, "is_available": True}, # Peak point
        {"recorded_at": now - 100, "power_w": 25.0, "is_available": True}, # Peak point
        {"recorded_at": now,       "power_w": 10.0, "is_available": True},
    ]

    # Update db
    setting.value = json.dumps(data_points_sustained)
    session.add(setting)
    session.commit()

    resp = test_client.get(
        f"/api/telemetry/summary/{entity_id}",
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert resp.status_code == 200
    summary = resp.json()["summary"]
    assert summary["peak_power_w"] == 25.0
    # Sustained duration should be from now - 300 to now - 100 = 200 seconds!
    assert summary["peak_duration_seconds"] == 200.0
