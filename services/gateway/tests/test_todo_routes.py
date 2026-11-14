"""The Lists page routes: household lists for every member, locked lists only for their permit list.

The hub is mocked at the HTTP layer; identity and the permission lookup are
faked. What matters: a member without any device assignment reads and writes
the household lists with their own HA credentials, a locked list does not exist
for anyone off its permit list, the page can only name items by id, and every
write answers with the list as it now is.
"""
import pytest
from conftest_media import DEFAULT_IDENTITY, TEST_HA_TOKEN, TEST_HA_URL, mock_upstream

from services.execution.entity_access import EntityPermissions
from services.gateway import dashboard_routes
from services.gateway import main as gateway_main

STATES = [
    {"entity_id": "todo.shopping_list", "state": "1",
     "attributes": {"friendly_name": "Shopping List", "supported_features": 15}},
    {"entity_id": "todo.reminders", "state": "0", "attributes": {"friendly_name": "Reminders", "supported_features": 127}},
    {"entity_id": "todo.workshop", "state": "2", "attributes": {"friendly_name": "Workshop", "supported_features": 15}},
    {"entity_id": "todo.broken", "state": "unavailable", "attributes": {"friendly_name": "Broken"}},
    {"entity_id": "light.porch", "state": "on", "attributes": {}},
]
SHOPPING = [{"uid": "u1", "summary": "Milk", "status": "needs_action"},
            {"uid": "u2", "summary": "Rye bread", "status": "completed"}]


def _items(*lists):
    return {"changed_states": [], "service_response": {eid: {"items": items} for eid, items in lists}}


def _posts(upstream, path):
    return [call.kwargs for (m, url), calls in upstream.requests.items()
            if m == "POST" and str(url).startswith(f"{TEST_HA_URL}{path}") for call in calls]


@pytest.fixture
def perms(monkeypatch):
    state = {"value": EntityPermissions(assigned=frozenset(), protected=frozenset({"todo.workshop"}))}

    async def load(username, is_admin=False):
        return EntityPermissions.everything() if is_admin else state["value"]

    monkeypatch.setattr(dashboard_routes, "load_entity_permissions", load)
    return state


@pytest.fixture
def member(monkeypatch):
    async def resolve(body):
        return {**DEFAULT_IDENTITY, "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", resolve)


def test_a_member_with_no_devices_sees_the_household_lists_but_not_a_locked_one(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/states", payload=STATES)
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/get_items",
                  payload=_items(("todo.reminders", []), ("todo.shopping_list", SHOPPING)))
    resp = client.get("/api/lists")
    assert resp.status_code == 200
    lists = resp.json()["lists"]
    assert [x["entity_id"] for x in lists] == ["todo.broken", "todo.reminders", "todo.shopping_list"]
    broken, reminders, shopping = lists
    assert broken["available"] is False and broken["items"] == []
    assert reminders["features"]["due_date"] and not shopping["features"]["due_date"]
    assert shopping["features"]["move"] and shopping["open"] == 1
    assert shopping["items"][1] == {"uid": "u2", "summary": "Rye bread", "status": "completed", "due": None,
                                    "description": None}
    (sent,) = _posts(upstream, "/api/services/todo/get_items")
    assert sent["json"] == {"entity_id": ["todo.reminders", "todo.shopping_list"]}
    assert sent["headers"]["Authorization"] == f"Bearer {TEST_HA_TOKEN}"
    assert TEST_HA_TOKEN not in resp.text


def test_a_locked_list_answers_404_until_the_member_is_on_its_permit_list(client, upstream, perms, member):
    assert client.get("/api/lists/todo.workshop").status_code == 404
    assert client.post("/api/lists/todo.workshop/items", json={"summary": "Glue"}).status_code == 404
    assert _posts(upstream, "/api/services/todo/") == []
    perms["value"] = EntityPermissions(protected=frozenset({"todo.workshop"}), permitted=frozenset({"todo.workshop"}))
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/states", payload=STATES)
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/get_items", payload=_items(("todo.workshop", [])))
    assert client.get("/api/lists/todo.workshop").json()["name"] == "Workshop"


def test_adding_an_item_runs_add_item_and_answers_with_the_new_list(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/states", payload=STATES, repeat=2)
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/get_items",
                  payload=_items(("todo.shopping_list", SHOPPING)))
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/add_item", payload=[])
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/get_items",
                  payload=_items(("todo.shopping_list", [*SHOPPING, {"uid": "u3", "summary": "Eggs",
                                                                     "status": "needs_action"}])))
    resp = client.post("/api/lists/todo.shopping_list/items", json={"summary": "  Eggs "})
    assert resp.status_code == 200
    assert [i["summary"] for i in resp.json()["items"]] == ["Milk", "Rye bread", "Eggs"]
    (sent,) = _posts(upstream, "/api/services/todo/add_item")
    assert sent["json"] == {"entity_id": "todo.shopping_list", "item": "Eggs"}


def test_due_dates_only_where_the_list_has_them(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/states", payload=STATES)
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/get_items",
                  payload=_items(("todo.shopping_list", SHOPPING)))
    resp = client.post("/api/lists/todo.shopping_list/items", json={"summary": "Jam", "due_date": "2026-12-01"})
    assert resp.status_code == 400 and resp.json()["detail"] == "This list has no due dates"
    assert _posts(upstream, "/api/services/todo/add_item") == []


def test_tick_rename_remove_move_and_clear_send_has_services_by_id(client, upstream, perms, member):
    for service in ("update_item", "remove_item", "move_item", "remove_completed_items"):
        mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/{service}", payload=[], repeat=2)
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/states", payload=STATES, repeat=20)
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/get_items",
                  payload=_items(("todo.shopping_list", SHOPPING)), repeat=20)
    base = "/api/lists/todo.shopping_list"
    assert client.patch(f"{base}/items/u1", json={"status": "completed", "summary": "Oat milk"}).status_code == 200
    assert client.delete(f"{base}/items/u2").status_code == 200
    assert client.post(f"{base}/items/u2/move", json={"previous_uid": None}).status_code == 200
    assert client.post(f"{base}/items/u1/move", json={"previous_uid": "u2"}).status_code == 200
    assert client.post(f"{base}/clear_completed").status_code == 200
    assert _posts(upstream, "/api/services/todo/update_item")[0]["json"] == {
        "entity_id": "todo.shopping_list", "item": "u1", "rename": "Oat milk", "status": "completed"}
    assert _posts(upstream, "/api/services/todo/remove_item")[0]["json"] == {"entity_id": "todo.shopping_list",
                                                                             "item": "u2"}
    moves = [p["json"] for p in _posts(upstream, "/api/services/todo/move_item")]
    assert moves == [{"entity_id": "todo.shopping_list", "item": "u2", "previous_uid": None},
                     {"entity_id": "todo.shopping_list", "item": "u1", "previous_uid": "u2"}]
    assert _posts(upstream, "/api/services/todo/remove_completed_items")[0]["json"] == {
        "entity_id": "todo.shopping_list"}


def test_bad_requests_never_reach_the_hub(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/states", payload=STATES, repeat=20)
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/get_items",
                  payload=_items(("todo.shopping_list", SHOPPING)), repeat=20)
    base = "/api/lists/todo.shopping_list"
    assert client.post(f"{base}/items", json={"summary": "   "}).status_code == 400
    assert client.post(f"{base}/items", json={"summary": "x" * 300}).status_code == 400
    assert client.patch(f"{base}/items/u1", json={"status": "done"}).status_code == 400
    assert client.patch(f"{base}/items/u1", json={}).status_code == 400
    # The hub would also match an item by its words; the page only names one by id.
    assert client.patch(f"{base}/items/Milk", json={"status": "completed"}).status_code == 404
    assert client.post(f"{base}/items/u1/move", json={"previous_uid": 7}).status_code == 400
    assert client.get("/api/lists/light.porch").status_code == 404
    assert client.get("/api/lists/todo.Bad-Id").status_code == 404
    for service in ("add_item", "update_item", "move_item"):
        assert _posts(upstream, f"/api/services/todo/{service}") == []


def test_the_hubs_refusal_is_passed_on(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/states", payload=STATES)
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/get_items",
                  payload=_items(("todo.reminders", [])))
    mock_upstream(upstream, "POST", f"{TEST_HA_URL}/api/services/todo/add_item", status=400,
                  payload={"message": "invalid due date 'soon'"})
    resp = client.post("/api/lists/todo.reminders/items", json={"summary": "Vet", "due_date": "soon"})
    assert resp.status_code == 400 and resp.json()["detail"] == "invalid due date 'soon'"
