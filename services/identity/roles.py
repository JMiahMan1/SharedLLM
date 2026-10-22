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

#: The family roles Identity understands. These are relationships, not roles in
#: the RBAC table: an admin can still give a child an explicit role, but the
#: household defaults below are derived from the relationship.
FAMILY_ROLES: tuple[str, ...] = ("father", "mother", "child", "teen", "adult_child", "other")
MINOR_FAMILY_ROLES = {"child", "teen"}

#: Defaults a relationship contributes on top of any explicitly assigned role.
#: "father" is deliberately absent: fatherhood is not an elevation path, the
#: admin flag is. A mother runs the house but cannot administer accounts.
FAMILY_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "mother": {
        "devices.use_all",
        "devices.use_locked",
        "devices.assign",
        "devices.lock",
        "family.manage",
    },
    "teen": {"devices.use_all"},
    "adult_child": {"devices.use_all"},
}

# Seeded on start when missing (matched by key, so a renamed one stays).
DEFAULT_ROLES: list[dict] = [
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

    Family relationships contribute household defaults (a mother can manage
    devices and family links; teens can use unlocked devices) without adding
    more built-in RBAC roles to the admin page. Explicit roles are still
    unioned in, so an admin can grant a child a narrow extra permission.
    """
    if user.is_admin:
        return set(PERMISSIONS)
    granted = set(FAMILY_ROLE_PERMISSIONS.get(user.family_role or "other", set()))
    ids = user_role_ids(session, user)
    if ids:
        held = session.exec(select(Role).where(Role.id.in_(ids))).all()  # type: ignore[union-attr]
        granted |= {key for role in held for key in role_permissions(role)}
    return granted


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
