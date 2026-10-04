"""Who may see whose chores.

The Skylight account is one shared household login, so the *credentials* are the
same for everyone; what differs is whose chores you are looking at. Skylight
labels chores with a category per family member, and the execution service
filters on that label (lenient, case-insensitive, either-direction substring
match), so "whose chores" reduces to "which login name to filter by".

An admin is not automatically looking at everybody's chores forever: they are a
family member too, and often have chores of their own. They get to choose --
`scope=me` for their own, `scope=all` (the default) for the whole frame -- which
is why this is an explicit, authorized decision rather than a boolean.
"""

# `scope` values that mean "the whole family frame".
WHOLE_FRAME_ALIASES = frozenset({"", "all", "everyone", "family", "*"})

# `scope` value that means "the caller themself", whoever they are.
SELF = "me"


class ChoreScopeError(Exception):
    """The caller asked for chores they are not allowed to see."""


def _clean(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def resolve_chore_scope(
    *,
    scope: object = None,
    caller_user: object = "",
    is_admin: bool = False,
) -> str:
    """Return the login name to filter chores by ('' means the whole frame).

    ``scope`` accepts:

    * ``None`` / ``""`` / ``"all"`` -- an admin sees the whole frame (today's
      behaviour), anyone else sees their own chores.
    * ``"me"`` -- the caller's own chores, admin or not.
    * any other name -- only an admin may ask for someone else's chores by name;
      a non-admin asking for a name that is not their own is refused rather than
      silently downgraded, so a typo never shows the wrong person's chores.
    """
    caller = _clean(caller_user)
    wanted = _clean(scope).lower()

    if wanted in WHOLE_FRAME_ALIASES:
        return "" if is_admin else caller

    if wanted == SELF:
        if not caller:
            # '' means "no filter" upstream, so a nameless caller asking for "me"
            # would silently receive the whole family's chores. Refuse instead.
            raise ChoreScopeError(
                "This account has no login name to match chores against."
            )
        return caller

    if not is_admin:
        # A non-admin may name themselves, in any casing. Anything else is a
        # refusal, not a fallback: quietly returning their own chores would hide
        # the fact that the request was not allowed.
        if caller and wanted == caller.lower():
            return caller
        raise ChoreScopeError(
            "You can only view your own chores; ask an admin for the family list."
        )

    return wanted