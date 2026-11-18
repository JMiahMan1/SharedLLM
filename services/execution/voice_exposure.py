"""Voice and chat control only the entities the hub exposes to them (Home Assistant's Assist exposure).

The hub keeps a per-entity "voice can control" setting (``services/homehub/exposure.py``): imported from
Home Assistant's exposure list, or Home Assistant's defaults on a fresh hub, and switched per entity in
the device sheet. A request Jarvis makes for the person (the chat handler and the agent loop set
``user_context.via = "assistant"``) may control an entity only when the hub says it is exposed. The app's
own controls do not set ``via`` and are governed by entity permissions alone. Reading a state is never
gated, nor a ``get_*`` service (``weather.get_forecasts``, ``todo.get_items``): those only read.

Fail fast: when the hub cannot answer (unreachable, or a server without the exposure API), the control is
refused with that reason, never carried out unchecked.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from services.execution.http_client import get_session, host_of
from services.execution.spoken_names import spoken_name

log = logging.getLogger("execution.voice_exposure")

ASSISTANT = "assistant"
_TIMEOUT = aiohttp.ClientTimeout(total=5.0, connect=3.0)
PATH = "/api/homehub/exposed_entities"
#: The hub's name for each entity it was asked about (its exposure answer carries it): said in a refusal.
_NAMES: dict[str, str] = {}


class EntityNotExposed(Exception):
    """Voice or chat asked to control an entity it may not (or whose setting could not be read).

    ``entity_id`` is the entity; ``unchecked`` is True when the hub could not be asked (rather than said no).
    The message names the device as people do (``spoken_names``), never by its id.
    """

    def __init__(self, message: str, *, entity_id: str = "", unchecked: bool = False) -> None:
        super().__init__(message)
        self.entity_id = entity_id
        self.unchecked = unchecked


def _field(ctx: Any, name: str) -> Any:
    return ctx.get(name) if isinstance(ctx, dict) else getattr(ctx, name, None)


def gated(ctx: Any, service: str | None = None) -> bool:
    """Does this request need the hub's exposure check?"""
    if _field(ctx, "via") != ASSISTANT:
        return False
    return not (service and str(service).startswith("get_"))


async def fetch_exposed(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """The hub's answer for one entity; raises EntityNotExposed when there is no answer."""
    url = f"{ha_url.rstrip('/')}{PATH}"
    headers = {"Authorization": f"Bearer {ha_token}"}
    try:
        session = await get_session(host_of(ha_url), verify=True)
        async with session.get(url, params={"entity_id": entity_id}, headers=headers, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                raise EntityNotExposed(
                    f"Could not check whether voice may control {spoken_name(entity_id)}: the home hub answered "
                    f"HTTP {resp.status} for its voice exposure list", entity_id=entity_id, unchecked=True)
            data = await resp.json(content_type=None)
    except EntityNotExposed:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
        raise EntityNotExposed(
            f"Could not check whether voice may control {spoken_name(entity_id)}: the home hub is unreachable "
            f"({type(exc).__name__})", entity_id=entity_id, unchecked=True) from exc
    if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"].strip():
        _NAMES[entity_id] = data["name"].strip()  # the hub's name for it, to say in a refusal
    exposed = data.get("exposed") if isinstance(data, dict) else None
    if not isinstance(exposed, bool):
        raise EntityNotExposed(f"Could not check whether voice may control {spoken_name(entity_id)}: the home "
                               "hub's answer had no exposed flag", entity_id=entity_id, unchecked=True)
    return exposed


async def require_exposed(ctx: Any, entity_id: str, *, service: str | None = None) -> None:
    """Raise EntityNotExposed unless this request may control ``entity_id`` (see the module docstring)."""
    if not entity_id or not gated(ctx, service):
        return
    # The caller's own hub login, else the hub's: the exposure flag is the same for everyone,
    # and a person with no stored HA token used to be refused every voice command here.
    from services.execution import hardware_router

    ha_url, ha_token = hardware_router.hub_pair(ctx)
    if not ha_url or not ha_token:
        raise EntityNotExposed(f"Could not check whether voice may control {spoken_name(entity_id)}: "
                               f"{hardware_router.NO_HUB_MESSAGE}", entity_id=entity_id, unchecked=True)
    if not await fetch_exposed(ha_url, ha_token, entity_id):
        log.info("voice control of %s refused for %s: not exposed to voice", entity_id, _field(ctx, "user"))
        raise EntityNotExposed(
            f"Voice can't control {spoken_name(entity_id, _NAMES.get(entity_id))}. An admin can turn on 'Voice can control' "
            "for it in Devices.", entity_id=entity_id)
