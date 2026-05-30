import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["EXECUTION_EXTERNAL_HOST"] = "localhost"

from fastapi.testclient import TestClient
from services.execution.main import app


def _async_return(value):
    """Create a coroutine that returns a value."""
    async def coro(*args, **kwargs):
        return value
    return coro

client = TestClient(app)

valid_context = {
    "user": "testuser",
    "is_admin": False,
    "ha_url": "http://ha.local",
    "ha_token": "mock-token",
}

# ─── Entity Search ───────────────────────────────────────────────────────────────


def test_entity_search_by_query(mocker):
    """Test searching entities by query term."""
    mocker.patch("services.execution.ha_client.get_states", return_value=[
        {"entity_id": "media_player.office_tv", "state": "idle", "attributes": {"friendly_name": "Office TV", "device_class": "tv"}},
        {"entity_id": "media_player.office_tv_chrome", "state": "off", "attributes": {"friendly_name": "Office TV Cast", "device_class": "speaker"}},
        {"entity_id": "light.office_desk", "state": "on", "attributes": {"friendly_name": "Office Desk Light", "device_class": "light"}},
    ])

    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "office tv",
            "domain": "media_player",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    entities = data["detail"]["entities"]
    assert len(entities) >= 1
    assert entities[0]["entity_id"].startswith("media_player.")


def test_entity_search_no_results(mocker):
    """Test entity search returns empty list when no matches."""
    mocker.patch("services.execution.ha_client.get_states", return_value=[
        {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Kitchen Light", "device_class": "light"}},
    ])

    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "nonexistent device xyz",
            "domain": "media_player",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert len(data["detail"]["entities"]) == 0


def test_entity_search_by_domain(mocker):
    """Test filtering entities by domain."""
    mocker.patch("services.execution.ha_client.get_states", return_value=[
        {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room Light"}},
        {"entity_id": "switch.bedroom_fan", "state": "off", "attributes": {"friendly_name": "Bedroom Fan"}},
        {"entity_id": "media_player.tv", "state": "on", "attributes": {"friendly_name": "TV"}},
    ])

    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "",
            "domain": "light",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    for entity in data["detail"]["entities"]:
        assert entity["domain"] == "light"


def test_entity_search_by_area(mocker):
    """Test filtering entities by area."""
    mocker.patch("services.execution.ha_client.get_states", return_value=[
        {"entity_id": "light.office_desk", "state": "on", "attributes": {"friendly_name": "Office Desk Light", "area_id": "office"}},
        {"entity_id": "light.kitchen_counter", "state": "off", "attributes": {"friendly_name": "Kitchen Counter", "area_id": "kitchen"}},
        {"entity_id": "light.bedroom_lamp", "state": "on", "attributes": {"friendly_name": "Bedroom Lamp", "area_id": "bedroom"}},
    ])

    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "",
            "area": "office",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert len(data["detail"]["entities"]) == 1
    assert data["detail"]["entities"][0]["entity_id"] == "light.office_desk"


def test_entity_search_by_state(mocker):
    """Test filtering entities by state."""
    mocker.patch("services.execution.ha_client.get_states", return_value=[
        {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room"}},
        {"entity_id": "light.bedroom", "state": "off", "attributes": {"friendly_name": "Bedroom"}},
        {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Kitchen"}},
    ])

    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "",
            "state": "on",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    for entity in data["detail"]["entities"]:
        assert entity["state"] == "on"


def test_entity_search_no_missing_credentials(mocker):
    """Test entity search returns failure when HA credentials missing."""
    empty_context = {"user": "testuser"}

    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": empty_context,
            "query": "test",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FAILURE"


def test_entity_search_no_internal_secret(mocker):
    """Test entity search requires internal secret."""
    resp = client.post("/execute/entity/search", json={"user_context": valid_context})
    assert resp.status_code == 403


def test_entity_search_missing_query(mocker):
    """Test entity search with empty query returns all entities."""
    mocker.patch("services.execution.ha_client.get_states", return_value=[
        {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room"}},
        {"entity_id": "media_player.tv", "state": "off", "attributes": {"friendly_name": "TV"}},
    ])

    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    # Empty query means no filtering by search terms
    assert len(data["detail"]["entities"]) == 2


# ─── HA Service Call ─────────────────────────────────────────────────────────────


def test_ha_service_turn_on(mocker):
    """Test calling turn_on service via HA service endpoint."""
    mocker.patch("services.execution.ha_client.call_service", return_value={"ok": True, "status_code": 200})

    resp = client.post("/execute/ha_service",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.living_room",
            "service_data": {"brightness_pct": 80},
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "light.turn_on executed" in data["message"]


def test_ha_service_turn_off(mocker):
    """Test calling turn_off service via HA service endpoint."""
    mocker.patch("services.execution.ha_client.call_service", return_value={"ok": True})

    resp = client.post("/execute/ha_service",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "domain": "media_player",
            "service": "turn_off",
            "entity_id": "media_player.tv",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"


def test_ha_service_failure(mocker):
    """Test HA service call failure."""
    mocker.patch("services.execution.ha_client.call_service", return_value={"ok": False, "error": "Connection refused"})

    resp = client.post("/execute/ha_service",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "domain": "switch",
            "service": "turn_on",
            "entity_id": "switch.garage",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FAILURE"
    assert "Service call failed" in data["message"]


def test_ha_service_no_internal_secret(mocker):
    """Test HA service requires internal secret."""
    mocker.patch("services.execution.ha_client.call_service", return_value={"ok": True})

    resp = client.post("/execute/ha_service", json={
        "user_context": valid_context,
        "domain": "light",
        "service": "turn_on",
        "entity_id": "light.living_room",
    })
    assert resp.status_code == 403


def test_ha_service_without_service_data(mocker):
    """Test HA service call without optional service_data."""
    mocker.patch("services.execution.ha_client.call_service", return_value={"ok": True})

    resp = client.post("/execute/ha_service",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "domain": "switch",
            "service": "turn_on",
            "entity_id": "switch.desk",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"


# ─── Entity Discovery ────────────────────────────────────────────────────────────


def test_discovery_entities_no_credentials():
    """Test entity discovery returns 400 when no HA credentials."""
    async def mock_resolve():
        return None

    import services.execution.main as exec_main
    original = exec_main.resolve_first_user
    exec_main.resolve_first_user = mock_resolve

    try:
        resp = client.get("/discovery/entities", headers={"X-Internal-Secret": "test-secret"})
        assert resp.status_code == 400
    finally:
        exec_main.resolve_first_user = original


def test_discovery_entities_with_credentials():
    """Test entity discovery returns states when credentials exist."""
    mock_creds = {
        "ha_url": "http://ha.local",
        "ha_token": "mock-token",
    }
    mock_states = [
        {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room"}},
        {"entity_id": "media_player.tv", "state": "off", "attributes": {"friendly_name": "TV"}},
    ]

    async def mock_resolve():
        return mock_creds

    import services.execution.main as exec_main
    import services.execution.ha_client as ha_client_mod
    import services.execution.device_registry as device_reg_mod

    original_resolve = exec_main.resolve_first_user
    original_get_states = ha_client_mod.get_states
    original_get_areas = ha_client_mod.get_areas
    original_list = device_reg_mod.list_devices

    exec_main.resolve_first_user = mock_resolve
    ha_client_mod.get_states = _async_return(mock_states)
    ha_client_mod.get_areas = _async_return({})
    device_reg_mod.list_devices = _async_return({})

    try:
        resp = client.get("/discovery/entities", headers={"X-Internal-Secret": "test-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict) and "entities" in data
        assert len(data["entities"]) >= 1
    finally:
        exec_main.resolve_first_user = original_resolve
        ha_client_mod.get_states = original_get_states
        ha_client_mod.get_areas = original_get_areas
        device_reg_mod.list_devices = original_list


def test_discovery_entities_with_areas():
    """Test entity discovery includes area information."""
    mock_creds = {"ha_url": "http://ha.local", "ha_token": "mock-token"}
    mock_states = [
        {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room"}},
    ]
    mock_areas = {"light.living_room": "living_room"}

    async def mock_resolve():
        return mock_creds

    import services.execution.main as exec_main
    import services.execution.ha_client as ha_client_mod
    import services.execution.device_registry as device_reg_mod

    original_resolve = exec_main.resolve_first_user
    original_get_states = ha_client_mod.get_states
    original_get_areas = ha_client_mod.get_areas
    original_list = device_reg_mod.list_devices

    exec_main.resolve_first_user = mock_resolve
    ha_client_mod.get_states = _async_return(mock_states)
    ha_client_mod.get_areas = _async_return(mock_areas)
    device_reg_mod.list_devices = _async_return({})

    try:
        resp = client.get("/discovery/entities", headers={"X-Internal-Secret": "test-secret"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict) and "entities" in data
        entities = data["entities"]
        assert len(entities) >= 1
        # Check that area_id was added to state attributes
        found = False
        for entity in entities:
            if isinstance(entity, dict) and entity.get("entity_id") == "light.living_room":
                assert entity.get("attributes", {}).get("area_id") == "living_room"
                found = True
                break
        assert found, "light.living_room entity not found in response"
    finally:
        exec_main.resolve_first_user = original_resolve
        ha_client_mod.get_states = original_get_states
        ha_client_mod.get_areas = original_get_areas
        device_reg_mod.list_devices = original_list
