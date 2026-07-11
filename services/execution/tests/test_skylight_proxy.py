import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

from unittest.mock import patch

from fastapi.testclient import TestClient

from services.execution.main import app

client = TestClient(app)


def _mock_session():
    return {
        "url": "https://app.ourskylight.com",
        "access_token": "mock-token",
        "frame_id": "1",
        "email": "test@example.com",
    }


def test_skylight_chores_missing_auth():
    """Test that skylight chores endpoint returns failure when not configured."""
    resp = client.get("/api/integrations/skylight/chores", headers={"X-Internal-Secret": "test-secret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FAILURE"
    assert "Skylight not configured" in data["message"]


def test_skylight_chores_user_filter():
    """Test that chores can be filtered by user."""
    mock_chores = [
        {"id": "1", "title": "Clean Room", "completed": False, "assignees": ["jeremiah"], "reward": 10},
        {"id": "2", "title": "Do Dishes", "completed": False, "assignees": ["michele"], "reward": 5},
        {"id": "3", "title": "Walk Dog", "completed": True, "assignees": ["jeremiah"], "reward": 10},
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"data": mock_chores}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/chores",
                headers={"X-Internal-Secret": "test-secret"},
                params={"user": "jeremiah"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["chores"]) == 2
            chore_ids = [c["id"] for c in data["chores"]]
            assert "1" in chore_ids
            assert "3" in chore_ids


def test_skylight_chores_date_filter():
    """Test that chores can be filtered by date."""
    mock_chores = [
        {"id": "1", "title": "Clean Room", "completed": False, "assignees": ["jeremiah"], "due_date": "2026-05-29"},
        {"id": "2", "title": "Do Dishes", "completed": False, "assignees": ["jeremiah"], "due_date": "2026-05-30"},
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"data": mock_chores}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/chores",
                headers={"X-Internal-Secret": "test-secret"},
                params={"date": "2026-05-29"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["chores"]) == 1
            assert data["chores"][0]["id"] == "1"


def test_skylight_chores_user_and_date_filter():
    """Test that chores can be filtered by both user and date."""
    mock_chores = [
        {"id": "1", "title": "Clean Room", "completed": False, "assignees": ["jeremiah"], "due_date": "2026-05-29"},
        {"id": "2", "title": "Do Dishes", "completed": False, "assignees": ["michele"], "due_date": "2026-05-29"},
        {"id": "3", "title": "Walk Dog", "completed": False, "assignees": ["jeremiah"], "due_date": "2026-05-30"},
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"data": mock_chores}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/chores",
                headers={"X-Internal-Secret": "test-secret"},
                params={"user": "jeremiah", "date": "2026-05-29"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["chores"]) == 1
            assert data["chores"][0]["id"] == "1"


def test_skylight_chore_complete():
    """Test completing a skylight chore."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"success": True}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.post(
                "/api/integrations/skylight/chores/1/complete",
                headers={"X-Internal-Secret": "test-secret"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert "Chore completed" in data["message"]


def test_skylight_chore_complete_failure():
    """Test completing a skylight chore when API fails."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return None

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.post(
                "/api/integrations/skylight/chores/1/complete",
                headers={"X-Internal-Secret": "test-secret"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "FAILURE"


def test_skylight_chore_uncomplete():
    """Test uncompleting a skylight chore."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"success": True}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.post(
                "/api/integrations/skylight/chores/1/uncomplete",
                headers={"X-Internal-Secret": "test-secret"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert "Chore uncompleted" in data["message"]


def test_skylight_rewards():
    """Test fetching skylight rewards."""
    mock_rewards = [
        {"id": "1", "title": "Ice Cream", "cost": 50},
        {"id": "2", "title": "Movie Night", "cost": 100},
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"data": mock_rewards}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/rewards",
                headers={"X-Internal-Secret": "test-secret"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["rewards"]) == 2


def test_skylight_rewards_failure():
    """Test fetching skylight rewards when API fails."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return None

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/rewards",
                headers={"X-Internal-Secret": "test-secret"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "FAILURE"


def test_skylight_redeem_reward():
    """Test redeeming a skylight reward."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"success": True}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.post(
                "/api/integrations/skylight/rewards/1/redeem",
                headers={"X-Internal-Secret": "test-secret"},
                json={"user_id": "jeremiah"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert "Reward redeemed" in data["message"]


def test_skylight_redeem_reward_failure():
    """Test redeeming a skylight reward when API fails."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return None

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.post(
                "/api/integrations/skylight/rewards/1/redeem",
                headers={"X-Internal-Secret": "test-secret"},
                json={"user_id": "jeremiah"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "FAILURE"


def test_skylight_no_internal_secret():
    """Test that skylight endpoints require internal secret."""
    resp = client.get("/api/integrations/skylight/chores")
    assert resp.status_code == 403


def test_skylight_wrong_internal_secret():
    """Test that skylight endpoints reject wrong internal secret."""
    resp = client.get("/api/integrations/skylight/chores", headers={"X-Internal-Secret": "wrong-secret"})
    assert resp.status_code == 403


def test_skylight_chores_empty_list():
    """Test that empty chore list returns empty array."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"data": []}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/chores",
                headers={"X-Internal-Secret": "test-secret"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert data["chores"] == []


def test_skylight_chores_case_insensitive_user_filter():
    """Test that user filter is case-insensitive."""
    mock_chores = [
        {"id": "1", "title": "Clean Room", "completed": False, "assignees": ["Jeremiah"], "reward": 10},
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None):
        return {"data": mock_chores}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/chores",
                headers={"X-Internal-Secret": "test-secret"},
                params={"user": "jeremiah"}  # lowercase
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["chores"]) == 1
