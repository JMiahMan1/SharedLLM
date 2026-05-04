"""
Test suite for the Identity Microservice (services/identity).
Tests user resolution, credential decryption, and device-to-user mapping.
Related code: services/identity/main.py, services/identity/models.py, services/identity/crypto.py
"""
import os
import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel, Session, select, create_engine
from sqlalchemy.pool import StaticPool

# Set env vars before importing identity modules
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

import identity.main as identity_main
from identity.main import require_internal, resolve_user, set_user_admin
from identity.models import User, DeviceAssignment
from identity.crypto import encrypt
from identity.schemas import ResolveRequest

# Create a dedicated test engine with StaticPool to keep in-memory data across sessions
test_engine = create_engine(
    "sqlite://", 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

@pytest.fixture(autouse=True)
def setup_db():
    identity_main.engine = test_engine
    SQLModel.metadata.drop_all(test_engine)
    SQLModel.metadata.create_all(test_engine)
    
    with Session(test_engine) as session:
        # Default user with shared creds
        default_user = User(
            username="default",
            is_system_default=True,
            nextcloud_url="https://cloud.local",
            nextcloud_user="admin",
            nextcloud_pass_enc=encrypt("shared-cloud-pass"),
            github_url="https://github.example.com",
            github_user="shared-gh",
            github_token_enc=encrypt("shared-gh-token"),
            gitlab_url="https://gitlab.example.com",
            gitlab_user="shared-gl",
            gitlab_token_enc=encrypt("shared-gl-token"),
        )
        # Personal user lacking nextcloud creds
        alice = User(
            username="alice",
            is_system_default=False,
            ha_url="http://ha.local",
            ha_token_enc=encrypt("alice-ha-token"),
        )
        session.add(default_user)
        session.add(alice)
        session.commit()
        
        # Device assignment
        device = DeviceAssignment(device_id="media_player.alice_speaker", user_id=alice.id)
        session.add(device)
        session.commit()
        
    yield
    
    SQLModel.metadata.drop_all(test_engine)

def test_resolve_voice_id():
    with Session(test_engine) as session:
        data = resolve_user(ResolveRequest(voice_id="alice"), session).model_dump()
    assert data["user"] == "alice"
    assert data["ha_url"] == "http://ha.local"
    assert data["ha_token"] == "alice-ha-token" # Decrypted

def test_resolve_credential_merging():
    """Alice has no nextcloud creds, should inherit from default."""
    with Session(test_engine) as session:
        data = resolve_user(ResolveRequest(rag_user="alice"), session).model_dump()
    assert data["user"] == "alice"
    assert data["nextcloud_url"] == "https://cloud.local" # Inherited
    assert data["nextcloud_pass"] == "shared-cloud-pass"  # Inherited & Decrypted
    assert data["github_url"] == "https://github.example.com"
    assert data["github_user"] == "shared-gh"
    assert data["github_token"] == "shared-gh-token"
    assert data["gitlab_url"] == "https://gitlab.example.com"
    assert data["gitlab_user"] == "shared-gl"
    assert data["gitlab_token"] == "shared-gl-token"

def test_resolve_device_id():
    with Session(test_engine) as session:
        data = resolve_user(ResolveRequest(device_id="media_player.alice_speaker"), session).model_dump()
    assert data["user"] == "alice"

def test_resolve_fallback_default():
    with Session(test_engine) as session:
        data = resolve_user(ResolveRequest(rag_user="unknown_user"), session).model_dump()
    assert data["user"] == "default"

def test_unauthorized_internal():
    with pytest.raises(HTTPException) as exc:
        require_internal("wrong-secret")
    assert exc.value.status_code == 403


def test_set_user_admin_flag():
    with Session(test_engine) as session:
        data = set_user_admin("alice", True, session)
    assert data["status"] == "SUCCESS"
    assert data["username"] == "alice"
    assert data["is_admin"] is True

    with Session(test_engine) as session:
        resolved = resolve_user(ResolveRequest(rag_user="alice"), session).model_dump()
    assert resolved["is_admin"] is True


def test_create_user_stores_git_provider_credentials():
    from identity.main import create_user
    from identity.schemas import UserCreate

    with Session(test_engine) as session:
        created = create_user(
            UserCreate(
                username="bob",
                github_url="https://github.com",
                github_user="bob-gh",
                github_token="bob-gh-token",
                gitlab_url="https://gitlab.com",
                gitlab_user="bob-gl",
                gitlab_token="bob-gl-token",
            ),
            session,
            User(username="admin"),
        )

    assert created.github_url == "https://github.com"
    assert created.github_user == "bob-gh"
    assert created.gitlab_url == "https://gitlab.com"
    assert created.gitlab_user == "bob-gl"

    with Session(test_engine) as session:
        resolved = resolve_user(ResolveRequest(rag_user="bob"), session).model_dump()
    assert resolved["github_token"] == "bob-gh-token"
    assert resolved["gitlab_token"] == "bob-gl-token"
