"""Roles: named sets of permissions an admin gives to users.

The permissions are a fixed list, each one checked somewhere in the code; a
role only bundles them. Admins hold every permission without a role. A user
can hold several roles and gets everything they add up to.
"""
import json
import logging

from sqlmodel import Session, select

from services.identity.models import Role, User, UserRole

log = logging.getLogger("identity.roles")

# Permission key -> what it lets someone do, as the admin page shows it.
PERMISSIONS: dict[str, str] = {
    "devices.use_all": "Control every device that is not locked",
    "devices.use_locked": "Control locked devices too",
    "devices.assign": "Choose who can use each device",
    "devices.lock": "Lock and unlock devices, and choose who may use a locked one",
    "family.manage": "Manage family roles, mother/child links and household sharing",
}

#: The family roles Identity understands. These are relationships whose
#: permissions come from the matching built-in role below, so the household
#: roles live in the same table the admin Roles panel edits.
FAMILY_ROLES: tuple[str, ...] = ("father", "mother", "child", "teen", "adult_child", "other")
MINOR_FAMILY_ROLES = {"child", "teen"}

#: Relationship -> built-in role key. Each family role below is a real role in
#: the Roles table, seeded on start and assigned when the relationship is set.
FAMILY_ROLE_BUILTIN: dict[str, str] = {
    "father": "father",
    "mother": "mother",
    "teen": "teen",
    "adult_child": "adult_child",
    "child": "child",
}

#: Every built-in key that represents a family relationship. Used by
#: ``sync_family_role`` to replace at most one of them per person.
FAMILY_BUILTINS = set(FAMILY_ROLE_BUILTIN.values())

# Seeded on start when missing (matched by key, so a renamed one stays).
DEFAULT_ROLES: list[dict] = [
    {
        "builtin": "father",
        "name": "Father (admin)",
        "description": "The admin account. The admin flag does the work; this role only labels the relationship.",
        "permissions": [],
    },
    {
        "builtin": "mother",
        "name": "Mother",
        "description": "Runs the household: every device, family links and family sharing. No account or server settings.",
        "permissions": ["devices.use_all", "devices.use_locked", "devices.assign", "devices.lock", "family.manage"],
    },
    {
        "builtin": "adult_child",
        "name": "Adult child",
        "description": "An adult in the household: every device that is not locked, with their own sharing choices.",
        "permissions": ["devices.use_all"],
    },
    {
        "builtin": "teen",
        "name": "Teen",
        "description": "A minor in the household: every device that is not locked. Family circle sharing stays on.",
        "permissions": ["devices.use_all"],
    },
    {
        "builtin": "child",
        "name": "Child",
        "description": "A minor in the household: only the devices assigned to them. Family circle sharing stays on.",
        "permissions": [],
    },
    {
        "builtin": "home_manager",
        "name": "Home manager",
        "description": "Runs the house: every device, who uses which, family roles and locks. No say over accounts or server settings.",
        "permissions": ["devices.use_all", "devices.use_locked", "devices.assign", "devices.lock", "family.manage"],
    },
    {
        "builtin": "family_member",
        "name": "Family member",
        "description": "Every device that is not locked.",
        "permissions": ["devices.use_all"],
    },
    {
        "builtin": "guest",
        "name": "Guest",
        "description": "Only the devices assigned to them.",
        "permissions": [],
    },
]


def role_permissions(role: Role) -> list[str]:
    """A role's permissions; unknown or corrupt entries grant nothing."""
    try:
        keys = json.loads(role.permissions or "[]")
    except ValueError:
        log.error("Role %s has unreadable permissions; treating as none", role.name)
        return []
    return [k for k in keys if k in PERMISSIONS] if isinstance(keys, list) else []


def clean_permissions(keys: list[str]) -> list[str]:
    """Validate a permission list from a request, in catalog order."""
    unknown = sorted(set(keys) - PERMISSIONS.keys())
    if unknown:
        raise ValueError(f"Unknown permission: {', '.join(unknown)}")
    return [k for k in PERMISSIONS if k in keys]


def user_role_ids(session: Session, user: User) -> list[int]:
    rows = session.exec(select(UserRole.role_id).where(UserRole.user_id == (user.id or 0))).all()
    return sorted(rows)


def user_permissions(session: Session, user: User) -> set[str]:
    """Everything this user may do; an admin may do all of it.

    Family relationships are ordinary roles in the table (Father, Mother,
    Teen, Adult child, Child) and are kept in sync by ``sync_family_role``, so
    there is one place permissions come from instead of a parallel system.
    """
    if user.is_admin:
        return set(PERMISSIONS)
    granted: set[str] = set()
    ids = user_role_ids(session, user)
    if ids:
        held = session.exec(select(Role).where(Role.id.in_(ids))).all()  # type: ignore[union-attr]
        granted |= {key for role in held for key in role_permissions(role)}
    return granted


def sync_family_role(session: Session, user: User) -> bool:
    """Point this person's family built-in role at their relationship.

    Called when a user is created, when a family role changes and once at
    startup so existing households are reconciled. Explicit roles a person
    holds are never touched. Returns True when a row changed.
    """
    wanted = FAMILY_ROLE_BUILTIN.get(user.family_role or "other")
    builtins = {
        role.builtin: role
        for role in session.exec(select(Role).where(Role.builtin.in_(tuple(FAMILY_BUILTINS)))).all()  # type: ignore[union-attr]
    }
    if not builtins:
        return False
    held = set(user_role_ids(session, user))
    changed = False
    for key, role in builtins.items():
        if role.id is None:
            continue
        should_hold = key == wanted
        holds = role.id in held
        if should_hold and not holds:
            session.add(UserRole(user_id=user.id or 0, role_id=role.id))
            changed = True
        elif not should_hold and holds:
            row = session.exec(
                select(UserRole).where(
                    UserRole.user_id == (user.id or 0),
                    UserRole.role_id == role.id,
                )
            ).first()
            if row is not None:
                session.delete(row)
                changed = True
    return changed


def has_permission(session: Session, user: User, permission: str) -> bool:
    return permission in user_permissions(session, user)


def seed_default_roles(session: Session) -> None:
    """Add any built-in role that is missing. Never changes an existing one."""
    have = set(session.exec(select(Role.builtin).where(Role.builtin.is_not(None))).all())  # type: ignore[union-attr]
    taken = set(session.exec(select(Role.name)).all())
    added = False
    for spec in DEFAULT_ROLES:
        if spec["builtin"] in have:
            continue
        name = spec["name"]
        if name in taken:  # an admin already made a role by that name
            name = f"{name} (default)"
        session.add(Role(
            name=name,
            description=spec["description"],
            permissions=json.dumps(spec["permissions"]),
            builtin=spec["builtin"],
        ))
        added = True
    if added:
        session.commit()
