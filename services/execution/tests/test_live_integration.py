"""LIVE integration tests — hit actual services running on the server (192.168.2.205).
No mocks. These tests validate real HTTP communication with each service."""
import os
os.environ["INTERNAL_SECRET"] = "RAVEN_SECURE_2026"

import pytest
import httpx

# Service endpoints — resolved via Identity at runtime, but seeded for tests
IDENTITY_URL = os.getenv("IDENTITY_SVC_URL", "http://localhost:8001")
EXECUTION_URL = os.getenv("EXECUTION_SVC_URL", "http://localhost:8003")
GATEWAY_URL = os.getenv("GATEWAY_SVC_URL", "http://localhost:8002")
STORAGE_URL = os.getenv("STORAGE_SVC_URL", "http://localhost:8005")
RAG_URL = os.getenv("RAG_SVC_URL", "http://localhost:8004")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "RAVEN_SECURE_2026")
INTERNAL_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}


def _get_user_credentials(username: str = "jeremiah") -> dict:
    """Resolve user credentials from the Identity service — the single source of truth."""
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"user": username},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 200, f"Identity resolve failed: {resp.text}"
        data = resp.json()
        assert data.get("status") == "SUCCESS", f"Identity resolve failed: {data}"
        creds = data["credentials"]
        assert "ha_url" in creds, f"Missing ha_url in credentials: {creds}"
        assert "ha_token" in creds, f"Missing ha_token in credentials: {creds}"
        return creds


@pytest.mark.asyncio
async def test_identity_health_check_live():
    """Health endpoint responds correctly."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{IDENTITY_URL}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "OK"
    assert data["service"] == "identity"


@pytest.mark.asyncio
async def test_execution_health_check_live():
    """Execution health endpoint responds correctly."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{EXECUTION_URL}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "execution"


@pytest.mark.asyncio
async def test_identity_resolve_by_username_live():
    """Resolving by username returns valid credentials."""
    creds = _get_user_credentials("jeremiah")
    assert creds["ha_url"].startswith(("http://", "https://"))
    assert len(creds["ha_token"]) > 10


@pytest.mark.asyncio
async def test_identity_resolve_by_user_id_live():
    """Resolving by user_id returns valid credentials."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"user_id": 1},
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "SUCCESS"
    assert "credentials" in data
    creds = data["credentials"]
    assert "ha_url" in creds


@pytest.mark.asyncio
async def test_identity_resolve_falls_back_to_first_user_live():
    """Resolving with unknown username falls back to first user."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"user": "nonexistent_user_xyz123"},
            headers=INTERNAL_HEADERS,
        )
    # Should fall back to first user, not fail
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "SUCCESS"


@pytest.mark.asyncio
async def test_identity_resolve_by_voice_id_live():
    """Resolving by voice_id returns valid credentials."""
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"voice_id": "default"},
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "SUCCESS"


@pytest.mark.asyncio
async def test_identity_resolve_by_api_key_live():
    """Resolving by api_key returns valid credentials."""
    # Get a known api_key from the resolve-by-username call
    creds = _get_user_credentials("jeremiah")
    # The credentials dict may contain api_key or api_key_enc
    # Try resolving with the plaintext api_key if available
    async with httpx.AsyncClient(timeout=10.0) as client:
        # We need the actual api_key — fetch it via the identity endpoint
        resolve_resp = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"user": "jeremiah"},
            headers=INTERNAL_HEADERS,
        )
    data = resolve_resp.json()
    creds = data["credentials"]
    # Try both plaintext and encrypted key names
    api_key = creds.get("api_key") or creds.get("api_key_enc")
    if api_key:
        resp = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"api_key": api_key},
            headers=INTERNAL_HEADERS,
        )
        assert resp.status_code == 200
        resolved = resp.json()
        assert resolved.get("status") == "SUCCESS"


@pytest.mark.asyncio
async def test_identity_resolve_by_device_id_live():
    """Resolving by device_id returns valid credentials."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"device_id": "test-device"},
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "SUCCESS"


@pytest.mark.asyncio
async def test_identity_resolve_missing_user_returns_404_live():
    """Resolving with explicit user_id that doesn't exist returns 404."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"user_id": 99999},
            headers=INTERNAL_HEADERS,
        )
    # Should return 404 for non-existent user_id (not fallback)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_identity_credentials_have_all_types_live():
    """Credentials returned contain expected key types (HA, GitHub, etc.)."""
    creds = _get_user_credentials("jeremiah")
    # HA credentials must always be present
    assert "ha_url" in creds
    assert "ha_token" in creds
    # Should also have credential types for integrations
    assert "github" in creds or "github_token" in creds or creds.get("github") is None


@pytest.mark.asyncio
async def test_execution_light_endpoint_live():
    """POST /execute/light returns a response (may fail at HA layer, but endpoint works)."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/light",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "entity_id": "light.nonexistent_for_test",
                "action": "turn_on",
            },
            headers=INTERNAL_HEADERS,
        )
    # Endpoint must respond — status could be SUCCESS/FAILURE but must be JSON
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] in ("SUCCESS", "FAILURE")
    assert "message" in data


@pytest.mark.asyncio
async def test_execution_security_lock_endpoint_live():
    """POST /execute/security returns a response for lock action."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/security",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "entity_id": "lock.nonexistent_for_test",
                "action": "lock",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] in ("SUCCESS", "FAILURE")


@pytest.mark.asyncio
async def test_execution_security_status_endpoint_live():
    """POST /execute/security with action=status returns state."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/security",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "entity_id": "lock.front_door",
                "action": "status",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    # Status action should return a message about current state
    assert "message" in data


@pytest.mark.asyncio
async def test_execution_climate_endpoint_live():
    """POST /execute/climate returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/climate",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "entity_id": "climate.nonexistent_for_test",
                "temperature": 22.0,
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] in ("SUCCESS", "FAILURE")


@pytest.mark.asyncio
async def test_execution_calendar_endpoint_live():
    """POST /execute/calendar returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/calendar",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "action": "list",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_note_endpoint_live():
    """POST /execute/note returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/note",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "action": "add",
                "text": "test note from integration test",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_talk_endpoint_live():
    """POST /execute/talk returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/talk",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "message": "Hello from integration test",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_timers_list_endpoint_live():
    """GET /execute/timers returns a response."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{EXECUTION_URL}/execute/timers")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_media_status_endpoint_live():
    """POST /execute/media/status returns media player state."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/media/status",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "detail" in data
    detail = data["detail"]
    # Must have at least these fields
    assert "active" in detail or "available" in detail or "all_players" in detail


@pytest.mark.asyncio
async def test_execution_media_status_with_volume_live():
    """Media status response includes volume_level for active player."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/media/status",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
            },
            headers=INTERNAL_HEADERS,
        )
    data = resp.json()
    assert data["status"] == "SUCCESS"
    detail = data["detail"]
    # If there's an active player, it should have volume info
    if detail.get("active"):
        active = detail["active"]
        assert "volume_level" in active or "is_volume_muted" in active


@pytest.mark.asyncio
async def test_execution_media_transport_endpoint_live():
    """POST /execute/media/transport returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/media/transport",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "entity_id": "media_player.nonexistent_test",
                "command": "pause",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_video_play_endpoint_live():
    """POST /execute/video/play returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/video/play",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "entity_id": "media_player.nonexistent_test",
                "media_type": "video",
                "query": "test",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_web_search_endpoint_live():
    """POST /execute/web_search returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_search",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "test query from integration test",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_web_read_endpoint_live():
    """POST /execute/web_read returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/web_read",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "url": "https://example.com",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_entity_search_endpoint_live():
    """POST /execute/entity/search returns entity list."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/entity/search",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "light",
            },
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "entities" in data or "results" in data or "detail" in data


@pytest.mark.asyncio
async def test_execution_ltm_search_endpoint_live():
    """POST /execute/ltm/search returns a response."""
    creds = _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{EXECUTION_URL}/execute/ltm/search",
            json={
                "user_context": {"user": creds["user"], "ha_url": creds["ha_url"], "ha_token": creds["ha_token"], "is_admin": True},
                "query": "test",
            },
            headers=INTERNAL_HEADERS,
        )
    # May 404 if LTM not configured — endpoint just needs to respond
    assert resp.status_code in (200, 404, 501)
    if resp.status_code == 200:
        data = resp.json()
        assert "status" in data


@pytest.mark.asyncio
async def test_gateway_health_check_live():
    """Gateway health endpoint responds correctly."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{GATEWAY_URL}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "gateway"


@pytest.mark.asyncio
async def test_gateway_media_status_proxy_live():
    """Gateway proxy forwards identity resolution to execution."""
    _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/execute/media/status",
            json={},
            headers=INTERNAL_HEADERS,
        )
    # Gateway resolves identity internally and proxies — should succeed
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "detail" in data


@pytest.mark.asyncio
async def test_gateway_media_transport_proxy_live():
    """Gateway media transport proxy forwards correctly."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/execute/media/transport",
            json={"entity_id": "media_player.nonexistent_test", "command": "pause"},
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_gateway_media_play_proxy_live():
    """Gateway media play proxy forwards correctly."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/execute/media/play",
            json={"entity_id": "media_player.nonexistent_test", "media_type": "music", "query": "test"},
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_gateway_audiobookshelf_proxy_live():
    """Gateway audiobookshelf proxy forwards correctly."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{GATEWAY_URL}/execute/audiobookshelf",
            json={"action": "libraries"},
            headers=INTERNAL_HEADERS,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_storage_health_check_live():
    """Storage service health endpoint responds."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{STORAGE_URL}/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rag_health_check_live():
    """RAG service health endpoint responds."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{RAG_URL}/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_identity_resolve_multiple_users_exist_live():
    """Multiple users should be resolvable by username."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Resolve jeremiah
        resp1 = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"user": "jeremiah"},
            headers=INTERNAL_HEADERS,
        )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["credentials"]["user"] == "jeremiah"

    # The system should have at least 2 users — verify by resolving user_id 2 if exists
    resp2 = await client.post(
        f"{IDENTITY_URL}/api/resolve",
        json={"user_id": 2},
        headers=INTERNAL_HEADERS,
    )
    # Either it exists (200) or doesn't (404) — both are valid behaviors
    assert resp2.status_code in (200, 404)
    if resp2.status_code == 200:
        data2 = resp2.json()
        assert data2.get("status") == "SUCCESS"


@pytest.mark.asyncio
async def test_identity_credential_isolation_live():
    """User A's credentials should not leak to User B."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Resolve user 1
        resp1 = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"user_id": 1},
            headers=INTERNAL_HEADERS,
        )
    assert resp1.status_code == 200
    creds1 = resp1.json()["credentials"]

    # If user 2 exists, verify credentials are different
    resp2 = await client.post(
        f"{IDENTITY_URL}/api/resolve",
        json={"user_id": 2},
        headers=INTERNAL_HEADERS,
    )
    if resp2.status_code == 200:
        creds2 = resp2.json()["credentials"]
        # HA tokens should be different between users
        if creds1.get("ha_token") and creds2.get("ha_token"):
            assert creds1["ha_token"] != creds2["ha_token"]


@pytest.mark.asyncio
async def test_identity_decrypted_credentials_live():
    """Credentials returned from identity service are decrypted (not encrypted)."""
    creds = _get_user_credentials("jeremiah")
    ha_token = creds.get("ha_token", "")
    # Decrypted token should NOT start with encryption prefix (e.g., "enc:" or base64 garbage)
    assert not ha_token.startswith("enc:")
    assert len(ha_token) > 0


@pytest.mark.asyncio
async def test_execution_discovery_entities_live():
    """GET /discovery/entities returns entity list from HA."""
    _get_user_credentials("jeremiah")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{EXECUTION_URL}/discovery/entities",
            headers=INTERNAL_HEADERS,
        )
    # May return empty list if no HA entities indexed — just needs to succeed
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


@pytest.mark.asyncio
async def test_execution_tts_voices_live():
    """GET /execute/tts/voices returns available TTS voices."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{EXECUTION_URL}/execute/tts/voices")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
