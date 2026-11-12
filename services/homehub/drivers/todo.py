"""To-do lists: HA's ``local_todo`` (Reminders) and ``shopping_list``, kept by the hub itself.

Each list is one ``todo.*`` entity whose state is the number of items still to
do, as HA shows it. The services are HA's: ``todo.add_item``,
``todo.update_item``, ``todo.remove_item``, ``todo.remove_completed_items`` and
``todo.get_items`` (with ``return_response``: ``{entity_id: {"items": [...]}}``).
Items have HA's shape: ``summary``, ``uid``, ``status`` (``needs_action`` or
``completed``) and, on Reminders, ``due`` and ``description``.

Where the items come from the first time: the importer passes HA's files in
the entry data, ``entry.data["ics"]`` (the text of
``.storage/local_todo.<storage_key>.ics``) for Reminders and
``entry.data["items"]`` (the list in ``.shopping_list.json``) for the shopping
list. After that the hub's store owns them, under ``todo:<entity_id>``.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from services.homehub.core import Driver, EntityEntry, Hub, HubError, ServiceCall, ServiceNotFound

# HA's TodoListEntityFeature.
CREATE, DELETE, UPDATE, MOVE, SET_DUE_DATE, SET_DUE_DATETIME, SET_DESCRIPTION = 1, 2, 4, 8, 16, 32, 64
NEEDS_ACTION, COMPLETED = "needs_action", "completed"
SERVICES = ("add_item", "update_item", "remove_item", "remove_completed_items", "get_items")


# -- iCalendar (only what local_todo writes) ------------------------------------

def _unescape(text: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def _ics_when(params: dict[str, str], value: str, default_tz: str) -> str | None:
    value = value.strip()
    try:
        if len(value) == 8 and value.isdigit():
            return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()
        utc = value.endswith("Z")
        dt = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    tz = ZoneInfo("UTC") if utc else ZoneInfo(params.get("TZID") or default_tz or "UTC")
    return dt.replace(tzinfo=tz).isoformat()


def parse_ics(text: str, default_tz: str = "UTC") -> list[dict[str, Any]]:
    """The VTODOs of a local_todo .ics file as HA's todo items."""
    lines: list[str] = []
    for raw in (text or "").splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in lines:
        if line == "BEGIN:VTODO":
            cur = {}
            continue
        if line == "END:VTODO":
            if cur is not None and cur.get("summary") is not None:
                cur.setdefault("uid", str(uuid.uuid4()))
                cur.setdefault("status", NEEDS_ACTION)
                items.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *rawparams = head.split(";")
        params = dict(p.split("=", 1) for p in rawparams if "=" in p)
        name = name.upper()
        if name == "UID":
            cur["uid"] = value
        elif name == "SUMMARY":
            cur["summary"] = _unescape(value)
        elif name == "DESCRIPTION":
            cur["description"] = _unescape(value)
        elif name == "STATUS":
            cur["status"] = COMPLETED if value.upper() == "COMPLETED" else NEEDS_ACTION
        elif name == "DUE":
            due = _ics_when(params, value, default_tz)
            if due:
                cur["due"] = due
    return items


# -- the services -------------------------------------------------------------

def _drivers_for(hub: Hub, call: ServiceCall) -> list[tuple[str, TodoDriver]]:
    if not call.entity_ids:
        raise HubError(f"todo.{call.service} needs a target todo entity")
    out = []
    for eid in call.entity_ids:
        ent = hub.entities.get(eid)
        driver = hub.drivers.get(ent.config_entry_id or "") if ent is not None else None
        if isinstance(driver, TodoDriver):
            out.append((eid, driver))
        elif eid.startswith("todo."):
            raise HubError(f"{eid}: no to-do list is running for it")
    return out


async def _handle(hub: Hub, call: ServiceCall) -> Any:
    data = {k: v for k, v in call.data.items() if k not in ("entity_id", "target", "area_id", "device_id")}
    responses = {}
    for eid, driver in _drivers_for(hub, call):
        responses[eid] = await driver.run_service(call.service, data)
    return responses if call.service == "get_items" else None


def ensure_todo_services(hub: Hub) -> None:
    """Register the todo services once per hub, whichever list starts first."""
    for service in SERVICES:
        if ("todo", service) not in hub.services:
            hub.register_service("todo", service, _handle)


class TodoDriver(Driver):
    """One HA to-do list kept in the hub's store."""

    poll_interval = None
    supported_features = CREATE | DELETE | UPDATE | MOVE

    def __init__(self, hub: Hub, entry) -> None:
        super().__init__(hub, entry)
        self.items: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def todo_entity(self) -> EntityEntry | None:
        return next((e for e in self.entities if e.domain == "todo"), None)

    @property
    def store_key(self) -> str:
        ent = self.todo_entity
        return f"todo:{ent.entity_id if ent else self.entry.entry_id}"

    def initial_items(self) -> list[dict[str, Any]]:
        return []

    async def async_setup(self) -> None:
        ensure_todo_services(self.hub)
        store = getattr(self.hub, "store", None)
        saved = None
        if store is not None:
            saved = await asyncio.to_thread(store.get_kv, self.store_key, None)
        self.items = list(saved) if isinstance(saved, list) else self.initial_items()
        if saved is None and store is not None:
            await self._save()
        self._publish()

    async def _save(self) -> None:
        store = getattr(self.hub, "store", None)
        if store is not None:
            await asyncio.to_thread(store.set_kv, self.store_key, self.items)

    def _publish(self) -> None:
        ent = self.todo_entity
        if ent is None:
            return
        open_count = sum(1 for i in self.items if i.get("status") != COMPLETED)
        self.publish(ent, open_count, {"supported_features": self.supported_features})

    # -- operations ------------------------------------------------------------
    def _find(self, ref: Any) -> dict[str, Any]:
        ref = str(ref)
        for item in self.items:
            if item.get("uid") == ref:
                return item
        for item in self.items:
            if item.get("summary") == ref:
                return item
        raise HubError(f"Unable to find to-do list item: {ref}")

    def _apply_fields(self, item: dict[str, Any], data: dict[str, Any]) -> None:
        if "due_date" in data or "due_datetime" in data:
            due = data.get("due_datetime") if "due_datetime" in data else data.get("due_date")
            needed = SET_DUE_DATETIME if "due_datetime" in data else SET_DUE_DATE
            if not self.supported_features & needed:
                raise HubError(f"{self.entry.title} does not support setting a due date")
            if due in (None, ""):
                item.pop("due", None)
            else:
                item["due"] = self._norm_due(due, with_time="due_datetime" in data)
        if "description" in data:
            if not self.supported_features & SET_DESCRIPTION:
                raise HubError(f"{self.entry.title} does not support descriptions")
            if data["description"] in (None, ""):
                item.pop("description", None)
            else:
                item["description"] = str(data["description"])

    def _norm_due(self, value: Any, *, with_time: bool) -> str:
        text = str(value)
        try:
            if not with_time:
                return date.fromisoformat(text[:10]).isoformat()
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HubError(f"invalid due date {text!r}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(self.hub.time_zone or "UTC"))
        return dt.isoformat()

    async def run_service(self, service: str, data: dict[str, Any]) -> Any:
        async with self._lock:
            result = self._run(service, data)
            if service != "get_items":
                await self._save()
                self._publish()
            return result

    def _run(self, service: str, data: dict[str, Any]) -> Any:
        if service == "add_item":
            summary = str(data.get("item") or "").strip()
            if not summary:
                raise HubError("todo.add_item needs an item")
            item: dict[str, Any] = {"uid": str(uuid.uuid4()), "summary": summary, "status": NEEDS_ACTION}
            self._apply_fields(item, data)
            self.items.append(item)
            return None
        if service == "update_item":
            item = self._find(data.get("item"))
            if not any(k in data for k in ("rename", "status", "due_date", "due_datetime", "description")):
                raise HubError("todo.update_item needs at least one of rename, status, due_date, due_datetime, "
                               "description")
            updated = dict(item)
            if data.get("rename"):
                updated["summary"] = str(data["rename"])
            if "status" in data:
                if data["status"] not in (NEEDS_ACTION, COMPLETED):
                    raise HubError(f"invalid status {data['status']!r}")
                updated["status"] = data["status"]
            self._apply_fields(updated, data)
            item.clear()
            item.update(updated)
            return None
        if service == "remove_item":
            refs = data.get("item")
            refs = [refs] if isinstance(refs, str) else list(refs or [])
            targets = [self._find(r) for r in refs]
            uids = {t["uid"] for t in targets}
            self.items = [i for i in self.items if i.get("uid") not in uids]
            return None
        if service == "remove_completed_items":
            self.items = [i for i in self.items if i.get("status") != COMPLETED]
            return None
        if service == "get_items":
            wanted = data.get("status") or [NEEDS_ACTION, COMPLETED]
            wanted = [wanted] if isinstance(wanted, str) else list(wanted)
            return {"items": [dict(i) for i in self.items if i.get("status") in wanted]}
        raise ServiceNotFound(f"todo.{service} is not a to-do service")

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if domain != "todo" or service not in SERVICES:
            raise ServiceNotFound(f"{self.platform} cannot run {domain}.{service} on {entity.entity_id}")
        return await self.run_service(service, data)


class LocalTodoDriver(TodoDriver):
    platform = "local_todo"
    supported_features = CREATE | DELETE | UPDATE | MOVE | SET_DUE_DATE | SET_DUE_DATETIME | SET_DESCRIPTION

    def initial_items(self) -> list[dict[str, Any]]:
        return parse_ics(str(self.entry.data.get("ics") or ""), self.hub.time_zone)


class ShoppingListDriver(TodoDriver):
    platform = "shopping_list"
    supported_features = CREATE | DELETE | UPDATE | MOVE

    def initial_items(self) -> list[dict[str, Any]]:
        raw = self.entry.data.get("items") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = []
        items = []
        for it in raw if isinstance(raw, list) else []:
            if not isinstance(it, dict) or not it.get("name"):
                continue
            items.append({"uid": str(it.get("id") or uuid.uuid4().hex), "summary": str(it["name"]),
                          "status": COMPLETED if it.get("complete") else NEEDS_ACTION})
        return items
