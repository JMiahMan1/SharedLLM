# test/local/test_auth_identity.py
import os
import httpx
import pytest
from dotenv import load_dotenv

# Load local .env
load_dotenv()

LIVE_TEST_URL = os.getenv("LIVE_TEST_URL")
IDENTITY_URL = LIVE_TEST_URL if LIVE_TEST_URL else "http://localhost:8001"
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

# Expected values from .env for verification
EXPECTED_USER = os.getenv("HA_DEFAULT_USER", "Summers")
EXPECTED_HA_URL = os.getenv("HA_URL")
EXPECTED_HA_TOKEN = os.getenv("HA_TOKEN")
EXPECTED_NC_USER = os.getenv("NEXTCLOUD_USER")

@pytest.fixture
def client():
    return httpx.Client(timeout=10.0)

@pytest.mark.local_only
@pytest.mark.skipif("LIVE_TEST_URL" not in os.environ, reason="Requires running Identity service")
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
    
    resolved_user = data.get("user")
    print(f"[Test] Resolved User: {resolved_user}")
    assert resolved_user in (EXPECTED_USER, "default")
    
    # Verification of decrypted credentials
    print(f"[Test] Verifying decrypted HA Token...")
    assert data.get("ha_url") == EXPECTED_HA_URL
    assert data.get("ha_token") == EXPECTED_HA_TOKEN
    
    print(f"[Test] Verifying decrypted Nextcloud Credentials...")
    assert data.get("nextcloud_user") == EXPECTED_NC_USER
    assert data.get("nextcloud_pass") is not None
    
    print(f"[Test] Identity and Database Auth verification successful.")

@pytest.mark.local_only
@pytest.mark.skipif("LIVE_TEST_URL" not in os.environ, reason="Requires running Identity service")
def test_identity_resolution_by_voice_id(client):
    """
    Test: Verify that a specific username (acting as voice_id) can be resolved.
    """
    # Resolve the actual username first
    resp_default = client.post(
        f"{IDENTITY_URL}/api/resolve",
        json={},
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    assert resp_default.status_code == 200
    actual_user = resp_default.json().get("user")
    
    print(f"\n[Test] Testing resolution via voice_id='{actual_user}'...")
    
    payload = {
        "voice_id": actual_user
    }
    
    resp = client.post(
        f"{IDENTITY_URL}/api/resolve",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    
    assert resp.status_code == 200
    assert resp.json().get("user") == actual_user
    print(f"[Test] Voice ID resolution successful.")

@pytest.mark.local_only
@pytest.mark.skipif("LIVE_TEST_URL" not in os.environ, reason="Requires running Identity service")
def test_api_key_generation_resolution_revocation(client):
    """
    Test: Verify full API key lifecycle (generate, resolve, revoke) on actual database/service.
    """
    print(f"\n[Test] Running live API key lifecycle test...")
    
    # 1. Resolve default user to get their existing main API key
    resolve_resp = client.post(
        f"{IDENTITY_URL}/api/resolve",
        json={},
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    assert resolve_resp.status_code == 200, f"Initial resolve failed: {resolve_resp.text}"
    resolved_data = resolve_resp.json()
    resolved_user = resolved_data.get("user")
    user_api_key = resolved_data.get("api_key")
    
    assert resolved_user is not None
    assert user_api_key is not None, f"Resolved user {resolved_user} has no API key"
    
    # 2. Use user_api_key to generate a new custom API key
    print(f"[Test] Generating a new custom API key for user '{resolved_user}'...")
    auth_headers = {"Authorization": f"Bearer {user_api_key}"}
    gen_resp = client.post(
        f"{IDENTITY_URL}/api/users/me/keys",
        json={"label": "Live Integration Test Key"},
        headers=auth_headers
    )
    assert gen_resp.status_code == 200, f"API key generation failed: {gen_resp.text}"
    gen_data = gen_resp.json()
    key_id = gen_data["id"]
    new_key = gen_data["key"]
    
    assert new_key.startswith("sk-")
    print(f"[Test] Successfully generated API key (ID: {key_id}, Prefix: {new_key[:8]}...)")
    
    try:
        # 3. Resolve identity using the new custom API key
        print(f"[Test] Resolving identity using the new custom API key...")
        resolve_custom = client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"api_key": new_key},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        assert resolve_custom.status_code == 200, f"Resolution with custom key failed: {resolve_custom.text}"
        assert resolve_custom.json().get("user") == resolved_user
        print(f"[Test] Successfully resolved to user '{resolved_user}' using custom key.")
        
    finally:
        # 4. Revoke the key to keep the database clean
        print(f"[Test] Revoking the custom API key (ID: {key_id})...")
        revoke_resp = client.delete(
            f"{IDENTITY_URL}/api/users/me/keys/{key_id}",
            headers=auth_headers
        )
        assert revoke_resp.status_code == 200, f"Key revocation failed: {revoke_resp.text}"
        assert revoke_resp.json().get("success") is True
        print(f"[Test] Successfully revoked key.")
        
    # 5. Verify that resolving with the revoked key no longer resolves to that user
    resolve_after = client.post(
        f"{IDENTITY_URL}/api/resolve",
        json={"api_key": new_key},
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    assert resolve_after.status_code == 200
    print(f"[Test] Resolution after revocation output user: {resolve_after.json().get('user')}")

if __name__ == "__main__":
    pytest.main([__file__, "-s"])
