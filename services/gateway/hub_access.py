"""Reading the hub's entities for one caller through execution's ``/discovery/*`` routes.

A caller with their own HA login sends it, as before. A caller without one
(every family member once Home Assistant's tokens are gone) sends nothing, and
execution reads with the hub's own login (``services.common.hub_login``), so
the internal secret never travels in a query string. The hub's login sees the
whole house, so what it returns is trimmed to the entities the caller may view.
"""
from __future__ import annotations

from typing import Any

from services.common.hub_login import has_own_login
from services.execution.entity_access import can_view, load_entity_permissions


def _field(creds: Any, name: str) -> Any:
    return creds.get(name) if isinstance(creds, dict) else getattr(creds, name, None)


def discovery_params(creds: Any) -> dict[str, str]:
    """Query params for ``/discovery/*``: the caller's own login, else none (execution uses the hub's)."""
    if has_own_login(creds):
        return {"ha_url": str(_field(creds, "ha_url")), "ha_token": str(_field(creds, "ha_token"))}
    return {}


def is_hidden(entity: Any) -> bool:
    """Execution marks an entity the hub hides (a duplicate device's copy) with ``_hidden``."""
    attrs = entity.get("attributes") if isinstance(entity, dict) else None
    return isinstance(attrs, dict) and bool(attrs.get("_hidden"))


def browsable(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``entities`` without the ones the hub hides: what lists, caches, pickers and RAG may hold.

    A hidden entity still works when a command names it; it is only never browsed.
    """
    return [e for e in entities if not is_hidden(e)]


async def visible_entities(creds: Any, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``entities`` as the caller may browse them: hidden ones never; all others on their own login or as
    an admin, else only those they may view."""
    entities = browsable(entities)
    if has_own_login(creds) or _field(creds, "is_admin"):
        return entities
    perms = await load_entity_permissions(str(_field(creds, "user") or ""), False)
    return [e for e in entities if isinstance(e, dict) and can_view(perms, str(e.get("entity_id") or ""))]
