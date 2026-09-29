"""
Entity-level access control for the Execution service.

Identity is the single authority. It answers one question per user:

    GET /api/internal/user-device-assignments?username=<user>
      device_ids            -> plain DeviceAssignment grants (unprotected entities)
      protected_entity_ids  -> every locked entity (NOT a grant; only says it is locked)
      permitted_entity_ids  -> the locked entities this user may actually control

Two rules, and they are the whole feature:

1. Protection wins. A locked entity ignores its DeviceAssignment row entirely;
   only admins (plus Identity's system-default user) and the entity's own permit
   list may control it.
2. Visibility follows control. An entity you may not control is not listed at
   all, so a locked entity simply does not exist as far as your UI is concerned.

Availability vs. safety
------------------------
Identity is a network hop, so it can be down. Failing *everything* closed would
take the house offline; failing everything open would quietly unlock the front
door. Instead we keep the last known good permission payload per user and
degrade per-entity:

  * Identity answered            -> use the fresh answer.
  * Identity failed, we have a
    last known good payload      -> use it. A locked entity is therefore still
                                    locked (its permit list is stale, not absent),
                                    while an ordinary entity keeps its
                                    availability-first behaviour.
  * Identity failed, no payload  -> we know nothing at all, so fall back to
                                    fail-open for plain entities and log loudly.
                                    A lock cannot be honoured when the list of
                                    locks is unknown.

The stale-payload window is bounded by the outage, and the failure direction for
protection is always "deny", never "grant".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import aiohttp

from services.common.http import get_client
from services.common.last_known_good import LastKnownGoodCache
from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

log = logging.getLogger("execution.entity_access")

# Identity is internal and local; if it has not answered this quickly something
# is genuinely wrong rather than merely slow.
_LOOKUP_TIMEOUT = aiohttp.ClientTimeout(total=5.0)


class IdentityUnreachable(RuntimeError):
    """Identity did not produce a usable permission answer."""


def _id_set(value: object) -> frozenset[str]:
    """Coerce a JSON list of ids to a frozenset, ignoring junk entries."""
    if not isinstance(value, (list, tuple, set)):
        return frozenset()
    return frozenset(str(item).strip() for item in value if str(item).strip())


@dataclass(frozen=True)
class EntityPermissions:
    """What one user may see and control."""

    # Plain DeviceAssignment grants. Applies only to unprotected entities.
    assigned: frozenset[str] = field(default_factory=frozenset)
    # Every locked entity id. A membership here means "assignment is ignored".
    protected: frozenset[str] = field(default_factory=frozenset)
    # The locked entities this user may control.
    permitted: frozenset[str] = field(default_factory=frozenset)
    # Admins bypass filtering entirely. A flag rather than a wildcard entry in
    # `assigned`, because a magic id would have to be honoured by every
    # membership test and would leak into payloads and comparisons.
    unrestricted: bool = False

    def may_control(self, entity_id: str) -> bool:
        """Protection wins: a locked entity ignores its assignment row."""
        if not entity_id:
            return False
        if self.unrestricted:
            return True
        if entity_id in self.protected:
            return entity_id in self.permitted
        return entity_id in self.assigned

    def may_view(self, entity_id: str) -> bool:
        """Same predicate as control: we do not advertise what you cannot use."""
        return self.may_control(entity_id)

    @classmethod
    def everything(cls) -> "EntityPermissions":
        """Admins are not filtered; nothing is protected *for* them."""
        return cls(unrestricted=True)


# Last successful answer per username. Doubles as the degraded-mode source so
# protection can fail closed while everything else keeps working.
_PERMISSION_CACHE: "LastKnownGoodCache[EntityPermissions]" = LastKnownGoodCache(
    "entity_access", max_entries=512
)


def forget_cached_permissions(username: str | None = None) -> None:
    """Clear the degraded-mode cache (tests, and the admin UI after a change)."""
    _PERMISSION_CACHE.forget(username)


def _permissions_from_payload(data: object) -> EntityPermissions:
    """Build an EntityPermissions from Identity's JSON response.

    A bare list is still accepted for `device_ids` so a partially-rolled-back
    Identity cannot hard-break every control path.
    """
    if isinstance(data, dict):
        assigned = _id_set(data.get("device_ids") or data.get("entities") or [])
        protected = _id_set(data.get("protected_entity_ids") or [])
        permitted = _id_set(data.get("permitted_entity_ids") or [])
    elif isinstance(data, (list, tuple)):
        assigned, protected, permitted = _id_set(data), frozenset(), frozenset()
    else:
        assigned, protected, permitted = frozenset(), frozenset(), frozenset()

    # A permit for something that is not locked is meaningless; ignore it rather
    # than letting it widen access later if the entity ever gets locked.
    permitted = permitted & protected
    return EntityPermissions(assigned=assigned, protected=protected, permitted=permitted)


async def _fetch_permissions(username: str) -> EntityPermissions:
    """One live read of Identity's permission answer.

    Raises on any non-200 or transport failure so the caller's last-known-good
    fallback covers both, rather than only the network-error case.
    """
    async with get_client() as client:
        resp = await client.get(
            f"{IDENTITY_SVC_URL}/api/internal/user-device-assignments",
            params={"username": username},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=_LOOKUP_TIMEOUT,
        )
        if resp.status != 200:
            raise IdentityUnreachable(
                f"Identity device-assignments HTTP {resp.status} for {username}"
            )
        return _permissions_from_payload(await resp.json())


async def load_entity_permissions(
    username: str,
    is_admin: bool = False,
) -> EntityPermissions | None:
    """Ask Identity what this user may control.

    Returns ``None`` only when Identity is unreachable *and* we have no last
    known good answer for this user. A ``None`` result means "do not filter".
    Admins short-circuit without a network call.
    """
    if is_admin:
        return EntityPermissions.everything()
    if not username:
        return EntityPermissions()

    perms, is_stale = await _PERMISSION_CACHE.fetch_or_recall(
        username, lambda: _fetch_permissions(username)
    )
    if is_stale:
        log.warning(
            "Serving stale entity permissions for %s; a locked entity stays "
            "locked but its permit list may be out of date",
            username,
        )
    return perms


async def verify_entity_access(ctx, entity_id: str) -> bool:
    """May this user control this entity?

    Admins bypass. Identity unreachable with no cached answer means we cannot
    know, and we allow (availability first) -- but that is only reachable for a
    user we have never successfully looked up, and it is logged.
    """
    if getattr(ctx, "is_admin", False):
        return True
    if not entity_id:
        return False

    username = getattr(ctx, "user", "") or ""
    perms = await load_entity_permissions(username, getattr(ctx, "is_admin", False))
    if perms is None:
        log.warning(
            "Access check for %s on %s: ALLOW (Identity unreachable and no cached "
            "permissions) — entity protection cannot be enforced right now",
            username,
            entity_id,
        )
        return True

    ok = perms.may_control(entity_id)
    if not ok:
        locked = entity_id in perms.protected
        log.info(
            "Access check for %s on %s: DENIED (%s)",
            username,
            entity_id,
            "protected entity, not on its permit list" if locked else "not assigned",
        )
    return ok


def can_view(perms: EntityPermissions | None, entity_id: str) -> bool:
    """Should this entity appear in a listing for the caller?"""
    if perms is None:
        return True
    return perms.may_view(entity_id)
