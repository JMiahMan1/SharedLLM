"""One person's rows out of a service, and back in, for the account export.

Each service that holds data for a person declares its tables as ``Table``
specs and mounts two internal-secret-only routes (export and import) on top of
``export_tables`` and ``import_tables``. The gateway gathers the exports into
one stream that the person's device encrypts (docs/ACCOUNT_EXPORT.md); a
restore comes back through the import routes one service at a time.

Rules that hold for every table:

* Only the subject's rows leave: every query filters on the owner column (or,
  for a child table, on the parent rows the subject owns).
* Secrets never leave: a column is dropped when its name looks like a secret
  (``is_secret_name``) or the spec lists it in ``exclude``. The export of a
  whole service is checked again before it is returned (``assert_no_secrets``).
* On import the owner column is always set to the subject, whatever the file
  says, so a file can never write into someone else's account.
* Merging is by a stable id: the spec's ``natural_key`` within the owner when
  it has one, else the row's primary key when that row belongs to the subject.
  A row with no match is added (keeping its id when that id is free).
* Newer data is never overwritten without asking: when both sides have an
  ``updated`` time and the server's is newer, or when a row differs and there
  is no time to compare, it is a *conflict* and is only replaced when the
  caller passes ``replace_conflicts``. A row the file has as newer is an
  *update*. Rows in the account that the file does not have are left alone:
  a restore adds and refreshes, it never deletes.
"""
from __future__ import annotations

import base64
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.types import Date, DateTime, LargeBinary, Time
from sqlmodel import Session, select

#: Column names that hold a credential or a verifier. Checked on every export.
_SECRET_NAME = re.compile(
    r"(_enc|_hash|password|passwd|_token|^token$|secret|api_key|key_value|^salt$|^iterations$|private_key)",
    re.IGNORECASE,
)


def is_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name))


@dataclass(frozen=True)
class Table:
    """How one table is exported and merged back.

    ``name`` is the collection's name in the export (stable across versions).
    ``owner`` is the column naming the person (a username, or a numeric user
    id when ``owner_is_id``); a child table has no owner and names its
    ``parent`` collection and the ``parent_fk`` column pointing at it.
    ``updated`` names the column that says when a row last changed, if any.
    """

    name: str
    model: type
    owner: str | None = None
    owner_is_id: bool = False
    natural_key: tuple[str, ...] = ()
    updated: str | None = None
    exclude: frozenset[str] = field(default_factory=frozenset)
    parent: str | None = None
    parent_fk: str | None = None
    #: Columns the import never writes (kept as the server has them).
    read_only: frozenset[str] = field(default_factory=frozenset)
    #: Rows exported but never restored (e.g. history the server owns).
    export_only: bool = False


@dataclass
class Subject:
    username: str
    user_id: int | None = None

    def owner_value(self, spec: Table) -> Any:
        return self.user_id if spec.owner_is_id else self.username


def _columns(model: type) -> dict[str, Any]:
    return {c.key: c for c in sa_inspect(model).columns}


def _pk(model: type) -> list[str]:
    return [c.key for c in sa_inspect(model).primary_key]


def _to_json(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return {"$base64": base64.b64encode(bytes(value)).decode()}
    return value


def _from_json(value: Any, column: Any) -> Any:
    if value is None:
        return None
    kind = column.type
    # A TypeDecorator (e.g. a UTC-aware datetime) stores through its impl.
    kind = getattr(kind, "impl_instance", None) or getattr(kind, "impl", kind)
    if isinstance(kind, type):
        kind = kind()
    try:
        if isinstance(kind, DateTime) and isinstance(value, str):
            return datetime.fromisoformat(value)
        if isinstance(kind, Date) and isinstance(value, str):
            return date.fromisoformat(value)
        if isinstance(kind, Time) and isinstance(value, str):
            return time.fromisoformat(value)
        if isinstance(kind, LargeBinary) and isinstance(value, dict) and "$base64" in value:
            return base64.b64decode(value["$base64"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{column.key}: {exc}") from exc
    return value


def _exported_columns(spec: Table) -> list[str]:
    return [name for name in _columns(spec.model) if name not in spec.exclude and not is_secret_name(name)]


def row_out(spec: Table, row: Any) -> dict[str, Any]:
    return {name: _to_json(getattr(row, name)) for name in _exported_columns(spec)}


def _owned_rows(session: Session, spec: Table, subject: Subject, parent_ids: dict[str, list[Any]]) -> list[Any]:
    model = spec.model
    if spec.parent:
        ids = parent_ids.get(spec.parent) or []
        if not ids:
            return []
        return list(session.exec(select(model).where(getattr(model, spec.parent_fk).in_(ids))).all())
    owner = subject.owner_value(spec)
    if owner is None:
        return []
    return list(session.exec(select(model).where(getattr(model, spec.owner) == owner)).all())


def export_tables(session: Session, specs: Iterable[Table], subject: Subject) -> dict[str, list[dict[str, Any]]]:
    """Every listed table's rows for the subject, secrets left out."""
    out: dict[str, list[dict[str, Any]]] = {}
    parent_ids: dict[str, list[Any]] = {}
    for spec in specs:
        rows = _owned_rows(session, spec, subject, parent_ids)
        pk = _pk(spec.model)
        parent_ids[spec.name] = [getattr(r, pk[0]) for r in rows] if len(pk) == 1 else []
        out[spec.name] = [row_out(spec, r) for r in rows]
    assert_no_secrets(out)
    return out


def assert_no_secrets(collections: dict[str, list[dict[str, Any]]]) -> None:
    for name, rows in collections.items():
        for row in rows:
            for key in row:
                if is_secret_name(key):
                    raise RuntimeError(f"export of {name} would carry a secret column ({key})")


def _same(existing: Any, incoming: dict[str, Any], spec: Table, skip: set[str]) -> bool:
    cols = _columns(spec.model)
    for name, value in incoming.items():
        if name in skip or name not in cols:
            continue
        mine = _to_json(getattr(existing, name))
        if mine != value:
            return False
    return True


def _as_time(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        # Epoch seconds or milliseconds: both compare correctly against their own kind.
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp() if value.tzinfo else value.replace(tzinfo=None).timestamp()
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed.timestamp() if parsed.tzinfo else parsed.replace(tzinfo=None).timestamp()
    return None


@dataclass
class TableResult:
    add: int = 0
    update: int = 0
    conflict: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "add": self.add,
            "update": self.update,
            "conflict": self.conflict,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "errors": self.errors[:20],
        }


MAX_ROWS_PER_TABLE = 200_000


def import_tables(
    session: Session,
    specs: Iterable[Table],
    subject: Subject,
    collections: dict[str, Any],
    *,
    dry_run: bool,
    replace_conflicts: bool = False,
) -> dict[str, dict[str, Any]]:
    """Merge the file's rows into the subject's account (see the module docstring).

    With ``dry_run`` nothing is written and the counts say what would happen.
    Returns ``{collection: {add, update, conflict, unchanged, skipped, errors}}``.
    """
    specs = list(specs)
    by_name = {s.name: s for s in specs}
    results: dict[str, dict[str, Any]] = {}
    #: Old id in the file -> id in this account, per collection, for child rows.
    id_maps: dict[str, dict[Any, Any]] = {}
    for spec in specs:
        rows = collections.get(spec.name)
        result = TableResult()
        id_maps[spec.name] = {}
        if not rows:
            results[spec.name] = result.as_dict()
            continue
        if spec.export_only:
            result.skipped = len(rows) if isinstance(rows, list) else 0
            results[spec.name] = result.as_dict()
            continue
        if not isinstance(rows, list) or len(rows) > MAX_ROWS_PER_TABLE:
            result.errors.append("not a list of rows, or too many rows")
            results[spec.name] = result.as_dict()
            continue
        parent_spec = by_name.get(spec.parent or "")
        _merge_table(session, spec, parent_spec, subject, rows, id_maps, result, replace_conflicts=replace_conflicts)
        results[spec.name] = result.as_dict()
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return results


def _merge_table(
    session: Session,
    spec: Table,
    parent_spec: Table | None,
    subject: Subject,
    rows: list[Any],
    id_maps: dict[str, dict[Any, Any]],
    result: TableResult,
    *,
    replace_conflicts: bool,
) -> None:
    model = spec.model
    cols = _columns(model)
    pk = _pk(model)
    single_pk = pk[0] if len(pk) == 1 else None
    writable = set(_exported_columns(spec)) - set(spec.read_only)
    parent_map = id_maps.get(spec.parent or "", {})
    owned_parent_ids: set[Any] = set()
    if spec.parent:
        if parent_spec is None:
            result.errors.append(f"unknown parent collection {spec.parent}")
            return
        session.flush()
        parent_pk = _pk(parent_spec.model)[0]
        owned_parent_ids = {getattr(r, parent_pk) for r in _owned_rows(session, parent_spec, subject, {})}
    for raw in rows:
        if not isinstance(raw, dict):
            result.skipped += 1
            continue
        incoming = {k: v for k, v in raw.items() if k in cols and (k in writable or k in pk)}
        # Never trust the file about whose row this is.
        if spec.owner:
            incoming[spec.owner] = subject.owner_value(spec)
        if spec.parent:
            old_parent = incoming.get(spec.parent_fk)
            new_parent = parent_map.get(old_parent, old_parent)
            if new_parent not in owned_parent_ids:
                result.skipped += 1
                continue
            incoming[spec.parent_fk] = new_parent
        try:
            values = {k: _from_json(v, cols[k]) for k, v in incoming.items()}
        except ValueError as exc:
            result.skipped += 1
            result.errors.append(str(exc))
            continue
        existing = _find_existing(session, spec, subject, values, owned_parent_ids)
        old_id = raw.get(single_pk) if single_pk else None
        if existing is None:
            free = True
            if single_pk and values.get(single_pk) is not None:
                free = session.get(model, values[single_pk]) is None
            if not free:
                if isinstance(values.get(single_pk), str):
                    # A text id taken by another row (another person's, on a new server): a fresh one.
                    values[single_pk] = uuid.uuid4().hex
                else:
                    values.pop(single_pk, None)
            if len(pk) > 1 and any(values.get(k) is None for k in pk):
                result.skipped += 1
                continue
            result.add += 1
            obj = model(**values)
            session.add(obj)
            session.flush()
            if single_pk:
                id_maps[spec.name][old_id] = getattr(obj, single_pk)
            continue
        if single_pk:
            id_maps[spec.name][old_id] = getattr(existing, single_pk)
        compare_skip = set(pk) | ({spec.owner} if spec.owner else set()) | set(spec.read_only)
        if _same(existing, incoming, spec, compare_skip):
            result.unchanged += 1
            continue
        theirs = _as_time(values.get(spec.updated)) if spec.updated else None
        mine = _as_time(getattr(existing, spec.updated)) if spec.updated else None
        newer_in_file = theirs is not None and mine is not None and theirs > mine
        if not newer_in_file:
            result.conflict += 1
            if not replace_conflicts:
                continue
        else:
            result.update += 1
        for key, value in values.items():
            if key in pk or key == spec.owner:
                continue
            setattr(existing, key, value)
        session.add(existing)
    session.flush()


def _find_existing(session: Session, spec: Table, subject: Subject, values: dict[str, Any], owned_parent_ids: set[Any]) -> Any:
    model = spec.model
    pk = _pk(model)
    if spec.natural_key and all(values.get(k) is not None for k in spec.natural_key):
        query = select(model)
        for key in spec.natural_key:
            query = query.where(getattr(model, key) == values[key])
        if spec.owner:
            query = query.where(getattr(model, spec.owner) == subject.owner_value(spec))
        if spec.parent:
            # Within the (already remapped) parent row, which the subject owns.
            if values.get(spec.parent_fk) not in owned_parent_ids:
                return None
            query = query.where(getattr(model, spec.parent_fk) == values[spec.parent_fk])
        found = session.exec(query).first()
        if found is not None:
            return found
        if spec.owner or spec.parent:
            # The natural key is the identity: a row with this id but another key is another row.
            return None
    if all(values.get(k) is not None for k in pk):
        key = tuple(values[k] for k in pk)
        found = session.get(model, key if len(key) > 1 else key[0])
        if found is None:
            return None
        if spec.owner and getattr(found, spec.owner) != subject.owner_value(spec):
            return None
        if spec.parent and getattr(found, spec.parent_fk) not in owned_parent_ids:
            return None
        return found
    return None


def account_router(
    service: str,
    specs: Callable[[], list[Table]],
    get_session: Callable[..., Any],
    *,
    prefix: str = "/internal/account",
    dependencies: list[Any] | None = None,
    normalize: Callable[[str], str] = lambda name: name.strip().lower(),
) -> Any:
    """The two internal routes a SQL service mounts: export and import for one username.

    The service's own guard (its internal-secret middleware or ``dependencies``)
    must cover ``prefix``; these routes add no check of their own.
    """
    from fastapi import APIRouter, Body, Depends, HTTPException

    router = APIRouter(prefix=prefix, dependencies=dependencies or [])

    def _subject(username: str) -> Subject:
        name = normalize(username or "")
        if not name or len(name) > 128:
            raise HTTPException(status_code=422, detail="username is required")
        return Subject(username=name)

    @router.get("/export/{username}")
    def export_account(username: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        return {"service": service, "collections": export_tables(session, specs(), _subject(username))}

    @router.post("/import/{username}")
    def import_account(username: str, body: dict[str, Any] = Body(...), session: Session = Depends(get_session)) -> dict[str, Any]:
        collections = body.get("collections")
        if not isinstance(collections, dict):
            raise HTTPException(status_code=422, detail="collections must be an object")
        dry_run = bool(body.get("dry_run", True))
        results = import_tables(
            session, specs(), _subject(username), collections,
            dry_run=dry_run, replace_conflicts=bool(body.get("replace_conflicts", False)),
        )
        return {"service": service, "dry_run": dry_run, "collections": results}

    return router
