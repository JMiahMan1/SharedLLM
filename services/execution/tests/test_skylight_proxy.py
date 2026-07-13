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


def _category_included(label, color="#ffcc00"):
    return {
        "id": f"cat-{label}",
        "type": "category",
        "attributes": {"id": 0, "label": label, "linked_to_profile": True, "color": color},
    }


def _raw_chore(cid, summary, status, start, reward=1, assignee=None):
    rel = {}
    if assignee:
        rel["category"] = {"data": {"id": f"cat-{assignee}", "type": "category"}}
    return {
        "id": cid,
        "type": "chore",
        "attributes": {
            "id": cid,
            "summary": summary,
            "status": status,
            "start": start,
            "reward_points": reward,
        },
        "relationships": rel,
    }


def test_skylight_chores_today_literal():
    """The widget passes date='today'; only today's chores should be returned."""
    from datetime import date as _date

    today = _date.today().isoformat()
    mock_chores = [
        _raw_chore("1", "Clean Room", "pending", today, assignee="Jeremiah"),
        _raw_chore("2", "Do Dishes", "pending", "2020-01-01", assignee="Jeremiah"),
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
        return {"data": mock_chores, "included": [_category_included("Jeremiah")]}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/chores",
                headers={"X-Internal-Secret": "test-secret"},
                params={"user": "jeremiah", "date": "today"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["chores"]) == 1
            assert data["chores"][0]["id"] == "1"


def test_skylight_chores_date_filter():
    """Test that chores are filtered to the requested YYYY-MM-DD day."""
    mock_chores = [
        _raw_chore("1", "Clean Room", "pending", "2026-05-29"),
        _raw_chore("2", "Do Dishes", "complete", "2026-05-30"),
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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


def test_skylight_chores_normalization():
    """Raw Skylight chores are flattened into the UI ChoreItem shape."""
    mock_chores = [
        _raw_chore("1-2026-05-29", "Clean Room", "complete", "2026-05-29", reward=10),
        _raw_chore("2-2026-05-29", "Do Dishes", "pending", "2026-05-29", reward=5),
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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
            chores = {c["id"]: c for c in data["chores"]}
            assert chores["1-2026-05-29"]["title"] == "Clean Room"
            assert chores["1-2026-05-29"]["completed"] is True
            assert chores["1-2026-05-29"]["reward"] == 10
            assert chores["2-2026-05-29"]["completed"] is False
            assert "assignees" in chores["1-2026-05-29"]


def test_skylight_chore_complete():
    """Test completing a skylight chore."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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
    """Uncompleting resets the chore status to pending."""
    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
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


def test_skylight_chores_user_filter():
    """Chores are scoped to the requested user via the `category` assignee label,
    and an empty (admin) user returns the whole family frame."""
    from datetime import date as _date

    today = _date.today().isoformat()
    mock_chores = [
        _raw_chore("1", "Clean Room", "pending", today, assignee="Jeremiah"),
        _raw_chore("2", "Do Dishes", "pending", today, assignee="Noah"),
    ]
    included = [
        _category_included("Jeremiah", "#ff0000"),
        _category_included("Noah", "#00ff00"),
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
        return {"data": mock_chores, "included": included}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            # A regular member only sees their own chores.
            resp = client.get(
                "/api/integrations/skylight/chores",
                headers={"X-Internal-Secret": "test-secret"},
                params={"user": "jeremiah", "date": "today"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["chores"]) == 1
            assert data["chores"][0]["id"] == "1"
            assert data["chores"][0]["assignees"] == ["Jeremiah"]
            assert data["assignee_meta"] == {"Jeremiah": "#ff0000"}

            # No user (admin) sees every chore in the frame.
            resp = client.get(
                "/api/integrations/skylight/chores",
                headers={"X-Internal-Secret": "test-secret"},
                params={"date": "today"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["chores"]) == 2
            assert data["assignee_meta"] == {"Jeremiah": "#ff0000", "Noah": "#00ff00"}


def test_skylight_calendar_events():
    """Test fetching Skylight calendar events."""
    mock_events = [
        {"id": "1", "summary": "Dentist", "start": "2026-07-15T09:00:00Z"},
        {"id": "2", "summary": "Birthday", "start": "2026-07-20T12:00:00Z"},
    ]

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
        return {"data": mock_events}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/calendar/events",
                headers={"X-Internal-Secret": "test-secret"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert len(data["events"]) == 2


def test_skylight_calendar_events_failure():
    """Test calendar events failure path."""

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
        return None

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.get(
                "/api/integrations/skylight/calendar/events",
                headers={"X-Internal-Secret": "test-secret"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "FAILURE"


def test_skylight_create_calendar_event():
    """Test creating a Skylight calendar event."""

    async def mock_get_session(*args, **kwargs):
        return _mock_session()

    async def mock_skylight_request(session, method, suffix, json_body=None, params=None):
        return {"data": {"id": "99", "summary": "Walk"}}

    with patch("services.execution.main._get_skylight_session", new=mock_get_session):
        with patch("services.execution.main._skylight_request", new=mock_skylight_request):
            resp = client.post(
                "/api/integrations/skylight/calendar/events",
                headers={"X-Internal-Secret": "test-secret"},
                json={"summary": "Walk", "start": "2026-07-15T09:00:00Z"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "SUCCESS"
            assert "created" in data["message"].lower()
