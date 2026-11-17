"""The assistant's to-do list tool: lists found by the words used, household access, no guessing between lists."""
from __future__ import annotations

import pytest

from services.execution.entity_access import EntityPermissions
from services.execution.handlers import todo_list as handler
from services.execution.schemas import TodoListRequest, UserContext

CTX = {"user": "sam", "is_admin": False, "ha_url": "http://hub.test:8123", "ha_token": "member-token"}


class FakeHub:
    """Just enough of the hub's REST API: states and the todo services."""

    def __init__(self):
        self.lists = {
            "todo.shopping_list": {"name": "Shopping List", "features": 15, "items": [
                {"uid": "u1", "summary": "Milk", "status": "needs_action"},
                {"uid": "u2", "summary": "Rye bread", "status": "completed"}]},
            "todo.reminders": {"name": "Reminders", "features": 127, "items": [
                {"uid": "u3", "summary": "Call the vet", "status": "needs_action"}]},
            "todo.workshop": {"name": "Workshop", "features": 15, "items": []},
        }
        self.calls = []
        self.fail_states = False

    async def get_states(self, url, token, account="", strict=False):
        if self.fail_states:
            raise ConnectionError("down")
        return [{"entity_id": eid, "state": str(sum(i["status"] != "completed" for i in v["items"])),
                 "attributes": {"friendly_name": v["name"], "supported_features": v["features"]}}
                for eid, v in self.lists.items()] + [{"entity_id": "light.porch", "state": "on", "attributes": {}}]

    async def call_service(self, url, token, domain, service, entity_id="", service_data=None, return_response=False):
        self.calls.append((domain, service, entity_id, dict(service_data or {}), token))
        items = self.lists[entity_id]["items"]
        data = service_data or {}
        if service == "get_items":
            return {"ok": True, "service_response": {"changed_states": [],
                                                     "service_response": {entity_id: {"items": list(items)}}}}
        if service == "add_item":
            items.append({"uid": f"n{len(items)}", "summary": data["item"], "status": "needs_action"})
        elif service == "update_item":
            next(i for i in items if i["uid"] == data["item"])["status"] = data["status"]
        elif service == "remove_item":
            items[:] = [i for i in items if i["uid"] != data["item"]]
        return {"ok": True, "status_code": 200}


@pytest.fixture
def hub(monkeypatch):
    fake = FakeHub()
    monkeypatch.setattr(handler.ha_client, "get_states", fake.get_states)
    monkeypatch.setattr(handler.ha_client, "call_service", fake.call_service)
    perms = {"value": EntityPermissions(assigned=frozenset({"light.porch"}), protected=frozenset({"todo.workshop"}))}

    async def load(user, is_admin=False):
        return EntityPermissions.everything() if is_admin else perms["value"]

    monkeypatch.setattr(handler, "load_entity_permissions", load)
    fake.perms = perms
    return fake


async def run(**kw):
    ctx = {**CTX, **kw.pop("ctx", {})}
    return await handler.handle_todo_list(TodoListRequest(user_context=UserContext(**ctx), **kw))


async def test_a_member_adds_to_a_household_list_by_its_spoken_name(hub):
    res = await run(action="add", list_name="the shopping list", items=["eggs", " ", "Oat milk"])
    assert res.status == "SUCCESS" and res.message == "Added eggs and Oat milk to Shopping List."
    adds = [c for c in hub.calls if c[1] == "add_item"]
    assert [(c[2], c[3]) for c in adds] == [("todo.shopping_list", {"item": "eggs"}),
                                             ("todo.shopping_list", {"item": "Oat milk"})]
    assert all(c[4] == "member-token" for c in hub.calls)  # the caller's own credentials


async def test_reading_a_list_says_what_is_left(hub):
    res = await run(action="list", list_name="reminders")
    assert res.status == "SUCCESS" and res.message == "Reminders has 1 to do: Call the vet."
    res = await run(action="list", list_name="shopping", include_completed=True)
    assert "Done: Rye bread" in res.message and len(res.detail["items"]) == 2


async def test_no_list_named_with_several_lists_asks_instead_of_guessing(hub):
    res = await run(action="add", item="batteries")
    assert res.status == "FAILURE" and res.message.startswith("Which list?")
    assert "Reminders" in res.message and "Shopping List" in res.message
    assert not [c for c in hub.calls if c[1] == "add_item"]


async def test_a_locked_list_does_not_exist_for_someone_off_its_permit_list(hub):
    res = await run(action="list", list_name="workshop")
    assert res.status == "FAILURE" and "no list called 'workshop'" in res.message
    assert "Workshop" not in res.message
    hub.perms["value"] = EntityPermissions(protected=frozenset({"todo.workshop"}),
                                           permitted=frozenset({"todo.workshop"}))
    assert (await run(action="list", list_name="workshop")).status == "SUCCESS"


async def test_only_list_is_used_when_no_name_is_given(hub):
    del hub.lists["todo.reminders"]  # and the workshop list stays locked to others
    res = await run(action="add", item="Jam")
    assert res.status == "SUCCESS" and "Shopping List" in res.message


async def test_tick_off_put_back_and_remove_find_the_item_by_its_words(hub):
    assert (await run(action="complete", list_name="shopping list", item="milk")).status == "SUCCESS"
    assert ("todo", "update_item", "todo.shopping_list", {"item": "u1", "status": "completed"}, "member-token") in hub.calls
    res = await run(action="uncomplete", list_name="shopping list", item="rye")
    assert res.status == "SUCCESS" and res.message == "Put back Rye bread on Shopping List."
    res = await run(action="remove", list_name="shopping list", item="rye bread")
    assert res.status == "SUCCESS"
    assert [i["summary"] for i in hub.lists["todo.shopping_list"]["items"]] == ["Milk"]
    res = await run(action="complete", list_name="shopping list", item="caviar")
    assert res.status == "FAILURE" and "'caviar' is not on that list" in res.message


async def test_two_items_matching_the_words_are_named_not_picked(hub):
    hub.lists["todo.shopping_list"]["items"].append({"uid": "u9", "summary": "Milk chocolate", "status": "needs_action"})
    hub.lists["todo.shopping_list"]["items"][0]["summary"] = "Whole milk"
    res = await run(action="complete", list_name="shopping list", item="milk")
    assert res.status == "FAILURE" and "Whole milk" in res.message and "Milk chocolate" in res.message


async def test_an_unreachable_hub_or_missing_credentials_says_so(hub):
    hub.fail_states = True
    res = await run(action="list", list_name="reminders")
    assert res.status == "FAILURE" and "could not reach the home hub" in res.message



async def test_without_a_login_of_their_own_the_hubs_login_reads_the_lists(hub, monkeypatch):
    import services.config as config

    monkeypatch.setattr(config, "HA_URL", "http://hub.test:8123")
    res = await run(action="lists", ctx={"ha_url": None, "ha_token": None})
    assert res.status == "SUCCESS", res.message
    assert "Workshop" not in res.message  # the hub's login sees it; the caller still may not
    monkeypatch.setattr(config, "HA_URL", None)
    res = await run(action="lists", ctx={"ha_url": None, "ha_token": None})
    assert res.status == "FAILURE" and "HA_URL" in res.message


async def test_lists_names_every_list_the_caller_may_use(hub):
    res = await run(action="lists")
    assert res.message == "The lists are Reminders and Shopping List."
    assert res.detail["lists"][1]["features"]["move"] is True
    assert res.detail["lists"][0]["features"]["due_date"] is True


def test_visibility_rule():
    perms = EntityPermissions(protected=frozenset({"todo.a"}), permitted=frozenset())
    assert handler.list_visible(perms, "todo.b") and not handler.list_visible(perms, "todo.a")
    assert not handler.list_visible(None, "light.x") and handler.list_visible(None, "todo.a")
    assert handler.list_visible(EntityPermissions.everything(), "todo.a")
