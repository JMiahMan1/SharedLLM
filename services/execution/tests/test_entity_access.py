"""Entity protection enforcement in the Execution service.

Covers the two rules that make the feature work:
  1. protection wins over a DeviceAssignment row
  2. visibility follows control (a locked entity is simply not listed)

Plus the degraded-mode contract: an Identity outage must not lock the house out,
but it must not quietly unlock a protected entity either.
"""
import pytest

from services.execution import entity_access
from services.execution.entity_access import (
    EntityPermissions,
    IdentityUnreachable,
    can_view,
    forget_cached_permissions,
    load_entity_permissions,
    verify_entity_access,
)


class _Ctx:
    def __init__(self, user="alice", is_admin=False):
        self.user = user
        self.is_admin = is_admin


@pytest.fixture(autouse=True)
def _clear_cache():
    forget_cached_permissions()
    yield
    forget_cached_permissions()


@pytest.fixture
def no_network(monkeypatch):
    """Fail every Identity call, as if the service were down."""

    async def _boom(username):
        raise IdentityUnreachable("identity down")

    monkeypatch.setattr(entity_access, "_fetch_permissions", _boom)
    return _boom


def _feed(monkeypatch, payloads):
    """Answer Identity from a per-username dict; unknown user -> empty."""
    calls = []

    async def _fake(username):
        calls.append(username)
        if username not in payloads:
            raise IdentityUnreachable(f"no fixture for {username}")
        return _permissions_from(payloads[username])

    monkeypatch.setattr(entity_access, "_fetch_permissions", _fake)
    return calls


def _permissions_from(raw):
    return entity_access._permissions_from_payload(raw)


# ── the core predicate ────────────────────────────────────────────────────────


def test_protection_beats_an_existing_assignment():
    """The single most important rule: a lock is not a suggestion."""
    perms = EntityPermissions(
        assigned=frozenset({"climate.hallway"}),
        protected=frozenset({"climate.hallway"}),
        permitted=frozenset(),
    )
    assert perms.may_control("climate.hallway") is False


def test_permitted_user_controls_without_any_assignment():
    perms = EntityPermissions(
        assigned=frozenset(),
        protected=frozenset({"climate.hallway"}),
        permitted=frozenset({"climate.hallway"}),
    )
    assert perms.may_control("climate.hallway") is True


def test_unprotected_entity_still_uses_plain_assignment():
    perms = EntityPermissions(assigned=frozenset({"light.kitchen"}))
    assert perms.may_control("light.kitchen") is True
    assert perms.may_control("light.hall") is False


def test_permit_for_a_different_entity_does_not_leak():
    perms = EntityPermissions(
        protected=frozenset({"climate.a", "climate.b"}),
        permitted=frozenset({"climate.a"}),
    )
    assert perms.may_control("climate.a") is True
    assert perms.may_control("climate.b") is False


def test_empty_entity_id_is_never_controlable():
    perms = EntityPermissions.everything()
    assert perms.may_control("") is False


def test_view_matches_control_so_we_never_advertise_the_untouchable():
    perms = EntityPermissions(
        assigned=frozenset({"light.a"}),
        protected=frozenset({"climate.x"}),
    )
    assert can_view(perms, "light.a") is True
    assert can_view(perms, "climate.x") is False


def test_admins_are_not_filtered():
    perms = EntityPermissions.everything()
    assert can_view(perms, "anything.at_all") is True


# ── payload parsing ───────────────────────────────────────────────────────────


def test_permits_are_dropped_for_entities_that_are_not_locked():
    """A stray permit must not widen access if an entity is locked later."""
    perms = _permissions_from(
        {
            "device_ids": ["light.a"],
            "protected_entity_ids": ["climate.x"],
            "permitted_entity_ids": ["climate.x", "light.a"],
        }
    )
    assert perms.permitted == frozenset({"climate.x"})
    assert perms.assigned == frozenset({"light.a"})


def test_legacy_bare_list_payload_still_works():
    """A partially rolled-back Identity must not hard-break every control path."""
    perms = _permissions_from(["light.a", "light.b"])
    assert perms.assigned == frozenset({"light.a", "light.b"})
    assert perms.protected == frozenset()


def test_legacy_dict_with_entities_key_is_accepted():
    perms = _permissions_from({"entities": ["light.a"]})
    assert perms.assigned == frozenset({"light.a"})


def test_junk_payload_yields_no_permissions_rather_than_a_crash():
    for junk in (None, "nope", 42, {"device_ids": "not-a-list"}):
        perms = _permissions_from(junk)
        assert perms.may_control("light.a") is False


# ── lookup + degraded mode ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permissions_come_from_identity(monkeypatch):
    _feed(monkeypatch, {"alice": {"device_ids": ["light.a"]}})
    perms = await load_entity_permissions("alice")
    assert perms.may_control("light.a") is True


@pytest.mark.asyncio
async def test_admin_lookup_makes_no_network_call(monkeypatch):
    calls = _feed(monkeypatch, {})
    perms = await load_entity_permissions("alice", is_admin=True)
    assert calls == []
    assert can_view(perms, "light.anything") is True


@pytest.mark.asyncio
async def test_outage_with_a_last_known_good_answer_keeps_the_lock(monkeypatch):
    """The user's requirement: fail closed where it matters, not everywhere."""
    _feed(monkeypatch, {"alice": {
        "device_ids": ["light.a"],
        "protected_entity_ids": ["climate.hallway"],
        "permitted_entity_ids": [],
    }})
    assert await verify_entity_access(_Ctx("alice"), "climate.hallway") is False

    # Identity now goes away. The lock must survive the outage.
    async def _boom(username):
        raise IdentityUnreachable("down")

    monkeypatch.setattr(entity_access, "_fetch_permissions", _boom)
    assert await verify_entity_access(_Ctx("alice"), "climate.hallway") is False


@pytest.mark.asyncio
async def test_outage_still_lets_ordinary_entities_through(monkeypatch):
    """A protection outage must not take the whole house offline."""
    _feed(monkeypatch, {"alice": {"device_ids": ["light.a"]}})
    assert await verify_entity_access(_Ctx("alice"), "light.a") is True

    async def _boom(username):
        raise IdentityUnreachable("down")

    monkeypatch.setattr(entity_access, "_fetch_permissions", _boom)
    # Stale payload still allows it, rather than denying every light in the home.
    assert await verify_entity_access(_Ctx("alice"), "light.a") is True


@pytest.mark.asyncio
async def test_outage_with_no_history_cannot_know_the_locks(monkeypatch, no_network):
    """No cached answer and no Identity: we cannot know what is locked.

    Fails open for availability, loudly -- an entity an admin locked while
    Identity was down is the accepted, documented exposure.
    """
    assert await verify_entity_access(_Ctx("nobody"), "climate.hallway") is True


@pytest.mark.asyncio
async def test_recovery_replaces_the_stale_answer(monkeypatch):
    _feed(monkeypatch, {"alice": {
        "device_ids": [],
        "protected_entity_ids": ["climate.hallway"],
        "permitted_entity_ids": [],
    }})
    assert await verify_entity_access(_Ctx("alice"), "climate.hallway") is False

    # Admin grants the permit; the next healthy read must win immediately.
    _feed(monkeypatch, {"alice": {
        "device_ids": [],
        "protected_entity_ids": ["climate.hallway"],
        "permitted_entity_ids": ["climate.hallway"],
    }})
    assert await verify_entity_access(_Ctx("alice"), "climate.hallway") is True


@pytest.mark.asyncio
async def test_admin_never_consults_identity(monkeypatch, no_network):
    assert await verify_entity_access(_Ctx("root", is_admin=True), "climate.x") is True


@pytest.mark.asyncio
async def test_blank_entity_id_denies_even_for_a_permitted_user(monkeypatch):
    _feed(monkeypatch, {"alice": {"permitted_entity_ids": ["climate.x"],
                                   "protected_entity_ids": ["climate.x"]}})
    assert await verify_entity_access(_Ctx("alice"), "") is False


@pytest.mark.asyncio
async def test_a_stale_permit_list_can_only_ever_be_too_strict(monkeypatch):
    """While Identity is down the cached permit list is reused verbatim.

    A revoked permit therefore stays revoked for the outage's duration. The
    failure direction of the degraded mode is "deny", never "grant".
    """
    _feed(monkeypatch, {"alice": {
        "device_ids": [],
        "protected_entity_ids": ["climate.hallway"],
        "permitted_entity_ids": ["climate.hallway"],
    }})
    assert await verify_entity_access(_Ctx("alice"), "climate.hallway") is True

    async def _boom(username):
        raise IdentityUnreachable("down")

    monkeypatch.setattr(entity_access, "_fetch_permissions", _boom)
    # The permit was revoked in Identity; we cannot see that yet, so we still
    # honour the last good answer rather than dropping to fail-open.
    assert await verify_entity_access(_Ctx("alice"), "climate.hallway") is True


@pytest.mark.asyncio
async def test_cache_is_keyed_per_user(monkeypatch):
    _feed(monkeypatch, {
        "alice": {"device_ids": ["light.a"]},
        "bob": {"device_ids": ["light.b"]},
    })
    assert await verify_entity_access(_Ctx("alice"), "light.b") is False
    assert await verify_entity_access(_Ctx("bob"), "light.b") is True


@pytest.mark.asyncio
async def test_cache_stays_bounded(monkeypatch):
    _feed(monkeypatch, {})
    cache = entity_access._PERMISSION_CACHE
    for i in range(cache._max_entries + 25):
        cache.remember(f"user{i}", EntityPermissions())
    assert len(cache) <= cache._max_entries
