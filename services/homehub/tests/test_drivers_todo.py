"""Reminders (local_todo) and the shopping list: import, HA's todo services, persistence."""
from __future__ import annotations

import pytest

from services.homehub.core import HubError
from services.homehub.drivers import todo
from services.homehub.tests.conftest import make_hub

ICS = """BEGIN:VCALENDAR\r
PRODID:-//homeassistant.io//local_todo 2.0//EN\r
VERSION:2.0\r
BEGIN:VTODO\r
DTSTAMP:20240905T002739\r
UID:a846c874-6b1d-11ef-a339-cc3d8294b015\r
CREATED:20240905T002739\r
SEQUENCE:0\r
STATUS:NEEDS-ACTION\r
SUMMARY:Call the plumber\\, about the\r
  water heater\r
DUE;VALUE=DATE:20240910\r
DESCRIPTION:Line one\\nline two\r
END:VTODO\r
BEGIN:VTODO\r
UID:beae3bc4-6b1d-11ef-a339-cc3d8294b015\r
STATUS:COMPLETED\r
SUMMARY:Return library books\r
DUE;TZID=America/Phoenix:20240911T170000\r
END:VTODO\r
END:VCALENDAR\r
"""


class FakeStore:
    def __init__(self):
        self.kv = {}

    def get_kv(self, key, default=None):
        return self.kv.get(key, default)

    def set_kv(self, key, value):
        self.kv[key] = value


def test_parse_ics():
    items = todo.parse_ics(ICS, "America/Phoenix")
    assert items[0] == {"uid": "a846c874-6b1d-11ef-a339-cc3d8294b015", "status": "needs_action",
                        "summary": "Call the plumber, about the water heater", "due": "2024-09-10",
                        "description": "Line one\nline two"}
    assert items[1]["status"] == "completed"
    assert items[1]["due"] == "2024-09-11T17:00:00-07:00"


async def reminders(store=None):
    hub, entry = make_hub("local_todo", data={"storage_key": "reminders", "todo_list_name": "Reminders", "ics": ICS},
                          entities=[{"entity_id": "todo.reminders", "unique_id": "01J6ZRRXVSQMX586W7QQ90M924"}])
    if store is not None:
        hub.store = store
    drv = todo.LocalTodoDriver(hub, entry)
    await drv.async_setup()
    hub.drivers[entry.entry_id] = drv
    return hub, drv


async def test_local_todo_state_and_services():
    hub, _drv = await reminders()
    st = hub.states.get("todo.reminders")
    assert st.state == "1" and st.attributes["supported_features"] == 127
    await hub.call_service("todo", "add_item", {"entity_id": "todo.reminders", "item": "Buy filters",
                                                "due_date": "2026-11-20", "description": "20x25x1"})
    assert hub.states.get("todo.reminders").state == "2"
    await hub.call_service("todo", "update_item", {"entity_id": "todo.reminders", "item": "Buy filters",
                                                   "status": "completed", "rename": "Buy furnace filters"})
    assert hub.states.get("todo.reminders").state == "1"
    res = await hub.call_service("todo", "get_items", {"entity_id": "todo.reminders", "status": "completed"},
                                 return_response=True)
    items = res["response"]["todo.reminders"]["items"]
    assert {i["summary"] for i in items} == {"Return library books", "Buy furnace filters"}
    added = next(i for i in items if i["summary"] == "Buy furnace filters")
    assert added["due"] == "2026-11-20" and added["description"] == "20x25x1"
    await hub.call_service("todo", "remove_item", {"entity_id": "todo.reminders", "item": [added["uid"]]})
    await hub.call_service("todo", "remove_completed_items", {"entity_id": "todo.reminders"})
    res = await hub.call_service("todo", "get_items", {"entity_id": "todo.reminders"}, return_response=True)
    assert [i["summary"] for i in res["response"]["todo.reminders"]["items"]] == \
        ["Call the plumber, about the water heater"]


async def test_due_datetime_without_zone_gets_the_hub_zone():
    hub, _ = await reminders()
    await hub.call_service("todo", "add_item", {"entity_id": "todo.reminders", "item": "Trash out",
                                                "due_datetime": "2026-11-12 19:00:00"})
    res = await hub.call_service("todo", "get_items", {"entity_id": "todo.reminders"}, return_response=True)
    item = next(i for i in res["response"]["todo.reminders"]["items"] if i["summary"] == "Trash out")
    assert item["due"] == "2026-11-12T19:00:00-07:00"


async def test_errors():
    hub, _ = await reminders()
    with pytest.raises(HubError):
        await hub.call_service("todo", "update_item", {"entity_id": "todo.reminders", "item": "nope",
                                                       "status": "completed"})
    with pytest.raises(HubError):
        await hub.call_service("todo", "add_item", {"entity_id": "todo.reminders", "item": "  "})
    with pytest.raises(HubError):
        await hub.call_service("todo", "update_item", {"entity_id": "todo.reminders",
                                                       "item": "Return library books"})


async def test_store_wins_over_the_import_and_is_kept_up_to_date():
    store = FakeStore()
    hub, _ = await reminders(store)
    assert len(store.kv["todo:todo.reminders"]) == 2  # the import is saved on first start
    await hub.call_service("todo", "add_item", {"entity_id": "todo.reminders", "item": "New one"})
    assert len(store.kv["todo:todo.reminders"]) == 3
    hub2, _ = await reminders(store)  # a restart: the store, not the .ics, is the truth
    assert hub2.states.get("todo.reminders").state == "2"


async def test_shopping_list():
    hub, entry = make_hub("shopping_list", data={"items": [{"name": "Milk", "id": "a1", "complete": False},
                                                           {"name": "Eggs", "id": "b2", "complete": True}]},
                          entities=[{"entity_id": "todo.shopping_list", "unique_id": "01J5KJB4XP12XV05MKXPRQ32N0"}])
    drv = todo.ShoppingListDriver(hub, entry)
    await drv.async_setup()
    hub.drivers[entry.entry_id] = drv
    st = hub.states.get("todo.shopping_list")
    assert st.state == "1" and st.attributes["supported_features"] == 15
    await hub.call_service("todo", "add_item", {"entity_id": "todo.shopping_list", "item": "Bread"})
    await hub.call_service("todo", "update_item", {"entity_id": "todo.shopping_list", "item": "a1",
                                                   "status": "completed"})
    assert hub.states.get("todo.shopping_list").state == "1"
    with pytest.raises(HubError):  # the shopping list has no due dates, as in HA
        await hub.call_service("todo", "add_item", {"entity_id": "todo.shopping_list", "item": "Jam",
                                                    "due_date": "2026-11-20"})


async def test_empty_shopping_list_json_text():
    hub, entry = make_hub("shopping_list", data={"items": "[]"},
                          entities=[{"entity_id": "todo.shopping_list", "unique_id": "x"}])
    drv = todo.ShoppingListDriver(hub, entry)
    await drv.async_setup()
    assert hub.states.get("todo.shopping_list").state == "0"
