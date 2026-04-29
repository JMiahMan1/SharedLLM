import os
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, select

# Set env vars before importing identity modules
os.environ["IDENTITY_DATABASE_URL"] = "sqlite:///:memory:"
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

from identity.main import app, get_session, engine
from identity.models import User, DeviceAssignment
from identity.crypto import encrypt

def override_get_session():
    with Session(engine) as session:
        yield session

app.dependency_overrides[get_session] = override_get_session

@pytest.fixture(autouse=True)
def setup_db():
    SQLModel.metadata.create_all(engine)

    
    with Session(engine) as session:
        # Default user with shared creds
        default_user = User(
            username="default",
            is_system_default=True,
            nextcloud_url="https://cloud.local",
            nextcloud_user="admin",
            nextcloud_pass_enc=encrypt("shared-cloud-pass")
        )
        # Personal user lacking nextcloud creds
        alice = User(
            username="alice",
            is_system_default=False,
            ha_url="http://ha.local",
            ha_token_enc=encrypt("alice-ha-token")
        )
        session.add(default_user)
        session.add(alice)
        session.commit()
        
        # Device assignment
        device = DeviceAssignment(device_id="media_player.alice_speaker", user_id=alice.id)
        session.add(device)
        session.commit()
        
    yield
    SQLModel.metadata.drop_all(engine)

client = TestClient(app)

def test_resolve_voice_id():
    resp = client.post(
        "/api/resolve", 
        json={"voice_id": "alice"},
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"] == "alice"
    assert data["ha_url"] == "http://ha.local"
    assert data["ha_token"] == "alice-ha-token" # Decrypted

def test_resolve_credential_merging():
    """Alice has no nextcloud creds, should inherit from default."""
    resp = client.post(
        "/api/resolve", 
        json={"rag_user": "alice"},
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"] == "alice"
    assert data["nextcloud_url"] == "https://cloud.local" # Inherited
    assert data["nextcloud_pass"] == "shared-cloud-pass"  # Inherited & Decrypted

def test_resolve_device_id():
    resp = client.post(
        "/api/resolve", 
        json={"device_id": "media_player.alice_speaker"},
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"] == "alice"

def test_resolve_fallback_default():
    resp = client.post(
        "/api/resolve", 
        json={"rag_user": "unknown_user"},
        headers={"X-Internal-Secret": "test-secret"}
    )
    assert resp.status_code == 200
    assert resp.json()["user"] == "default"

def test_unauthorized_internal():
    resp = client.post("/api/resolve", json={"rag_user": "alice"})
    # Missing required header X-Internal-Secret throws 422 Unprocessable Entity in FastAPI
    assert resp.status_code == 422
