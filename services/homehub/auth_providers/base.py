"""What a login provider is: who the users are, how one logs in, what each may use.

The hub always has the ``local`` provider (its own users and passwords, as imported from Home Assistant
or added by an admin). An external provider (``jarvis_identity`` today; an OpenID Connect provider would be
another) supplies users from somewhere else. :mod:`services.homehub.auth_providers.sync` mirrors them into
the hub's users and persons, and :mod:`services.homehub.auth_providers.manager` sends logins to them. Neither
of those, nor :mod:`services.homehub.auth`, knows anything about a particular provider: a new provider is a
subclass of :class:`AuthProvider` registered in ``manager.PROVIDER_TYPES``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """A provider could not do what was asked."""


class ProviderUnavailable(ProviderError):
    """The provider did not answer (down, misconfigured). Nothing about the user is known."""


class LoginRejected(ProviderError):
    """Wrong username or password, or an account that may not log in."""


class LoginLocked(ProviderError):
    """Too many failed logins: the provider refuses this username for a while."""


@dataclass(frozen=True)
class ProviderUser:
    """One user as a provider describes them. ``key`` is stable for the life of the account."""

    key: str
    username: str
    name: str
    is_admin: bool = False
    disabled: bool = False
    avatar: str | None = None
    #: Words that describe the user's place in the household (``"father"``, ``"guest"``...), shown, never checked.
    roles: tuple[str, ...] = ()
    #: Whether the account can log in at all (has a password); an admin who cannot is no way into the hub.
    can_login: bool = True
    #: Whether the user is a person in the house (gets a ``person.*``); a provider's service account is not.
    person: bool = True


@dataclass(frozen=True)
class EntityAccess:
    """What a user whose provider limits entities may see and control.

    The rules are Jarvis's (``services/execution/entity_access.py``): a locked entity ignores its assignment and
    needs a permit; otherwise a role may grant every entity, or the user has the ones assigned to them. Seeing
    follows control. ``None`` in place of an ``EntityAccess`` means no limits at all.
    """

    assigned: frozenset[str] = field(default_factory=frozenset)
    protected: frozenset[str] = field(default_factory=frozenset)
    permitted: frozenset[str] = field(default_factory=frozenset)
    all_unprotected: bool = False

    def may_control(self, entity_id: str) -> bool:
        if not entity_id:
            return False
        if entity_id in self.protected:
            return entity_id in self.permitted
        return self.all_unprotected or entity_id in self.assigned

    def may_view(self, entity_id: str) -> bool:
        return self.may_control(entity_id)

    def snapshot(self) -> dict[str, Any]:
        return {"assigned": sorted(self.assigned), "protected": sorted(self.protected),
                "permitted": sorted(self.permitted), "all_unprotected": self.all_unprotected}

    @classmethod
    def from_snapshot(cls, data: Any) -> EntityAccess:
        """A stored snapshot back; anything unreadable is "nothing at all" (never everything)."""
        if not isinstance(data, dict):
            return cls()

        def ids(key: str) -> frozenset[str]:
            raw = data.get(key)
            return frozenset(str(x) for x in raw if str(x).strip()) if isinstance(raw, list) else frozenset()

        protected = ids("protected")
        return cls(assigned=ids("assigned"), protected=protected, permitted=ids("permitted") & protected,
                   all_unprotected=data.get("all_unprotected") is True)


class AuthProvider:
    """One source of users. Subclasses set ``type``/``label`` and implement what they support."""

    type: str = ""
    label: str = ""
    #: External providers' users are mirrored into the hub (``sync``); ``local`` is the hub itself.
    external: bool = True
    #: Whether this provider's usernames are Jarvis usernames (what a person's location follows).
    jarvis_usernames: bool = False

    async def list_users(self) -> list[ProviderUser]:
        """Every user the provider has. Raises :class:`ProviderUnavailable`."""
        raise NotImplementedError

    async def verify_login(self, username: str, password: str) -> str:
        """The user's ``key`` for a right username and password.

        Raises :class:`LoginRejected`, :class:`LoginLocked` or :class:`ProviderUnavailable`. The provider applies
        its own rate limits and lockouts; the password goes nowhere but the provider.
        """
        raise NotImplementedError

    async def entity_access(self, user: ProviderUser) -> EntityAccess | None:
        """The user's entity limits; ``None`` when this provider does not limit entities. Admins are never asked."""
        return None

    def describe(self) -> dict[str, Any]:
        """Non-secret settings to show an admin (an address, never a secret)."""
        return {}
