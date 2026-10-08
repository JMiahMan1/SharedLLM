"""Account audit trail: who changed which account fields, never the values.

Every route that alters an account records one :class:`AuditEvent`. A change is
described by field name and kind ("set", "changed", "cleared"); a secret's
value never reaches this table or the log, and neither does a plain value
except for boolean flags, whose new state is harmless and useful ("is_admin
-> true").
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from services.identity.models import AuditEvent, User

log = logging.getLogger("identity.audit")

# Columns never worth recording a change to: they move on their own.
_IGNORED_COLUMNS = {"id", "last_seen", "updated_at"}


def _public_name(column: str) -> str:
    """nextcloud_pass_enc -> nextcloud_pass, the name a person would recognise."""
    for suffix in ("_enc", "_hash"):
        if column.endswith(suffix):
            return column[: -len(suffix)]
    return column


def is_secret_column(column: str) -> bool:
    return column.endswith(("_enc", "_hash")) or column in {"api_key", "password_hash"}


def snapshot(user: User | None) -> dict[str, Any]:
    """Every column of ``user`` as it stands, for diffing after a change."""
    if user is None:
        return {}
    return {name: getattr(user, name, None) for name in User.model_fields if name not in _IGNORED_COLUMNS}


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """What changed between two snapshots, described without values."""
    changes: list[dict[str, Any]] = []
    for column in sorted(set(before) | set(after)):
        old, new = before.get(column), after.get(column)
        if old == new or (not old and not new and not isinstance(old, bool) and not isinstance(new, bool)):
            continue
        entry: dict[str, Any] = {"field": _public_name(column)}
        if isinstance(new, bool) or isinstance(old, bool):
            entry["change"] = "changed"
            entry["to"] = bool(new)
        elif not old:
            entry["change"] = "set"
        elif not new:
            entry["change"] = "cleared"
        else:
            entry["change"] = "changed"
        if is_secret_column(column):
            entry["secret"] = True
        changes.append(entry)
    return changes


def record(
    session: Session,
    *,
    actor: str,
    actor_kind: str,
    action: str,
    target: str | None = None,
    changes: list[dict[str, Any]] | None = None,
    source: str | None = None,
    client: str | None = None,
    user_agent: str | None = None,
    note: str | None = None,
    commit: bool = True,
) -> AuditEvent:
    """Write one event (and a matching log line) and return it."""
    event = AuditEvent(
        at=datetime.now(UTC).isoformat(),
        actor=actor,
        actor_kind=actor_kind,
        action=action,
        target=target,
        changes=json.dumps(changes or []),
        source=source,
        client=client,
        user_agent=(user_agent or "")[:200] or None,
        note=note,
    )
    session.add(event)
    if commit:
        session.commit()
    fields = ", ".join(f"{c['field']}:{c['change']}" for c in (changes or [])) or "-"
    log.info(f"[audit] {actor} ({actor_kind}) {action} target={target or '-'} fields=[{fields}] via {source or '-'}")
    return event


def as_dict(event: AuditEvent) -> dict[str, Any]:
    data = event.model_dump()
    try:
        data["changes"] = json.loads(event.changes or "[]")
    except ValueError:
        data["changes"] = []
    return data
