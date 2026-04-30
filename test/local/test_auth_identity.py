# test/local/test_auth_identity.py
import os
import httpx
import pytest
from dotenv import load_dotenv

# Load local .env
load_dotenv()

IDENTITY_URL = "http://localhost:8001"
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

# Expected values from .env for verification
EXPECTED_USER = os.getenv("HA_DEFAULT_USER", "Summers")
EXPECTED_HA_URL = os.getenv("HA_URL")
EXPECTED_HA_TOKEN = os.getenv("HA_TOKEN")
EXPECTED_NC_USER = os.getenv("NEXTCLOUD_USER")

@pytest.fixture
def client():
    return httpx.Client(timeout=10.0)

def test_identity_resolution_from_db(client):
    """
    Test: Verify that the Identity service correctly resolves the system default user
    and decrypts their credentials from the SQLite database.
    """
    print(f"\n[Test] Testing identity resolution for default user...")
    
    payload = {
        "rag_user": None,
        "voice_id": None,
        "device_id": None
    }
    
    resp = client.post(
        f"{IDENTITY_URL}/api/resolve",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    
    assert resp.status_code == 200, f"Identity resolution failed: {resp.text}"
    data = resp.json()
    
    print(f"[Test] Resolved User: {data.get('user')}")
    assert data.get("user") == EXPECTED_USER
    
    # Verification of decrypted credentials
    print(f"[Test] Verifying decrypted HA Token...")
    assert data.get("ha_url") == EXPECTED_HA_URL
    assert data.get("ha_token") == EXPECTED_HA_TOKEN
    
    print(f"[Test] Verifying decrypted Nextcloud Credentials...")
    assert data.get("nextcloud_user") == EXPECTED_NC_USER
    assert data.get("nextcloud_pass") is not None
    
    print(f"[Test] Identity and Database Auth verification successful.")

def test_identity_resolution_by_voice_id(client):
    """
    Test: Verify that a specific username (acting as voice_id) can be resolved.
    """
    print(f"\n[Test] Testing resolution via voice_id='{EXPECTED_USER}'...")
    
    payload = {
        "voice_id": EXPECTED_USER
    }
    
    resp = client.post(
        f"{IDENTITY_URL}/api/resolve",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    
    assert resp.status_code == 200
    assert resp.json().get("user") == EXPECTED_USER
    print(f"[Test] Voice ID resolution successful.")

if __name__ == "__main__":
    pytest.main([__file__, "-s"])
