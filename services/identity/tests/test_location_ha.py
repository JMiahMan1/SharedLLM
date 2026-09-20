import os
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

from services.identity import main as identity_main
from services.identity.main import app, _forward_location_to_ha, LocationUpdate
from services.identity.models import User, GlobalSetting
from services.identity.seed import seed_from_env

@pytest.fixture(name="test_client")
def client_fixture():
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(test_engine)
    identity_main.engine = test_engine
    with Session(test_engine) as session:
        seed_from_env(session, force=True)
        user = session.exec(select(User).where(User.username == "default")).first()
        user.ha_url = "http://mock-ha:8123"
        user.ha_token_enc = "mocked-token-enc"
        session.add(user)
        session.commit()
    return TestClient(app)

def test_update_user_location_stores_and_responds(test_client):
    response = test_client.post(
        "/api/users/default/location",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "latitude": 37.7749,
            "longitude": -122.4194,
            "accuracy": 15,
            "speed": 5.5,
            "bearing": 180.0,
            "battery": 85,
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"

    # Verify retrieval
    get_resp = test_client.get(
        "/api/users/default/location",
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert get_resp.status_code == 200
    loc_data = get_resp.json()
    assert loc_data["latitude"] == 37.7749
    assert loc_data["longitude"] == -122.4194
    assert loc_data["battery"] == 85

@pytest.mark.asyncio
async def test_forward_location_to_ha_calls_device_tracker_see():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value="[]")

    mock_client = MagicMock()
    mock_client.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_client.post.return_value.__aexit__ = AsyncMock(return_value=None)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_get_client():
        yield mock_client

    update = LocationUpdate(
        latitude=34.0522,
        longitude=-118.2437,
        accuracy=10,
        speed=1.2,
        bearing=90.0,
        battery=92,
    )

    with patch("services.identity.main.get_client_insecure", mock_get_client), \
         patch("services.identity.main.decrypt", return_value="decrypted-ha-token"):
        await _forward_location_to_ha("default", update)

    assert mock_client.post.called
    called_url = mock_client.post.call_args[0][0]
    assert "device_tracker/see" in called_url
    called_json = mock_client.post.call_args[1]["json"]
    assert called_json["gps"] == [34.0522, -118.2437]
    assert called_json["battery"] == 92
    assert "dev_id" in called_json
